# Current Sprint — v0.1 Manual-to-Automated Handoff

## Sprint goal

Run one real Research Assistant feature through:

```text
Chat planning
→ GitHub Issue / Execution Packet
→ Codex local implementation
→ tests
→ independent review
→ BuildLog evidence export
→ public narrative
```

## In scope

- [ ] Initialize the new public repository.
- [x] Run and understand the deterministic starter.
- [ ] Create GitHub Project fields and Issue Forms.
- [ ] Select one narrow Research Assistant feature.
- [ ] Create the first Task Envelope.
- [ ] Produce the first approved Execution Packet.
- [ ] Implement through Codex in a feature branch.
- [ ] Capture tests, diff, decisions, and lessons.
- [ ] Export the run to BuildLog.
- [ ] Publish the first architecture narrative.

## Local preparation completed

- Baseline commit created on local `main`.
- Hardening revision `9fd720b` passes locally across Ruff, `mypy src tests`, 28 tests, the installed demo, and isolated package builds.
- Planning contracts, evidence-backed transitions, and approval enforcement are covered by tests.
- GitHub Project setup and Vercel evolution are documented.
- Public-safe conversation distillation, X/LinkedIn drafts, and editable architecture source are prepared.

Public repository creation, push, Project fields, labels, Issue, PR, release, and publishing remain explicit human-gated actions.

## Definition of done

- CI is green.
- One PR demonstrates the full workflow.
- The run has inspectable evidence.
- No secrets or raw private chats are committed.
- README contains a demo path and real screenshots.
- v0.1.0 is tagged.
