import pytest
from pathlib import Path
from daemon.mailbox.thread_store import ThreadStore, STATES
from daemon.mailbox.poller import EmailMessage, _infer_company, _decode_body
from datetime import datetime, timezone

@pytest.fixture
def store(tmp_path):
    return ThreadStore(db_path=tmp_path / "test.db")

def test_set_and_get_state(store):
    store.set_state("revenuecat", "applied")
    assert store.get_state("revenuecat") == "applied"

def test_update_state(store):
    store.set_state("revenuecat", "applied")
    store.set_state("revenuecat", "screening")
    assert store.get_state("revenuecat") == "screening"

def test_invalid_state(store):
    with pytest.raises(ValueError):
        store.set_state("revenuecat", "hired")

def test_get_state_unknown_company(store):
    assert store.get_state("nonexistent") is None

def test_mark_seen_and_is_seen(store):
    assert not store.is_seen("msg_abc")
    store.mark_seen("msg_abc")
    assert store.is_seen("msg_abc")

def test_log_message(store):
    store.set_state("revenuecat", "applied")
    msg = EmailMessage(
        id="msg_001",
        thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Your application",
        body="Thanks for applying.",
        timestamp=datetime.now(timezone.utc),
    )
    store.log_message("thread_001", msg, "revenuecat")
    messages = store.get_messages("revenuecat")
    assert len(messages) == 1
    assert messages[0]["subject"] == "Your application"

def test_log_message_idempotent(store):
    store.set_state("revenuecat", "applied")
    msg = EmailMessage(
        id="msg_002",
        thread_id="thread_001",
        from_addr="a@b.com",
        subject="Test",
        body="Body",
        timestamp=datetime.now(timezone.utc),
    )
    store.log_message("thread_001", msg, "revenuecat")
    store.log_message("thread_001", msg, "revenuecat")  # duplicate
    assert len(store.get_messages("revenuecat")) == 1

def test_infer_company_standard():
    msg = EmailMessage(
        id="x", thread_id="t",
        from_addr="jobs@revenuecat.com",
        subject="", body="",
        timestamp=datetime.now(timezone.utc)
    )
    assert _infer_company(msg) == "revenuecat"

def test_infer_company_angle_brackets():
    msg = EmailMessage(
        id="x", thread_id="t",
        from_addr="Recruiter <jobs@lever.co>",
        subject="", body="",
        timestamp=datetime.now(timezone.utc)
    )
    assert _infer_company(msg) == "lever"

def test_infer_company_fallback():
    msg = EmailMessage(
        id="x", thread_id="t",
        from_addr="notanemail",
        subject="", body="",
        timestamp=datetime.now(timezone.utc)
    )
    assert _infer_company(msg) == "notanemail"

def test_decode_body_simple():
    import base64
    data = base64.urlsafe_b64encode(b"Hello world").decode()
    payload = {"mimeType": "text/plain", "body": {"data": data}}
    assert _decode_body(payload) == "Hello world"

def test_decode_body_multipart():
    import base64
    data = base64.urlsafe_b64encode(b"Plain text").decode()
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": data}},
            {"mimeType": "text/html", "body": {"data": data}},
        ]
    }
    assert _decode_body(payload) == "Plain text"

def test_decode_body_empty():
    assert _decode_body({}) == ""

def test_list_applications(store):
    store.set_state("revenuecat", "screening")
    store.set_state("stripe", "applied")
    apps = store.list_applications()
    companies = [a["company"] for a in apps]
    assert "revenuecat" in companies
    assert "stripe" in companies
