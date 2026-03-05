import json
import httpx

from ..schemas import FormField, Profile, Mapping, FieldDecision, MappingSource


def _is_eeo(field: FormField) -> bool:
    text = (field.label + " " + (field.hints.name or "") + " " + (field.hints.id or "")).lower()
    import re
    return bool(re.search(r"gender|pronouns|race|ethnic|veteran|military|disability|disabled", text))


async def llm_map(
    fields: list[FormField],
    profile: Profile,
    model: str = "qwen2.5:3b",
    base_url: str = "http://localhost:11434",
    retries: int = 3,
) -> Mapping:
    non_eeo = [f for f in fields if not _is_eeo(f)]

    if not non_eeo:
        return Mapping(decisions=[])

    fields_desc = []
    for f in non_eeo:
        fields_desc.append({
            "field_id": f.field_id,
            "label": f.label,
            "type": f.type.value,
            "required": f.required,
            "options": f.options,
        })

    profile_data = profile.model_dump(exclude={"eeo"})

    prompt = (
        "You are a form-filling assistant. Given form fields and a user profile, "
        "return ONLY valid JSON with no preamble or explanation.\n\n"
        f"Form fields:\n{json.dumps(fields_desc, indent=2)}\n\n"
        f"User profile:\n{json.dumps(profile_data, indent=2)}\n\n"
        'Return exactly: {"decisions": [{"field_id": "...", "value": "..."}]}\n'
        "Only include fields you can confidently fill. Omit fields you cannot fill."
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(retries):
            try:
                resp = await client.post(
                    f"{base_url}/api/generate",
                    json={"model": model, "prompt": prompt, "stream": False},
                )
                resp.raise_for_status()
                body = resp.json()
                text = body.get("response", "").strip()

                # Try to extract JSON from response
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    text = text[start:end]

                data = json.loads(text)
                decisions = []
                field_map = {f.field_id: f for f in non_eeo}
                for d in data.get("decisions", []):
                    fid = d.get("field_id", "")
                    if fid in field_map:
                        decisions.append(FieldDecision(
                            field_id=fid,
                            selector=field_map[fid].selector,
                            value=d.get("value", ""),
                            confidence=0.75,
                            source=MappingSource.llm,
                            note="LLM mapped",
                        ))
                return Mapping(decisions=decisions)

            except Exception:
                if attempt == retries - 1:
                    # All retries exhausted — skip all fields
                    decisions = [
                        FieldDecision(
                            field_id=f.field_id,
                            selector=f.selector,
                            value="",
                            confidence=0.0,
                            source=MappingSource.skip,
                            note="LLM failed after retries",
                        )
                        for f in non_eeo
                    ]
                    return Mapping(decisions=decisions)

    return Mapping(decisions=[])
