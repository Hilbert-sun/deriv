"""Per-run execution evidence and append-only, secret-free request logs."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from io_utils import save_text
from workflow import WorkflowState, WorkflowTransitionError


OUTPUTS = (
    "normalized_tickets.json", "triage_predictions.json", "review_overrides.json",
    "final_queue.json", "queue_summary.md", "escalations.json", "llm_prompt.json", "llm_response.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


class RunLog:
    def __init__(self, root: Path, workflow):
        self.root = root
        self.workflow = workflow
        self.run_id = str(uuid4())
        self.sequence = 0
        for name in OUTPUTS:
            (root / name).unlink(missing_ok=True)
        save_text(root / "pipeline_events.jsonl", "")
        (root / "llm_calls.jsonl").touch(exist_ok=True)
        (root / "recovery_log.jsonl").touch(exist_ok=True)
        self.event("state", state=WorkflowState.INIT.value, from_state=None, artifacts={})

    def event(self, kind: str, **details) -> dict:
        event = {"run_id": self.run_id, "sequence": self.sequence, "timestamp": utc_now(), "kind": kind, **details}
        append_jsonl(self.root / "pipeline_events.jsonl", event)
        self.sequence += 1
        return event

    def transition(self, target: WorkflowState, artifacts=(), **details) -> None:
        states = tuple(WorkflowState)
        index = states.index(self.workflow.state) + 1
        if index >= len(states) or states[index] != target:
            raise WorkflowTransitionError("Execution log cannot record an invalid workflow transition.")
        hashes = {name: sha256_file(self.root / name) for name in artifacts}
        self.event("state", state=target.value, from_state=self.workflow.state.value, artifacts=hashes, **details)
        self.workflow.transition(target)

    def recovery_error(self, ticket_id, error: str) -> None:
        append_jsonl(self.root / "recovery_log.jsonl", {
            "run_id": self.run_id, "timestamp": utc_now(), "ticket_id": ticket_id, "error": error,
        })


class CallAudit:
    def __init__(self, run: RunLog):
        self.run = run
        self.record = None

    def prepare(self, request: dict, settings) -> None:
        prompt = json.dumps(request["messages"], ensure_ascii=False, separators=(",", ":"))
        save_text(self.run.root / "llm_prompt.json", prompt)
        self.record = {
            "stage": WorkflowState.TRIAGE_PREDICTED.value,
            "timestamp": utc_now(), "provider": settings.provider_label, "model": settings.model,
            "prompt_hash": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "input_artifacts": ["normalized_tickets.json", "triage_config.json"],
            "input_hashes": {name: sha256_file(self.run.root / name) for name in ("normalized_tickets.json", "triage_config.json")},
            "output_artifact": "triage_predictions.json", "run_id": self.run.run_id,
            "request_id": str(uuid4()), "response_format": settings.response_format,
        }

    def started(self) -> None:
        self.run.workflow.require_state(WorkflowState.TICKETS_NORMALIZED)
        event = self.run.event("llm_request", request_id=self.record["request_id"], prompt_hash=self.record["prompt_hash"])
        self.record["event_sequence"] = event["sequence"]

    def finish(self, status: str, http_status=None, response_hash=None, error_type=None) -> None:
        append_jsonl(self.run.root / "llm_calls.jsonl", {
            **self.record, "status": status, "http_status": http_status,
            "response_hash": response_hash, "error_type": error_type,
        })
