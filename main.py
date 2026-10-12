"""Run Task 1: load, validate, and normalize support tickets."""

from pathlib import Path
import sys

from io_utils import InputValidationError, load_json, save_json, validate_config, validate_tickets
from normalizer import normalize_tickets
from workflow import Workflow, WorkflowState


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    workflow = Workflow()
    artifact_path = project_dir / "normalized_tickets.json"
    try:
        tickets = load_json(project_dir / "tickets.json")
        config = load_json(project_dir / "triage_config.json")
        validate_tickets(tickets)
        validate_config(config)
        workflow.transition(WorkflowState.INPUTS_LOADED)

        normalized = normalize_tickets(tickets)
        save_json(artifact_path, normalized)
        workflow.transition(WorkflowState.TICKETS_NORMALIZED)
    except (InputValidationError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Input ticket count: {len(tickets)}")
    print(f"Output artifact: {artifact_path}")
    print(f"Current state: {workflow.state.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
