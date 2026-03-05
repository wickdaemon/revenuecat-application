import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.classifier import ClassificationResult, Category
from daemon.mailbox.approval_loop import (
    run, ApprovalLoopDecision, ApprovalLoopResult,
    MAX_CYCLES,
)


def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_001", thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Offer letter",
        body="We'd like to extend an offer.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def make_result(category="offer"):
    return ClassificationResult(
        category=Category(category),
        confidence=0.95,
        raw_response="",
        model="qwen2.5:3b",
        fallback=False,
    )


def mock_store(tmp_path):
    from daemon.mailbox.thread_store import ThreadStore
    store = ThreadStore(db_path=tmp_path / "test.db")
    store.set_state("revenuecat", "screening")
    return store


# ── Approve on first cycle ────────────────────────────────────────

@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft text.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "approve", "body": ""})
def test_approve_cycle_1_sends(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    mock_send.return_value = "sent_001"
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.sent
    assert result.cycles_used == 1
    assert result.sent_id == "sent_001"
    mock_send.assert_called_once()


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "approve", "body": ""})
def test_approve_returns_final_draft(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    mock_send.return_value = "sent_001"
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.final_draft == "Draft.\n\nDaemon Wick"


# ── Reject then approve ───────────────────────────────────────────

@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply")
@patch("daemon.mailbox.approval_loop.anthropic")
def test_reject_then_approve_uses_2_cycles(
    mock_anthropic, mock_poll, mock_draft,
    mock_request, mock_send, tmp_path
):
    # Cycle 1: reject. Cycle 2: approve.
    mock_poll.side_effect = [
        {"decision": "reject", "body": "Make it shorter."},
        {"decision": "approve", "body": ""},
    ]
    mock_send.return_value = "sent_002"

    # Mock Claude API for redraft
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(
        text="Shorter draft.\n\nDaemon Wick"
    )]
    mock_client.messages.create.return_value = mock_resp

    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.sent
    assert result.cycles_used == 2
    assert mock_request.call_count == 2


# ── Max cycles reached ────────────────────────────────────────────

@patch("daemon.mailbox.approval_loop._send_email")
@patch("daemon.mailbox.approval_loop.build")
@patch("daemon.mailbox.approval_loop._get_credentials")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "reject", "body": "Try again."})
@patch("daemon.mailbox.approval_loop.anthropic")
def test_max_cycles_returns_rejected(
    mock_anthropic, mock_poll, mock_draft, mock_send,
    mock_request, mock_creds, mock_build, mock_send_email,
    tmp_path
):
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Redraft.\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_resp

    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.cycles_used == MAX_CYCLES
    assert result.sent_id is None
    assert mock_request.call_count == MAX_CYCLES


@patch("daemon.mailbox.approval_loop._send_email")
@patch("daemon.mailbox.approval_loop.build")
@patch("daemon.mailbox.approval_loop._get_credentials")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "reject", "body": "Instructions."})
@patch("daemon.mailbox.approval_loop.anthropic")
def test_max_cycles_never_sends(
    mock_anthropic, mock_poll, mock_draft, mock_send,
    mock_request, mock_creds, mock_build, mock_send_email,
    tmp_path
):
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Redraft.\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_resp

    run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    mock_send.assert_not_called()


# ── Timeout ───────────────────────────────────────────────────────

@patch("daemon.mailbox.approval_loop._send_email")
@patch("daemon.mailbox.approval_loop.build")
@patch("daemon.mailbox.approval_loop._get_credentials")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value=None)
def test_timeout_returns_timed_out(
    mock_poll, mock_draft, mock_request,
    mock_creds, mock_build, mock_send_email, tmp_path
):
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.timed_out
    assert result.sent_id is None


@patch("daemon.mailbox.approval_loop._send_email")
@patch("daemon.mailbox.approval_loop.build")
@patch("daemon.mailbox.approval_loop._get_credentials")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value=None)
def test_timeout_never_sends(
    mock_poll, mock_draft, mock_send, mock_request,
    mock_creds, mock_build, mock_send_email, tmp_path
):
    run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    mock_send.assert_not_called()


# ── Reject with empty body (kill signal) ─────────────────────────

@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "reject", "body": ""})
def test_reject_empty_body_kills_immediately(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    """Empty body reject → killed at cycle 1, never redrafts."""
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.cycles_used == 1
    assert result.sent_id is None
    # Only one request_approval call (cycle 1) — no redraft
    assert mock_request.call_count == 1


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "reject", "body": "   "})
def test_reject_whitespace_body_kills_immediately(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    """Whitespace-only body reject → killed immediately."""
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.cycles_used == 1
    mock_send.assert_not_called()


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply")
@patch("daemon.mailbox.approval_loop.anthropic")
def test_reject_with_instructions_still_redrafts(
    mock_anthropic, mock_poll, mock_draft,
    mock_request, mock_send, tmp_path
):
    """Non-empty body reject → still redrafts (existing behavior)."""
    mock_poll.side_effect = [
        {"decision": "reject", "body": "Make it shorter."},
        {"decision": "approve", "body": ""},
    ]
    mock_send.return_value = "sent_003"

    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Short.\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_resp

    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.sent
    assert result.cycles_used == 2
    assert mock_request.call_count == 2


# ── Stop signal ──────────────────────────────────────────────────

@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "stop", "body": "", "subject": "stop"})
def test_stop_signal_kills_immediately(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    """Stop signal → killed immediately, never sends."""
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.cycles_used == 1
    assert result.sent_id is None
    mock_send.assert_not_called()


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "stop", "body": "some body text",
                     "subject": "stop this"})
def test_stop_signal_with_body_still_kills(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    """Stop with non-empty body → still kills. Body is ignored."""
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.sent_id is None
    mock_send.assert_not_called()


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply")
@patch("daemon.mailbox.approval_loop.anthropic")
def test_stop_after_reject_kills(
    mock_anthropic, mock_poll, mock_draft,
    mock_request, mock_send, tmp_path
):
    """Reject cycle 1 with instructions, then stop on cycle 2."""
    mock_poll.side_effect = [
        {"decision": "reject", "body": "Make it shorter.",
         "subject": "reject"},
        {"decision": "stop", "body": "", "subject": "stop"},
    ]
    mock_client = MagicMock()
    mock_anthropic.Anthropic.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(text="Short.\n\nDaemon Wick")]
    mock_client.messages.create.return_value = mock_resp

    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    assert result.cycles_used == 2
    assert result.sent_id is None
    mock_send.assert_not_called()


@patch("daemon.mailbox.approval_loop.send")
@patch("daemon.mailbox.approval_loop.request_approval")
@patch("daemon.mailbox.approval_loop.draft_reply",
       return_value="Draft.\n\nDaemon Wick")
@patch("daemon.mailbox.approval_loop._poll_for_reply",
       return_value={"decision": "stop", "body": "",
                     "subject": "stop - too aggressive"})
def test_stop_subject_with_extra_text_kills(
    mock_poll, mock_draft, mock_request, mock_send, tmp_path
):
    """'stop - too aggressive' contains stop → kills immediately."""
    result = run(
        make_message(), make_result(),
        mock_store(tmp_path), "revenuecat",
    )
    assert result.decision == ApprovalLoopDecision.rejected
    mock_send.assert_not_called()


# ── MAX_CYCLES constant ───────────────────────────────────────────

def test_max_cycles_is_3():
    assert MAX_CYCLES == 3
