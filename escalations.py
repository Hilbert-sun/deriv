"""Post-review escalation rules; no model calls."""


def generate_escalations(queue: list[dict], predictions: list[dict], config: dict) -> list[dict]:
    by_id = {p["ticket_id"]: p for p in predictions}
    result = []
    for ticket in queue:
        confidence = by_id[ticket["ticket_id"]]["confidence"]
        reasons = []
        if "other" in config["allowed_categories"] and ticket["final_category"] == "other":
            reasons.append("post_review_category_other")
        if confidence < 0.60:
            reasons.append("confidence_below_0.60")
        if reasons:
            result.append({
                "ticket_id": ticket["ticket_id"], "final_category": ticket["final_category"],
                "final_priority": ticket["final_priority"], "final_route_to": ticket["final_route_to"],
                "confidence": confidence, "reasons": reasons,
            })
    return result
