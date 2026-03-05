import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.approver import (
    review, ApprovalDecision, ApprovalResult
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


@patch("daemon.mailbox.approver.input", return_value="a")
@patch("daemon.mailbox.approver.console")
def test_approve_returns_approved(mock_console, mock_input):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.approved
    assert result.final_draft == DRAFT
    assert result.edits_made is False


@patch("daemon.mailbox.approver.input", return_value="s")
@patch("daemon.mailbox.approver.console")
def test_skip_returns_skipped(mock_console, mock_input):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.skipped
    assert result.final_draft is None


@patch("daemon.mailbox.approver.input", return_value="f")
@patch("daemon.mailbox.approver.console")
def test_flag_returns_flagged(mock_console, mock_input):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.flagged
    assert result.final_draft is None


@patch("daemon.mailbox.approver._open_in_editor",
       return_value="Edited reply.\n\nDaemon Wick")
@patch("daemon.mailbox.approver.input", side_effect=["e", "a"])
@patch("daemon.mailbox.approver.console")
def test_edit_then_approve(mock_console, mock_input, mock_editor):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.approved
    assert result.final_draft == "Edited reply.\n\nDaemon Wick"
    assert result.edits_made is True
    assert result.original_draft == DRAFT


@patch("daemon.mailbox.approver._open_in_editor",
       return_value="Edited reply.\n\nDaemon Wick")
@patch("daemon.mailbox.approver.input", side_effect=["e", "s"])
@patch("daemon.mailbox.approver.console")
def test_edit_then_skip(mock_console, mock_input, mock_editor):
    # Editing does not lock in approval — operator can still skip
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.skipped
    assert result.final_draft is None


@patch("daemon.mailbox.approver.input", side_effect=["x", "x", "a"])
@patch("daemon.mailbox.approver.console")
def test_invalid_keys_loop_until_valid(mock_console, mock_input):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.approved


@patch("daemon.mailbox.approver.input",
       side_effect=KeyboardInterrupt)
@patch("daemon.mailbox.approver.console")
def test_keyboard_interrupt_skips(mock_console, mock_input):
    result = review(make_message(), DRAFT, [])
    assert result.decision == ApprovalDecision.skipped
    assert result.final_draft is None
