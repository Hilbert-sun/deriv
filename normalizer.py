"""Deterministic ticket normalization, with no external services."""


def normalize_text(value: str) -> str:
    """Standardize line endings and trim edges without changing internal text."""
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def normalize_ticket(ticket: dict) -> dict:
    """Normalize a validated ticket into the exact model-input schema."""
    normalized = {
        field: normalize_text(ticket[field])
        for field in ("ticket_id", "subject", "message", "channel", "created_at")
    }
    text = f"Subject: {normalized['subject']}\nMessage: {normalized['message']}"
    normalized["text_for_model"] = text
    normalized["char_count"] = len(text)
    return normalized


def normalize_tickets(tickets: list[dict]) -> list[dict]:
    """Return one normalized record per ticket, preserving input order."""
    return [normalize_ticket(ticket) for ticket in tickets]
