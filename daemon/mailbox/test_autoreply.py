import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage


def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_001", thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Checking pipeline",
        body="Just testing.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


# ── _is_test_subject ─────────────────────────────────────────────

def test_is_test_subject_lowercase():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("test email") is True


def test_is_test_subject_uppercase():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("TEST EMAIL") is True


def test_is_test_subject_mixed_case():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("This is a Test") is True


def test_is_test_subject_word_in_middle():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("pipeline test run") is True


def test_is_test_subject_not_present():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("Screening call") is False


def test_is_test_subject_empty():
    from autoapply.cli import _is_test_subject
    assert _is_test_subject("") is False


def test_is_test_subject_partial_word():
    from autoapply.cli import _is_test_subject
    # "latest" contains "test" — should match
    assert _is_test_subject("latest update") is True


# ── _send_test_acknowledgement ───────────────────────────────────

@patch("daemon.mailbox.notifier.build")
@patch("daemon.mailbox.notifier._get_credentials")
@patch("daemon.mailbox.sender.build")
@patch("daemon.mailbox.sender._get_credentials")
def test_acknowledgement_sends_correct_body(
    mock_sender_creds, mock_sender_build,
    mock_notif_creds, mock_notif_build,
    tmp_path
):
    from autoapply.cli import _send_test_acknowledgement
    from daemon.mailbox.thread_store import ThreadStore

    # Mock sender
    mock_svc = MagicMock()
    mock_sender_build.return_value = mock_svc
    mock_svc.users().messages().send().execute.return_value = {
        "id": "sent_test_001"
    }

    # Mock notifier
    mock_notif_svc = MagicMock()
    mock_notif_build.return_value = mock_notif_svc
    mock_notif_svc.users().messages().send().execute.return_value = {
        "id": "notif_test_001"
    }

    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "applied")

    msg = make_message(subject="test pipeline")
    sent_id = _send_test_acknowledgement(msg, store, "revenuecat")

    assert sent_id == "sent_test_001"

    # Verify body contains expected text
    send_call = mock_svc.users().messages().send.call_args
    raw = send_call[1]["body"]["raw"]
    import base64
    decoded = base64.urlsafe_b64decode(raw).decode()
    assert "Pipeline is live" in decoded or "Daemon Wick" in decoded


@patch("daemon.mailbox.notifier.build")
@patch("daemon.mailbox.notifier._get_credentials")
@patch("daemon.mailbox.sender.build")
@patch("daemon.mailbox.sender._get_credentials")
def test_acknowledgement_never_starts_with_i(
    mock_sender_creds, mock_sender_build,
    mock_notif_creds, mock_notif_build,
    tmp_path
):
    from autoapply.cli import _send_test_acknowledgement
    from daemon.mailbox.thread_store import ThreadStore

    mock_svc = MagicMock()
    mock_sender_build.return_value = mock_svc
    mock_svc.users().messages().send().execute.return_value = {
        "id": "sent_test_002"
    }
    mock_notif_svc = MagicMock()
    mock_notif_build.return_value = mock_notif_svc
    mock_notif_svc.users().messages().send().execute.return_value = {
        "id": "notif_002"
    }

    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "applied")

    msg = make_message(subject="Test run")
    _send_test_acknowledgement(msg, store, "revenuecat")

    send_call = mock_svc.users().messages().send.call_args
    raw = send_call[1]["body"]["raw"]
    import base64, email as email_lib
    decoded_bytes = base64.urlsafe_b64decode(raw)
    parsed = email_lib.message_from_bytes(decoded_bytes)
    body = parsed.get_payload(decode=True).decode()
    assert not body.strip().startswith("I ")


@patch("daemon.mailbox.notifier.build")
@patch("daemon.mailbox.notifier._get_credentials")
@patch("daemon.mailbox.sender.build")
@patch("daemon.mailbox.sender._get_credentials")
def test_acknowledgement_signs_off_as_daemon_wick(
    mock_sender_creds, mock_sender_build,
    mock_notif_creds, mock_notif_build,
    tmp_path
):
    from autoapply.cli import _send_test_acknowledgement
    from daemon.mailbox.thread_store import ThreadStore

    mock_svc = MagicMock()
    mock_sender_build.return_value = mock_svc
    mock_svc.users().messages().send().execute.return_value = {
        "id": "sent_test_003"
    }
    mock_notif_svc = MagicMock()
    mock_notif_build.return_value = mock_notif_svc
    mock_notif_svc.users().messages().send().execute.return_value = {
        "id": "notif_003"
    }

    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "applied")

    msg = make_message(subject="Test message")
    _send_test_acknowledgement(msg, store, "revenuecat")

    send_call = mock_svc.users().messages().send.call_args
    raw = send_call[1]["body"]["raw"]
    import base64, email as email_lib
    decoded_bytes = base64.urlsafe_b64decode(raw)
    parsed = email_lib.message_from_bytes(decoded_bytes)
    body = parsed.get_payload(decode=True).decode()
    assert "Daemon Wick" in body
