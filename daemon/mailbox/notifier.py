import base64
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .poller import _get_credentials, EmailMessage
from .classifier import ClassificationResult

logger = logging.getLogger(__name__)

OPERATOR_EMAIL = "boris.polania@gmail.com"

# Gmail scopes — must match token.json
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _get_service():
    """Build and return an authenticated Gmail API service."""
    creds = _get_credentials()
    return build("gmail", "v1", credentials=creds)


def _send_email(
    service,
    to: str,
    subject: str,
    body: str,
    from_addr: str = "wickdaemon@gmail.com",
) -> str:
    """
    Send a plain text email via Gmail API.
    Returns the sent message ID.
    Raises HttpError on failure.
    """
    msg = MIMEText(body, "plain")
    msg["To"] = to
    msg["From"] = from_addr
    msg["Subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()
    return result["id"]


def notify(
    message: EmailMessage,
    result: ClassificationResult,
    sent_draft: str | None,
    company: str,
) -> str:
    """
    Send a trace notification to the operator after an action
    has been taken on an incoming email.

    Called for ALL classifications including confirmation and
    rejection — the operator always knows what happened.

    Args:
        message:     The incoming EmailMessage that was processed
        result:      The ClassificationResult from the classifier
        sent_draft:  The draft that was sent, or None if no reply
                     was generated (confirmation, rejection)
        company:     Company slug

    Returns:
        Sent notification message ID

    Subject format:
        [SENT] Re: {original subject} ({company})
        [LOGGED] {original subject} ({company})
    """
    service = _get_service()

    if sent_draft:
        subject = f"[SENT] Re: {message.subject} ({company})"
        body = (
            f"Daemon Wick sent a reply.\n\n"
            f"CLASSIFICATION: {result.category.value} "
            f"({result.confidence:.0%} confidence)\n"
            f"COMPANY: {company}\n"
            f"FROM: {message.from_addr}\n"
            f"ORIGINAL SUBJECT: {message.subject}\n\n"
            f"{'─' * 60}\n"
            f"ORIGINAL EMAIL:\n"
            f"{message.body[:1000]}\n\n"
            f"{'─' * 60}\n"
            f"REPLY SENT:\n"
            f"{sent_draft}\n"
        )
    else:
        subject = f"[LOGGED] {message.subject} ({company})"
        body = (
            f"Daemon Wick logged an email. No reply was sent.\n\n"
            f"CLASSIFICATION: {result.category.value} "
            f"({result.confidence:.0%} confidence)\n"
            f"COMPANY: {company}\n"
            f"FROM: {message.from_addr}\n"
            f"ORIGINAL SUBJECT: {message.subject}\n\n"
            f"{'─' * 60}\n"
            f"ORIGINAL EMAIL:\n"
            f"{message.body[:1000]}\n"
        )

    sent_id = _send_email(service, OPERATOR_EMAIL, subject, body)
    logger.info(
        f"Notification sent to {OPERATOR_EMAIL} "
        f"[{result.category.value}] id={sent_id}"
    )
    return sent_id


def request_approval(
    message: EmailMessage,
    draft: str,
    result: ClassificationResult,
    company: str,
    cycle: int = 1,
) -> str:
    """
    Email the operator a draft for approval.
    Used for offer and ambiguous classifications.

    The operator replies with:
      subject contains "approve" → send the draft
      subject contains "reject"  → read body for instructions,
                                   redraft, repeat

    Args:
        message:  The incoming EmailMessage being replied to
        draft:    The proposed reply text
        result:   ClassificationResult for context
        company:  Company slug
        cycle:    Current approval cycle number (1-3)

    Returns:
        Sent approval-request message ID

    Subject format:
        [APPROVAL NEEDED] Re: {original subject} ({company})
        [REDRAFT {n}] Re: {original subject} ({company})
    """
    service = _get_service()

    if cycle == 1:
        subject = (
            f"[APPROVAL NEEDED] Re: {message.subject} ({company})"
        )
    else:
        subject = (
            f"[REDRAFT {cycle}] Re: {message.subject} ({company})"
        )

    body = (
        f"Daemon Wick needs your approval before sending "
        f"this reply.\n\n"
        f"CLASSIFICATION: {result.category.value} "
        f"({result.confidence:.0%} confidence)\n"
        f"COMPANY: {company}\n"
        f"FROM: {message.from_addr}\n"
        f"ORIGINAL SUBJECT: {message.subject}\n"
        f"CYCLE: {cycle}/3\n\n"
        f"{'─' * 60}\n"
        f"ORIGINAL EMAIL:\n"
        f"{message.body[:1000]}\n\n"
        f"{'─' * 60}\n"
        f"PROPOSED REPLY:\n"
        f"{draft}\n\n"
        f"{'─' * 60}\n"
        f"Reply to this email with:\n"
        f"  Subject: approve  → sends this draft immediately\n"
        f"  Subject: reject   → include instructions in the body,\n"
        f"                      agent will redraft and re-send\n"
        f"  Subject: stop     → kills this thread immediately,\n"
        f"                      nothing will be sent\n"
    )

    sent_id = _send_email(service, OPERATOR_EMAIL, subject, body)
    logger.info(
        f"Approval request sent to {OPERATOR_EMAIL} "
        f"[cycle {cycle}] id={sent_id}"
    )
    return sent_id
