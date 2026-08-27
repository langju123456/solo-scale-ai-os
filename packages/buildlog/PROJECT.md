# BuildLog — Project Specification

> This file is the primary product and engineering context for humans and coding agents working on BuildLog.

---

## 1. Project identity

**Name:** BuildLog

**Current version:** v0.2

**Project type:** Evidence-aware engineering publishing workflow

**Current outputs:** A reviewed LinkedIn-oriented final draft, an optional
local LinkedIn-targeted Publishing Package, and optional explicitly approved
text delivery through validated LinkedIn and X adapters

**Near-term product direction:** Channel-specific engineering content
transformation

**Long-term product category:** Personal work intelligence and content
production system

**Primary user:** A developer who wants to convert real development work into accurate, reusable technical content

---

## 2. Vision

BuildLog is an AI-assisted workspace for preserving and reusing the value created during software development.

Software development produces more than code. It produces:

- problem understanding
- debugging knowledge
- architecture decisions
- trade-off analysis
- failed attempts
- implementation lessons
- reusable technical insight

Most of this disappears after the code works.

Git records what changed, but it rarely preserves why the change mattered, what alternatives were considered, what failed, or what the developer learned.

BuildLog accepts one real development iteration and transforms it into
structured, evidence-grounded technical communication.

BuildLog is not primarily a LinkedIn generator or a multi-platform posting
tool. LinkedIn is the first generation format and the first visual Publishing
Package target. LinkedIn and X delivery adapters are optional last-mile
transport. The broader product owns the evidence-aware workflow that turns
engineering work into reviewable, traceable, reusable, and eventually
channel-specific artifacts.

The long-term goal is not content generation for its own sake.

The long-term goal is to preserve, organize, evaluate, and reuse the value created through real engineering work.

---

## 3. Problem statement

Developers regularly solve meaningful problems but struggle to communicate that work.

Common outcomes include:

- GitHub repositories with little explanation
- generic LinkedIn posts
- outdated portfolios
- weak resume bullets
- forgotten technical decisions
- repeated debugging work
- undocumented lessons
- content that exaggerates what was actually built

Existing content-generation tools often begin with a writing request and ask the model to create a compelling story.

This creates several risks:

- hallucinated results
- exaggerated business impact
- unsupported technical claims
- generic writing
- loss of the developer's real reasoning
- unstable output quality

BuildLog begins with structured development evidence rather than a blank prompt.

---

## 4. Product goal

### v0.1 goal

BuildLog v0.1 must:

> Transform one real software-development iteration into one technically accurate, specific, readable, and useful LinkedIn post draft.

### Input

One structured development iteration.

### Output

One Markdown file containing a LinkedIn post draft.

### Success condition

A user can provide evidence from a real development iteration, run the pipeline, inspect every major intermediate artifact, and receive a final draft that requires only human review or light editing.

---

### v0.2 product goal

BuildLog v0.2 validates three connected but separate capabilities:

1. Generate and review an evidence-grounded engineering story.
2. Build one local LinkedIn-targeted visual Publishing Package from a reviewed
   run.
3. Optionally deliver an existing reviewed text artifact through a
   human-controlled LinkedIn or X adapter.

Publishing is not required to receive product value. Manual copy or upload
remains a valid endpoint. Delivery does not change generation, evaluation,
revision, prompts, the final artifact contract, or Publishing Package
construction.

The current story generation and Publishing Package are LinkedIn-oriented. X
delivery is technically validated, but distinct X content generation is not
yet implemented. The next product validation is whether the same reviewed
engineering evidence can produce genuinely different LinkedIn and X artifacts
that require little human editing.

### Current product contract

```text
User-supplied engineering evidence
        ↓
Reviewed engineering story
        ↓
Target-aware publishing artifact or package
        ↓
Human review
        ↓
Manual use or optional approved delivery
```

BuildLog owns evidence, knowledge transformation, channel-specific artifacts,
and human review. Delivery adapters own only final transport.

---

## 5. Historical non-goals for v0.1

The following were explicitly outside the v0.1 generation baseline:

- automatic LinkedIn authentication
- automatic LinkedIn publishing
- scraping LinkedIn
- GitHub API integration
- automatic Git-diff collection
- screenshot understanding
- web browsing
- vector databases
- RAG
- persistent user memory
- external database servers
- database-backed authentication
- database migrations beyond table creation on startup
- multi-user accounts
- web application
- mobile application
- analytics dashboard
- autonomous multi-agent organization
- unbounded self-revision
- resume generation
- portfolio generation
- article generation
- content scheduling

v0.2 intentionally implements narrowly scoped LinkedIn and X authentication
and human-controlled text publishing. The remaining items are still out of
scope. There is no automatic, scheduled, background, organization-page,
thread, or media publishing.

---

## 6. Product philosophy

### 6.1 Start from a real need

The project must begin with real user work, not with a technology looking for a use case.

The sequence is:

```text
Real need
    ↓
Observed friction
    ↓
Defined problem
    ↓
Evidence
    ↓
Decision
    ↓
Implementation
    ↓
Validation
    ↓
Iteration
```

Technology is a mechanism, not the objective.

### 6.2 Iteration is the unit of work

BuildLog is organized around an `Iteration`.

An iteration can represent:

- a debugging session
- a feature implementation
- an architecture decision
- a failed experiment
- a compatibility fix
- a deployment improvement
- a workflow redesign
- a product experiment
- a lesson from actual use

An iteration does not need to be a major milestone.

It only needs to contain meaningful evidence of problem-solving.

### 6.3 Deterministic work belongs in code

Never use an LLM for operations that can be implemented reliably with normal code.

Examples:

- loading files
- checking file existence
- validating fields
- parsing JSON
- sorting timestamps
- creating directories
- generating run IDs
- enforcing revision limits
- applying numeric thresholds
- writing Markdown files

### 6.4 LLMs are used for judgment

Use an LLM only when the task requires interpretation, selection, synthesis, or language generation.

Examples:

- selecting the strongest story
- identifying the most useful technical insight
- explaining why a decision mattered
- describing trade-offs
- adapting detail for the target audience
- evaluating clarity and specificity
- revising weak writing

### 6.5 High-agentic steps require evaluation

The more freedom a model has, the more inspection and evaluation the system must provide.

Every major LLM output must be:

- stored
- inspectable
- associated with a prompt version
- evaluated where appropriate
- reproducible as far as practical

### 6.6 Human approval is mandatory

The system generates a draft.

It does not decide whether the post is true, safe to publish, confidential, or representative of the user.

The human remains responsible for final approval.

### 6.7 Evolution principle

Every BuildLog baseline should create value in three dimensions:

1. Product capability: BuildLog becomes more useful for real engineering
   communication.
2. Engineering capability: the implementation proves a concrete AI Engineer
   skill.
3. Engineering knowledge: the iteration preserves reusable decisions,
   trade-offs, failures, and lessons.

A future baseline should not exist only because a technology is popular. It
must connect a real product improvement to a demonstrable engineering
capability and a reusable knowledge artifact.

### 6.8 Capability baselines

BuildLog evolves through capability baselines rather than disconnected feature
additions. Version numbers describe product releases. Baseline names describe
the capability being established.

Completed or current baselines:

- Architecture Baseline
- Output Quality Baseline
- Generalization Baseline
- Example Showcase
- Agent Observability Baseline
- LinkedIn Publishing Baseline (implemented early in v0.2)
- Publishing Package Baseline
- X Publisher Implementation Baseline
- X Publisher Validation Baseline

Planned capability baselines:

- Portfolio Baseline
- Tool Calling Baseline
- Evidence Collection Baseline
- Embedding Baseline
- Retrieval Baseline
- Engineering Memory Baseline
- Multimodal Communication Baseline
- Workflow Automation Baseline
- Channel-Specific Content Validation

Each planned baseline is out of scope unless explicitly moved into
the current task file.

### 6.9 Product, portfolio, and learning dimensions

BuildLog has three strategic dimensions:

- Product: solve the real problem of turning engineering work into high-quality
  communication.
- Portfolio: demonstrate AI Engineer capabilities through working software,
  tests, documentation, evaluation, and observability.
- Learning: preserve engineering decisions and failure modes as reusable
  knowledge for future coaching, review, and skill development.

When choosing the next iteration, prefer work that strengthens all three
dimensions without expanding the current version's frozen scope.

---

## 7. Domain model

The primary domain object is `Iteration`.

### 7.1 Iteration

```text
Iteration
├── id
├── title
├── goal
├── context
├── problem
├── actions
├── decisions
├── trade_offs
├── result
├── lessons
├── evidence
├── audience
├── created_at
└── metadata
```

### 7.2 Field definitions

#### `id`

Unique identifier for the iteration.

#### `title`

Short human-readable name.

#### `goal`

What the developer was trying to achieve.

#### `context`

Relevant background needed to understand the work.

#### `problem`

The concrete friction, failure, limitation, or uncertainty addressed.

#### `actions`

Steps actually taken.

#### `decisions`

Important choices made during the iteration.

Each decision should contain:

```text
decision
reason
alternatives_considered
```

#### `trade_offs`

Costs, limitations, or compromises associated with a decision.

#### `result`

What happened after the implementation.

The result must not contain unsupported metrics.

#### `lessons`

Reusable knowledge derived from the work.

#### `evidence`

Facts supporting the final narrative.

Examples:

- terminal output
- test result
- observed behavior
- code change
- error message
- working pipeline
- benchmark result
- screenshot reference
- commit reference

#### `audience`

The intended readers.

Example:

```text
AI engineers, software engineers, and technical recruiters
```

#### `metadata`

Optional structured context such as:

- project name
- repository
- branch
- tools used
- model used
- operating system
- tags

---

## 8. Example input schema

```json
{
  "id": "local-agent-001",
  "title": "Running my first local AI agent",
  "goal": "Run the Hugging Face Agents Course example locally with Ollama and Qwen3.",
  "context": "The original example used a hosted model, while the goal was to understand and run the full pipeline locally.",
  "problem": "The model backend and installed Gradio version were incompatible with the original tutorial defaults.",
  "actions": [
    "Replaced the hosted model wrapper with LiteLLMModel.",
    "Connected LiteLLMModel to the local Ollama endpoint.",
    "Used Qwen3 as the local model.",
    "Changed only the incompatible Gradio arguments."
  ],
  "decisions": [
    {
      "decision": "Preserve the original tutorial structure.",
      "reason": "A large rewrite would hide the pipeline being studied.",
      "alternatives_considered": [
        "Rewrite the application around a different framework."
      ]
    },
    {
      "decision": "Use LiteLLM as the model adapter.",
      "reason": "The application could change model backends without changing the rest of the agent workflow.",
      "alternatives_considered": [
        "Call Ollama directly from every model-dependent component."
      ]
    }
  ],
  "trade_offs": [
    "The local model may be slower than a hosted endpoint.",
    "Preserving the tutorial structure limits architectural cleanup in this iteration."
  ],
  "result": "The Gradio interface successfully completed the local agent pipeline through smolagents, LiteLLM, Ollama, and Qwen3.",
  "lessons": [
    "Adapter layers reduce coupling between applications and model providers.",
    "Minimal compatibility changes make debugging easier.",
    "A working agent pipeline depends on clear boundaries between UI, framework, model adapter, and model runtime."
  ],
  "evidence": [
    "Ollama served the selected Qwen3 model locally.",
    "The Gradio interface returned a valid response.",
    "The final execution path was Gradio -> smolagents -> LiteLLM -> Ollama -> Qwen3."
  ],
  "audience": "AI engineers, software engineers, and technical recruiters",
  "metadata": {
    "project": "Local AI Agent",
    "language": "Python",
    "tools": [
      "smolagents",
      "LiteLLM",
      "Ollama",
      "Qwen3",
      "Gradio"
    ]
  }
}
```

---

## 9. Business logic

### 9.1 Main workflow

```text
1. User supplies one iteration JSON file.
2. The system loads the file.
3. Pydantic validates the schema.
4. Deterministic preprocessing normalizes the input.
5. The planner selects the central story.
6. The writer creates the first LinkedIn-oriented draft.
7. The evaluator scores the draft.
8. Code compares scores with fixed thresholds.
9. If required, the reviser performs one revision.
10. The system stores all artifacts.
11. The final Markdown draft is returned for human review.
12. In v0.2, the user may build a local LinkedIn-targeted Publishing Package.
13. Separately, the user may resolve the existing final text artifact, preview
    it, require exact human approval, and deliver it through LinkedIn or X.
14. Manual copy or upload remains a valid endpoint; delivery is optional.
```

### 9.2 Revision rule

Only one automatic revision is allowed in v0.1.

```text
Draft
  ↓
Evaluation
  ↓
Pass ───────────────→ Final
  ↓
Fail
  ↓
One revision
  ↓
Final
```

No autonomous infinite loop is permitted.

### 9.3 Evidence rule

Every factual claim in the generated post must be supported by the input.

The system must not invent:

- performance numbers
- user counts
- revenue
- deployment scale
- production usage
- hiring outcomes
- business impact
- development duration
- technologies not listed in the input

### 9.4 Privacy rule

The user is responsible for removing confidential information before input.

The system should include a final human-review warning reminding the user to check:

- secrets
- API keys
- employer-confidential information
- customer data
- private repository details
- unpublished business information

The warning remains in `06_final.md` for inspection. The downstream publishing
resolver removes only this exact fixed warning from the network post body.

---

## 10. System architecture

```text
┌─────────────────────────────┐
│       Iteration JSON        │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Input Loader + Validation   │  Deterministic
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Normalization               │  Deterministic
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Story Planner               │  LLM
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ LinkedIn Writer             │  LLM
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Draft Evaluator             │  LLM + fixed rubric
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Threshold Decision          │  Deterministic
└───────┬─────────────────────┘
        │
        ├── pass ────────────────┐
        │                        │
        ▼                        │
┌─────────────────────────────┐  │
│ Constrained Reviser         │  │ LLM
└──────────────┬──────────────┘  │
               │                 │
               └─────────────────┘
                         │
                         ▼
┌─────────────────────────────┐
│ Filesystem + SQLite Output  │  Deterministic
└─────────────────────────────┘
               │
               ▼
┌─────────────────────────────┐
│ Human Review                │  Product authority
└──────────────┬──────────────┘
               │
        ┌──────┴────────┐
        │               │
        ▼               ▼
┌─────────────────┐  ┌─────────────────────┐
│ Package Builder │  │ PublishingService   │  Optional delivery
└────────┬────────┘  └──────────┬──────────┘
         │                       │
         ▼                 ┌─────┴─────┐
┌─────────────────┐        ▼           ▼
│ LinkedIn-ready  │   ┌──────────┐ ┌──────────┐
│ local package   │   │ LinkedIn │ │ X text   │
└─────────────────┘   │ adapter  │ │ adapter  │
                      └──────────┘ └──────────┘
```

---

## 11. Agentic boundaries

### 11.1 Low-agentic components

These must be implemented as ordinary Python modules:

- configuration loading
- path handling
- JSON loading
- schema validation
- whitespace normalization
- run ID generation
- trace-directory creation
- output persistence
- content hashing
- SQLite metadata persistence
- score threshold comparison
- revision-count enforcement
- error handling
- logging

### 11.2 High-agentic components

These may use an LLM:

#### Planner

Purpose:

- identify the strongest evidence-grounded narrative
- determine the central engineering lesson
- select useful technical details
- avoid generic storytelling

Structured output:

```text
central_idea
hook
technical_points
decision_story
reader_value
ending
```

#### Writer

Purpose:

- convert the iteration and plan into a LinkedIn draft
- preserve technical accuracy
- communicate a useful engineering lesson
- avoid exaggerated language

#### Evaluator

Purpose:

- score the draft
- identify unsupported claims
- identify vague language
- provide actionable revision instructions

#### Reviser

Purpose:

- revise the draft based on evaluator feedback
- remove unsupported claims
- increase specificity using existing evidence
- preserve the original meaning

---

## 12. Evaluation strategy

### 12.1 Evaluation dimensions

Each draft receives a score from 1 to 10 for:

#### Technical accuracy

Are all technical claims supported by the input?

#### Specificity

Does the post contain concrete problems, decisions, tools, and results?

#### Readability

Is the post clear, concise, and easy to follow?

#### Reader value

Does the reader receive a transferable lesson or useful insight?

#### Evidence coverage

Does the draft use the most relevant supplied evidence without inventing new facts?

### 12.2 Hard-failure conditions

A draft must be revised if it contains:

- unsupported metrics
- invented business impact
- technologies absent from the evidence
- false production claims
- confidential-looking values
- contradictions with the iteration input

### 12.3 Suggested thresholds

```text
technical_accuracy >= 8
specificity >= 7
readability >= 7
reader_value >= 7
evidence_coverage >= 7
```

Technical accuracy is the highest-priority dimension.

### 12.4 Future evaluation improvements

Not required for v0.1:

- claim extraction
- claim-to-evidence mapping
- deterministic word-count checks
- cliché detection
- repeated-phrase detection
- human rating storage
- prompt-version comparison
- model comparison
- regression test dataset

---

## 13. Agent observability

### 13.1 Objective and invariant

The v0.1 observability objective is:

> Make every BuildLog run explainable and reproducible without changing any
> pipeline behavior.

Observability is a cross-cutting record of existing behavior. It must not:

- change prompt content
- change model settings
- add or reorder LLM calls
- add retries
- change threshold decisions
- add post-revision evaluation
- move or rename existing content artifacts
- turn a successful generation into a failure

An observability failure may mark the observation as partial, but it must not
remove a final draft, trigger another LLM call, or alter the revision decision.
Business exceptions still propagate according to the original pipeline
behavior; observability records them but does not swallow them.

### 13.2 Replay definition

Reproducible means BuildLog retains enough evidence to replay the same input,
code, prompt files, model, and generation configuration.

It does not mean a non-deterministic model must produce byte-identical text.
At `temperature=0.4`, identical replay conditions may still produce different
tokens.

The replay checklist contains:

- input artifact hash
- normalized input hash
- Git commit
- Git branch and working-tree state
- prompt file hashes
- rendered prompt hashes
- provider
- model
- model digest
- temperature
- maximum output tokens
- canonical configuration fingerprint

If any required value is unavailable, `reproducibility_status` is `partial`.
BuildLog records the missing fields and never fabricates them. A dirty working
tree is partial unless its exact code state is otherwise preserved.

The configuration fingerprint is a SHA-256 hash of canonical JSON containing
only generation-affecting configuration: provider, model, model digest,
temperature, maximum output tokens, prompt file hashes, output type, and the
active revision thresholds. It excludes timestamps, durations, and generated
content.

### 13.3 Run artifact contract

Every run creates a unique directory while preserving the established artifact
filenames:

```text
runs/
└── 2026-07-27T19-30-12_local-agent-001/
    ├── 00_input.json
    ├── 01_normalized_input.json
    ├── 02_plan.json
    ├── 03_draft.md
    ├── 04_evaluation.json
    ├── 05_revised_draft.md
    ├── 06_final.md
    ├── run_metadata.json
    ├── timeline.json
    └── events.jsonl
```

`05_revised_draft.md` exists only when revision runs, matching the previous
artifact contract.

The three observation files serve different readers:

- `run_metadata.json` is the run summary, configuration manifest,
  reproducibility checklist, token summary, and revision evidence.
- `timeline.json` is the human-readable fixed-step status and timing view.
- `events.jsonl` is the ordered detailed audit stream for run, step, LLM-call,
  artifact, revision, and error events.

JSONL is the detailed event record. SQLite is the query projection. JSON and
Markdown artifact files remain the source of truth for payload content.

### 13.4 Independent statuses

One status cannot express all three concerns. Every run records:

```text
pipeline_status: completed | failed
observability_status: complete | partial | failed
reproducibility_status: complete | partial
```

Examples:

- completed pipeline + partial observability: the draft exists, but some
  telemetry could not be saved
- failed pipeline + complete observability: the business run failed and the
  failure path was fully captured
- complete observability + partial reproducibility: execution is fully
  explained, but the model digest or clean code state is missing

### 13.5 Fixed steps

Every run contains each of these steps exactly once and in this order:

1. `validation`
2. `preprocessing`
3. `prompt_loading`
4. `planner`
5. `writer`
6. `evaluator`
7. `revision_decision`
8. `reviser`
9. `finalization`
10. `persistence`

Each step records status, start, end, duration, attempt count, and skip reason.
An unexecuted conditional step is `skipped`; a step not reached after a failure
is `skipped` with `skip_reason=upstream_failure`.

Attempt counts have one meaning:

- not executed: `0`
- executed once: `1`
- executed twice: `2`

This baseline adds no retry behavior. Persistence timing aggregates the
existing persistence operations and identifies its timing mode explicitly.

### 13.6 LLM-call observations

Each LLM call belongs to one fixed step and records:

- provider, model, and optional immutable model digest
- temperature and maximum output tokens
- prompt file hash and rendered prompt hash
- start, end, and duration
- attempt number
- provider finish reason when available
- provider prompt, completion, and total token counts when available
- status and structured error reference

Full prompt text and full model responses are not stored in SQLite telemetry.

When token usage is unavailable, all missing counts remain `null` and the call
records:

```text
token_usage_status: unavailable
token_usage_source: provider_not_returned
```

Token counts must never be estimated from character or word counts.

### 13.7 Revision evidence

The existing deterministic revision decision is represented as structured
evidence:

```json
{
  "revision_required": true,
  "decision_rule_version": "v1",
  "triggered_by": [
    {
      "type": "score_threshold",
      "metric": "specificity",
      "actual": 6,
      "operator": "<",
      "threshold": 7
    }
  ]
}
```

Hard-failure codes are recorded only when the existing evaluator provides
them. The observability layer does not invent unavailable reasons.

BuildLog records whether revision executed and whether the revised text hash
differs from the draft hash. Since v0.1 does not evaluate the revised draft
again, an executed revision records:

```text
revision_improvement_status: not_measured
```

Changed does not mean improved.

### 13.8 Artifact lineage

Each artifact records its producing step and direct source artifacts.
`source_artifact_ids` means direct dependencies, not every transitive ancestor.

The final artifact has one direct source:

```text
no revision: 03_draft.md -> 06_final.md
revision:    05_revised_draft.md -> 06_final.md
```

The complete upstream chain is recoverable by recursively following direct
dependencies.

### 13.9 Error taxonomy

Observed errors use a stable `error_category` and a more specific
`error_code`. The frozen categories are:

- `input_validation`
- `prompt_loading`
- `transport`
- `timeout`
- `empty_response`
- `json_parse`
- `schema_validation`
- `artifact_write`
- `persistence`
- `unknown`

Each error also records step name, LLM-call ID when relevant, attempt,
occurrence time, terminal status, exception type, and a sanitized message.
Secrets and full environment values must not be recorded.

### 13.10 Hybrid persistence

BuildLog uses two persistence mechanisms with separate responsibilities:

- filesystem artifacts store inspectable payloads under `runs/`
- SQLite stores business metadata and queryable observability projections

Existing business tables retain their meaning:

1. `projects`
2. `iterations`
3. `runs`
4. `artifacts`
5. `evaluations`
6. `prompt_versions`

The v0.2 publishing baseline adds one operational table:

1. `publish_receipts`

It stores platform, account reference, content hash, outcome, external post
identifier, API version, safe error metadata, and duplicate lineage. It never
stores post bodies, access tokens, refresh tokens, authorization codes, client
secrets, or raw ID tokens. Publication failure does not change the completed
generation run.

The observability baseline adds:

1. `run_observations`
2. `step_observations`
3. `llm_call_observations`
4. `error_observations`
5. `artifact_dependencies`

SQLite does not store full prompts, post bodies, or model responses. It is not
required to be transactionally identical to the JSONL stream. If a projection
write fails, filesystem output continues and `observability_status` becomes
`partial`.

Business logic does not depend directly on SQLAlchemy persistence models.
Domain records and repository protocols form the boundary between the pipeline
and persistence. The SQLAlchemy-backed repositories are the v0.1
implementations.

Creating tables on startup is acceptable for v0.1. Do not add Alembic, async
database access, repository factories, or an additional service layer.

### Artifact asset layers

The persistence architecture and the repository's curated assets have
different responsibilities:

1. `runs/` contains complete raw execution traces. Raw runs are internal
   evaluation source data, remain ignored by Git, and may contain failed
   outputs, local paths, debugging context, or private evidence.
2. `eval_corpus/` is reserved for deliberately reviewed and sanitized
   evaluation records. Nothing is promoted from raw runs automatically.
3. `examples/outputs/` contains selected public showcase outputs that are safe
   and useful for GitHub visitors.
4. `docs/output_quality_baseline.md` defines the current scoring baseline and
   evaluation protocol used to accept or reject future quality changes.

The intended promotion path is:

```text
Raw run
    ↓
Human review
    ↓
Evaluation baseline
    ↓
Selected public showcase
    ↓
Future reviewed evaluation or few-shot asset
```

This distinction does not add a pipeline stage or change filesystem and SQLite
persistence behavior.

---

## 14. Technical stack

### Required for v0.1

| Area | Technology |
|---|---|
| Language | Python |
| Data validation | Pydantic |
| Model abstraction | LiteLLM |
| Local model runtime | Ollama |
| Initial model | Qwen3 |
| Configuration | python-dotenv |
| Input format | JSON |
| Output format | Markdown |
| Metadata persistence | SQLite |
| Database access | SQLAlchemy 2.0 |
| External HTTP | HTTPX |
| Testing | Pytest |
| Logging | Python `logging` |
| Packaging | `pyproject.toml` |
| Version control | Git + GitHub |

### Optional later

| Area | Technology |
|---|---|
| API | FastAPI |
| UI | Gradio or Streamlit |
| External database | PostgreSQL |
| Workflow graph | LangGraph |
| Observability | LangSmith, OpenTelemetry, or custom traces |
| Cloud deployment | Docker + AWS/Azure/GCP |

### Framework decision

Do not introduce an agent framework in v0.1 unless the implementation clearly requires it.

A normal Python pipeline is preferred because:

- the workflow is mostly fixed
- only specific stages require model judgment
- explicit control improves reliability
- traces are easier to understand
- revision count must remain bounded

---

## 15. Planned repository structure

```text
BuildLog/
├── README.md
├── PROJECT.md
├── TASK.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── eval_corpus/
│   └── README.md
├── examples/
│   ├── local_agent_iteration.json
│   ├── buildlog_architecture_iteration.json
│   └── outputs/
│       └── architecture/
│           ├── README.md
│           ├── linkedin_v1.md
│           └── linkedin_v2.md
├── prompts/
│   ├── planner_v1.md
│   ├── planner_v2.md
│   ├── writer_v1.md
│   ├── writer_v2.md
│   ├── evaluator_v1.md
│   ├── evaluator_v2.md
│   ├── reviser_v1.md
│   └── reviser_v2.md
├── src/
│   └── buildlog/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── event_writer.py
│       ├── models.py
│       ├── domain.py
│       ├── hashing.py
│       ├── input_loader.py
│       ├── preprocessor.py
│       ├── llm_client.py
│       ├── planner.py
│       ├── writer.py
│       ├── evaluator.py
│       ├── reviser.py
│       ├── observability_models.py
│       ├── observability_utils.py
│       ├── observability_repository.py
│       ├── observer.py
│       ├── pipeline.py
│       ├── review_policy.py
│       ├── terminal_safety.py
│       ├── trace.py
│       ├── repository.py
│       ├── run_persistence.py
│       ├── persistence_models.py
│       ├── sqlalchemy_observability_repository.py
│       ├── linkedin_callback.py
│       ├── linkedin_cli.py
│       ├── linkedin_config.py
│       ├── linkedin_errors.py
│       ├── linkedin_http.py
│       ├── linkedin_identity.py
│       ├── linkedin_oauth.py
│       ├── linkedin_publisher.py
│       ├── linkedin_security.py
│       ├── linkedin_token_store.py
│       ├── publication_content.py
│       ├── publishing_models.py
│       ├── publishing_observability.py
│       ├── publishing_repository.py
│       ├── publishing_service.py
│       ├── sqlalchemy_publishing_repository.py
│       ├── sqlalchemy_repository.py
│       └── exceptions.py
├── tests/
│   ├── test_models.py
│   ├── test_input_loader.py
│   ├── test_preprocessor.py
│   ├── test_threshold_logic.py
│   ├── test_repository.py
│   ├── test_pipeline.py
│   ├── test_observability.py
│   ├── test_prompt_loader.py
│   ├── test_trace.py
│   ├── test_event_writer.py
│   ├── test_linkedin_cli.py
│   ├── test_linkedin_config_and_security.py
│   ├── test_linkedin_identity_and_publisher.py
│   ├── test_linkedin_oauth.py
│   ├── test_publishing_service.py
│   └── fixtures/
│       └── valid_iteration.json
├── runs/
│   └── .gitkeep
└── docs/
    ├── adr/
    ├── implementation/
    ├── linkedin/
    ├── research/
    ├── ideas.md
    ├── output_quality_baseline.md
    └── generalization_baseline.md
```

---

## 16. Module responsibilities

### `main.py`

- command-line entry point
- accepts input path
- starts pipeline
- prints final result path
- returns non-zero exit code on failure

### `config.py`

- loads environment variables
- exposes validated settings
- contains no business logic

### `models.py`

- defines Pydantic domain models
- validates required fields
- rejects blank list entries
- defines planner and evaluator output schemas

### `input_loader.py`

- checks path existence
- loads JSON
- returns validated `Iteration`

### `preprocessor.py`

- normalizes whitespace
- removes exact duplicate list entries
- preserves semantic content
- must not use an LLM

### `llm_client.py`

- wraps LiteLLM
- supports text and structured JSON output
- centralizes retries and errors
- does not contain prompts

### `planner.py`

- loads planner prompt
- generates structured `StoryPlan`

### `writer.py`

- loads writer prompt
- generates first Markdown draft

### `evaluator.py`

- loads evaluation prompt
- returns structured scores and feedback

### `reviser.py`

- performs one constrained revision

### `pipeline.py`

- coordinates components
- contains revision decision logic
- does not contain prompt text

### `observability_models.py`

- defines validated observation schemas and fixed status vocabularies
- contains no filesystem, provider, or SQLAlchemy behavior

### `observability_utils.py`

- provides timing, sanitization, error classification, Git-state inspection,
  and canonical hash helpers
- contains no pipeline business decisions

### `observer.py`

- observes the existing Run, Step, LLM-call, Error, and Artifact lifecycles
- writes summary, timeline, and ordered event views
- builds revision evidence and replay completeness without changing behavior
- isolates telemetry failures from generation

### `observability_repository.py`

- defines the minimal protocol for queryable observation projections
- contains no SQLAlchemy imports

### `trace.py`

- creates run directory
- writes readable JSON and Markdown artifacts
- writes run metadata
- computes artifact content hashes

### `domain.py`

- defines persistence-facing domain records
- contains no SQLAlchemy imports

### `hashing.py`

- computes deterministic SHA-256 hashes for files
- contains no persistence logic

### `repository.py`

- defines the minimal persistence protocol used by the pipeline
- exposes only current v0.1 use cases

### `run_persistence.py`

- maps validated pipeline data to persistence-facing domain records
- records artifact hashes and evaluation feedback through the repository
- contains no SQLAlchemy imports

### `persistence_models.py`

- defines SQLAlchemy table mappings
- contains no pipeline business logic

### `sqlalchemy_repository.py`

- creates the SQLite schema on startup
- implements the business repository protocol
- stores run relationships, scores, paths, and hashes

### `sqlalchemy_observability_repository.py`

- implements the observability query projection
- stores no full prompt, model-response, or post payload

### `exceptions.py`

Defines project-specific exceptions such as:

- `InputFileError`
- `ValidationError`
- `ModelResponseError`
- `StructuredOutputError`
- `TraceWriteError`

### Publishing modules

- `review_policy.py` owns the exact cross-stage human-review warning without
  coupling generation to publishing.
- `terminal_safety.py` identifies control characters that can make terminal
  approval text misleading without introducing a platform dependency.
- `event_writer.py` provides the reusable crash-tolerant append-only event
  writer used by generation and publishing observability.
- `linkedin_config.py` loads publishing configuration independently from
  generation settings.
- `linkedin_callback.py`, `linkedin_http.py`, `linkedin_security.py`, and
  `linkedin_errors.py` isolate callback handling, transport, redaction, and
  typed failures.
- `linkedin_token_store.py` stores OAuth state and tokens atomically in a
  restricted user-level directory.
- `linkedin_oauth.py` implements Authorization Code exchange without automatic
  refresh.
- `linkedin_identity.py` resolves the authenticated member through OIDC
  userinfo.
- `publication_content.py` resolves only a completed final artifact and strips
  the exact fixed review footer.
- `publishing_models.py`, `publishing_repository.py`, and
  `publishing_service.py` define approval, duplicate, result, and receipt
  behavior without LinkedIn HTTP details.
- `linkedin_publisher.py` is the text-only `/rest/posts` adapter.
- `publishing_observability.py` appends safe events to the existing run stream.
- `sqlalchemy_publishing_repository.py` persists receipt metadata without post
  bodies or credentials.
- `linkedin_cli.py` exposes the local login, status, whoami, preview, publish,
  and logout flow.
- `x_config.py`, `x_oauth.py`, `x_callback.py`, `x_token_store.py`, and
  `x_identity.py` implement the isolated OAuth 2.0 PKCE and verified identity
  boundary for X.
- `x_publisher.py` is the text-only `POST /2/tweets` adapter.
- `x_cli.py` exposes X login, status, whoami, preview, publish, and logout
  without introducing a second publishing workflow.

The Publishing Package is a separate output boundary. It is currently
LinkedIn-targeted and is not consumed by the X adapter.

---

## 17. Prompt requirements

Prompts are source code and must be versioned.

### Planner prompt rules

The planner must:

- use only supplied evidence
- identify one central story
- prefer engineering decisions over generic motivation
- avoid presenting routine setup as a major breakthrough
- return structured JSON

### Writer prompt rules

The writer must:

- use first person
- begin with a concrete problem, observation, or decision
- explain what changed and why it mattered
- include supported technical details
- provide a reusable lesson
- avoid exaggerated language
- avoid fake metrics
- avoid unsupported production claims
- produce approximately 180–350 words
- use no more than five hashtags
- return only the post

Avoid phrases such as:

- thrilled to announce
- excited to share
- game changer
- revolutionary
- cutting-edge solution
- groundbreaking
- transformed everything

### Evaluator prompt rules

The evaluator must:

- compare the draft against the original iteration
- score every rubric dimension
- list unsupported claims
- identify vague sections
- provide actionable revision instructions
- return structured JSON

### Reviser prompt rules

The reviser must:

- follow evaluator feedback
- preserve supported facts
- remove unsupported claims
- improve specificity only from existing evidence
- return only the revised post

---

## 18. Coding standards

All coding agents must follow these rules.

### Architecture

- Prefer explicit pipelines over hidden autonomy.
- Use one responsibility per module.
- Keep domain models independent of model providers.
- Keep prompts outside Python source files.
- Keep model calls behind one client abstraction.
- Do not add frameworks without a demonstrated need.

### Python

- Use Python type hints.
- Use Pydantic for external data validation.
- Use `pathlib.Path` for file paths.
- Use custom exceptions for domain failures.
- Use small functions with clear names.
- Add docstrings to public functions and classes.
- Avoid global mutable state.
- Do not suppress exceptions without logging.
- Do not store secrets in source control.

### Reliability

- Validate all model-generated structured data.
- Fail clearly when JSON output is invalid.
- Store raw model output when parsing fails.
- Limit automatic retries.
- Limit revision to one pass.
- Never silently replace missing evidence.
- Never invent default facts.

### Testing

At minimum, test:

- valid input
- missing required field
- blank list values
- missing input file
- invalid JSON
- duplicate normalization
- evaluation threshold pass
- evaluation threshold fail
- one-revision limit
- trace-directory creation

LLM calls should be mocked in unit tests.

---

## 19. CLI behavior

Initial command:

```bash
python -m buildlog.main examples/local_agent_iteration.json
```

Expected output:

```text
BuildLog completed.

Run:
runs/2026-07-27T19-30-12_local-agent-001

Final draft:
runs/2026-07-27T19-30-12_local-agent-001/06_final.md

Evaluation:
technical_accuracy: 9
specificity: 8
readability: 8
reader_value: 8
evidence_coverage: 9

Revision performed: no
```

Failure example:

```text
BuildLog failed: input field "evidence" must contain at least one non-empty item.
```

The process must return:

- exit code `0` on success
- non-zero exit code on failure

LinkedIn commands added in v0.2:

```bash
buildlog linkedin login
buildlog linkedin status
buildlog linkedin whoami
buildlog linkedin preview <run-id>
buildlog linkedin publish <run-id> --confirm
buildlog linkedin logout
```

`preview` never creates a post. `publish` requires `--confirm` and the exact
interactive input `PUBLISH`. Duplicate successful content is blocked by
default. A timeout, transport interruption, user interrupt during submission,
unexpected 2xx, HTTP 408, HTTP 5xx, or missing success identifier after
submission is recorded as `indeterminate`. A matching indeterminate receipt
also blocks publication until the human inspects LinkedIn and explicitly
overrides the block.

X commands added in v0.2:

```bash
buildlog x login
buildlog x status
buildlog x whoami
buildlog x preview <run-id>
buildlog x publish <run-id> --confirm
buildlog x logout
```

X follows the same preview, approval, duplicate, indeterminate-result, and
receipt contract. Its adapter makes at most one client-side POST attempt and
does not claim exactly-once server-side delivery.

---

## 20. Environment configuration

Suggested `.env.example`:

```env
BUILDLOG_MODEL=ollama_chat/qwen3
BUILDLOG_MODEL_DIGEST=
BUILDLOG_API_BASE=http://127.0.0.1:11434
BUILDLOG_TEMPERATURE=0.4
BUILDLOG_MAX_TOKENS=2200
BUILDLOG_PROMPT_VERSION=v1
BUILDLOG_EVAL_THRESHOLD_ACCURACY=8
BUILDLOG_EVAL_THRESHOLD_SPECIFICITY=7
BUILDLOG_EVAL_THRESHOLD_READABILITY=7
BUILDLOG_EVAL_THRESHOLD_VALUE=7
BUILDLOG_EVAL_THRESHOLD_EVIDENCE=7
BUILDLOG_DATABASE_URL=sqlite:///buildlog.db
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
LINKEDIN_REDIRECT_URI=http://localhost:8765/auth/linkedin/callback
LINKEDIN_API_VERSION=202607
X_CLIENT_ID=
X_REDIRECT_URI=http://127.0.0.1:8766/auth/x/callback
```

No real secrets should be committed.

---

## 21. Definition of done for v0.1

v0.1 is complete when:

- [ ] The repository installs locally.
- [ ] A sample iteration JSON is included.
- [ ] Invalid input produces a clear error.
- [ ] The planner returns validated structured output.
- [ ] The writer produces a LinkedIn Markdown draft.
- [ ] The evaluator returns validated scores and feedback.
- [ ] Threshold logic is deterministic.
- [ ] At most one revision occurs.
- [ ] Every pipeline artifact is stored.
- [ ] Every fixed step is represented exactly once in the timeline.
- [ ] Every LLM call is associated with a fixed step.
- [ ] Missing provider token usage is stored as unavailable, never estimated.
- [ ] Revision triggers and direct final-artifact lineage are inspectable.
- [ ] Revision improvement is marked `not_measured` without post-evaluation.
- [ ] Pipeline, observability, and reproducibility statuses are independent.
- [ ] Replay completeness is based on an explicit requirement checklist.
- [ ] Observability failures do not alter pipeline output or model-call count.
- [ ] SQLite tables are created on startup.
- [ ] Project, iteration, run, artifact, evaluation, and prompt metadata are persisted.
- [ ] Run, step, LLM-call, error, and artifact-dependency projections are persisted.
- [ ] Artifact and prompt paths and SHA-256 hashes are persisted.
- [ ] Domain and business logic do not import SQLAlchemy models.
- [ ] The final draft is written to Markdown.
- [ ] Unit tests cover deterministic behavior.
- [ ] The README explains how to run the project.
- [ ] No automatic LinkedIn publishing exists in the v0.1 generation
      baseline.
- [ ] No unsupported claims are present in the sample result.

---

### Definition of done for the v0.2 publishing and package baselines

- [x] Existing generation behavior and artifacts remain unchanged.
- [x] OAuth state is one-time, validated, and stored outside runs.
- [x] Tokens are atomic, private, local, redacted, and deletable.
- [x] Authenticated identity is resolved without trusting an unverified JWT.
- [x] Preview shows the exact final content and cannot publish.
- [x] Human approval is mandatory.
- [x] Duplicate successful publication is blocked by default.
- [x] Text-only personal-member publishing is behind a publisher boundary.
- [x] Success, failure, and indeterminate receipts are persisted.
- [x] Publishing events contain hashes and safe metadata, not content or
      credentials.
- [x] All automated network tests are mocked.
- [x] Real OAuth and the first real post were completed only through separate,
      explicit human approval.
- [x] The same Publisher Boundary is reused by LinkedIn and X.
- [x] X OAuth 2.0 PKCE and identity resolution are validated against a real
      account.
- [x] One explicitly approved X smoke post returned HTTP 201 with a persisted
      local receipt.
- [x] One reviewed run can produce a local LinkedIn-targeted caption, card
      assets, alt text, and manifest without publishing.
- [x] Publishing Package construction remains separate from delivery.

---

## 22. Current implementation iteration

### Completed objective

Validate the reusable human-controlled Publisher Boundary through real
LinkedIn and X text delivery while keeping transport downstream and optional.

### Required sequence

```text
Reviewed final artifact
        ↓
Exact preview
        ↓
Explicit human approval
        ↓
One client-side publication attempt
        ↓
Durable receipt and event trace
```

LinkedIn and X have both completed this controlled real-world validation.
Further delivery expansion is frozen.

### Next product validation

Determine whether the same reviewed engineering evidence can produce distinct
LinkedIn and X artifacts that the user is willing to publish with low editing
effort. This is a product validation question, not authorization to implement
a template registry, scheduler, new publisher, or autonomous workflow.

### Freeze rule

New product ideas must not change the current iteration unless they solve a blocking problem.

Store future ideas in:

```text
docs/ideas.md
```

---

## 23. Future direction

Possible future evidence sources:

- Git diffs
- commit history
- pull requests
- issues
- terminal logs
- screenshots
- architecture notes
- test reports
- deployment records

Possible future outputs:

- README updates
- portfolio case studies
- resume bullets
- interview stories
- technical articles
- presentation outlines
- video scripts

Possible product evolution:

```text
One iteration
      ↓
Structured engineering knowledge
      ↓
Multiple reusable outputs
```

These are long-term possibilities, not v0.1 requirements.

---

## 24. Instructions for Codex or another coding agent

When implementing this project:

1. Read this entire file before writing code.
2. Do not expand the active scope beyond `TASK.md`.
3. Implement deterministic components before LLM components.
4. Use the repository structure defined above unless a concrete technical conflict exists.
5. Keep prompts in separate versioned Markdown files.
6. Validate every external input and structured model output.
7. Create tests for deterministic business logic.
8. Store every major pipeline artifact.
9. Permit at most one automatic revision.
10. Keep delivery adapters downstream, text-only, human-controlled, optional,
    and independent from generation and Publishing Package construction.
11. Use only the specified SQLite persistence layer; do not introduce
    LangGraph, PostgreSQL, Redis, Celery, RAG, or a web UI in v0.1.
12. Before making a significant architectural change, document the reason.
13. Prefer a simple working pipeline over abstract extensibility.
14. Never invent requirements not present in this document.
15. End implementation by running the sample input and reporting:
    - files created
    - tests run
    - test results
    - command used
    - final output path
    - any unresolved limitations

### Confirmed architecture revision

SQLite metadata persistence was explicitly added to v0.1 after the original
file-only design. This is a deliberate product decision, not permission to add
unrelated backend infrastructure. If a persistence design decision is not
specified here, choose the simplest implementation that satisfies v0.1.
