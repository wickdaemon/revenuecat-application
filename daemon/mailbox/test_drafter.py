import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from pathlib import Path

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.drafter import (
    draft, _build_system_prompt, _build_user_prompt, _load_persona
)


def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_001", thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Screening call",
        body="Hi, we'd like to schedule a call.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def test_load_persona_returns_dict():
    persona = _load_persona()
    assert "identity" in persona
    assert "voice" in persona
    assert "email_style" in persona
    assert "operator" in persona


def test_system_prompt_contains_identity():
    persona = _load_persona()
    prompt = _build_system_prompt(persona)
    assert "Daemon Wick" in prompt
    assert "wickdaemon@gmail.com" in prompt
    assert "github.com/wickdaemon" in prompt


def test_system_prompt_contains_never_say():
    persona = _load_persona()
    prompt = _build_system_prompt(persona)
    for phrase in persona["voice"]["never_say"]:
        assert phrase in prompt


def test_system_prompt_contains_operator_rules():
    persona = _load_persona()
    prompt = _build_system_prompt(persona)
    assert "my creator" in prompt
    assert "Boris" in prompt   # in the never-use list
    assert "never reveal" in prompt.lower()


def test_system_prompt_never_start_with_i():
    persona = _load_persona()
    prompt = _build_system_prompt(persona)
    assert 'Never start with the word "I"' in prompt


def test_user_prompt_contains_email_content():
    msg = make_message(
        from_addr="jobs@stripe.com",
        subject="Quick question",
        body="Can you tell us more about your architecture?",
    )
    prompt = _build_user_prompt(msg, [], "screening")
    assert "jobs@stripe.com" in prompt
    assert "Quick question" in prompt
    assert "more about your architecture" in prompt


def test_user_prompt_contains_app_state():
    msg = make_message()
    prompt = _build_user_prompt(msg, [], "screening")
    assert "screening" in prompt


def test_user_prompt_truncates_long_body():
    msg = make_message(body="x" * 5000)
    prompt = _build_user_prompt(msg, [], "applied")
    assert "x" * 1501 not in prompt


def test_user_prompt_includes_thread_history():
    msg = make_message()
    history = [{
        "from_addr": "recruiter@revenuecat.com",
        "subject": "Initial contact",
        "body": "We saw your application.",
        "timestamp": "2026-03-01T10:00:00+00:00",
    }]
    prompt = _build_user_prompt(msg, history, "screening")
    assert "We saw your application" in prompt


def test_user_prompt_limits_history_to_5():
    msg = make_message()
    history = [
        {"from_addr": f"r@r.com", "subject": f"msg{i}",
         "body": f"body{i}", "timestamp": "2026-01-01T00:00:00+00:00"}
        for i in range(10)
    ]
    prompt = _build_user_prompt(msg, history, "screening")
    # Only last 5 should appear
    assert "body9" in prompt
    assert "body4" not in prompt


@patch("daemon.mailbox.drafter.anthropic.Anthropic")
def test_draft_returns_string(mock_anthropic_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Read your message. Happy to chat.\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_response

    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")

    result = draft(make_message(), store, "revenuecat")
    assert isinstance(result, str)
    assert len(result) > 0


@patch("daemon.mailbox.drafter.anthropic.Anthropic")
def test_draft_uses_correct_model(mock_anthropic_cls, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_anthropic_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Two things:\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_response

    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")

    draft(make_message(), store, "revenuecat")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-5"
    assert call_kwargs["max_tokens"] == 300


def test_draft_raises_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        draft(make_message(), store, "revenuecat")
