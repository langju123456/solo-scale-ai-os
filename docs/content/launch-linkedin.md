# Draft LinkedIn Post — One strong brain, many execution surfaces

I recently noticed that I was using coding agents for work that did not require a local codebase.

I was asking them to define product requirements, compare architectures, reason about business trade-offs, inspect online tools, and only then write code.

That worked, but it coupled two very different jobs:

1. deciding what should be built;
2. operating on local files, terminals, tests, and Git state.

The result was repeated context, duplicated reasoning, and unnecessary coding-agent usage.

I am now building a different workflow.

The core model is:

**One strong reasoning core. Many bounded execution surfaces.**

ChatGPT handles research, product thinking, architecture, and final review.

Connected plugins handle actions in cloud systems such as Figma, Vercel, and GitHub.

Codex appears only when the task requires local repository state, terminal commands, dependencies, tests, builds, or uncommitted changes.

API-backed agents appear only when the product itself needs realtime, scheduled, repeated, or unattended execution.

A human remains the approval gate for spending, production changes, public publishing, security boundaries, and irreversible actions.

I am turning this workflow into an open engineering project called **SoloScale AI OS**.

The first version will not be a large “agent company.” It will use a small number of bounded roles:

- a planner that freezes decisions;
- an executor that performs constrained work;
- an independent reviewer that checks evidence;
- deterministic code that controls state, retries, budgets, and completion;
- a human who approves risk.

The project will record every meaningful run as structured evidence: the problem, alternatives, decision, implementation, commands, tests, result, and lesson.

That evidence will then flow into my existing BuildLog project, which turns real engineering work into reviewed portfolio and publishing assets.

The first experiment is concrete: run one Research Agent feature through the entire loop, from Chat planning to GitHub Issue, Codex implementation, tests, review, and a public technical narrative.

The larger hypothesis is that a solo builder does not need more AI personalities.

A solo builder needs better routing, stronger contracts, clearer permissions, and evidence that compounds.
