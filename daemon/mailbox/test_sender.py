import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.sender import send, _build_reply


def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_gmail_001",
        thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Screening call",
        body="We'd like to schedule a call.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def test_build_reply_sets_headers():
    msg = make_message(subject="Hello")
    body = _build_reply(msg, "Reply body.")
    assert "raw" in body
    assert body["threadId"] == "thread_001"


def test_build_reply_prepends_re():
    msg = make_message(subject="Hello")
    body = _build_reply(msg, "Reply body.")
    import base64, email
    decoded = base64.urlsafe_b64decode(body["raw"]).decode()
    assert "Re: Hello" in decoded


def test_build_reply_no_double_re():
    msg = make_message(subject="Re: Hello")
    body = _build_reply(msg, "Reply body.")
    import base64
    decoded = base64.urlsafe_b64decode(body["raw"]).decode()
    assert "Re: Re:" not in decoded


def test_build_reply_contains_body():
    msg = make_message()
    body = _build_reply(msg, "This is my reply text.")
    import base64
    decoded = base64.urlsafe_b64decode(body["raw"]).decode()
    assert "This is my reply text." in decoded


@patch("daemon.mailbox.sender._get_credentials")
@patch("daemon.mailbox.sender.build")
def test_send_returns_message_id(mock_build, mock_creds, tmp_path):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {
        "id": "sent_msg_001"
    }

    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")

    sent_id = send(
        make_message(), "Reply text.", store, "revenuecat"
    )
    assert sent_id == "sent_msg_001"


@patch("daemon.mailbox.sender._get_credentials")
@patch("daemon.mailbox.sender.build")
def test_send_logs_to_store(mock_build, mock_creds, tmp_path):
    mock_service = MagicMock()
    mock_build.return_value = mock_service
    mock_service.users().messages().send().execute.return_value = {
        "id": "sent_msg_002"
    }

    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")

    send(make_message(), "Reply text.", store, "revenuecat")

    messages = store.get_messages("revenuecat")
    sent = [m for m in messages if m["message_id"] == "sent_msg_002"]
    assert len(sent) == 1
    assert sent[0]["from_addr"] == "wickdaemon@gmail.com"


@patch("daemon.mailbox.sender._get_credentials")
@patch("daemon.mailbox.sender.build")
def test_send_raises_permission_error_on_403(
    mock_build, mock_creds, tmp_path
):
    from googleapiclient.errors import HttpError
    from unittest.mock import MagicMock
    import json

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    mock_resp = MagicMock()
    mock_resp.status = 403
    mock_service.users().messages().send().execute.side_effect = (
        HttpError(mock_resp, b"Forbidden")
    )

    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")

    with pytest.raises(PermissionError, match="gmail.send scope"):
        send(make_message(), "Reply.", store, "revenuecat")
