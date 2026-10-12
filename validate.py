"""Validate current artifacts and recorded execution order without API calls."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import sys

from escalations import generate_escalations
from io_utils import load_json, validate_config, validate_tickets
from llm_client import SYSTEM_PROMPT
from normalizer import normalize_tickets
from queue_generator import generate_final_queue, render_queue_summary
from reviewer import validate_overrides
from run_log import sha256_file
from triage import recover_predictions, validate_predictions
from workflow import WorkflowState


class ValidationError(ValueError):
    pass


def read_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                raise ValidationError(f"{path.name}:{index}: invalid JSON record.") from None
            if not isinstance(record, dict):
                raise ValidationError(f"{path.name}:{index}: expected a JSON object.")
            records.append(record)
    return records


def _require(condition, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _utc_timestamp(value) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).utcoffset() == timedelta(0)
    except ValueError:
        return False


def _check_secret_free(records) -> None:
    text = json.dumps(records)
    key = os.environ.get("OPENAI_API_KEY", "")
    _require(not (len(key) >= 8 and key in text), "Execution logs contain an API credential.")
    _require(not re.search(r"\bsk-[A-Za-z0-9_-]{12,}", text), "Execution logs contain a credential-like token.")
    def walk(value):
        if isinstance(value, dict):
            for name, item in value.items():
                _require(name.lower() not in {"api_key", "authorization", "access_token", "secret"}, "Execution logs contain a forbidden secret field.")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(records)


def validate_artifacts(root: Path, require_finalised: bool = True) -> list[str]:
    """Importable by main; the internal check accepts the pre-validation prefix."""
    root = Path(root).resolve()
    try:
        return _validate(root, require_finalised)
    except ValidationError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise ValidationError(f"Artifact validation failed: {error}") from None


def _validate(root: Path, require_finalised: bool) -> list[str]:
    required = (
        "tickets.json", "triage_config.json", "normalized_tickets.json", "triage_predictions.json",
        "review_overrides.json", "final_queue.json", "queue_summary.md", "escalations.json",
        "llm_calls.jsonl", "pipeline_events.jsonl", "recovery_log.jsonl",
    )
    missing = [name for name in required if not (root / name).is_file()]
    _require(not missing, "Missing required artifacts: " + ", ".join(missing))
    tickets = load_json(root / "tickets.json")
    config = load_json(root / "triage_config.json")
    validate_tickets(tickets)
    validate_config(config)
    normalized = load_json(root / "normalized_tickets.json")
    expected_normalized = normalize_tickets(tickets)
    _require(normalized == expected_normalized, "Normalized records must exactly match deterministic normalization of current disk inputs.")
    checks = ["All required artifacts exist; current inputs are valid and normalization matches disk inputs."]

    predictions = load_json(root / "triage_predictions.json")
    validated = validate_predictions({"predictions": predictions}, normalized, config)
    _require(predictions == validated, "Predictions must have the exact schema, input order, configured routes, and one record per ticket.")
    checks.append("Predictions have valid labels, priorities, confidence, replies, routes, and one record per input ticket.")
    overrides = load_json(root / "review_overrides.json")
    _require(overrides == validate_overrides(overrides, predictions, config), "Overrides must preserve original ticket order.")
    queue = load_json(root / "final_queue.json")
    _require(isinstance(queue, list) and all(isinstance(p, dict) and isinstance(p.get("was_overridden"), bool) for p in queue), "Final queue requires boolean was_overridden fields.")
    _require(queue == generate_final_queue(predictions, overrides, config), "Final queue must apply all effective overrides, recompute routes, and preserve validated replies and ticket order.")
    checks.append("Overrides and final queue are consistent; post-review routes and priorities are correct.")
    _require((root / "queue_summary.md").read_text(encoding="utf-8") == render_queue_summary(queue, overrides, config), "Queue summary counts or override listing do not match the final queue.")
    _require(load_json(root / "escalations.json") == generate_escalations(queue, predictions, config), "Escalations do not match post-review category and confidence rules.")
    checks.append("Queue summary counts and post-review escalations match final data.")

    events = read_jsonl(root / "pipeline_events.jsonl")
    _require(bool(events), "Pipeline execution evidence is empty.")
    run_id = events[0].get("run_id")
    _require(isinstance(run_id, str) and bool(run_id), "Execution evidence requires a run ID.")
    _require(all(e.get("run_id") == run_id and e.get("sequence") == i and _utc_timestamp(e.get("timestamp")) for i, e in enumerate(events)), "Execution events must have one run ID, consecutive sequence numbers, and UTC timestamps.")
    _require(all(e.get("kind") in ("state", "llm_request") for e in events), "Execution evidence has an unrecognized event.")
    stages = [e for e in events if e["kind"] == "state"]
    expected_states = [s.value for s in WorkflowState]
    if not require_finalised:
        expected_states = expected_states[:6]
    _require([e.get("state") for e in stages] == expected_states, "Workflow stages are missing, skipped, repeated, or unfinished.")
    _require([e.get("from_state") for e in stages] == [None, *expected_states[:-1]], "Workflow transition origins are inconsistent.")
    by_state = {e["state"]: e for e in stages}
    required_evidence = {
        "INPUTS_LOADED": {"tickets.json", "triage_config.json"},
        "TICKETS_NORMALIZED": {"normalized_tickets.json"},
        "TRIAGE_PREDICTED": {"triage_predictions.json"},
        "HUMAN_REVIEW_COMPLETE": {"review_overrides.json"},
        "FINAL_QUEUE_GENERATED": {"final_queue.json", "queue_summary.md", "escalations.json"},
    }
    for event in stages:
        hashes = event.get("artifacts")
        _require(isinstance(hashes, dict), "Stage events require artifact hash mappings.")
        _require(required_evidence.get(event["state"], set()) <= hashes.keys(), "Stage event lacks required input/output evidence.")
        for name, digest in hashes.items():
            _require(name in required + ("llm_prompt.json", "llm_response.json"), "Stage event references an unexpected artifact.")
            _require(sha256_file(root / name) == digest, f"{name} changed after its recorded stage; stale or modified artifacts detected.")
    _require(by_state["INPUTS_LOADED"].get("disk_inputs_read") is True, "Disk input-loading evidence is missing.")
    _require(by_state["INPUTS_LOADED"].get("ticket_count") == len(tickets), "Recorded input count differs from disk inputs.")
    checks.append("Hash-bound stage evidence proves normalization, review, output, and validation ordering for current inputs.")

    calls = read_jsonl(root / "llm_calls.jsonl")
    recoveries = read_jsonl(root / "recovery_log.jsonl")
    _check_secret_free([events, calls, recoveries])
    for call in calls:
        _require(call.get("stage") == "TRIAGE_PREDICTED" and _utc_timestamp(call.get("timestamp")), "LLM log stage or UTC timestamp is invalid.")
        _require(all(isinstance(call.get(k), str) and call[k] for k in ("run_id", "provider", "model", "request_id")), "LLM log identity fields are missing.")
        _require(isinstance(call.get("prompt_hash"), str) and re.fullmatch(r"[0-9a-f]{64}", call["prompt_hash"]), "LLM log requires a SHA-256 prompt hash.")
        _require(call.get("input_artifacts") == ["normalized_tickets.json", "triage_config.json"] and call.get("output_artifact") == "triage_predictions.json", "LLM log artifact references are invalid.")
        _require(call.get("status") in ("success", "failed"), "LLM log request status is missing.")
    current_calls = [c for c in calls if c["run_id"] == run_id]
    request_events = [e for e in events if e["kind"] == "llm_request"]
    expected_count = 1 if normalized else 0
    _require(len(current_calls) == len(request_events) == expected_count, "Expected exactly one actual LLM request per nonempty batch, and none for an empty batch.")
    if normalized:
        call, event = current_calls[0], request_events[0]
        _require(call["status"] == "success", "The current run has no successful model response.")
        _require(by_state["TICKETS_NORMALIZED"]["sequence"] < event["sequence"] < by_state["TRIAGE_PREDICTED"]["sequence"], "The model request must occur after saved normalization and before prediction completion.")
        _require(call.get("event_sequence") == event["sequence"] and call["request_id"] == event.get("request_id"), "LLM request log and event do not match.")
        _require(call["prompt_hash"] == event.get("prompt_hash") == sha256_file(root / "llm_prompt.json"), "Actual serialized prompt hash does not match the request log.")
        _require(call.get("response_hash") == sha256_file(root / "llm_response.json"), "Recorded model response hash differs from the actual response artifact.")
        _require(call.get("input_hashes") == {name: sha256_file(root / name) for name in call["input_artifacts"]}, "LLM request inputs are stale.")
        prompt = load_json(root / "llm_prompt.json")
        _require(isinstance(prompt, list) and len(prompt) == 2, "Recorded model prompt is malformed.")
        _require(prompt[0] == {"role": "system", "content": SYSTEM_PROMPT} and prompt[1].get("role") == "user", "Recorded prompt instructions or roles differ from the implementation.")
        _require(json.loads(prompt[1]["content"]) == {"normalized_tickets": normalized, "triage_config": config}, "Actual LLM prompt does not include all current normalized tickets and the full configuration.")
        expected_errors = []
        replayed = recover_predictions(load_json(root / "llm_response.json"), normalized, config, lambda ticket_id, error: expected_errors.append((ticket_id, error)))
        _require(replayed == predictions, "Predictions do not match the recorded model response and deterministic recovery.")
        actual_errors = [(e.get("ticket_id"), e.get("error")) for e in recoveries if e.get("run_id") == run_id]
        _require(actual_errors == expected_errors, "Prediction recovery errors were not logged consistently.")
    else:
        _require(not predictions, "An empty input batch cannot contain predictions.")
    checks.append("One-request logging, full prompt, recorded response, and recovery are consistent; no secrets were found in logs.")
    if require_finalised:
        _require(isinstance(by_state["VALIDATION_COMPLETE"].get("checks_passed"), int) and by_state["VALIDATION_COMPLETE"]["checks_passed"] > 0, "Successful validation evidence is missing.")
    return checks


def main() -> int:
    try:
        checks = validate_artifacts(Path(__file__).resolve().parent)
    except ValidationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    for check in checks:
        print(f"PASS: {check}")
    print(f"Validation complete: {len(checks)} groups passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
