"""Batch prediction and deterministic validation/routing of LLM results."""

from pathlib import Path
import math

from llm_client import LLMSettings, request_predictions


class PredictionValidationError(ValueError):
    """The whole batch is rejected when any prediction is invalid."""


def validate_predictions(model_response, normalized_tickets: list[dict], triage_config: dict) -> list[dict]:
    """Validate the entire batch, derive routes, and restore input order.

    Reply word count is len(suggested_reply.split()), using Python's Unicode
    whitespace tokenization. Any model-generated route_to is ignored.
    """
    if not isinstance(model_response, dict) or not isinstance(model_response.get("predictions"), list):
        raise PredictionValidationError("LLM response must be an object containing a predictions array.")
    predictions = model_response["predictions"]
    ticket_ids = [ticket["ticket_id"] for ticket in normalized_tickets]
    expected_ids = set(ticket_ids)
    by_id = {}
    categories = triage_config["allowed_categories"]
    priorities = triage_config["allowed_priorities"]
    max_words = triage_config["reply_style"]["max_words"]
    fields = ("ticket_id", "category", "priority", "reason", "suggested_reply")
    for index, prediction in enumerate(predictions):
        location = f"predictions[{index}]"
        if not isinstance(prediction, dict):
            raise PredictionValidationError(f"{location}: expected an object.")
        for name in fields:
            if not isinstance(prediction.get(name), str):
                raise PredictionValidationError(f"{location}.{name}: required string field.")
        ticket_id = prediction["ticket_id"]
        if ticket_id not in expected_ids:
            raise PredictionValidationError(f"{location}.ticket_id: unexpected ticket ID.")
        if ticket_id in by_id:
            raise PredictionValidationError(f"{location}.ticket_id: duplicate prediction for an input ticket.")
        if prediction["category"] not in categories:
            raise PredictionValidationError(f"{location}.category: value is not in allowed_categories.")
        if prediction["priority"] not in priorities:
            raise PredictionValidationError(f"{location}.priority: value is not in allowed_priorities.")
        for name in ("reason", "suggested_reply"):
            if not prediction[name].strip():
                raise PredictionValidationError(f"{location}.{name}: must be nonempty.")
        word_count = len(prediction["suggested_reply"].split())
        if word_count > max_words:
            raise PredictionValidationError(
                f"{location}.suggested_reply: {word_count} words exceeds reply_style.max_words ({max_words})."
            )
        confidence = prediction.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1 or not math.isfinite(confidence):
            raise PredictionValidationError(f"{location}.confidence: expected a finite number between 0 and 1.")
        record = {name: prediction[name] for name in fields}
        record["confidence"] = confidence
        record["route_to"] = triage_config["routing_rules"][record["category"]]
        by_id[ticket_id] = record
    missing_count = len(expected_ids - by_id.keys())
    if missing_count:
        raise PredictionValidationError(f"LLM response is missing predictions for {missing_count} input ticket(s).")
    return [by_id[ticket_id] for ticket_id in ticket_ids]


def recover_predictions(model_response, normalized_tickets: list[dict], config: dict, error_callback=None) -> list[dict]:
    """Keep valid tickets and deterministically recover individual bad records."""
    if not isinstance(model_response, dict) or not isinstance(model_response.get("predictions"), list):
        raise PredictionValidationError("LLM response must contain a predictions array; the batch cannot be recovered.")
    expected = {t["ticket_id"] for t in normalized_tickets}
    grouped = {ticket_id: [] for ticket_id in expected}
    for prediction in model_response["predictions"]:
        ticket_id = prediction.get("ticket_id") if isinstance(prediction, dict) else None
        if not isinstance(ticket_id, str) or ticket_id not in expected:
            if error_callback:
                error_callback(None, "Unidentifiable or unexpected model prediction; no input ticket was discarded.")
            continue
        grouped[ticket_id].append(prediction)
    result = []
    for ticket in normalized_tickets:
        ticket_id = ticket["ticket_id"]
        candidates = grouped[ticket_id]
        try:
            if len(candidates) != 1:
                raise PredictionValidationError("Missing prediction." if not candidates else "Duplicate predictions for one input ticket.")
            result.extend(validate_predictions({"predictions": candidates}, [ticket], config))
        except PredictionValidationError as error:
            if error_callback:
                error_callback(ticket_id, str(error))
            if "other" not in config["allowed_categories"]:
                raise PredictionValidationError("Cannot recover an invalid prediction: no allowed 'other' fallback category is configured.") from error
            reply = " ".join("Thank you. Your request needs manual review.".split()[:config["reply_style"]["max_words"]])
            result.append({
                "ticket_id": ticket_id, "category": "other", "priority": config["allowed_priorities"][0],
                "reason": "Prediction unavailable or invalid; manual review required.",
                "suggested_reply": reply, "confidence": 0.0, "route_to": config["routing_rules"]["other"],
            })
    return validate_predictions({"predictions": result}, normalized_tickets, config)


def predict_tickets(normalized_tickets: list[dict], triage_config: dict, settings: LLMSettings | None = None, audit=None, error_callback=None) -> list[dict]:
    settings = settings or LLMSettings.from_environment(Path(__file__).resolve().parent / ".env")
    response = request_predictions(normalized_tickets, triage_config, settings, audit=audit)
    return recover_predictions(response, normalized_tickets, triage_config, error_callback)
