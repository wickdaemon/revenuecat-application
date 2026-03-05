import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .poller import _get_credentials, EmailMessage
from .classifier import ClassificationResult
from .thread_store import ThreadStore
from .drafter import draft as draft_reply
from .notifier import request_approval, _send_email, OPERATOR_EMAIL
from .sender import send

logger = logging.getLogger(__name__)

MAX_CYCLES = 3
POLL_INTERVAL = 60       # seconds between inbox checks
APPROVAL_TIMEOUT = 86400 # 24 hours — give up if no reply


class ApprovalLoopDecision(str, Enum):
    sent      = "sent"       # approved and sent
    rejected  = "rejected"   # max cycles reached, not sent
    timed_out = "timed_out"  # no reply within 24 hours


@dataclass
class ApprovalLoopResult:
    decision: ApprovalLoopDecision
    cycles_used: int
    final_draft: Optional[str]   # the draft that was sent, or None
    sent_id: Optional[str]       # Gmail message ID if sent


def _get_operator_reply(
    since_timestamp: float,
    original_subject: str,
) -> Optional[dict]:
    """
    Poll Boris's inbox for a reply to an approval request.

    Looks for emails TO wickdaemon@gmail.com FROM
    boris.polania@gmail.com where the subject contains
    "approve" or "reject" (case-insensitive), received
    after since_timestamp.

    Returns the first matching message dict, or None.
    Message dict contains: subject, body, decision
      decision is "approve" or "reject"
    """
    creds = _get_credentials()
    service = build("gmail", "v1", credentials=creds)

    # Search for replies from operator after the request was sent
    after_epoch = int(since_timestamp)
    query = (
        f"from:{OPERATOR_EMAIL} "
        f"after:{after_epoch} "
        f"(subject:approve OR subject:reject OR subject:stop)"
    )

    try:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=10,
        ).execute()
    except HttpError as e:
        logger.error(f"Gmail API error polling for reply: {e}")
        return None

    messages = result.get("messages", [])
    if not messages:
        return None

    # Get the most recent matching message
    for msg_stub in messages:
        try:
            raw = service.users().messages().get(
                userId="me",
                id=msg_stub["id"],
                format="full",
            ).execute()
        except HttpError:
            continue

        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }
        subject = headers.get("subject", "").lower()

        if "stop" in subject:
            return {"decision": "stop", "body": "", "subject": subject}
        elif "approve" in subject:
            return {"decision": "approve", "body": "", "subject": subject}
        elif "reject" in subject:
            # Extract body for redraft instructions
            from .poller import _decode_body
            body = _decode_body(raw.get("payload", {}))
            return {
                "decision": "reject",
                "body": body,
                "subject": subject,
            }

    return None


def _poll_for_reply(
    since_timestamp: float,
    original_subject: str,
    poll_interval: int = POLL_INTERVAL,
    timeout: int = APPROVAL_TIMEOUT,
) -> Optional[dict]:
    """
    Poll until a reply arrives or timeout is reached.
    Returns the reply dict or None on timeout.
    Logs each poll attempt.
    """
    elapsed = 0
    attempt = 0

    while elapsed < timeout:
        attempt += 1
        logger.info(
            f"Polling for operator reply "
            f"(attempt {attempt}, elapsed {elapsed}s)..."
        )

        reply = _get_operator_reply(since_timestamp, original_subject)
        if reply:
            logger.info(
                f"Reply received: {reply['decision']} "
                f"after {elapsed}s"
            )
            return reply

        time.sleep(poll_interval)
        elapsed += poll_interval

    logger.warning(
        f"Approval timeout after {timeout}s. "
        f"No reply from {OPERATOR_EMAIL}."
    )
    return None


def run(
    message: EmailMessage,
    result: ClassificationResult,
    store: ThreadStore,
    company: str,
    poll_interval: int = POLL_INTERVAL,
    timeout: int = APPROVAL_TIMEOUT,
) -> ApprovalLoopResult:
    """
    Run the email-based approval loop for offer/ambiguous emails.

    Flow:
      1. Draft a reply using Claude API
      2. Email draft to operator via request_approval()
      3. Poll inbox for approve/reject reply
      4. If approve: send the draft, return sent
      5. If reject: read instructions from email body,
                    redraft incorporating instructions,
                    increment cycle, repeat from step 2
      6. If max cycles (3) reached without approval:
                    notify operator, return rejected
      7. If timeout: return timed_out

    Args:
        message:       The incoming EmailMessage to reply to
        result:        ClassificationResult for context
        store:         ThreadStore for draft context and logging
        company:       Company slug
        poll_interval: Seconds between inbox polls (default 60)
        timeout:       Max seconds to wait for reply (default 86400)

    Returns:
        ApprovalLoopResult with decision, cycles used, final draft
    """
    current_draft = None
    redraft_instructions = None

    for cycle in range(1, MAX_CYCLES + 1):
        logger.info(
            f"Approval loop cycle {cycle}/{MAX_CYCLES} "
            f"for {company} [{result.category.value}]"
        )

        # Draft (or redraft with instructions)
        if redraft_instructions:
            # Inject operator instructions into a new draft
            from .drafter import _build_system_prompt, _load_persona
            load_dotenv()

            persona = _load_persona()
            system_prompt = _build_system_prompt(persona)

            thread_history = store.get_messages(company)
            app_state = store.get_state(company) or "applied"

            user_prompt = (
                f"Draft a reply to this recruiter email.\n\n"
                f"CURRENT APPLICATION STATE: {app_state}\n\n"
                f"EMAIL TO REPLY TO:\n"
                f"From: {message.from_addr}\n"
                f"Subject: {message.subject}\n"
                f"Body:\n{message.body[:1500]}\n\n"
                f"PREVIOUS DRAFT (rejected):\n"
                f"{current_draft}\n\n"
                f"OPERATOR INSTRUCTIONS FOR REDRAFT:\n"
                f"{redraft_instructions}\n\n"
                f"Write a new reply incorporating the operator's "
                f"instructions. Reply body only. Sign off as "
                f"Daemon Wick."
            )

            client = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY")
            )
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=300,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            current_draft = response.content[0].text.strip()

        else:
            # First cycle — standard draft
            current_draft = draft_reply(message, store, company)

        # Email draft to operator for approval
        request_sent_at = time.time()
        request_approval(
            message, current_draft, result, company, cycle=cycle
        )

        # Poll for operator reply
        reply = _poll_for_reply(
            since_timestamp=request_sent_at,
            original_subject=message.subject,
            poll_interval=poll_interval,
            timeout=timeout,
        )

        if reply is None:
            # Timeout — give up
            logger.warning(
                f"Approval loop timed out at cycle {cycle}."
            )
            _send_email(
                build("gmail", "v1", credentials=_get_credentials()),
                OPERATOR_EMAIL,
                f"[TIMED OUT] Re: {message.subject} ({company})",
                f"No reply received within {timeout}s. "
                f"Draft was NOT sent. Manual action required.\n\n"
                f"Draft:\n{current_draft}",
            )
            return ApprovalLoopResult(
                decision=ApprovalLoopDecision.timed_out,
                cycles_used=cycle,
                final_draft=None,
                sent_id=None,
            )

        if reply["decision"] == "approve":
            # Send the draft
            sent_id = send(
                message, current_draft, store, company,
                edits_made=False,
            )
            logger.info(
                f"Approval loop: approved at cycle {cycle}. "
                f"Sent id={sent_id}"
            )
            return ApprovalLoopResult(
                decision=ApprovalLoopDecision.sent,
                cycles_used=cycle,
                final_draft=current_draft,
                sent_id=sent_id,
            )

        elif reply["decision"] == "stop":
            logger.info(
                f"Approval loop: STOP signal received at cycle {cycle}. "
                f"Draft NOT sent."
            )
            return ApprovalLoopResult(
                decision=ApprovalLoopDecision.rejected,
                cycles_used=cycle,
                final_draft=None,
                sent_id=None,
            )

        elif reply["decision"] == "reject":
            redraft_instructions = reply["body"].strip()

            # Empty body = kill signal. Stop immediately.
            if not redraft_instructions:
                logger.info(
                    f"Approval loop: reject with no instructions "
                    f"at cycle {cycle}. Kill signal received. "
                    f"Draft NOT sent."
                )
                return ApprovalLoopResult(
                    decision=ApprovalLoopDecision.rejected,
                    cycles_used=cycle,
                    final_draft=None,
                    sent_id=None,
                )

            logger.info(
                f"Approval loop: rejected at cycle {cycle}. "
                f"Instructions: {redraft_instructions[:100]}..."
            )
            if cycle == MAX_CYCLES:
                # Max cycles reached — notify and give up
                logger.warning(
                    f"Max cycles ({MAX_CYCLES}) reached. "
                    f"Draft NOT sent."
                )
                _send_email(
                    build(
                        "gmail", "v1",
                        credentials=_get_credentials()
                    ),
                    OPERATOR_EMAIL,
                    f"[MAX CYCLES] Re: {message.subject} ({company})",
                    f"Daemon Wick reached the maximum redraft limit "
                    f"({MAX_CYCLES} cycles). Draft was NOT sent. "
                    f"Manual action required.\n\n"
                    f"Last draft:\n{current_draft}\n\n"
                    f"Last rejection instructions:\n"
                    f"{redraft_instructions}",
                )
                return ApprovalLoopResult(
                    decision=ApprovalLoopDecision.rejected,
                    cycles_used=cycle,
                    final_draft=None,
                    sent_id=None,
                )
            # Continue to next cycle with instructions
            continue

    # Should not reach here
    return ApprovalLoopResult(
        decision=ApprovalLoopDecision.rejected,
        cycles_used=MAX_CYCLES,
        final_draft=None,
        sent_id=None,
    )
