import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from daemon.mailbox.poller import EmailMessage
from daemon.mailbox.classifier import (
    classify, _build_prompt, _parse_response,
    Category, ClassificationResult, CONFIDENCE_THRESHOLD,
    DEFAULT_ACTIONS,
)

# ── Fixtures ──────────────────────────────────────────────────────

def make_message(**kwargs) -> EmailMessage:
    defaults = dict(
        id="msg_001",
        thread_id="thread_001",
        from_addr="recruiter@revenuecat.com",
        subject="Your application to RevenueCat",
        body="Thanks for applying. We'll be in touch.",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def mock_ollama_response(category: str, confidence: float):
    """Return a mock httpx response with valid classifier JSON."""
    import json
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": json.dumps({
            "category": category,
            "confidence": confidence,
        })
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ── _build_prompt ─────────────────────────────────────────────────

def test_prompt_contains_all_categories():
    msg = make_message()
    prompt = _build_prompt(msg)
    for cat in Category:
        assert cat.value in prompt

def test_prompt_contains_email_content():
    msg = make_message(
        from_addr="jobs@stripe.com",
        subject="Interview invitation",
        body="We'd like to schedule a call.",
    )
    prompt = _build_prompt(msg)
    assert "jobs@stripe.com" in prompt
    assert "Interview invitation" in prompt
    assert "schedule a call" in prompt

def test_prompt_truncates_long_body():
    msg = make_message(body="x" * 5000)
    prompt = _build_prompt(msg)
    # Body is capped at 2000 chars in the prompt
    assert "x" * 2001 not in prompt

# ── _parse_response ───────────────────────────────────────────────

def test_parse_valid_response():
    result = _parse_response('{"category": "confirmation", "confidence": 0.95}')
    assert result == ("confirmation", 0.95)

def test_parse_strips_markdown_fences():
    result = _parse_response(
        '```json\n{"category": "rejection", "confidence": 0.88}\n```'
    )
    assert result == ("rejection", 0.88)

def test_parse_clamps_confidence_above_1():
    result = _parse_response('{"category": "screening", "confidence": 1.5}')
    assert result[1] == 1.0

def test_parse_clamps_confidence_below_0():
    result = _parse_response('{"category": "screening", "confidence": -0.1}')
    assert result[1] == 0.0

def test_parse_unknown_category_returns_none():
    result = _parse_response('{"category": "hired", "confidence": 0.9}')
    assert result is None

def test_parse_invalid_json_returns_none():
    result = _parse_response("not json at all")
    assert result is None

def test_parse_missing_confidence_returns_none():
    result = _parse_response('{"category": "confirmation"}')
    assert result is None

# ── classify — happy path ─────────────────────────────────────────

@patch("daemon.mailbox.classifier.httpx.post")
def test_classify_confirmation(mock_post):
    mock_post.return_value = mock_ollama_response("confirmation", 0.95)
    result = classify(make_message())
    assert result.category == Category.confirmation
    assert result.confidence == 0.95
    assert result.fallback is False

@patch("daemon.mailbox.classifier.httpx.post")
def test_classify_rejection(mock_post):
    mock_post.return_value = mock_ollama_response("rejection", 0.92)
    msg = make_message(
        subject="Update on your application",
        body="We've decided to move forward with other candidates.",
    )
    result = classify(msg)
    assert result.category == Category.rejection
    assert result.fallback is False

@patch("daemon.mailbox.classifier.httpx.post")
def test_classify_offer(mock_post):
    mock_post.return_value = mock_ollama_response("offer", 0.97)
    msg = make_message(
        subject="Offer letter — Agentic AI Developer",
        body="We are pleased to extend an offer of employment.",
    )
    result = classify(msg)
    assert result.category == Category.offer

# ── classify — confidence threshold enforcement ───────────────────

@patch("daemon.mailbox.classifier.httpx.post")
def test_low_confidence_forces_ambiguous(mock_post):
    # Model returns screening but with low confidence
    mock_post.return_value = mock_ollama_response("screening", 0.55)
    result = classify(make_message())
    assert result.category == Category.ambiguous
    assert result.confidence == 0.55   # original confidence preserved
    assert result.fallback is False    # not a fallback — deliberate

@patch("daemon.mailbox.classifier.httpx.post")
def test_exactly_at_threshold_is_not_ambiguous(mock_post):
    mock_post.return_value = mock_ollama_response(
        "screening", CONFIDENCE_THRESHOLD
    )
    result = classify(make_message())
    assert result.category == Category.screening

@patch("daemon.mailbox.classifier.httpx.post")
def test_ambiguous_from_model_passes_through(mock_post):
    mock_post.return_value = mock_ollama_response("ambiguous", 0.85)
    result = classify(make_message())
    assert result.category == Category.ambiguous
    assert result.fallback is False

# ── classify — fallback behavior ─────────────────────────────────

@patch("daemon.mailbox.classifier.httpx.post",
       side_effect=__import__("httpx").ConnectError("refused"))
def test_ollama_unavailable_returns_ambiguous_fallback(mock_post):
    result = classify(make_message(), retries=1)
    assert result.category == Category.ambiguous
    assert result.confidence == 0.0
    assert result.fallback is True

@patch("daemon.mailbox.classifier.httpx.post")
def test_bad_json_after_retries_returns_fallback(mock_post):
    bad_resp = MagicMock()
    bad_resp.json.return_value = {"response": "not json"}
    bad_resp.raise_for_status = MagicMock()
    mock_post.return_value = bad_resp
    result = classify(make_message(), retries=2)
    assert result.category == Category.ambiguous
    assert result.fallback is True

# ── DEFAULT_ACTIONS ───────────────────────────────────────────────

def test_offer_action_is_escalate():
    assert DEFAULT_ACTIONS[Category.offer] == "escalate"

def test_ambiguous_action_is_escalate():
    assert DEFAULT_ACTIONS[Category.ambiguous] == "escalate"

def test_confirmation_action_is_log():
    assert DEFAULT_ACTIONS[Category.confirmation] == "log"

def test_all_categories_have_actions():
    for cat in Category:
        assert cat in DEFAULT_ACTIONS
