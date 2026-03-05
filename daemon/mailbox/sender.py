import base64
import logging
from datetime import datetime, timezone
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .poller import _get_credentials, EmailMessage
from .thread_store import ThreadStore

logger = logging.getLogger(__name__)

# Both scopes required — token.json must have been issued with both.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def _build_reply(
    original: EmailMessage,
    body: str,
    from_addr: str = "wickdaemon@gmail.com",
) -> dict:
    """
    Construct a Gmail API message dict for sending.
    Sets Reply-To, In-Reply-To, and References headers correctly
    so Gmail threads the reply with the original message.
    """
    msg = MIMEText(body, "plain")
    msg["To"] = original.from_addr
    msg["From"] = from_addr
    msg["Subject"] = (
        original.subject
        if original.subject.lower().startswith("re:")
        else f"Re: {original.subject}"
    )
    msg["In-Reply-To"] = original.id
    msg["References"] = original.id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {
        "raw": raw,
        "threadId": original.thread_id,
    }


def send(
    original: EmailMessage,
    approved_draft: str,
    store: ThreadStore,
    company: str,
    edits_made: bool = False,
) -> str:
    """
    Send an approved draft reply via Gmail API.

    Args:
        original:       The EmailMessage being replied to
        approved_draft: The final text approved by the operator
        store:          ThreadStore for logging the sent message
        company:        Company slug for state update
        edits_made:     Whether the operator edited the draft

    Returns:
        The sent Gmail message ID

    Raises:
        HttpError: On Gmail API failure
        FileNotFoundError: If token.json missing
        PermissionError: If token lacks gmail.send scope
    """
    creds = _get_credentials()
    service = build("gmail", "v1", credentials=creds)

    message_body = _build_reply(original, approved_draft)

    try:
        sent = service.users().messages().send(
            userId="me",
            body=message_body,
        ).execute()
    except HttpError as e:
        if e.resp.status == 403:
            raise PermissionError(
                "Gmail send failed: token may be missing gmail.send scope. "
                "Delete token.json and re-run OAuth flow with both scopes."
            ) from e
        raise

    sent_id = sent["id"]
    now = datetime.now(timezone.utc)

    logger.info(
        f"Sent reply to {original.from_addr} "
        f"[{company}] message_id={sent_id} "
        f"edits={'yes' if edits_made else 'no'}"
    )

    # Log the sent message back to thread store
    sent_message = EmailMessage(
        id=sent_id,
        thread_id=original.thread_id,
        from_addr="wickdaemon@gmail.com",
        subject=f"Re: {original.subject}",
        body=approved_draft,
        timestamp=now,
    )
    store.log_message(original.thread_id, sent_message, company)

    return sent_id
