# Replayable AI Support Triage Pipeline

Tasks 1–2 load disk-backed support tickets and configuration, normalize tickets
deterministically, and classify the entire batch with one real LLM request.
Python validates every prediction and assigns routing destinations from the
configuration. Human review and later stages are not implemented.

## Setup and execution

Use Python 3.10 or newer. From the project root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with your provider settings, then run:

```sh
python main.py
```

An actual working API credential or accessible OpenAI-compatible local model is
required for predictions. `.env.example` contains no working credential or model.
Existing environment variables take precedence over `.env`; only the `.env` next
to `main.py` is loaded. Do not commit credentials. `.env`, its variants (except
`.env.example`), private-key files, virtual environments, and generated artifacts
are ignored by Git.

Inputs and outputs are resolved relative to `main.py`, regardless of the shell's
working directory. Use `python3` if your system has no `python` command.

## LLM settings

| Variable | Meaning |
| --- | --- |
| `OPENAI_API_KEY` | Required provider credential; local servers still need an explicit placeholder accepted by that server |
| `OPENAI_MODEL` | Required model ID supported by your endpoint; no model is hardcoded |
| `OPENAI_BASE_URL` | Optional HTTP(S) API base URL; blank uses the official SDK default |
| `OPENAI_RESPONSE_FORMAT` | `json_schema` (default), `json_object`, or `text` |
| `OPENAI_TEMPERATURE_ZERO` | `false` by default; set `true` only when the selected model supports `temperature=0` |
| `OPENAI_TIMEOUT_SECONDS` | Finite positive SDK timeout in seconds; default `120` |

Choose the response format **before running**, according to provider/model
capabilities. `json_schema` requests strict structured output with allowed labels
derived from the configuration. `json_object` requests JSON mode. `text` omits
`response_format` for compatible servers without structured-output support; the
prompt still demands strict JSON and Python validates it. The pipeline never
probes capabilities, lists models, retries with a different format, or sends a
repair request. An incompatible setting fails the batch. Temperature is omitted
unless explicitly enabled; do not enable it for an unsupported model.

The integration uses the official OpenAI Python SDK's Chat Completions endpoint
for OpenAI-compatible providers. Automatic SDK retries are disabled with
`max_retries=0`. Every run that reaches prediction sends one completion request
containing **all** normalized tickets and the **full** configuration, including
extra fields. Even an empty batch is sent once and must receive an empty
predictions array. There are no chunked or per-ticket calls.

The prompt treats ticket text as untrusted data, restricts classification to
configured labels, asks for issue/business-impact urgency assessment, preserves
ticket IDs, and requires honest customer replies following the configured tone
and word limit. No category, priority, route, ticket ID, or prediction is
hardcoded in application logic. See the official [structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
and [Python SDK reference](https://developers.openai.com/api/reference/python).

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Tasks 1–2 command line orchestration |
| `io_utils.py` | UTF-8 JSON loading, input validation, atomic artifact writing |
| `normalizer.py` | Unchanged deterministic ticket normalization |
| `workflow.py` | Reusable sequential state machine and stage guard |
| `llm_client.py` | Environment settings, prompt, dynamic schema, one SDK request, JSON parsing |
| `triage.py` | `predict_tickets`, full-batch validation, deterministic routing |
| `tickets.json` | Runtime ticket input |
| `triage_config.json` | Runtime configuration |
| `.env.example` | Provider-setting template with no secrets |
| `requirements.txt` | Official OpenAI SDK and python-dotenv dependencies |
| `normalized_tickets.json` | Generated Task 1 artifact |
| `triage_predictions.json` | Generated, fully validated Task 2 artifact |

## Inputs and normalization

`tickets.json` contains an array of objects with string fields `ticket_id`,
`subject`, `message`, `channel`, and `created_at`. Ticket IDs must be nonempty and
unique after normalization. An empty array is valid. Extra fields are tolerated.

`triage_config.json` requires nonempty `allowed_categories` and
`allowed_priorities` arrays of unique, nonempty strings; `reply_style` with a
nonempty string `tone` and positive integer `max_words`; and `routing_rules`
mapping every allowed category to a nonempty destination string. Both input
files are read on every run and remain unchanged. Invalid input rejects the
whole run without silently dropping tickets.

The configuration follows the challenge screenshot. The current `tickets.json`
still contains the initial representative starter data. The screenshot clips
the messages for `T-1001`, `T-1002`, and `T-1004`; full source text is required to
replace it with the exact challenge sample. No additional fixtures are added.

Each normalized record has exactly `ticket_id`, `subject`, `message`, `channel`,
`created_at`, `text_for_model`, and `char_count`, in original ticket order.
The five source string fields replace CRLF/CR with LF and strip edge whitespace,
preserving internal content. Extra fields such as `customer_id` are excluded.

`text_for_model` is exactly `Subject: {subject}\nMessage: {message}`, using the
normalized values. `char_count` is Python's `len(text_for_model)`, not byte length.
Normalization never calls an LLM. Identical inputs regenerate identical
normalized JSON content; LLM predictions may vary across runs.

## Predictions and validation

The LLM returns an object containing a `predictions` array of five string fields
per ticket: `ticket_id`, `category`, `priority`, `reason`, `suggested_reply`.
Python checks the entire batch for missing, duplicate, or unexpected IDs,
allowed categories/priorities, field types, nonempty reasons/replies, and word
limits. JSON fences, malformed JSON, duplicate object keys, non-JSON numeric
constants, refusals, and interrupted completions fail clearly. There is no
automatic repair, invented classification, or fallback prediction.

Reply word count is **`len(suggested_reply.split())`**: each nonempty token
separated by Python-recognized Unicode whitespace counts as one word.
Punctuation does not split tokens; unspaced text counts as one token.

After all predictions pass, Python computes
`route_to = triage_config["routing_rules"][category]`, ignoring any model-supplied
route. The output is an array in original input order, with exactly six fields
per record: `ticket_id`, `category`, `priority`, `reason`, `suggested_reply`,
`route_to`. Both generated files use UTF-8, `ensure_ascii=False`, two-space
indentation, and atomic replacement. No partially validated batch is saved.

## Workflow and failures

The enforced sequence for the implemented pipeline is:

```text
INIT -> INPUTS_LOADED -> TICKETS_NORMALIZED -> TRIAGE_PREDICTED
```

The LLM request is guarded by `TICKETS_NORMALIZED`, reached only after writing
`normalized_tickets.json`. `TRIAGE_PREDICTED` is reached only after full response
validation and successfully saving `triage_predictions.json`. The reusable state
machine still declares the later states, but no later stage is executed.

The command prints loaded/normalized counts, the provider hostname and model,
prediction count, artifact paths, and the final state. Errors exit nonzero and
print the current state. Credentials and raw provider error bodies are not
printed. Missing credentials, API failures, or invalid predictions leave the
state at `TICKETS_NORMALIZED`; no fabricated prediction artifact is generated.
Earlier input/write failures leave the state at the last completed stage.

On a failed run, artifacts from an earlier successful run remain unchanged and
do not represent the failed run. A fresh checkout requires successful execution
to generate artifacts; no precomputed predictions are committed.

Task 2 implements no human review, review overrides, final queue, queue summary,
confidence scoring, escalation, batch recovery, validation command, test suite,
additional test fixtures, or frontend. These are deferred to Tasks 3–6. Automated
checks cannot guarantee semantic correctness or that the model obeys every
instruction; human review remains a later stage.
