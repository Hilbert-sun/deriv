"""JSON input/output and validation for disk-backed triage inputs."""

import json
import os
from pathlib import Path
import tempfile

from normalizer import normalize_text


class InputValidationError(ValueError):
    """An actionable error in a pipeline input."""


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as error:
        raise InputValidationError(
            f"{path.name}: invalid JSON at line {error.lineno}, "
            f"column {error.colno}: {error.msg}."
        ) from error


def validate_tickets(tickets) -> None:
    if not isinstance(tickets, list):
        raise InputValidationError("tickets.json: expected a list of ticket objects.")
    seen_ids = set()
    for index, ticket in enumerate(tickets):
        location = f"tickets.json[{index}]"
        if not isinstance(ticket, dict):
            raise InputValidationError(f"{location}: expected a ticket object.")
        for field in ("ticket_id", "subject", "message", "channel", "created_at"):
            if field not in ticket or not isinstance(ticket[field], str):
                raise InputValidationError(f"{location}.{field}: required string field.")
        ticket_id = normalize_text(ticket["ticket_id"])
        if not ticket_id:
            raise InputValidationError(f"{location}.ticket_id: must not be empty or whitespace.")
        if ticket_id in seen_ids:
            raise InputValidationError(
                f"{location}.ticket_id: duplicate ID {ticket_id!r} after normalization; "
                "assign a unique ticket_id to each ticket."
            )
        seen_ids.add(ticket_id)


def _validate_values(config: dict, field: str) -> list[str]:
    values = config.get(field)
    if not isinstance(values, list) or not values:
        raise InputValidationError(f"triage_config.json.{field}: expected a nonempty array.")
    seen = set()
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise InputValidationError(
                f"triage_config.json.{field}[{index}]: expected a nonempty string."
            )
        if value in seen:
            raise InputValidationError(
                f"triage_config.json.{field}[{index}]: duplicate value {value!r}."
            )
        seen.add(value)
    return values


def validate_config(config) -> None:
    if not isinstance(config, dict):
        raise InputValidationError("triage_config.json: expected a configuration object.")
    categories = _validate_values(config, "allowed_categories")
    _validate_values(config, "allowed_priorities")
    reply_style = config.get("reply_style")
    if not isinstance(reply_style, dict):
        raise InputValidationError("triage_config.json.reply_style: expected an object.")
    tone = reply_style.get("tone")
    if not isinstance(tone, str) or not tone.strip():
        raise InputValidationError("triage_config.json.reply_style.tone: expected a nonempty string.")
    max_words = reply_style.get("max_words")
    if type(max_words) is not int or max_words <= 0:
        raise InputValidationError(
            "triage_config.json.reply_style.max_words: expected a positive integer."
        )
    routing_rules = config.get("routing_rules")
    if not isinstance(routing_rules, dict):
        raise InputValidationError("triage_config.json.routing_rules: expected an object.")
    for category in categories:
        destination = routing_rules.get(category)
        if not isinstance(destination, str) or not destination.strip():
            raise InputValidationError(
                f"triage_config.json.routing_rules[{category!r}]: "
                "provide a nonempty destination string for every allowed category."
            )


def save_json(path: Path, data) -> None:
    """Replace an artifact atomically, preserving any prior file on failure."""
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
