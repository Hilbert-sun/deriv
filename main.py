"""Run the full disk-backed support triage workflow."""

from pathlib import Path
import sys

from escalations import generate_escalations
from io_utils import load_json, save_json, save_text, validate_config, validate_tickets
from llm_client import LLMSettings
from normalizer import normalize_tickets
from queue_generator import generate_final_queue, render_queue_summary
from reviewer import review_predictions
from run_log import CallAudit, RunLog
from triage import predict_tickets
from validate import validate_artifacts
from workflow import Workflow, WorkflowState


def run_pipeline(project_dir=None, input_fn=None, settings=None, workflow=None, output_fn=print) -> Workflow:
    root = Path(project_dir or Path(__file__).resolve().parent).resolve()
    workflow = workflow or Workflow()
    workflow.require_state(WorkflowState.INIT)
    run = RunLog(root, workflow)
    tickets = load_json(root / "tickets.json")
    config = load_json(root / "triage_config.json")
    validate_tickets(tickets)
    validate_config(config)
    run.transition(WorkflowState.INPUTS_LOADED, ("tickets.json", "triage_config.json"), disk_inputs_read=True, ticket_count=len(tickets))
    output_fn(f"Input ticket count: {len(tickets)}")

    normalized = normalize_tickets(tickets)
    save_json(root / "normalized_tickets.json", normalized)
    run.transition(WorkflowState.TICKETS_NORMALIZED, ("normalized_tickets.json",))
    output_fn(f"Normalized ticket count: {len(normalized)}")
    output_fn(f"Normalized artifact: {root / 'normalized_tickets.json'}")
    workflow.require_state(WorkflowState.TICKETS_NORMALIZED)
    if normalized:
        settings = settings or LLMSettings.from_environment(root / ".env")
        output_fn(f"LLM provider: {settings.provider_label}")
        output_fn(f"LLM model: {settings.model}")
        predictions = predict_tickets(normalized, config, settings, audit=CallAudit(run), error_callback=run.recovery_error)
    else:
        predictions = []
        output_fn("Empty batch: no LLM request needed.")
    save_json(root / "triage_predictions.json", predictions)
    run.transition(WorkflowState.TRIAGE_PREDICTED, ("triage_predictions.json",))
    output_fn(f"Predictions generated: {len(predictions)}")
    output_fn(f"Prediction artifact: {root / 'triage_predictions.json'}")

    workflow.require_state(WorkflowState.TRIAGE_PREDICTED)
    overrides = review_predictions(predictions, config, input_fn, output_fn)
    save_json(root / "review_overrides.json", overrides)
    run.transition(WorkflowState.HUMAN_REVIEW_COMPLETE, ("review_overrides.json",))

    workflow.require_state(WorkflowState.HUMAN_REVIEW_COMPLETE)
    queue = generate_final_queue(predictions, overrides, config)
    save_json(root / "final_queue.json", queue)
    save_text(root / "queue_summary.md", render_queue_summary(queue, overrides, config))
    save_json(root / "escalations.json", generate_escalations(queue, predictions, config))
    run.transition(WorkflowState.FINAL_QUEUE_GENERATED, ("final_queue.json", "queue_summary.md", "escalations.json"))

    workflow.require_state(WorkflowState.FINAL_QUEUE_GENERATED)
    checks = validate_artifacts(root, require_finalised=False)
    for check in checks:
        output_fn(f"PASS: {check}")
    run.transition(WorkflowState.VALIDATION_COMPLETE, checks_passed=len(checks))
    run.transition(WorkflowState.RESULTS_FINALISED)
    output_fn(f"Final tickets: {len(queue)}; effective overrides: {len(overrides)}")
    output_fn(f"Output directory: {root}")
    for name in ("review_overrides.json", "final_queue.json", "queue_summary.md", "escalations.json"):
        output_fn(f"Output artifact: {root / name}")
    output_fn(f"Current state: {workflow.state.value}")
    return workflow


def main() -> int:
    workflow = Workflow()
    try:
        run_pipeline(workflow=workflow)
    except (ValueError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print(f"Current state: {workflow.state.value}")
        return 1
    except KeyboardInterrupt:
        print("Pipeline interrupted; review or later stages are incomplete.", file=sys.stderr)
        print(f"Current state: {workflow.state.value}")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
