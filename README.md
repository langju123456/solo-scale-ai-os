# SoloScale AI OS

> **Working title:** a human-controlled AI operating system for routing reasoning, cloud plugins, local coding, and runtime agents.

**One strong reasoning core. Many execution surfaces. Deterministic evidence.**

SoloScale AI OS is both:

1. a **personal AI operating system** that routes work across ChatGPT Chat, connected plugins, Codex, and human approval; and
2. an **agent orchestration runtime** that can later automate the same workflow through APIs, Codex SDK, sandboxes, queues, and cloud workers.

The project is deliberately not a six-agent debate club. It uses the smallest number of bounded roles that produce independent value:

- **Reasoner / Planner** — defines the problem, freezes decisions, and creates an execution contract.
- **Executor** — modifies code or invokes tools inside a constrained environment.
- **Reviewer** — independently checks evidence, diffs, tests, and policy compliance.
- **Human gate** — approves risky, expensive, public, or irreversible actions.

## Why this exists

Modern AI tools are often used as isolated chat boxes. The same context gets re-explained to multiple models; coding agents spend expensive turns rediscovering product decisions; development evidence disappears after the code works; and content starts from a blank prompt rather than real engineering work.

SoloScale treats the workflow as a system:

```mermaid
flowchart LR
    H[Human operator] --> R[ChatGPT reasoning control plane]
    R --> P[Cloud plugins<br/>Figma / Vercel / GitHub]
    R --> C[Codex local execution<br/>repo / terminal / tests]
    R --> A[API agent runtime<br/>realtime / scheduled / autonomous]
    P --> E[Evidence event stream]
    C --> E
    A --> E
    E --> G[Independent review + policy gates]
    G --> B[BuildLog adapter]
    B --> X[X / LinkedIn / portfolio artifacts]
    G --> H
```

## Two operating modes

### Personal mode

Use the paid ChatGPT product as the control plane:

```text
ChatGPT Chat
  → research, product thinking, architecture, trade-offs, final review

Plugins
  → cloud actions in Figma, Vercel, GitHub, and other connected systems

Codex
  → only work that requires local files, terminal state, tests, builds, or Git

Human
  → budget, production, public posting, security, and irreversible decisions
```

The bridge between Chat and Codex is not raw conversation history. It is a compact, versioned **Execution Packet** committed to GitHub or attached to an Issue.

### Runtime mode

When latency, scheduling, or autonomy matters, replace the manual Chat surface with API-backed roles while preserving the same contracts:

```text
Task Envelope
  → deterministic router
  → planner
  → approval gate
  → Codex executor
  → deterministic verification
  → independent reviewer
  → repair loop
  → final evidence package
```

## Current scope: v0.1

The starter implements the deterministic foundations:

- typed Task Envelope
- route classification: `CHAT`, `PLUGIN`, `CODEX`, `RUNTIME`, `HUMAN`
- guarded state transitions
- append-only JSONL run events with from/to state and evidence receipts
- persisted run/task continuity checks before every transition
- approval-receipt enforcement before every `EXECUTING` transition
- Markdown Execution Packet generation
- BuildLog-compatible evidence export
- private Casebook evidence archives with SHA-256 integrity receipts
- append-only interview practice attempts; passing gates require integrity receipts
- deterministic interview packets and a local Control Tower
- GitHub Issue Forms and PR template
- CI and unit tests
- content/narrative templates

It intentionally does **not** yet invoke ChatGPT, Codex, Vercel, Figma, or any paid API.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'

soloscale demo
pytest
```

Create and route a task:

```bash
soloscale task-create \
  --title "Add source-grounded citations to the Research Agent" \
  --goal "Every research claim must link to inspectable source evidence" \
  --repo "../AI-Research-Assistant-LangJu-Edition" \
  --branch "feat/source-grounded-citations" \
  --constraint "Preserve the existing user-facing workflow" \
  --frozen-decision "Missing source evidence must never be invented" \
  --required-change "Return structured source receipts" \
  --acceptance-criterion "Every claim has inspectable evidence" \
  --test-to-run "pytest" \
  --reasoning-depth high \
  --requires-local \
  --latency-tolerance batch

soloscale task-route .soloscale/tasks/<task-id>/task.json
```

Generate a Codex handoff packet:

```bash
soloscale packet-create .soloscale/tasks/<task-id>/task.json
```

Export an engineering iteration for the existing BuildLog project:

```bash
soloscale buildlog-export .soloscale/tasks/<task-id>/run-summary.json
```

### Preserve a case and practice it

Casebook keeps selected evidence private under ignored `.soloscale/` storage. It does
not scrape an account, send files to an API, or embed raw evidence in generated views.

```bash
soloscale case-create \
  --case-id source-grounded-citations \
  --title "Adding citations without breaking a legacy AI assistant" \
  --project "AI Research Assistant" \
  --problem "Retrieved evidence had no strict public citation contract." \
  --expected "Answers expose validated provenance without changing legacy callers." \
  --actual "The legacy response exposed only text and no inspectable source contract." \
  --root-cause "Generation and public provenance were not joined by one validated contract." \
  --resolution "Add a structured API over shared orchestration and preserve the legacy API." \
  --verification "70 local tests and all post-merge main CI jobs passed." \
  --concept "API evolution without duplicate side effects" \
  --concept "Provenance normalization and collision policy" \
  --unknown "Business impact and token cost were not measured." \
  --evidence document=examples/casebook/source-grounded-citations-case.md \
  --evidence ci=examples/casebook/citation-verification.txt

soloscale case-status source-grounded-citations
soloscale control-tower-build
```

The new case starts at `0/5`. Complete a stage with your own non-empty receipt:

```bash
soloscale case-attempt source-grounded-citations \
  --stage explain \
  --outcome pass \
  --receipt path/to/my-unaided-explanation.md \
  --note "Explained the confirmed boundary and unknowns without notes."
```

The five gates are:

```text
Explain → Trace → Rebuild → Debug → Defend
```

Passing all five produces `SELF_ASSESSED_INTERVIEW_READY`; it is deliberately not an
external certification. See [the Casebook guide](docs/casebook.md).

## Repository map

```text
.
├── README.md                         public product story
├── PROJECT.md                        product and engineering source of truth
├── TASK.md                           current sprint
├── ROADMAP.md                        milestone plan
├── AGENTS.md                         instructions for coding agents
├── src/soloscale/
│   ├── models.py                     contracts
│   ├── router.py                     deterministic route policy
│   ├── state_machine.py              guarded workflow transitions
│   ├── event_store.py                append-only evidence
│   ├── orchestration.py              validated, receipt-backed transitions
│   ├── handoff.py                    Execution Packet
│   ├── buildlog_adapter.py           evidence-to-content bridge
│   ├── casebook_models.py            learning and evidence contracts
│   ├── casebook_store.py             private archives and append-only practice
│   ├── interview_packet.py           deterministic interview exercises
│   ├── control_tower.py              local visual current-state projection
│   └── cli.py                        local CLI
├── tests/                            deterministic tests
├── examples/                         dogfooding inputs
├── docs/
│   ├── architecture.md
│   ├── decisions/                    ADRs
│   ├── devlogs/                      public development history
│   ├── conversations/                distilled insights, never raw secrets
│   ├── integrations/
│   └── content/                      X / LinkedIn narrative assets
└── .github/
    ├── ISSUE_TEMPLATE/
    ├── PULL_REQUEST_TEMPLATE.md
    └── workflows/ci.yml
```

## Portfolio thesis

This project demonstrates more than prompt engineering:

- human-in-the-loop system design
- deterministic and LLM-driven orchestration trade-offs
- multi-surface tool routing
- structured outputs and contract-driven handoffs
- sandboxed coding-agent execution
- guardrails and approval gates
- observability, replay, and cost accounting
- evaluation and independent review
- evidence-to-content transformation
- local-to-cloud evolution

## Relationship to existing projects

- **SoloScale AI OS** — orchestration and control plane.
- **AI Research Assistant — LangJu Edition** — first real workload used to dogfood the system.
- **BuildLog** — downstream evidence-to-story and publishing system.

Keeping them separate makes each portfolio artifact clearer while creating a compounding ecosystem.

## Project operations

- [SoloScale Execution Manual v1](docs/operating-manual/README.md)
- [GitHub evidence-plane setup](docs/github-project.md)
- [Local-to-cloud and Vercel path](docs/deployment.md)
- [Conversation distillation policy](docs/conversations/README.md)
- [Casebook local evidence and learning workflow](docs/casebook.md)
- [Evidence-to-multichannel content template](docs/content/TEMPLATE.md)
- [Editable Figma architecture board](https://www.figma.com/board/psWfF0mEOdHqUvyOWrJWeF)

Local hardening revision `9fd720b` passed 28 tests, Ruff, `mypy src tests`, the installed CLI demo from outside the repository, and isolated wheel/source-distribution builds. That was the starter baseline: the later Citation Feature completed its external Issue → PR → review → merge → main-CI loop, while this local Casebook branch still requires its own GitHub PR and CI gate.

The current repository is still a local v0.1 control plane. It does not invoke ChatGPT,
plugins, Codex, or a hosted runtime automatically, and it makes no measured efficiency
or commercial-demand claim yet. Casebook archives only files the operator explicitly
selects; automatic chat-history capture remains future work.

## Public-development rule

Do not publish raw chat transcripts, credentials, private prompts, customer details, or unreviewed claims.

Publish distilled evidence:

```text
problem
→ observation
→ decision
→ alternatives
→ implementation
→ verification
→ result
→ lesson
```

See `docs/conversations/README.md` and `docs/content/`.
