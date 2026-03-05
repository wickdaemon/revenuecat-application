import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.autosender import (
    review_and_send, AutoSendDecision, AutoSendResult,
    DEFAULT_KILL_WINDOW,
)


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


DRAFT = "Read your message. Happy to chat.\n\nDaemon Wick"


# ── Happy path — countdown completes ─────────────────────────────

@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_countdown_completes_returns_sent(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=3)
    assert result.decision == AutoSendDecision.sent
    assert result.draft == DRAFT
    assert mock_sleep.call_count == 3


@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_sent_result_preserves_draft(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=1)
    assert result.draft == DRAFT


@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_default_kill_window_is_60(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT)
    assert mock_sleep.call_count == DEFAULT_KILL_WINDOW
    assert DEFAULT_KILL_WINDOW == 60


# ── Abort path — Ctrl+C during countdown ─────────────────────────

@patch("daemon.mailbox.autosender.time.sleep",
       side_effect=KeyboardInterrupt)
@patch("daemon.mailbox.autosender.console")
def test_ctrl_c_returns_aborted(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=5)
    assert result.decision == AutoSendDecision.aborted
    assert result.draft == DRAFT


@patch("daemon.mailbox.autosender.time.sleep",
       side_effect=KeyboardInterrupt)
@patch("daemon.mailbox.autosender.console")
def test_abort_does_not_raise(mock_console, mock_sleep):
    # Must not propagate KeyboardInterrupt to caller
    try:
        review_and_send(make_message(), DRAFT, kill_window=5)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt leaked out of review_and_send")


@patch("daemon.mailbox.autosender.time.sleep",
       side_effect=KeyboardInterrupt)
@patch("daemon.mailbox.autosender.console")
def test_abort_preserves_draft(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=5)
    assert result.draft == DRAFT


# ── Kill window parameter ─────────────────────────────────────────

@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_kill_window_zero_sends_immediately(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=0)
    assert result.decision == AutoSendDecision.sent
    assert mock_sleep.call_count == 0


@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_custom_kill_window_respected(mock_console, mock_sleep):
    review_and_send(make_message(), DRAFT, kill_window=10)
    assert mock_sleep.call_count == 10


# ── Display ───────────────────────────────────────────────────────

@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_display_called_before_countdown(mock_console, mock_sleep):
    review_and_send(make_message(), DRAFT, kill_window=1)
    # Console was called (display happened)
    assert mock_console.print.called


@patch("daemon.mailbox.autosender.time.sleep")
@patch("daemon.mailbox.autosender.console")
def test_result_has_elapsed_time(mock_console, mock_sleep):
    result = review_and_send(make_message(), DRAFT, kill_window=2)
    assert isinstance(result.elapsed, float)
    assert result.elapsed >= 0
