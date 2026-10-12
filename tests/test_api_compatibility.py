"""Dual API compatibility through the actual SDK; no paid requests."""

from dataclasses import replace
import json

import pytest

import main
from io_utils import load_json
from llm_client import LLMError, LLMSettings
from run_log import OUTPUTS
from validate import ValidationError, read_jsonl, validate_artifacts
from workflow import Workflow, WorkflowState


MODES = ("chat_completions", "responses")


def run_api(project, settings, mode, response_format="text", input_fn=None):
    return main.run_pipeline(
        project, settings=replace(settings, api_mode=mode, response_format=response_format),
        input_fn=input_fn or (lambda: ""),
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("response_format", ["json_schema", "json_object", "text"])
def test_complete_pipeline_correct_api_arguments(project, provider, settings, mode, response_format):
    workflow = run_api(project, settings, mode, response_format)
    assert workflow.state == WorkflowState.RESULTS_FINALISED
    assert len(provider.requests) == 1
    assert provider.paths == ["/v1/responses" if mode == "responses" else "/v1/chat/completions"]
    request = provider.requests[0]
    parameter = "input" if mode == "responses" else "messages"
    prompt = request[parameter]
    assert json.loads(prompt[1]["content"]) == {
        "normalized_tickets": load_json(project / "normalized_tickets.json"),
        "triage_config": load_json(project / "triage_config.json"),
    }
    assert "temperature" not in request
    if mode == "responses":
        assert "messages" not in request and "response_format" not in request
        if response_format == "text":
            assert "text" not in request
        else:
            format_spec = request["text"]["format"]
            assert format_spec["type"] == response_format
            if response_format == "json_schema":
                assert "json_schema" not in format_spec
                assert format_spec["strict"] is True
                assert format_spec["schema"]["properties"]["predictions"]["items"]["properties"]["confidence"]["type"] == "number"
    else:
        assert "input" not in request and "text" not in request
        if response_format == "text": assert "response_format" not in request
        else: assert request["response_format"]["type"] == response_format
    call = read_jsonl(project / "llm_calls.jsonl")[0]
    diagnostics = load_json(project / "llm_diagnostics.json")
    assert call["api_mode"] == mode and call["prompt_parameter"] == parameter
    assert call["diagnostics"] == diagnostics
    assert diagnostics == {
        "api_mode": mode, "model": settings.model, "response_format": response_format,
        "http_status": 200, "completion_status": "completed" if mode == "responses" else "stop",
        "text_output_present": True, "json_failure_type": None,
    }
    assert all((project / name).is_file() for name in OUTPUTS)
    assert len(validate_artifacts(project)) == 6


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fence", ["json", "", "JSON", "json_crlf"])
def test_one_complete_json_fence_is_accepted(project, provider, settings, mode, fence):
    newline = "\r\n" if fence == "json_crlf" else "\n"
    language = "json" if fence == "json_crlf" else fence
    provider.content_transform = lambda text: f"  ```{language}{newline}{text}{newline}``` \n"
    run_api(project, settings, mode)
    assert len(provider.requests) == 1
    validate_artifacts(project)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("wrapping", ["prose", "leading_prose", "trailing_prose", "wrong_language", "unclosed_fence", "multiple_fences"])
def test_no_json_substring_extraction_or_repair(project, provider, settings, mode, wrapping):
    sentinel = "PRIVATE_CUSTOMER_TEXT_MUST_NOT_BE_PRINTED"
    wrappers = {
        "prose": lambda text: sentinel,
        "leading_prose": lambda text: sentinel + "\n" + text,
        "trailing_prose": lambda text: text + "\n" + sentinel,
        "wrong_language": lambda text: "```python\n" + text + "\n```",
        "unclosed_fence": lambda text: "```json\n" + text,
        "multiple_fences": lambda text: "```json\n" + text + "\n```\n```json\n{}\n```",
    }
    provider.content_transform = wrappers[wrapping]
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, mode)
    diagnostics = caught.value.diagnostics
    assert diagnostics["api_mode"] == mode and diagnostics["model"] == settings.model
    assert diagnostics["response_format"] == "text" and diagnostics["http_status"] == 200
    assert diagnostics["completion_status"] in ("stop", "completed")
    assert diagnostics["text_output_present"] is True
    assert diagnostics["json_failure_type"] in ("invalid_json", "invalid_json_fence")
    assert sentinel not in str(caught.value) and settings.api_key not in str(caught.value)
    assert len(provider.requests) == 1
    assert not (project / "triage_predictions.json").exists()
    assert read_jsonl(project / "llm_calls.jsonl")[0]["status"] == "failed"


@pytest.mark.parametrize("mode", MODES)
def test_empty_output_diagnostics(project, provider, settings, mode):
    provider.raw = " \n\t "
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, mode)
    assert caught.value.failure_type == "empty_output"
    assert caught.value.diagnostics["text_output_present"] is False
    assert caught.value.diagnostics["http_status"] == 200
    assert len(provider.requests) == 1


@pytest.mark.parametrize("mode", MODES)
def test_refusal_is_rejected_without_echoing_body(project, provider, settings, mode):
    provider.refusal = "PRIVATE_REFUSAL_TEXT"
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, mode)
    assert caught.value.failure_type == "refusal"
    assert "PRIVATE_REFUSAL_TEXT" not in str(caught.value)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "queued", "in_progress", None])
def test_responses_must_be_completed(project, provider, settings, status):
    provider.response_status = status
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, "responses")
    assert caught.value.failure_type == "incomplete_response"
    assert caught.value.diagnostics["completion_status"] == (status or "missing")
    assert caught.value.diagnostics["text_output_present"] is True
    assert len(provider.requests) == 1


def test_responses_incomplete_message_blocks_success(project, provider, settings):
    def transform(response):
        response["output"][0]["status"] = "incomplete"
        return response
    provider.response_transform = transform
    with pytest.raises(LLMError, match="did not complete"):
        run_api(project, settings, "responses")
    assert len(provider.requests) == 1


def test_responses_incomplete_details_blocks_success(project, provider, settings):
    provider.response_transform = lambda response: {**response, "incomplete_details": {"reason": "max_output_tokens"}}
    with pytest.raises(LLMError):
        run_api(project, settings, "responses")


def test_responses_extracts_only_final_text_blocks(project, provider, settings):
    def transform(response):
        final = response["output"][0]
        final["phase"] = "final_answer"
        text = final["content"][0]["text"]
        midpoint = len(text) // 2
        final["content"] = [
            {"type": "output_text", "text": text[:midpoint], "annotations": []},
            {"type": "output_text", "text": text[midpoint:], "annotations": []},
        ]
        response["output"] = [
            {"type": "reasoning", "id": "test-reasoning", "summary": [{"type": "summary_text", "text": "PRIVATE_REASONING"}]},
            {"type": "message", "id": "test-commentary", "status": "completed", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "PRIVATE_COMMENTARY", "annotations": []}]},
            final,
        ]
        return response
    provider.response_transform = transform
    run_api(project, settings, "responses")
    assert len(provider.requests) == 1
    validate_artifacts(project)


def test_responses_commentary_alone_is_not_final_output(project, provider, settings):
    def transform(response):
        response["output"][0]["phase"] = "commentary"
        return response
    provider.response_transform = transform
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, "responses")
    assert caught.value.failure_type == "empty_output"


def test_responses_flat_output_text_is_supported(project, provider, settings):
    def transform(response):
        response["output_text"] = response["output"][0]["content"][0]["text"]
        response["output"] = []
        return response
    provider.response_transform = transform
    run_api(project, settings, "responses")
    validate_artifacts(project)


def test_responses_complete_review_with_overrides(project, provider, settings):
    tickets = load_json(project / "tickets.json")
    config = load_json(project / "triage_config.json")
    lines = iter([f"{tickets[0]['ticket_id']},{config['allowed_categories'][1]},{config['allowed_priorities'][0]}", ""])
    run_api(project, settings, "responses", input_fn=lambda: next(lines))
    queue = load_json(project / "final_queue.json")
    assert queue[0]["was_overridden"] is True
    assert queue[0]["final_route_to"] == config["routing_rules"][config["allowed_categories"][1]]
    assert len(provider.requests) == 1
    validate_artifacts(project)


@pytest.mark.parametrize("mode", MODES)
def test_http_error_diagnostics_do_not_leak_provider_body(project, provider, settings, mode):
    provider.status = 400
    with pytest.raises(LLMError) as caught:
        run_api(project, settings, mode)
    assert caught.value.diagnostics["http_status"] == 400
    assert caught.value.diagnostics["completion_status"] == "not_received"
    assert caught.value.diagnostics["text_output_present"] is False
    assert "test-not-a-credential" not in str(caught.value)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("field,value", [("api_mode", "chat_completions"), ("prompt_parameter", "messages")])
def test_validator_rejects_mode_evidence_mismatch(project, provider, settings, field, value):
    run_api(project, settings, "responses")
    call = read_jsonl(project / "llm_calls.jsonl")[0]
    call[field] = value
    (project / "llm_calls.jsonl").write_text(json.dumps(call) + "\n")
    with pytest.raises(ValidationError):
        validate_artifacts(project)


def test_validator_checks_diagnostic_file_hash(project, provider, settings):
    run_api(project, settings, "responses")
    with (project / "llm_diagnostics.json").open("a") as handle:
        handle.write(" ")
    with pytest.raises(ValidationError, match="diagnostic"):
        validate_artifacts(project)


@pytest.mark.parametrize("mode", MODES)
def test_api_mode_environment_configuration(monkeypatch, mode):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-credential")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_MODE", mode)
    assert LLMSettings.from_environment().api_mode == mode


def test_invalid_api_mode_stops_before_request(project, provider, settings):
    workflow = Workflow()
    with pytest.raises(LLMError, match="API_MODE"):
        main.run_pipeline(project, settings=replace(settings, api_mode="invalid"), input_fn=lambda: "", workflow=workflow)
    assert workflow.state == WorkflowState.TICKETS_NORMALIZED
    assert provider.requests == []
    assert read_jsonl(project / "llm_calls.jsonl") == []
