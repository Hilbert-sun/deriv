"""Run Tasks 1–2: normalize tickets, then predict the full batch once."""

from pathlib import Path
import sys

from io_utils import InputValidationError, load_json, save_json, validate_config, validate_tickets
from normalizer import normalize_tickets
from llm_client import LLMError, LLMSettings
from triage import PredictionValidationError, predict_tickets
from workflow import Workflow, WorkflowState


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    workflow = Workflow()
    artifact_path = project_dir / "normalized_tickets.json"
    prediction_path = project_dir / "triage_predictions.json"
    try:
        tickets = load_json(project_dir / "tickets.json")
        config = load_json(project_dir / "triage_config.json")
        validate_tickets(tickets)
        validate_config(config)
        workflow.transition(WorkflowState.INPUTS_LOADED)
        print(f"Input ticket count: {len(tickets)}")

        normalized = normalize_tickets(tickets)
        save_json(artifact_path, normalized)
        workflow.transition(WorkflowState.TICKETS_NORMALIZED)
        print(f"Normalized ticket count: {len(normalized)}")
        print(f"Normalized artifact: {artifact_path}")

        settings = LLMSettings.from_environment(project_dir / ".env")
        print(f"LLM provider: {settings.provider_label}")
        print(f"LLM model: {settings.model}")
        workflow.require_state(WorkflowState.TICKETS_NORMALIZED)
        predictions = predict_tickets(normalized, config, settings)
        save_json(prediction_path, predictions)
        workflow.transition(WorkflowState.TRIAGE_PREDICTED)
    except (InputValidationError, LLMError, PredictionValidationError, OSError, UnicodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print(f"Current state: {workflow.state.value}")
        return 1

    print(f"Predictions generated: {len(predictions)}")
    print(f"Prediction artifact: {prediction_path}")
    print(f"Current state: {workflow.state.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
