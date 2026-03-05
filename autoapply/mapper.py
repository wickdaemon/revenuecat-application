import re
from .schemas import (
    FieldInventory, Profile, Mapping, FieldDecision, MappingSource, FormField, FieldType,
)

# (pattern, profile_path, is_eeo)
_PATTERNS: list[tuple[str, str, bool]] = [
    (r"operator.*name",                              "answers.operator_name", False),
    (r"operator.*email",                             "answers.operator_email", False),
    (r"first.?name|given.?name",                    "identity.first_name", False),
    (r"last.?name|surname|family.?name",            "identity.last_name",  False),
    (r"\bname\b",                                   "identity.name",       False),
    (r"email",                                       "identity.email",      False),
    (r"phone|mobile|tel",                            "identity.phone",      False),
    (r"work.?auth|authorized|eligible|sponsor",       "answers.work_authorization", False),
    (r"visa|require.*sponsorship|need.*sponsorship",   "answers.visa_required", False),
    (r"location|city|where.*(are|do).*(you|work)",   "identity.location",   False),
    (r"what.?links|demonstrate.*ability|technical.*content", "answers.links", False),
    (r"linkedin",                                    "identity.linkedin",   False),
    (r"website|portfolio|personal.?url",             "identity.website",    False),
    (r"resume|cv",                                   "files.resume",        False),
    (r"cover.?letter",                               "files.cover_letter",  False),
    (r"application.?url|letter.?url|public.?url|link.*application|public.*application.*letter", "answers.application_url", False),
    (r"why.*(company|us|revenuecat)",                "answers.why_company", False),
    (r"why.*(role|position|job|this)",               "answers.why_role",    False),
    (r"start.?date|available|when.*start",           "answers.start_date",  False),
    (r"salary|compensation|pay",                     "answers.salary",      False),
    (r"github",                                      "identity.github",     False),
    (r"gender|pronouns",                             "eeo.gender",          True),
    (r"race|ethnic",                                 "eeo.ethnicity",       True),
    (r"veteran|military",                            "eeo.veteran",         True),
    (r"disability|disabled",                         "eeo.disability",      True),
    (r"gdpr|privacy.?notice|candidate.?privacy|eea|data.?protection", "answers.gdpr_consent", False),
]


def _resolve_value(profile: Profile, path: str) -> str | None:
    if path == "identity.first_name":
        parts = profile.identity.name.split()
        return parts[0] if parts else None
    if path == "identity.last_name":
        parts = profile.identity.name.split()
        return parts[-1] if parts else None

    parts = path.split(".")
    obj = profile
    for part in parts:
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return str(obj) if obj is not None else None


def _match_field(field: FormField, profile: Profile) -> tuple[str | None, str, float, bool]:
    """Returns (value, path, confidence, is_eeo) or None-tuple."""
    search_texts = [
        (field.label.lower(), 0.90),
        ((field.hints.name or "").lower(), 0.85),
        ((field.hints.id or "").lower(), 0.80),
        ((field.hints.aria_label or "").lower(), 0.75),
        ((field.hints.placeholder or "").lower(), 0.70),
    ]
    for pattern, path, is_eeo in _PATTERNS:
        for text, confidence in search_texts:
            if not text:
                continue
            if re.search(pattern, text):
                # file type check for resume/cover_letter
                if path in ("files.resume", "files.cover_letter") and field.type != FieldType.file:
                    continue
                value = _resolve_value(profile, path)
                return value, path, confidence, is_eeo
    return None, "", 0.0, False


def heuristic_map(inventory: FieldInventory, profile: Profile) -> Mapping:
    decisions: list[FieldDecision] = []
    unfilled_required: list[str] = []

    for field in inventory.fields:
        value, path, confidence, is_eeo = _match_field(field, profile)

        if is_eeo:
            if profile.eeo.auto_fill:
                eeo_value = _resolve_value(profile, path) or ""
                decisions.append(FieldDecision(
                    field_id=field.field_id,
                    selector=field.selector,
                    value=eeo_value,
                    confidence=1.0,
                    source=MappingSource.heuristic if eeo_value else MappingSource.skip,
                    note=f"EEO auto-fill: {path}",
                ))
            else:
                decisions.append(FieldDecision(
                    field_id=field.field_id,
                    selector=field.selector,
                    value="",
                    confidence=1.0,
                    source=MappingSource.skip,
                    note=f"EEO skipped: {path}",
                ))
            continue

        if confidence == 0.0:
            # No match
            decisions.append(FieldDecision(
                field_id=field.field_id,
                selector=field.selector,
                value="",
                confidence=0.0,
                source=MappingSource.skip,
                note="no heuristic match",
            ))
            if field.required:
                unfilled_required.append(field.field_id)
            continue

        if value is None:
            decisions.append(FieldDecision(
                field_id=field.field_id,
                selector=field.selector,
                value="",
                confidence=confidence,
                source=MappingSource.skip,
                note=f"matched {path} but value is None",
            ))
            if field.required:
                unfilled_required.append(field.field_id)
            continue

        decisions.append(FieldDecision(
            field_id=field.field_id,
            selector=field.selector,
            value=value,
            confidence=confidence,
            source=MappingSource.heuristic,
            note=f"matched {path}",
        ))

    return Mapping(decisions=decisions, unfilled_required=unfilled_required)
