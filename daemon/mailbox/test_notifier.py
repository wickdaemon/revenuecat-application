import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.classifier import ClassificationResult, Category
from daemon.mailbox.notifier import notify, request_approval, OPERATOR_EMAIL


def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_001", thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Screening call",
        body="We'd like to schedule a call.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def make_result(category="screening", confidence=0.92):
    return ClassificationResult(
        category=Category(category),
        confidence=confidence,
        raw_response="",
        model="qwen2.5:3b",
        fallback=False,
    )


def mock_service():
    svc = MagicMock()
    svc.users().messages().send().execute.return_value = {
        "id": "notif_001"
    }
    return svc


@patch("daemon.mailbox.notifier._get_service")
def test_notify_sent_returns_id(mock_get_svc):
    mock_get_svc.return_value = mock_service()
    msg_id = notify(
        make_message(), make_result(),
        sent_draft="Read your message.\n\nDaemon Wick",
        company="revenuecat",
    )
    assert msg_id == "notif_001"


@patch("daemon.mailbox.notifier._get_service")
def test_notify_sent_subject_format(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    notify(
        make_message(), make_result(),
        sent_draft="Draft text.",
        company="revenuecat",
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64, email
    decoded = base64.urlsafe_b64decode(
        call_body["raw"]
    ).decode()
    assert "[SENT]" in decoded
    assert "revenuecat" in decoded


@patch("daemon.mailbox.notifier._get_service")
def test_notify_logged_subject_format(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    notify(
        make_message(), make_result(category="confirmation"),
        sent_draft=None,
        company="revenuecat",
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64
    decoded = base64.urlsafe_b64decode(call_body["raw"]).decode()
    assert "[LOGGED]" in decoded


@patch("daemon.mailbox.notifier._get_service")
def test_notify_sent_to_operator(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    notify(
        make_message(), make_result(),
        sent_draft="Draft.",
        company="revenuecat",
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64
    decoded = base64.urlsafe_b64decode(call_body["raw"]).decode()
    assert OPERATOR_EMAIL in decoded


@patch("daemon.mailbox.notifier._get_service")
def test_request_approval_cycle_1_subject(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    request_approval(
        make_message(), "Draft text.",
        make_result(category="offer"),
        company="revenuecat", cycle=1,
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64
    decoded = base64.urlsafe_b64decode(call_body["raw"]).decode()
    assert "[APPROVAL NEEDED]" in decoded


@patch("daemon.mailbox.notifier._get_service")
def test_request_approval_redraft_subject(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    request_approval(
        make_message(), "Draft text.",
        make_result(category="offer"),
        company="revenuecat", cycle=2,
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64
    decoded = base64.urlsafe_b64decode(call_body["raw"]).decode()
    assert "[REDRAFT 2]" in decoded


@patch("daemon.mailbox.notifier._get_service")
def test_request_approval_contains_instructions(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    request_approval(
        make_message(), "Draft text.",
        make_result(category="ambiguous"),
        company="revenuecat", cycle=1,
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64, email as email_mod
    raw_bytes = base64.urlsafe_b64decode(call_body["raw"])
    msg = email_mod.message_from_bytes(raw_bytes)
    body = msg.get_payload(decode=True).decode()
    assert "approve" in body
    assert "reject" in body


@patch("daemon.mailbox.notifier._get_service")
def test_request_approval_contains_draft(mock_get_svc):
    svc = mock_service()
    mock_get_svc.return_value = svc
    request_approval(
        make_message(), "My specific draft content.",
        make_result(category="offer"),
        company="revenuecat", cycle=1,
    )
    call_body = svc.users().messages().send.call_args[1]["body"]
    import base64, email as email_mod
    raw_bytes = base64.urlsafe_b64decode(call_body["raw"])
    msg = email_mod.message_from_bytes(raw_bytes)
    body = msg.get_payload(decode=True).decode()
    assert "My specific draft content." in body
