import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openai import OpenAI
from pydantic import EmailStr, TypeAdapter, ValidationError

from ..config import get_settings
from ..schemas import ParsedEmailIntent


SYSTEM_PROMPT = """You extract an email scheduling request into a strict schema.
Return exactly one JSON object and no markdown or commentary.
Return action='schedule_email' only when recipient, subject, body, and an exact send time are all known.
Otherwise return action='needs_clarification', put a short question in clarification_message,
and use null for unknown fields. Never invent an email address, message content, or time.
Interpret relative dates using the supplied current time and IANA timezone. Return scheduled_at as
an ISO 8601 local wall-clock time without
an offset (for example, 2026-10-05T16:37:00 for 4:37 PM). The application applies the supplied
IANA timezone. For repeated sends, count is the total number of emails and
interval_minutes is the spacing between them. A single email uses count=1 and interval_minutes=0.
"""


def _parse_json_object(content: str) -> dict[str, Any]:
    """Extract the first complete JSON object from a model response."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("The model did not return a valid JSON scheduling intent")


def _normalize_local_wall_time(intent: ParsedEmailIntent) -> ParsedEmailIntent:
    """Strip any model-supplied offset so application code owns timezone conversion."""
    if intent.action != "schedule_email" or not intent.scheduled_at:
        return intent
    try:
        parsed = datetime.fromisoformat(intent.scheduled_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("The model returned an invalid scheduled time") from exc
    local_wall_time = parsed.replace(tzinfo=None).isoformat()
    return intent.model_copy(update={"scheduled_at": local_wall_time})


def _validated_intent(payload: dict[str, Any]) -> ParsedEmailIntent:
    """Turn incomplete model output into a deterministic clarification request."""
    email_adapter = TypeAdapter(EmailStr)
    recipient = payload.get("recipient")
    try:
        recipient = email_adapter.validate_python(recipient) if recipient else None
    except ValidationError:
        recipient = None

    def optional_text(name: str) -> str | None:
        value = payload.get(name)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
        value = payload.get(name)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if minimum <= parsed <= maximum else default

    intent = ParsedEmailIntent(
        action=(
            payload.get("action")
            if payload.get("action") in {"schedule_email", "needs_clarification"}
            else "needs_clarification"
        ),
        recipient=recipient,
        subject=optional_text("subject"),
        body=optional_text("body"),
        scheduled_at=optional_text("scheduled_at"),
        count=bounded_integer("count", 1, 1, 100),
        interval_minutes=bounded_integer("interval_minutes", 0, 0, 43_200),
        clarification_message=optional_text("clarification_message"),
    )

    missing: list[str] = []
    if not intent.recipient:
        missing.append("recipient email address")
    if not intent.subject or not intent.subject.strip():
        missing.append("subject")
    if not intent.body or not intent.body.strip():
        missing.append("message")
    if not intent.scheduled_at:
        missing.append("send time")
    if intent.count > 1 and intent.interval_minutes < 1:
        missing.append("interval between emails")

    if missing:
        if len(missing) == 1:
            details = missing[0]
        elif len(missing) == 2:
            details = f"{missing[0]} and {missing[1]}"
        else:
            details = f"{', '.join(missing[:-1])}, and {missing[-1]}"
        return intent.model_copy(
            update={
                "action": "needs_clarification",
                "clarification_message": f"Please provide the {details}.",
            }
        )
    return intent.model_copy(update={"action": "schedule_email", "clarification_message": None})


def parse_email_intent(message: str, user_timezone: str) -> ParsedEmailIntent:
    settings = get_settings()
    if not settings.nvidia_api_key:
        raise RuntimeError("NVIDIA_API_KEY is required to parse natural-language requests")

    client = OpenAI(
        api_key=settings.nvidia_api_key,
        base_url=settings.nvidia_base_url,
    )
    now = datetime.now(timezone.utc)
    try:
        local_now = now.astimezone(ZoneInfo(user_timezone)).isoformat()
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(f"Unknown timezone: {user_timezone}") from exc
    schema = json.dumps(ParsedEmailIntent.model_json_schema(), separators=(",", ":"))
    response = client.chat.completions.create(
        model=settings.nvidia_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Current UTC time: {now.isoformat()}\n"
                    f"Current local time: {local_now}\n"
                    f"User timezone: {user_timezone}\n"
                    f"JSON schema: {schema}\n"
                    f"Request: {message}"
                ),
            },
        ],
        temperature=0,
        max_tokens=1_000,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("The model did not return a usable scheduling intent")
    intent = _validated_intent(_parse_json_object(content))
    return _normalize_local_wall_time(intent)

