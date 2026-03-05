import base64
import email as email_lib
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .thread_store import ThreadStore

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS_PATH = Path("credentials.json")
TOKEN_PATH = Path("token.json")
POLL_INTERVAL_SECONDS = 900  # 15 minutes


@dataclass
class EmailMessage:
    id: str                    # Gmail message ID
    thread_id: str             # Gmail thread ID
    from_addr: str
    subject: str
    body: str                  # plain text body, decoded
    timestamp: datetime        # UTC


def _get_credentials() -> Credentials:
    """
    Load credentials from token.json if available and valid.
    Refresh if expired. Run OAuth flow if no token exists.
    token.json is written after first successful auth and reused.
    """
    creds = None

    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"credentials.json not found at {CREDENTIALS_PATH}. "
                    "Download OAuth credentials from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds


def _decode_body(payload: dict) -> str:
    """
    Extract plain text body from a Gmail message payload.
    Handles both simple (body.data) and multipart MIME structures.
    Returns empty string if no plain text part found.
    """
    def _extract(part: dict) -> Optional[str]:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        if mime.startswith("multipart/"):
            for subpart in part.get("parts", []):
                result = _extract(subpart)
                if result:
                    return result
        return None

    return _extract(payload) or ""


def _parse_message(raw: dict) -> EmailMessage:
    """
    Convert a raw Gmail API message dict to an EmailMessage.
    Extracts headers (From, Subject, Date) and decodes body.
    """
    headers = {
        h["name"].lower(): h["value"]
        for h in raw.get("payload", {}).get("headers", [])
    }

    # Gmail internalDate is milliseconds since epoch
    ts_ms = int(raw.get("internalDate", 0))
    timestamp = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

    return EmailMessage(
        id=raw["id"],
        thread_id=raw["threadId"],
        from_addr=headers.get("from", ""),
        subject=headers.get("subject", "(no subject)"),
        body=_decode_body(raw.get("payload", {})),
        timestamp=timestamp,
    )


def _infer_company(message: EmailMessage) -> str:
    """
    Infer company name from sender email domain.
    e.g. "recruiting@revenuecat.com" -> "revenuecat"
    Falls back to full from_addr if domain parsing fails.
    """
    try:
        domain = message.from_addr.split("@")[1].split(">")[0]
        company = domain.split(".")[0]
        return company.lower()
    except (IndexError, AttributeError):
        return message.from_addr.lower()


class GmailPoller:
    """
    Polls wickdaemon@gmail.com for new messages.
    Yields EmailMessage objects for messages not previously seen.
    Tracks seen message IDs in ThreadStore to prevent reprocessing.
    """

    def __init__(self, store: Optional[ThreadStore] = None):
        self.store = store or ThreadStore()
        self._service = None

    def _get_service(self):
        if self._service is None:
            creds = _get_credentials()
            self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def fetch_new_messages(self) -> Generator[EmailMessage, None, None]:
        """
        Fetch all unread messages not previously seen.
        Yields EmailMessage objects one at a time.
        Marks each as seen after yielding.
        """
        service = self._get_service()

        try:
            result = service.users().messages().list(
                userId="me",
                q="is:unread",
                maxResults=50,
            ).execute()
        except HttpError as e:
            logger.error(f"Gmail API error listing messages: {e}")
            return

        messages = result.get("messages", [])
        if not messages:
            logger.debug("No unread messages found.")
            return

        for msg_stub in messages:
            msg_id = msg_stub["id"]

            if self.store.is_seen(msg_id):
                logger.debug(f"Skipping already-seen message {msg_id}")
                continue

            try:
                raw = service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()
            except HttpError as e:
                logger.error(f"Gmail API error fetching message {msg_id}: {e}")
                continue

            message = _parse_message(raw)
            company = _infer_company(message)

            # Ensure company is tracked in state store
            if self.store.get_state(company) is None:
                self.store.set_state(company, "applied")

            self.store.log_message(message.thread_id, message, company)
            self.store.mark_seen(msg_id)

            logger.info(
                f"New message from {message.from_addr} "
                f"[{company}] subject='{message.subject}'"
            )
            yield message

    def watch(self) -> None:
        """
        Poll loop. Runs forever, polling every POLL_INTERVAL_SECONDS.
        Logs each poll cycle. Catches and logs exceptions without
        crashing — a transient Gmail API failure should not kill
        the watcher.
        """
        logger.info(
            f"Starting Gmail poller. Interval: {POLL_INTERVAL_SECONDS}s "
            f"({POLL_INTERVAL_SECONDS // 60} minutes)"
        )

        while True:
            logger.info("Polling for new messages...")
            try:
                count = 0
                for message in self.fetch_new_messages():
                    count += 1
                logger.info(f"Poll complete. {count} new message(s) processed.")
            except Exception as e:
                logger.error(f"Unexpected error during poll: {e}", exc_info=True)

            logger.info(f"Sleeping {POLL_INTERVAL_SECONDS}s until next poll...")
            time.sleep(POLL_INTERVAL_SECONDS)
