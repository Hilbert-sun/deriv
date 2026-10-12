# Replayable AI Support Triage Pipeline

A terminal-based Python pipeline for disk-backed support ticket normalization,
one-request LLM triage, human review, final agent queues, escalation, and
independent artifact validation. Tasks 1–6 are implemented; no frontend or server
is required.

## Clean-checkout setup

Use Python 3.10 or newer:

```sh
git clone https://github.com/Hilbert-sun/deriv.git
cd deriv
# Until the draft PR is merged, select the implementation branch:
git switch task2/llm-batch-triage
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`, then run:

```sh
python main.py
python validate.py
python -m pytest
```

Use `python3` if your system has no `python` alias. Paths are resolved relative to
`main.py` and `validate.py`, so execution does not depend on the shell's working
directory. The input JSON files are read from disk on every run and never
modified. Tests copy isolated fixtures into temporary directories and do not
change the project inputs or require paid API calls.

## API configuration

| Variable | Meaning |
| --- | --- |
| `OPENAI_API_KEY` | Required real provider credential, or an explicit placeholder accepted by a local server |
| `OPENAI_BASE_URL` | Optional HTTP(S) API base URL; blank uses the official OpenAI SDK default |
| `OPENAI_MODEL` | Required model ID supported by your provider; no model is hardcoded |
| `OPENAI_RESPONSE_FORMAT` | `json_schema` (default), `json_object`, or `text` |
| `OPENAI_TEMPERATURE_ZERO` | Default `false`; enable only when the selected model supports `temperature=0` |
| `OPENAI_TIMEOUT_SECONDS` | Finite positive SDK timeout; default `120` seconds |

Only the project-root `.env` is automatically loaded; existing environment
variables take precedence. Other credential files such as `API.env` are ignored
by Git and are not automatically sourced. Never commit credentials.

The official OpenAI Python SDK uses Chat Completions for compatibility with
third-party providers. Choose the response format before a run, according to the
provider's capabilities: strict dynamic schema, JSON mode, or prompt-only JSON.
`text` omits the `response_format` parameter but still requires strict JSON.
There is no automatic capability probing, endpoint switching, retry, or LLM
repair request. SDK retries are disabled with `max_retries=0`. Temperature is
omitted unless explicitly enabled. See the official [structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Python SDK reference](https://developers.openai.com/api/reference/python).

Every nonempty run sends all normalized tickets and the full configuration in
one request, including a confidence request in that same prompt. Empty batches
skip the API and generate empty queue artifacts. Ticket text is treated as
untrusted data. No LLM is used for normalization, review, routing, statistics,
escalation, recovery, or validation.

## Workflow and architecture

```text
INIT -> INPUTS_LOADED -> TICKETS_NORMALIZED -> TRIAGE_PREDICTED
     -> HUMAN_REVIEW_COMPLETE -> FINAL_QUEUE_GENERATED
     -> VALIDATION_COMPLETE -> RESULTS_FINALISED
```

`workflow.py` rejects skipped, repeated, reversed, and terminal-state
transitions. Stage guards and persisted events enforce the execution sequence:
normalization must be saved before the request; review must finish and be saved
before queue generation; validation must pass before finalisation.

| Module | Responsibility |
| --- | --- |
| `main.py` | Full workflow orchestration; importable `run_pipeline` supports isolated testing |
| `io_utils.py` | Disk input validation and atomic UTF-8 artifact writes |
| `normalizer.py` | Deterministic text normalization |
| `llm_client.py` | Settings, prompt, dynamic schema, one SDK request, strict JSON parsing |
| `triage.py` | Prediction validation, configured routing, individual-record recovery |
| `reviewer.py` | Interactive review and effective override validation |
| `queue_generator.py` | Post-review final queue and deterministic Markdown counts |
| `escalations.py` | Post-review category/confidence rules |
| `run_log.py` | Run IDs, ordered stage evidence, input/output hashes, request and recovery logs |
| `validate.py` | Importable validation and independent command line check, with no API calls |
| `tests/` | Mock-transport integration, failure, regeneration, and edge-case tests |

## Inputs and normalization

`tickets.json` must be an array of objects with string `ticket_id`, `subject`,
`message`, `channel`, and `created_at`. IDs must be nonempty and unique after
normalization. An empty array is valid. Extra input fields are tolerated.

`triage_config.json` requires nonempty `allowed_categories` and
`allowed_priorities` arrays of unique, nonempty strings; `reply_style` with
nonempty `tone` and positive integer `max_words`; and `routing_rules` mapping
every allowed category to a nonempty destination. Labels and routes come from
this configuration, not from sample-dependent application code.

The configuration matches the supplied challenge screenshot. The current root
`tickets.json` still contains the original starter data: the screenshot clips
three message strings, so their full source is still needed to reproduce the
exact challenge ticket sample. Task 6 fixtures are isolated in `tests/fixtures/`.

Normalization replaces CRLF/CR with LF and strips edge whitespace from the five
source string fields, preserving internal content and original ticket order.
The exact normalized schema is `ticket_id`, `subject`, `message`, `channel`,
`created_at`, `text_for_model`, `char_count`. Extra fields such as `customer_id`
are excluded. Model text is `Subject: {subject}\nMessage: {message}` and the
character count is Python's `len(text_for_model)`.

## Prediction validation and recovery

Each prediction has exactly `ticket_id`, `category`, `priority`, `reason`,
`suggested_reply`, `confidence`, and `route_to`. Confidence must be a finite
number between 0 and 1; booleans are rejected. Python checks required types,
nonempty reasons/replies, allowed labels, exact ticket coverage, and word limits,
then assigns routes from `routing_rules`. Any model-supplied route is ignored.

Reply word count is **`len(suggested_reply.split())`**, using Python Unicode
whitespace tokenization. Punctuation does not split tokens; unspaced text counts
as one token. Output records preserve original input order.

If an individual prediction is missing, duplicated, malformed, or invalid,
valid predictions for other tickets are retained. Recovery requires the allowed
category `other`; its route is looked up dynamically. Fallback priority is the
first configured priority, confidence is `0.0`, and a safe manual-review reply
is truncated to the configured word limit. The error is recorded locally and
the fallback ticket is included in human review. Unknown/unidentifiable model
rows are logged; every actual input ticket is still accounted for. Without a
configured fallback category, the run fails clearly.

Total API failure, invalid top-level response shape, malformed JSON, Markdown
fences, refusals, and interrupted responses stop the run without fabricating a
successful prediction batch. A provider that ignores its requested JSON mode
may therefore require a configuration/model correction before a new run.

## Human review

Every ticket's ID, predicted category, and priority is displayed, followed by:

```text
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

Enter one correction per line. CSV quoting supports labels containing commas.
Invalid IDs, labels, priorities, or malformed lines produce an error and permit
another attempt. An empty line finishes review; EOF or interruption does not
silently approve it. Review adds no API calls.

Repeated edits preserve the original predicted values and the last accepted
correction. No-ops and edits restored to the original values are omitted. The
override artifact contains at most one effective correction per ticket, in
input order. Category and priority corrections affect final data; routes are
always recomputed from configuration. Suggested replies are preserved from the
validated predictions; changing a category does not regenerate a reply.

## Generated artifacts

| Artifact | Meaning |
| --- | --- |
| `normalized_tickets.json` | Deterministic normalized inputs |
| `triage_predictions.json` | Validated/recovered predictions with confidence and configured routes |
| `review_overrides.json` | Original/final category and priority for effective overrides; `[]` if none |
| `final_queue.json` | Post-review category, priority, route, suggested reply, and boolean override flag |
| `queue_summary.md` | Total, category/priority/destination counts, and old/new override values |
| `escalations.json` | Post-review tickets requiring manual attention, confidence, and reason codes |
| `llm_calls.jsonl` | One record per attempted SDK request, including UTC time, prompt SHA-256, model/provider, hashes, outcome, and HTTP status when available |
| `recovery_log.jsonl` | Per-ticket recovery errors associated with a run ID |
| `pipeline_events.jsonl` | Current run's ordered stage/request events and input/output SHA-256 hashes |
| `llm_prompt.json` | Actual canonical serialized messages supplied to the SDK, without credentials |
| `llm_response.json` | Parsed runtime model response, used for offline validation/replay |

JSON artifacts use UTF-8, `ensure_ascii=False`, readable indentation, and atomic
replacement. Queue summaries use configured label order and sorted destinations;
all three sets of counts sum to the ticket total. Escalation applies when the
post-review category is `other` (only if allowed), or confidence is below `0.60`.
Human corrections do not artificially raise the model's confidence.

## Validation, failure handling, and reproducibility

`python validate.py` checks disk inputs, exact normalization/schema/ordering,
prediction coverage, routes, confidence, reply limits, effective overrides,
final queue, summary counts, escalations, and all required workflow transitions.
It also verifies the serialized full-batch prompt, recorded response and recovery,
current-file hashes, and exactly one request in the current nonempty run. It can
run independently and never contacts the model. Failures exit nonzero with a
readable message. The same validator runs inside `main.py` before finalisation,
without circular imports or relying on modification timestamps.

Each run clears its generated data and prompt/response artifacts before loading
inputs, so a failed rerun cannot pass validation using a stale final queue.
Request and recovery histories are append-only and filtered by run ID; the
ordered stage log is replaced for the current run. Do not run two pipelines
concurrently in the same output directory. Hash-based evidence detects stale or
modified artifacts; it is a local consistency check, not a remote attestation.

Errors stop at the last completed state. Provider error bodies and API keys are
not logged. The program never reaches `RESULTS_FINALISED` after failed review,
artifact writes, or validation. Failed-run partial outputs cannot pass the
standalone validator. Delete generated files and rerun to regenerate them.

`python -m pytest` exercises actual parsing, SDK serialization, review, routing,
artifact generation, and validation using an in-memory HTTP transport and
injected reviewer input. It covers the required standard/changed inputs and
configuration, multiple/no/invalid overrides, malformed/missing predictions,
word limits, clean regeneration, and additional failure cases. Live credentials
are never needed by tests.

Deterministic stages reproduce identical content for identical inputs, recorded
responses, and effective corrections. Live LLM replies and confidence are not
promised to be identical between separate requests. Human review remains
necessary to assess semantic correctness and approve the final classifications.
