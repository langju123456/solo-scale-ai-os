# Architecture

## 1. System boundaries

SoloScale has two distinct planes.

### Personal control plane

The operator uses ChatGPT Chat and plugins directly. Chat outputs a compact artifact instead of trying to programmatically expose a paid subscription.

### Automated runtime plane

API-backed agents and Codex SDK execute workflows when realtime, scheduled, or unattended operation is required.

Both planes share the same contracts and evidence model.

## 2. Core contracts

### Task Envelope

Describes the outcome, constraints, required state, latency, risk, and available execution surfaces.

### Route Decision

Selects the primary surface and any secondary roles.

### Execution Packet

Freezes product and architecture decisions before local implementation.

### Run Event

Append-only evidence of each state transition, tool call, command, approval, and result.

### Review Result

Independent findings with severity, evidence, and required remediation.

### BuildLog Iteration

A distilled engineering story grounded in the completed run.

## 3. Bounded topology

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> TRIAGED
    TRIAGED --> PLANNED
    PLANNED --> APPROVED
    APPROVED --> EXECUTING
    EXECUTING --> VERIFYING
    VERIFYING --> REVIEWING
    VERIFYING --> FIXING
    REVIEWING --> FIXING
    FIXING --> VERIFYING
    REVIEWING --> ACCEPTED
    ACCEPTED --> CLOSED
    NEW --> BLOCKED
    TRIAGED --> BLOCKED
    PLANNED --> BLOCKED
    EXECUTING --> BLOCKED
```

The repair loop is bounded. A later policy module will enforce retry, time, cost, and file-change budgets.

## 4. Personal-mode routing

```mermaid
flowchart TD
    T[Task] --> Q{Needs realtime or schedule?}
    Q -->|Yes| R[RUNTIME]
    Q -->|No| L{Needs local repo or terminal?}
    L -->|Yes| C[CODEX]
    L -->|No| P{Supported plugin can act?}
    P -->|Yes| G[PLUGIN]
    P -->|No| H[CHAT]
    R --> K{High risk / public / irreversible?}
    C --> K
    G --> K
    H --> K
    K -->|Yes| A[HUMAN GATE]
    K -->|No| D[Continue]
```

## 5. Runtime evolution

### v0.1

Manual Chat, local CLI, GitHub artifacts, BuildLog export.

### v0.2

Codex SDK controls local coding threads. Deterministic verification and bounded repairs.

### v0.3

Agents SDK provides planner/reviewer roles. Code controls routing; specialists are tools, not a free-form committee.

### v0.4

Queue workers, sandboxed repositories, persistence, observability, and cloud deployment.
