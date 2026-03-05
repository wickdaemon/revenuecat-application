import httpx
import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .poller import EmailMessage

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL_SMALL = "qwen2.5:3b"
DEFAULT_MODEL_LARGE = "qwen2.5:14b"
CONFIDENCE_THRESHOLD = 0.7


class Category(str, Enum):
    confirmation  = "confirmation"
    screening     = "screening"
    scheduling    = "scheduling"
    rejection     = "rejection"
    offer         = "offer"
    ambiguous     = "ambiguous"


@dataclass
class ClassificationResult:
    category: Category
    confidence: float
    raw_response: str        # full LLM output, for debugging
    model: str               # which model produced this
    fallback: bool = False   # True if LLM was unavailable


# Category definitions injected into the prompt so the model has
# explicit criteria rather than guessing from names alone.
CATEGORY_DEFINITIONS = {
    "confirmation": (
        "An automated acknowledgment that the application was received. "
        "Typically from an ATS (Ashby, Greenhouse, Lever). "
        "Contains no personal content, no questions, no scheduling."
    ),
    "screening": (
        "A human recruiter initiating contact. Asks questions about "
        "background, availability, or interest. May request a call or "
        "written responses. This is the first human touch in the process."
    ),
    "scheduling": (
        "An email whose primary purpose is coordinating a specific time "
        "for a call or interview. May include calendar links, time zone "
        "questions, or proposed time slots."
    ),
    "rejection": (
        "Informs the candidate they will not be moving forward. May be "
        "polite or terse. Key signal: the process is ending, not continuing."
    ),
    "offer": (
        "Contains compensation, title, start date, or explicit language "
        "about extending an offer of employment. Even informal or verbal "
        "offer signals count."
    ),
    "ambiguous": (
        "Does not clearly fit any other category. Use this when the email "
        "could be multiple categories, when context is missing, or when "
        "confidence is low."
    ),
}


def _build_prompt(message: EmailMessage) -> str:
    """
    Construct the classification prompt. Injects category definitions,
    the email content, and strict JSON output instructions.
    """
    categories_block = "\n".join(
        f'  "{k}": {v}' for k, v in CATEGORY_DEFINITIONS.items()
    )

    return f"""You are classifying a recruiter email for a job application.

CATEGORIES:
{categories_block}

EMAIL TO CLASSIFY:
From: {message.from_addr}
Subject: {message.subject}
Body:
{message.body[:2000]}

TASK:
1. Choose exactly one category from: confirmation, screening, scheduling,
   rejection, offer, ambiguous
2. Assign a confidence score from 0.0 to 1.0
3. If you are not confident (score below {CONFIDENCE_THRESHOLD}), you MUST
   use "ambiguous" as the category regardless of your best guess

Respond with ONLY valid JSON. No explanation, no preamble, no markdown.
Exact format required:
{{"category": "<one of the six categories>", "confidence": <float>}}"""


def _parse_response(text: str) -> Optional[tuple[str, float]]:
    """
    Parse LLM response text into (category, confidence).
    Strips markdown fences if present.
    Returns None if parsing fails.
    """
    # Strip markdown code fences if model ignored instructions
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse classifier JSON: {text!r}")
        return None

    category = data.get("category", "").lower().strip()
    confidence = data.get("confidence")

    if category not in [c.value for c in Category]:
        logger.warning(f"Unknown category from classifier: {category!r}")
        return None

    if not isinstance(confidence, (int, float)):
        logger.warning(f"Invalid confidence value: {confidence!r}")
        return None

    confidence = float(confidence)
    confidence = max(0.0, min(1.0, confidence))  # clamp to [0.0, 1.0]

    return category, confidence


def classify(
    message: EmailMessage,
    model: str = DEFAULT_MODEL_SMALL,
    base_url: str = OLLAMA_BASE_URL,
    retries: int = 3,
) -> ClassificationResult:
    """
    Classify a single EmailMessage using a local Ollama model.

    Rules:
    - Returns ClassificationResult with category and confidence
    - If confidence < CONFIDENCE_THRESHOLD: category is forced to
      "ambiguous" regardless of what the model returned
    - If LLM is unavailable or returns unparseable JSON after all
      retries: returns ambiguous with confidence=0.0 and fallback=True
    - Never raises exceptions — all errors are caught and returned
      as ambiguous fallback results
    """
    prompt = _build_prompt(message)
    last_raw = ""

    for attempt in range(1, retries + 1):
        try:
            response = httpx.post(
                f"{base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,   # low temp for consistency
                        "num_predict": 64,    # category + confidence is short
                    },
                },
                timeout=30.0,
            )
            response.raise_for_status()
            last_raw = response.json().get("response", "")

        except httpx.ConnectError:
            logger.warning(
                f"Ollama not reachable at {base_url} "
                f"(attempt {attempt}/{retries})"
            )
            if attempt == retries:
                return ClassificationResult(
                    category=Category.ambiguous,
                    confidence=0.0,
                    raw_response="",
                    model=model,
                    fallback=True,
                )
            continue

        except (httpx.HTTPError, Exception) as e:
            logger.warning(f"Classifier HTTP error (attempt {attempt}): {e}")
            if attempt == retries:
                return ClassificationResult(
                    category=Category.ambiguous,
                    confidence=0.0,
                    raw_response=str(e),
                    model=model,
                    fallback=True,
                )
            continue

        parsed = _parse_response(last_raw)

        if parsed is None:
            logger.warning(
                f"Unparseable response (attempt {attempt}/{retries}): "
                f"{last_raw!r}"
            )
            if attempt == retries:
                return ClassificationResult(
                    category=Category.ambiguous,
                    confidence=0.0,
                    raw_response=last_raw,
                    model=model,
                    fallback=True,
                )
            continue

        category_str, confidence = parsed

        # Enforce confidence threshold — low confidence always → ambiguous
        if confidence < CONFIDENCE_THRESHOLD:
            logger.info(
                f"Confidence {confidence:.2f} below threshold "
                f"{CONFIDENCE_THRESHOLD} — forcing ambiguous "
                f"(original: {category_str})"
            )
            return ClassificationResult(
                category=Category.ambiguous,
                confidence=confidence,
                raw_response=last_raw,
                model=model,
                fallback=False,
            )

        return ClassificationResult(
            category=Category(category_str),
            confidence=confidence,
            raw_response=last_raw,
            model=model,
            fallback=False,
        )

    # Should not reach here, but safety net
    return ClassificationResult(
        category=Category.ambiguous,
        confidence=0.0,
        raw_response=last_raw,
        model=model,
        fallback=True,
    )


# Category → default action mapping
# Used by the CLI and future phases to decide what to do with a result.
DEFAULT_ACTIONS: dict[Category, str] = {
    Category.confirmation: "log",          # no further action
    Category.screening:    "queue_draft",  # Phase 4: queue for drafting
    Category.scheduling:   "queue_draft",  # Phase 4: queue + flag calendar
    Category.rejection:    "log_rejected", # log, set state to rejected
    Category.offer:        "escalate",     # ALWAYS escalate, never draft
    Category.ambiguous:    "escalate",     # ALWAYS escalate, never draft
}
