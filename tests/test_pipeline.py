import copy
from dataclasses import replace
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

import pytest

import main
from io_utils import load_json, save_json
import io_utils
from llm_client import LLMError
from run_log import OUTPUTS
from triage import PredictionValidationError, validate_predictions
from validate import ValidationError, read_jsonl, validate_artifacts
from workflow import Workflow, WorkflowState, WorkflowTransitionError

from conftest import FIXTURES


def run(project, settings, lines=("",), **kwargs):
    iterator = iter(lines)
    return main.run_pipeline(project, input_fn=lambda: next(iterator), settings=settings, **kwargs)


def test_standard_input_complete_workflow(project, provider, settings):
    originals = [(project / name).read_bytes() for name in ("tickets.json", "triage_config.json")]
    workflow = run(project, settings)
    assert workflow.state == WorkflowState.RESULTS_FINALISED
    assert len(provider.requests) == 1
    assert len(validate_artifacts(project)) == 6
    assert [e["state"] for e in read_jsonl(project / "pipeline_events.jsonl") if e["kind"] == "state"] == [s.value for s in WorkflowState]
    assert all((project / name).is_file() for name in OUTPUTS)
    assert [(project / name).read_bytes() for name in ("tickets.json", "triage_config.json")] == originals


def test_changed_ticket_content(project, provider, settings):
    shutil.copyfile(FIXTURES / "changed_tickets.json", project / "tickets.json")
    run(project, settings)
    inputs = load_json(project / "tickets.json")
    queue = load_json(project / "final_queue.json")
    assert [p["ticket_id"] for p in queue] == [t["ticket_id"] for t in inputs]
    assert "谢谢" in (project / "normalized_tickets.json").read_text()
    assert len(provider.requests) == 1


def test_changed_configuration(project, provider, settings):
    shutil.copyfile(FIXTURES / "changed_config.json", project / "triage_config.json")
    run(project, settings)
    config = load_json(project / "triage_config.json")
    payload = json.loads(provider.requests[0]["messages"][1]["content"])
    assert payload["triage_config"] == config
    for p in load_json(project / "final_queue.json"):
        assert p["final_route_to"] == config["routing_rules"][p["final_category"]]
        assert p["final_priority"] in config["allowed_priorities"]


def test_multiple_overrides_and_repeated_edit(project, provider, settings):
    config = load_json(project / "triage_config.json")
    tickets = load_json(project / "tickets.json")
    first, second = (t["ticket_id"].strip() for t in tickets[:2])
    category, priority = config["allowed_categories"][1], config["allowed_priorities"][0]
    lines = (f"{second},{category},{priority}", f"{first},{category},{priority}", f"{first},{config['allowed_categories'][-1]},{priority}", "")
    run(project, settings, lines)
    overrides = load_json(project / "review_overrides.json")
    assert [p["ticket_id"] for p in overrides] == [first, second]
    assert all(p["old_category"] == config["allowed_categories"][0] for p in overrides)
    queue = load_json(project / "final_queue.json")
    assert queue[0]["final_category"] == config["allowed_categories"][-1]
    assert queue[1]["final_category"] == category
    assert all(p["final_priority"] == priority and p["was_overridden"] for p in queue[:2])
    assert all(p["final_route_to"] == config["routing_rules"][p["final_category"]] for p in queue)
    assert len(provider.requests) == 1


def test_no_overrides(project, provider, settings, capsys):
    run(project, settings)
    assert load_json(project / "review_overrides.json") == []
    queue = load_json(project / "final_queue.json")
    predicted = load_json(project / "triage_predictions.json")
    assert all(not p["was_overridden"] for p in queue)
    assert [p["final_category"] for p in queue] == [p["category"] for p in predicted]
    assert "Enter any overrides as: ticket_id,category,priority\nPress Enter on an empty line when done." in capsys.readouterr().out


def test_invalid_override_allows_retry(project, provider, settings, capsys):
    ticket_id = load_json(project / "tickets.json")[0]["ticket_id"]
    config = load_json(project / "triage_config.json")
    category, priority = config["allowed_categories"][1], config["allowed_priorities"][0]
    run(project, settings, ("malformed", f"unknown,{category},{priority}", f"{ticket_id},invalid,{priority}", f"{ticket_id},{category},invalid", f"{ticket_id},{category},{priority}", ""))
    assert capsys.readouterr().out.count("Invalid override:") == 4
    assert len(load_json(project / "review_overrides.json")) == 1


def test_undo_and_noop_do_not_count(project, provider, settings):
    ticket_id = load_json(project / "tickets.json")[0]["ticket_id"]
    config = load_json(project / "triage_config.json")
    old_c, old_p = config["allowed_categories"][0], config["allowed_priorities"][-1]
    run(project, settings, (f"{ticket_id},{old_c},{old_p}", f"{ticket_id},{config['allowed_categories'][1]},{old_p}", f"{ticket_id},{old_c},{old_p}", ""))
    assert load_json(project / "review_overrides.json") == []


@pytest.mark.parametrize("kind", ["invalid_category", "invalid_priority", "missing_reply", "duplicate", "missing", "wrong_type", "missing_confidence", "invalid_confidence"])
def test_individual_prediction_recovery(project, provider, settings, kind):
    affected = []
    def mutate(response):
        items = response["predictions"]
        affected.append(items[0]["ticket_id"])
        if kind == "invalid_category": items[0]["category"] = "invalid"
        elif kind == "invalid_priority": items[0]["priority"] = "invalid"
        elif kind == "missing_reply": del items[0]["suggested_reply"]
        elif kind == "duplicate": items.append(copy.deepcopy(items[0]))
        elif kind == "missing": items.pop(0)
        elif kind == "wrong_type": items[0]["reason"] = []
        elif kind == "missing_confidence": del items[0]["confidence"]
        else: items[0]["confidence"] = True
        return response
    provider.mutate = mutate
    run(project, settings)
    config = load_json(project / "triage_config.json")
    predicted = load_json(project / "triage_predictions.json")
    fallback = next(p for p in predicted if p["ticket_id"] == affected[0])
    assert fallback["category"] == "other" and fallback["confidence"] == 0.0
    assert fallback["route_to"] == config["routing_rules"]["other"]
    assert len(predicted) == len(load_json(project / "tickets.json"))
    assert all(p["confidence"] == 0.82 for p in predicted if p["ticket_id"] != affected[0])
    assert read_jsonl(project / "recovery_log.jsonl")
    assert len(provider.requests) == 1


def test_no_fallback_category_fails_clearly(project, provider, settings):
    shutil.copyfile(FIXTURES / "changed_config.json", project / "triage_config.json")
    provider.mutate = lambda response: {"predictions": response["predictions"][1:]}
    workflow = Workflow()
    with pytest.raises(PredictionValidationError, match="fallback"):
        run(project, settings, workflow=workflow)
    assert workflow.state == WorkflowState.TICKETS_NORMALIZED
    assert not (project / "triage_predictions.json").exists()


def test_reply_word_limit_recovery(project, provider, settings):
    config = load_json(project / "triage_config.json")
    config["reply_style"]["max_words"] = 1
    save_json(project / "triage_config.json", config)
    def mutate(response):
        response["predictions"][0]["suggested_reply"] = "too many words"
        return response
    provider.mutate = mutate
    run(project, settings)
    assert all(len(p["suggested_reply"].split()) <= 1 for p in load_json(project / "triage_predictions.json"))


def test_clean_regeneration_and_determinism(project, provider, settings):
    run(project, settings)
    contents = {name: (project / name).read_bytes() for name in OUTPUTS if name not in ("llm_prompt.json", "llm_response.json")}
    for name in OUTPUTS:
        (project / name).unlink()
    run(project, settings)
    assert all((project / name).read_bytes() == content for name, content in contents.items())
    assert len(provider.requests) == 2
    assert len(read_jsonl(project / "llm_calls.jsonl")) == 2
    validate_artifacts(project)


def test_escalation_uses_post_review_values_and_threshold(project, provider, settings):
    def mutate(response):
        for p, confidence in zip(response["predictions"], (0.60, 0.59, 0.9)):
            p["confidence"] = confidence
        return response
    provider.mutate = mutate
    tickets = load_json(project / "tickets.json")
    config = load_json(project / "triage_config.json")
    priority = config["allowed_priorities"][-1]
    run(project, settings, (f"{tickets[0]['ticket_id']},other,{priority}", ""))
    escalations = load_json(project / "escalations.json")
    assert [p["ticket_id"] for p in escalations] == [tickets[0]["ticket_id"], tickets[1]["ticket_id"]]
    assert escalations[0]["reasons"] == ["post_review_category_other"]
    assert escalations[1]["reasons"] == ["confidence_below_0.60"]


@pytest.mark.parametrize("filename", ["tickets.json", "triage_config.json", "normalized_tickets.json", "triage_predictions.json", "review_overrides.json", "final_queue.json", "queue_summary.md", "llm_prompt.json", "llm_response.json"])
def test_validator_rejects_changed_artifacts(project, provider, settings, filename):
    run(project, settings)
    with (project / filename).open("a", encoding="utf-8") as handle:
        handle.write(" ")
    with pytest.raises(ValidationError):
        validate_artifacts(project)


def test_validator_rejects_extra_requests(project, provider, settings):
    run(project, settings)
    with (project / "llm_calls.jsonl").open("a") as handle:
        handle.write(json.dumps(read_jsonl(project / "llm_calls.jsonl")[0]) + "\n")
    with pytest.raises(ValidationError, match="exactly one"):
        validate_artifacts(project)


def test_api_failure_logged_without_retries_or_secrets(project, provider, settings):
    provider.status = 429
    workflow = Workflow()
    with pytest.raises(LLMError, match="429"):
        run(project, settings, workflow=workflow)
    assert workflow.state == WorkflowState.TICKETS_NORMALIZED
    assert len(provider.requests) == 1
    log = read_jsonl(project / "llm_calls.jsonl")
    assert len(log) == 1 and log[0]["status"] == "failed" and log[0]["http_status"] == 429
    assert settings.api_key not in (project / "llm_calls.jsonl").read_text()
    assert not (project / "triage_predictions.json").exists()
    assert not (project / "final_queue.json").exists()


def test_review_precedes_queue_and_eof_blocks_completion(project, provider, settings):
    workflow = Workflow()
    def review_input():
        assert not (project / "final_queue.json").exists()
        assert workflow.state == WorkflowState.TRIAGE_PREDICTED
        raise EOFError
    with pytest.raises(ValueError, match="closed"):
        main.run_pipeline(project, input_fn=review_input, settings=settings, workflow=workflow)
    assert workflow.state == WorkflowState.TRIAGE_PREDICTED
    assert not (project / "review_overrides.json").exists()


def test_validation_failure_blocks_finalisation(project, provider, settings, monkeypatch):
    def fail(*args, **kwargs):
        raise ValidationError("injected validation failure")
    monkeypatch.setattr(main, "validate_artifacts", fail)
    workflow = Workflow()
    with pytest.raises(ValidationError):
        run(project, settings, workflow=workflow)
    assert workflow.state == WorkflowState.FINAL_QUEUE_GENERATED


def test_write_failure_blocks_stage(project, provider, settings, monkeypatch):
    real_save = main.save_json
    def fail(path, data):
        if path.name == "review_overrides.json":
            raise OSError("injected write failure")
        return real_save(path, data)
    monkeypatch.setattr(main, "save_json", fail)
    workflow = Workflow()
    with pytest.raises(OSError):
        run(project, settings, workflow=workflow)
    assert workflow.state == WorkflowState.TRIAGE_PREDICTED
    assert not (project / "final_queue.json").exists()


@pytest.mark.parametrize("response_format", ["json_schema", "json_object", "text"])
def test_provider_format_modes(project, provider, settings, response_format):
    run(project, replace(settings, response_format=response_format))
    body = provider.requests[0]
    assert len(provider.requests) == 1 and "temperature" not in body
    if response_format == "text": assert "response_format" not in body
    else: assert body["response_format"]["type"] == response_format


@pytest.mark.parametrize("raw", ["not JSON", "```json\n{}\n```", '{"predictions":[],"predictions":[]}', '{"predictions":NaN}'])
def test_malformed_entire_response_fails_without_repair(project, provider, settings, raw):
    provider.raw = raw
    workflow = Workflow()
    with pytest.raises((LLMError, PredictionValidationError)):
        run(project, settings, workflow=workflow)
    assert len(provider.requests) == 1
    assert workflow.state == WorkflowState.TICKETS_NORMALIZED


def test_empty_batch_requires_no_model(project, provider):
    save_json(project / "tickets.json", [])
    workflow = run(project, None)
    assert workflow.state == WorkflowState.RESULTS_FINALISED
    assert not provider.requests
    assert load_json(project / "final_queue.json") == []
    validate_artifacts(project)


def test_workflow_rejects_skips_and_stage_guards():
    workflow = Workflow()
    with pytest.raises(WorkflowTransitionError): workflow.transition(WorkflowState.FINAL_QUEUE_GENERATED)
    with pytest.raises(WorkflowTransitionError): workflow.require_state(WorkflowState.HUMAN_REVIEW_COMPLETE)
    for state in tuple(WorkflowState)[1:]: workflow.transition(state)
    with pytest.raises(WorkflowTransitionError): workflow.transition(WorkflowState.RESULTS_FINALISED)


def test_standalone_validator_from_other_working_directory(project, provider, settings):
    run(project, settings)
    for source in Path(main.__file__).parent.glob("*.py"):
        shutil.copyfile(source, project / source.name)
    result = subprocess.run([sys.executable, str(project / "validate.py")], cwd=project.parent, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "Validation complete: 6 groups passed." in result.stdout


def test_fallback_route_and_priority_are_configuration_driven(project, provider, settings):
    config = load_json(project / "triage_config.json")
    config["routing_rules"]["other"] = "custom_manual_dispatch"
    config["allowed_priorities"] = ["attention", "later"]
    save_json(project / "triage_config.json", config)
    provider.mutate = lambda response: {"predictions": response["predictions"][1:]}
    run(project, settings)
    fallback = next(p for p in load_json(project / "triage_predictions.json") if p["confidence"] == 0.0)
    assert fallback["route_to"] == "custom_manual_dispatch" and fallback["priority"] == "attention"


@pytest.mark.parametrize("value", [-1, 1.1, "0.5", None, False, 10**400])
def test_invalid_confidence_recovers(project, provider, settings, value):
    def mutate(response):
        response["predictions"][0]["confidence"] = value
        return response
    provider.mutate = mutate
    run(project, settings)
    assert sum(p["confidence"] == 0.0 for p in load_json(project / "triage_predictions.json")) == 1


def test_unknown_prediction_is_logged_without_losing_inputs(project, provider, settings):
    def mutate(response):
        extra = copy.deepcopy(response["predictions"][0])
        extra["ticket_id"] = "not-an-input-ticket"
        response["predictions"].extend([extra, None])
        return response
    provider.mutate = mutate
    run(project, settings)
    assert len(load_json(project / "final_queue.json")) == len(load_json(project / "tickets.json"))
    assert len(read_jsonl(project / "recovery_log.jsonl")) == 2


@pytest.mark.parametrize("kind", ["refusal", "truncated", "timeout", "connection"])
def test_request_failure_is_logged_and_stops_workflow(project, provider, settings, kind):
    if kind == "refusal": provider.refusal = "test refusal"
    elif kind == "truncated": provider.finish_reason = "length"
    elif kind == "timeout": provider.transport_error = httpx.ReadTimeout
    else: provider.transport_error = httpx.ConnectError
    workflow = Workflow()
    with pytest.raises(LLMError):
        run(project, settings, workflow=workflow)
    assert len(provider.requests) == 1
    assert workflow.state == WorkflowState.TICKETS_NORMALIZED
    log = read_jsonl(project / "llm_calls.jsonl")[0]
    assert log["status"] == "failed" and log["error_type"]


def test_invalid_new_run_cannot_reuse_stale_outputs(project, provider, settings):
    run(project, settings)
    (project / "tickets.json").write_text("{}")
    workflow = Workflow()
    with pytest.raises(ValueError):
        run(project, settings, workflow=workflow)
    assert workflow.state == WorkflowState.INIT
    assert not (project / "final_queue.json").exists()
    with pytest.raises(ValidationError): validate_artifacts(project)
    assert len(provider.requests) == 1


def test_temperature_is_only_explicitly_enabled(project, provider, settings):
    run(project, replace(settings, temperature_zero=True))
    assert provider.requests[0]["temperature"] == 0


def test_review_accepts_exact_labels_with_edge_spaces(project, provider, settings):
    config = load_json(project / "triage_config.json")
    config["allowed_categories"].append(" custom ")
    config["routing_rules"][" custom "] = "custom_team"
    save_json(project / "triage_config.json", config)
    ticket_id = load_json(project / "tickets.json")[0]["ticket_id"]
    run(project, settings, (f"{ticket_id}, custom ,{config['allowed_priorities'][0]}", ""))
    assert load_json(project / "final_queue.json")[0]["final_category"] == " custom "


def test_atomic_write_preserves_existing_file_on_failure(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact.json"
    save_json(artifact, {"original": "Unicode ✓"})
    original = artifact.read_bytes()
    def fail(*args):
        raise OSError("injected atomic replacement failure")
    monkeypatch.setattr(io_utils.os, "replace", fail)
    with pytest.raises(OSError):
        save_json(artifact, {"replacement": "should not persist"})
    assert artifact.read_bytes() == original
    assert list(tmp_path.iterdir()) == [artifact]


def test_summary_group_counts_each_sum_to_ticket_total(project, provider, settings):
    run(project, settings)
    report = (project / "queue_summary.md").read_text()
    total = len(load_json(project / "tickets.json"))
    for title in ("Final categories", "Final priorities", "Routing destinations"):
        section = report.split(f"## {title}\n", 1)[1].split("## ", 1)[0]
        counts = [int(line.rsplit("|", 2)[1].strip()) for line in section.splitlines() if line.startswith("| ") and line.rsplit("|", 2)[1].strip().isdigit()]
        assert sum(counts) == total
