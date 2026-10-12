# Replayable AI Support Triage Pipeline

A terminal-based Python pipeline for disk-backed support ticket normalization,
one-request LLM triage, human review, final agent queues, escalation, and
independent artifact validation. Code for Tasks 1–6 is present and tested with a
mocked provider. Live end-to-end acceptance is still incomplete; see the status
checkpoint below. No frontend or server is required.

## 项目进度检查点（2026-10-12）

**运行状态：已暂停。** 已检查本项目的流水线及测试进程，没有仍在运行的
进程。本次收尾只保存文档、提交及合并，不启动流水线、不新增 API 调用。
暂停是当前维护状态，不是新增的工作流枚举；最近真实运行仍停在
`TICKETS_NORMALIZED`。恢复运行应等待用户下一条明确指令。

代码交付到 `main`，采用 merge commit 合并 PR [#1](https://github.com/Hilbert-sun/deriv/pull/1)
和 [#2](https://github.com/Hilbert-sun/deriv/pull/2)，保留源分支及完整提交历史。
最新代码实现检查点为 `ceccdd9a9e640d976e0586aaae5df66600ec8783`；
本说明在该提交基础上补充。完整功能代码已保存，但不能据此宣称所有真实
验收条件都已完成。

### 已完成事项

- [x] **Task 1：磁盘输入与规范化。** 每次读取两个输入 JSON，验证必需字段、
  ID 唯一性、配置标签及路由；使用项目相对路径；确定性处理换行和边缘空白，
  保留 Unicode 和输入顺序，计算准确字符数并生成规范化文件。根目录输入及
  `normalizer.py` 未被后续任务覆盖。配置已按截图样例修正。
- [x] **Task 2：批量 LLM 集成代码。** 使用官方 SDK 和可配置的密钥、端点、
  模型；规范化保存后才发送整批工单和完整配置；禁用自动重试；支持预先选择
  结构化 JSON、JSON mode 或文本 JSON；校验结果并用 Python 计算路由。
  已真实发起一次请求，但尚未取得可用的 JSON 预测。
- [x] **Task 3：人工复核代码及模拟验证。** 展示全部分类/优先级，支持多次
  CSV 覆盖、错误后重试、重复修改、撤销及无修改；保留原始值和最后有效值，
  每张工单最多一个有效覆盖。EOF 不等于批准，复核完成并保存后才推进状态。
- [x] **Task 4：最终队列及统计代码。** 应用复核后的分类和优先级，重新计算
  配置路由，保留已校验回复及输入顺序；生成最终队列、稳定排序的 Markdown
  分类/优先级/路由统计和原值/新值覆盖清单，各组计数均与工单总数一致。
- [x] **Task 5：置信度与升级规则。** 在同一批请求中索取置信度，校验有限
  数值范围，拒绝布尔值；按复核后的允许类别 `other` 或置信度低于 `0.60`
  生成升级记录，不额外调用模型。
- [x] **Task 5：单条回退及调用日志。** 保留有效预测；在配置允许时，对缺失、
  重复或损坏预测使用动态 `other` 路由、配置优先级、置信度 `0.0` 和限长安全
  回复；无可用回退则明确失败。记录 UTC 调用、实际序列化提示 SHA-256、
  输入/输出引用、运行 ID、失败状态和恢复错误，不记录 API 密钥。
- [x] **Task 6：独立校验与隔离测试。** 实现 `validate.py`、测试夹具和完整
  模拟集成测试；检查当前输入、产物、覆盖、统计、升级、状态顺序和单次调用。
  使用阶段事件及文件哈希校验执行证据，不只依赖修改时间。
- [x] **可靠性及可复现性。** 原子写入；每次运行清除本次生成数据以防旧结果
  误用；保留按运行 ID 区分的调用/恢复历史；测试在临时目录执行，不覆盖根
  输入，也不产生付费 API 请求。确定性阶段可复现，真实模型文本不承诺一致。
- [x] **凭据及工作进度保存。** 源码、隔离测试和文档进入 Git；`.env`、
  `API.env`、虚拟环境及生成数据继续保留在本地并被 Git 忽略。提交前已检查
  实际 API 密钥不在待提交文件中。新机器需自行配置凭据并重新生成产物。

### 尚未完成事项与真实验收条件

- [ ] **补齐挑战原始工单样例。** 截图中的 `T-1001`、`T-1002`、`T-1004`
  消息右侧被截断，尚无法逐字还原完整 `tickets.json`。当前根输入仍为先前
  的三条 starter 工单，不应称作挑战原始四条工单；不能猜测或编造缺失文字。
- [ ] **解决供应商的 JSON 输出兼容问题。** 最近真实运行预先选择
  `json_object`，供应商返回了 completion，但正文在第 1 行第 1 列未通过
  JSON 解析。未发送重试或修复请求；目前没有成功解析的真实模型响应。
  恢复后需确认提供方、模型和响应格式的兼容设置，并在独立新运行中验证。
- [ ] **生成真实有效的预测及置信度。** 必须由实际运行输入及真实响应生成
  `triage_predictions.json`，满足完整覆盖、允许标签、回复限长、路由及置信度
  校验。当前根目录没有该文件，不能用测试预测或预计算结果替代。
- [ ] **完成真实人工复核。** 真实预测成功后，需由用户检查分类/优先级并
  输入有效覆盖，或明确以空行结束复核；目前尚未进入此步骤。
- [ ] **生成真实下游产物。** 复核后生成 `review_overrides.json`、
  `final_queue.json`、`queue_summary.md`、`escalations.json`，并验证覆盖、路由、
  统计及升级规则对本次输入全部生效。当前根目录这些文件均不存在。
- [ ] **完成真实端到端验收。** 在当前真实输入及真实 API 结果下，内部校验
  和独立 `python validate.py` 都需通过，最终实际状态达到
  `RESULTS_FINALISED`。该条件目前仅在模拟集成测试中成立，不代表真实运行成功。

### 最近已执行的验证结果

| 命令/检查 | 已观察到的结果 | 范围与限制 |
| --- | --- | --- |
| `.venv/bin/python -m pytest -q` | **60 passed，0 failed** | 官方 SDK + 内存 HTTP 模拟；成功完整执行达到 `RESULTS_FINALISED`，临时目录生成全部产物；无付费调用 |
| Python 编译检查、`git diff --check` | 通过 | 源码语法和差异格式检查 |
| `.venv/bin/python main.py` | 退出码 1；实际调用一次；停在 `TICKETS_NORMALIZED` | 规范化 3 张当前工单；真实返回正文不是合法 JSON；无重试、无伪造预测 |
| `.venv/bin/python validate.py` | 退出码 1，报告缺失预测及下游产物 | 对真实失败运行的正确拒绝，不是测试套件失败，也不能算真实验收通过 |
| 本次暂停检查 | 无本项目流水线/测试进程在运行 | 未重新运行测试或模型，保留上述真实运行证据 |

最近真实目录保留的产物：`normalized_tickets.json`、`llm_prompt.json`、
`llm_calls.jsonl`、`pipeline_events.jsonl`、`recovery_log.jsonl`（本次无逐条恢复，
该日志可为空）。没有 `llm_response.json`，因为正文未成功解析。模拟测试
生成的完整队列产物位于隔离临时目录，不能作为真实根目录产物提交。

### 保存、回滚与后续恢复

- 合并前主分支检查点：`9408839219657583b2f8c03382a9b6df52f829c5`，
  对应标签 `checkpoint-before-main-merge-2026-10-12`。
- Task 1 配置修正检查点：`5ec86145d24400990c7985fb35eb9aa70c4ca2cf`。
- Task 2 检查点：`e01f1bae863dda0d56dfd5307aad39bef8514a6a`。
- Tasks 3–6 实现检查点：`ceccdd9a9e640d976e0586aaae5df66600ec8783`。
- 暂停交付检查点：标签 `checkpoint-paused-2026-10-12`，指向本次合并后的
  主分支提交。使用 Git 历史或新分支检查旧版本，避免强制覆盖已共享历史。
- 用户明确恢复后，先补齐原始工单并处理供应商响应格式，再运行真实批次、
  完成人工复核，最后检查全部产物和独立校验结果；暂停期间不自动重启。

## Clean-checkout setup

Use Python 3.10 or newer:

```sh
git clone https://github.com/Hilbert-sun/deriv.git
cd deriv
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
