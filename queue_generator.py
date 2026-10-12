"""Deterministic post-review agent queue and Markdown counts."""

from collections import Counter

from reviewer import validate_overrides


def generate_final_queue(predictions: list[dict], overrides: list[dict], config: dict) -> list[dict]:
    corrections = {p["ticket_id"]: p for p in validate_overrides(overrides, predictions, config)}
    queue = []
    for p in predictions:
        correction = corrections.get(p["ticket_id"])
        category = correction["new_category"] if correction else p["category"]
        priority = correction["new_priority"] if correction else p["priority"]
        if category not in config["allowed_categories"] or priority not in config["allowed_priorities"]:
            raise ValueError("Final category or priority is not allowed by the configuration.")
        queue.append({
            "ticket_id": p["ticket_id"], "final_category": category, "final_priority": priority,
            "final_route_to": config["routing_rules"][category],
            "suggested_reply": p["suggested_reply"], "was_overridden": correction is not None,
        })
    return queue


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r", "<br>").replace("\n", "<br>")


def render_queue_summary(queue: list[dict], overrides: list[dict], config: dict) -> str:
    lines = ["# Agent Queue Summary", "", f"Total tickets: {len(queue)}", ""]
    for title, field, labels in (
        ("Final categories", "final_category", config["allowed_categories"]),
        ("Final priorities", "final_priority", config["allowed_priorities"]),
        ("Routing destinations", "final_route_to", sorted(set(config["routing_rules"][c] for c in config["allowed_categories"]))),
    ):
        counts = Counter(item[field] for item in queue)
        if sum(counts.values()) != len(queue) or set(counts) - set(labels):
            raise ValueError("Queue summary contains unconfigured values or inconsistent counts.")
        lines.extend([f"## {title}", "", "| Value | Count |", "| --- | ---: |"])
        lines.extend(f"| {_escape(label)} | {counts[label]} |" for label in labels)
        lines.append("")
    lines.extend(["## Overridden tickets", ""])
    if not overrides:
        lines.append("None.")
    else:
        lines.extend(["| Ticket | Old category | New category | Old priority | New priority |", "| --- | --- | --- | --- | --- |"])
        for item in overrides:
            lines.append("| " + " | ".join(_escape(item[key]) for key in ("ticket_id", "old_category", "new_category", "old_priority", "new_priority")) + " |")
    return "\n".join(lines) + "\n"
