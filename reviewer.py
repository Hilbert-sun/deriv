"""Interactive human corrections with one effective override per ticket."""

import csv


class ReviewError(ValueError):
    pass


def validate_overrides(overrides, predictions: list[dict], config: dict) -> list[dict]:
    if not isinstance(overrides, list):
        raise ReviewError("Overrides must be a JSON array.")
    original = {item["ticket_id"]: item for item in predictions}
    by_id = {}
    fields = {"ticket_id", "old_category", "new_category", "old_priority", "new_priority"}
    for item in overrides:
        if not isinstance(item, dict) or set(item) != fields or any(not isinstance(v, str) for v in item.values()):
            raise ReviewError("Each override must contain exactly the five required string fields.")
        ticket_id = item["ticket_id"]
        if ticket_id not in original or ticket_id in by_id:
            raise ReviewError("Override has an unknown or duplicate ticket ID.")
        predicted = original[ticket_id]
        if (item["old_category"], item["old_priority"]) != (predicted["category"], predicted["priority"]):
            raise ReviewError("Override old values must match the original prediction.")
        if item["new_category"] not in config["allowed_categories"] or item["new_priority"] not in config["allowed_priorities"]:
            raise ReviewError("Override category or priority is not allowed by the configuration.")
        if (item["old_category"], item["old_priority"]) == (item["new_category"], item["new_priority"]):
            raise ReviewError("No-op corrections must not be stored as effective overrides.")
        by_id[ticket_id] = dict(item)
    return [by_id[p["ticket_id"]] for p in predictions if p["ticket_id"] in by_id]


def review_predictions(predictions: list[dict], config: dict, input_fn=None, output_fn=print) -> list[dict]:
    input_fn = input if input_fn is None else input_fn
    original = {p["ticket_id"]: p for p in predictions}
    effective = {}
    for p in predictions:
        output_fn(f"{p['ticket_id']} | category={p['category']} | priority={p['priority']}")
    output_fn("Enter any overrides as: ticket_id,category,priority")
    output_fn("Press Enter on an empty line when done.")
    while True:
        try:
            line = input_fn()
        except EOFError:
            raise ReviewError("Review input closed before completion. Enter an empty line to finish review.") from None
        if line == "":
            break
        try:
            parts = next(csv.reader([line], strict=True))
            if len(parts) != 3:
                raise ReviewError("Expected three CSV fields: ticket_id,category,priority.")
            ticket_id, category, priority = parts
            ticket_id = ticket_id if ticket_id in original else ticket_id.strip()
            category = category if category in config["allowed_categories"] else category.strip()
            priority = priority if priority in config["allowed_priorities"] else priority.strip()
            if ticket_id not in original:
                raise ReviewError("Unknown ticket ID.")
            if category not in config["allowed_categories"]:
                raise ReviewError("Category is not in allowed_categories.")
            if priority not in config["allowed_priorities"]:
                raise ReviewError("Priority is not in allowed_priorities.")
            predicted = original[ticket_id]
            if (category, priority) == (predicted["category"], predicted["priority"]):
                effective.pop(ticket_id, None)
            else:
                effective[ticket_id] = {
                    "ticket_id": ticket_id,
                    "old_category": predicted["category"], "new_category": category,
                    "old_priority": predicted["priority"], "new_priority": priority,
                }
            output_fn(f"Accepted {ticket_id}: category={category}, priority={priority}, route={config['routing_rules'][category]}")
        except (ReviewError, csv.Error) as error:
            output_fn(f"Invalid override: {error} Please try again.")
    return validate_overrides([effective[p["ticket_id"]] for p in predictions if p["ticket_id"] in effective], predictions, config)
