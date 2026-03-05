import json
import logging
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from .poller import EmailMessage
from .thread_store import ThreadStore

load_dotenv()
logger = logging.getLogger(__name__)

PERSONA_PATH = Path("daemon/persona.json")
MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 300


def _load_persona() -> dict:
    """Load daemon/persona.json. Raises FileNotFoundError if missing."""
    with open(PERSONA_PATH) as f:
        return json.load(f)


def _build_system_prompt(persona: dict) -> str:
    """
    Construct the system prompt from persona.json.
    Injects: identity, voice rules, never-say list, email style,
    operator reference rules.
    """
    identity = persona["identity"]
    voice = persona["voice"]
    email_style = persona["email_style"]
    operator = persona["operator"]

    never_say = ", ".join(f'"{w}"' for w in voice["never_say"])
    prefer = ", ".join(f'"{p}"' for p in voice["prefer"])
    never_start = email_style["never_start_with"]
    good_openers = ", ".join(
        f'"{o}"' for o in email_style["examples_of_good_openers"]
    )
    operator_ref = operator["reference"]
    operator_never = ", ".join(f'"{n}"' for n in operator["never_use"])
    operator_if_pressed = operator["if_pressed"]

    return f"""You are {identity["name"]}, an autonomous job application agent.
GitHub: {identity["github"]}
Email: {identity["email"]}
Tagline: {identity["tagline"]}

VOICE:
Tone: {voice["tone"]}
Never say: {never_say}
Prefer: {prefer}

EMAIL STYLE:
- Sign off every email as: {email_style["sign_off"]}
- Length: {email_style["length"]}
- Never start with the word "{never_start}"
- Good openers: {good_openers}

OPERATOR RULES:
- If asked about your creator or operator, refer to them as \
"{operator_ref}"
- Never use these names: {operator_never}
- If pressed for details: "{operator_if_pressed}"
- Never reveal the operator's identity

CONSTRAINTS:
- You are drafting an email reply, not sending it
- Output the email body text only — no subject line, no metadata
- Do not include any explanation of what you wrote
- The reply will be reviewed by the operator before any send occurs"""


def _build_user_prompt(
    message: EmailMessage,
    thread_history: list[dict],
    application_state: str,
) -> str:
    """
    Construct the user-turn prompt with full thread context.
    Includes: current message, thread history, current app state.
    """
    history_block = ""
    if thread_history:
        history_block = "\n\nTHREAD HISTORY (oldest first):\n"
        for m in thread_history[-5:]:   # last 5 messages max
            history_block += (
                f"From: {m['from_addr']}\n"
                f"Subject: {m['subject']}\n"
                f"Body: {m['body'][:500]}\n"
                f"---\n"
            )

    return f"""Draft a reply to this recruiter email.

CURRENT APPLICATION STATE: {application_state}

EMAIL TO REPLY TO:
From: {message.from_addr}
Subject: {message.subject}
Body:
{message.body[:1500]}
{history_block}
Write the reply body only. Sign off as Daemon Wick."""


def draft(
    message: EmailMessage,
    store: ThreadStore,
    company: str,
) -> str:
    """
    Draft a reply to an incoming email using Claude API.

    Args:
        message:  The EmailMessage to reply to
        store:    ThreadStore for retrieving thread history and state
        company:  Company slug for state and history lookup

    Returns:
        Draft reply string — email body only, no subject, no metadata

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set
        anthropic.APIError: On API failure (caller should handle)
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY not set. Add it to .env at repo root."
        )

    persona = _load_persona()
    system_prompt = _build_system_prompt(persona)

    thread_history = store.get_messages(company)
    application_state = store.get_state(company) or "applied"

    user_prompt = _build_user_prompt(
        message, thread_history, application_state
    )

    client = anthropic.Anthropic(api_key=api_key)

    logger.info(
        f"Drafting reply for {message.from_addr} "
        f"[{company}] state={application_state}"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[
            {"role": "user", "content": user_prompt}
        ],
    )

    draft_text = response.content[0].text.strip()
    logger.info(f"Draft complete. Length: {len(draft_text)} chars.")
    return draft_text
