"""Reusable, sequential workflow states for the support triage pipeline."""

from enum import Enum


class WorkflowState(str, Enum):
    INIT = "INIT"
    INPUTS_LOADED = "INPUTS_LOADED"
    TICKETS_NORMALIZED = "TICKETS_NORMALIZED"
    TRIAGE_PREDICTED = "TRIAGE_PREDICTED"
    HUMAN_REVIEW_COMPLETE = "HUMAN_REVIEW_COMPLETE"
    FINAL_QUEUE_GENERATED = "FINAL_QUEUE_GENERATED"
    VALIDATION_COMPLETE = "VALIDATION_COMPLETE"
    RESULTS_FINALISED = "RESULTS_FINALISED"


class WorkflowTransitionError(ValueError):
    """Raised when a transition does not advance exactly one state."""


class Workflow:
    """Guard stage completion with transitions in the declared state order."""

    def __init__(self) -> None:
        self._state = WorkflowState.INIT

    @property
    def state(self) -> WorkflowState:
        return self._state

    def transition(self, target: WorkflowState) -> WorkflowState:
        states = tuple(WorkflowState)
        next_index = states.index(self._state) + 1
        expected = states[next_index] if next_index < len(states) else None
        if not isinstance(target, WorkflowState) or expected is None or target != expected:
            expected_name = expected.value if expected is not None else "no further state"
            raise WorkflowTransitionError(
                f"Invalid transition from {self._state.value} to {target!r}; "
                f"expected {expected_name}."
            )
        self._state = target
        return self._state
