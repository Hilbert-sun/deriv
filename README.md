# Replayable AI Support Triage Pipeline

This Python pipeline reads customer support tickets and triage configuration from
disk, validates both, and deterministically prepares tickets for future stages.
Task 1 is implemented. It requires no third-party packages, API keys, or external
services.

## Prerequisites and execution

Use Python 3.10 or newer. From this directory, run:

```sh
python main.py
```

If your system exposes Python as `python3`, use `python3 main.py`. You can also
run the script by its path from another directory: inputs and outputs are always
resolved relative to `main.py`, regardless of the shell's working directory.

The command prints the input ticket count, the output artifact path, and the
current state. Invalid inputs produce an actionable error on stderr and a nonzero
exit status. Input files are never modified.

## Files

| File | Purpose |
| --- | --- |
| `main.py` | Task 1 command line entrypoint |
| `io_utils.py` | UTF-8 JSON loading, validation, and writing |
| `normalizer.py` | `normalize_ticket` and `normalize_tickets` |
| `workflow.py` | Reusable workflow state machine |
| `tickets.json` | Runtime ticket input |
| `triage_config.json` | Runtime triage configuration |
| `normalized_tickets.json` | Generated Task 1 artifact in the project root |
| `.gitignore` | Excludes generated output, caches, and local environments |

No `requirements.txt` is needed because only the Python standard library is used.

## Input contract

`tickets.json` must contain an array of objects. Every object requires string
fields `ticket_id`, `subject`, `message`, `channel`, and `created_at`. Ticket IDs
must be nonempty and unique after normalization. An empty ticket array is valid.

`triage_config.json` must contain nonempty `allowed_categories` and
`allowed_priorities` arrays of unique, nonempty strings, a `reply_style` object
with a nonempty string `tone` and positive integer `max_words`, and a
`routing_rules` object mapping every allowed category to a nonempty destination
string. Configuration values are loaded from disk; application logic does not
hardcode categories, priorities, or destinations.

Extra input fields are tolerated. Replace the input files with your own data
without changing the code. Validation rejects the entire run on invalid input;
it never silently drops tickets. On a validation failure, any artifact from a
previous successful run remains unchanged and does not represent the failed run.

The supplied task did not include the referenced challenge sample JSON. The
initial input files therefore contain representative starter data, including
Unicode and mixed line endings, rather than the original challenge samples.

## Normalized artifact

Each input ticket produces exactly one record, in the same order, with exactly
these fields: `ticket_id`, `subject`, `message`, `channel`, `created_at`,
`text_for_model`, and `char_count`. Extra fields such as `customer_id` are excluded.

All five source string fields replace CRLF and CR line endings with LF and strip
leading/trailing whitespace. Internal whitespace and meaningful content are
preserved. The model text is exactly:

```text
Subject: {normalized_subject}
Message: {normalized_message}
```

`char_count` is Python's `len(text_for_model)`, not the UTF-8 byte count.
The artifact uses UTF-8, `ensure_ascii=False`, and two-space indentation.
Unchanged inputs produce identical JSON content on repeated runs.

## Workflow and scope

The reusable `Workflow` starts at `WorkflowState.INIT`. Its read-only `state`
property exposes the current state. Call `transition(WorkflowState.<name>)` to
advance exactly one step; repeats, reversals, skipped states, unknown states,
and transitions beyond the final state raise `WorkflowTransitionError`.

The complete state sequence is:

```text
INIT -> INPUTS_LOADED -> TICKETS_NORMALIZED -> TRIAGE_PREDICTED
     -> HUMAN_REVIEW_COMPLETE -> FINAL_QUEUE_GENERATED
     -> VALIDATION_COMPLETE -> RESULTS_FINALISED
```

Task 1 executes only `INIT -> INPUTS_LOADED -> TICKETS_NORMALIZED`, advancing
after input validation and then after successfully saving the normalized artifact.
Later stage implementations must use these guarded transitions.

LLM calls, triage predictions, human review, final queue generation, escalation
rules, and the automated test suite are intentionally deferred to Tasks 2–6.
No outputs for those stages are generated.
