# BuildLog v0.1 Output Quality Baseline

## Objective

This iteration tested whether BuildLog can turn real development evidence into
a LinkedIn post that is more grounded, specific, and useful than a generic AI
summary. It did not add product features or change the pipeline architecture.

The same validated input was run with v1 and v2 prompts:

`examples/buildlog_architecture_iteration.json`

The input describes the decision to evolve BuildLog from file-only traces to a
hybrid persistence model with readable filesystem artifacts and queryable
SQLite metadata.

## Baseline Role

This document is BuildLog's current quality baseline and evaluation protocol,
not only a historical experiment report. Future changes to prompts, models,
writers, evaluators, revision behavior, or model settings should be compared
against this baseline rather than accepted because an output feels better.

The protocol combines technical accuracy, specificity, readability, reader
value, evidence coverage, authenticity, unsupported-claim review,
publishability, opening quality, and transferable-lesson quality.

```text
Hypothesis
    ↓
Controlled run
    ↓
Automated evaluation
    ↓
Human evaluation
    ↓
Baseline comparison
    ↓
Accept or reject
```

Raw runs are the internal source evidence for this process. Reviewed evaluation
records may later be promoted to `eval_corpus/`, while only selected and
sanitized outputs belong in the public showcase under `examples/outputs/`.

## Experiment Setup

| Setting | v1 | v2 |
|---|---|---|
| Model | `ollama_chat/qwen3:8b` | `ollama_chat/qwen3:8b` |
| Ollama model digest | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` | Same |
| Temperature | `0.4` | `0.4` |
| Maximum output tokens | `2200` | `4000` |
| Prompt version | `v1` | `v2` |
| Revision performed | No | No |
| Draft word count | 236 | 260 |

The v2 output ceiling was increased after an initial v2 evaluator call used the
entire 2200-token budget for local Qwen3 reasoning and returned empty final
content. The failed run was preserved as
`runs/2026-07-27T15-16-22_buildlog-architecture-001`. The higher value is an
output ceiling, not an instruction to produce a longer post, but it is still a
comparison limitation that should be controlled in the next experiment.

## Run Artifacts

### v1

- Run: `runs/2026-07-27T15-10-25_buildlog-architecture-001`
- Plan: `02_plan.json`
- First draft: `03_draft.md`
- Evaluation: `04_evaluation.json`
- Final: `06_final.md`
- Final SHA-256:
  `2721708bb7622d808a31e7b8658dbe3de96df1f9237818a19834fbacc20b915c`

### v2

- Run: `runs/2026-07-27T15-23-21_buildlog-architecture-001`
- Plan: `02_plan.json`
- First draft: `03_draft.md`
- Evaluation: `04_evaluation.json`
- Final: `06_final.md`
- Final SHA-256:
  `7a9f83151ff283b4e69e4599175452b510398360164b40d4cf5fe76bdf81a57d`

Neither run triggered revision, so each final post is the first draft plus the
standard human-review warning.

## Automated Evaluation

| Dimension | v1 | v2 |
|---|---:|---:|
| Technical accuracy | 9 | 10 |
| Specificity | 8 | 10 |
| Readability | 9 | 10 |
| Reader value | 9 | 10 |
| Evidence coverage | 8 | 10 |
| Unsupported claims reported | 0 | 0 |

The automated scores indicate improvement, but the v2 all-10 result is not
credible as a complete quality judgment. Human review found groundedness and
style issues that the evaluator missed. The score increase therefore measures
some real writer improvement and some remaining evaluator leniency.

## Human-Style Critique

Scores below are editorial judgments for this baseline, not pipeline outputs.

| Criterion | v1 | v2 | Human assessment |
|---|---:|---:|---|
| Factual accuracy and grounding | 7 | 7 | Both preserve the architecture, but each broadens some evidence. |
| Technical specificity | 8 | 9 | v2 explains mappings, tables, protocol boundaries, and migration trade-offs more concretely. |
| Clarity | 8 | 9 | v2 has a stronger causal sequence and fewer list-like transitions. |
| Usefulness to software and AI engineers | 8 | 9 | v2 makes the storage-responsibility decision easier to transfer to another pipeline. |
| Authenticity | 5 | 8 | v1 reads like generated architecture documentation; v2 reads more like a developer recounting a decision. |
| Absence of exaggerated claims | 6 | 7 | v2 removes the model-viability claim but still uses unsupported absolute language. |
| Opening creates interest | 5 | 8 | v1 opens with a generic bold question; v2 opens with a concrete first-person decision. |
| Ending communicates a transferable lesson | 6 | 8 | v2 is declarative and relevant, although still long and abstract. |

### v1 critique

The v1 plan selected the correct topic but framed it as a broad how-to question.
The post then summarized technologies and lessons instead of showing the
decision process. Its opening, bold formatting, bullet-list conclusion, emoji,
five hashtags, and engagement question made the result feel templated.

The sentence saying the Qwen3 run "confirmed the model's viability" was stronger
than the evidence. The input only established that one local sample completed.
The evaluator reported no unsupported claims and suggested adding facts that
were either already present or not safe to infer.

### v2 critique

The v2 post is more concrete and authentic. It starts from a first-person
decision, names the rejected file-only and database-only directions, explains
why domain records were separated from SQLAlchemy models, and preserves the
startup-table-creation trade-off.

It is still not ready for direct publishing without a light edit:

- "trace comparison impossible" is stronger than the input's claim that
  comparison would be difficult
- `ACID-compliant` is generally true of SQLite but was not supplied as iteration
  evidence and does not help this particular story
- the explanation of what a SQLite-only alternative would lose is speculative
  rather than directly evidenced
- the ending combines two lessons and could be shorter

The v2 evaluator missed all four issues and assigned perfect scores.

## Three Largest v1 Weaknesses

1. The opening and ending used recognizable LinkedIn templates instead of a
   specific engineering moment and a concise conclusion.
2. The body read as an architecture inventory rather than a causal decision
   story with a rejected alternative and an explicit remaining cost.
3. The evaluator accepted overstated claims and proposed revisions that were not
   strictly constrained to available evidence.

## Prompt Changes

### Planner v2

- requires one decision tension instead of a field summary
- requires an initial state, limitation, rejected alternative, trade-off, and
  observed result
- forbids rhetorical-question hooks, hashtags, emoji, and unsupported outcome
  broadening
- requires a declarative transferable ending

### Writer v2

- requires first-person singular unless a team is explicitly present
- replaces question or announcement openings with a concrete situation
- requires one causal story and two to four explained technical details
- distinguishes successful runs from production, reliability, or model claims
- prevents hashes from being described as guarantees
- removes template headings, bullet-list conclusions, engagement questions, and
  unnecessary emoji
- limits hashtags to three

### Evaluator v2

- introduces stricter anchors for every existing score
- reserves scores of 9 or 10 for drafts needing no substantive edit
- checks broadened causal claims and strength of evidence
- requires revision instructions to be achievable from the supplied iteration
- explicitly distinguishes content hashes from consistency guarantees

### Reviser v2

- ignores evaluator requests that require missing evidence
- narrows unsupported claims
- preserves one causal story
- enforces the concrete opening and declarative ending if revision is triggered

## Prompt Hashes

| Prompt | v1 SHA-256 | v2 SHA-256 |
|---|---|---|
| Planner | `e196d7bf930390eb9646de496d840ca4f31cee007fb1734cebd02b968373b661` | `f150eb8a53ccdcc74ba8ce2597db9eaecec0c414c6eef6418747384700a7e277` |
| Writer | `4f8c9e30f28eea591d9448292363bc7c2e77d2e480e1662e26632c37c9fb81e8` | `8eab760630075a4151a30bcdd72c8d9d350fe7459cdaf3262843683e516d1623` |
| Evaluator | `3240779d5143a91e513a0c06f40356936c35e96ce5c5fb7f08dd9eb13e555bca` | `ab2e5f6bfee4ee072b0960c679a351c43787e2e8e492b1dcee20f31ffd108eeb` |
| Reviser | `ddad407dfb221d37eb5f665a55c7762dab80871416195750bc5c1763896a17d8` | `338990c02cb4748efebaf19047d62a31ea489b20647dfe04c707103b11e6ef8c` |

SQLite links each run to the exact prompt-version records above.

## Remaining Weaknesses

1. The evaluator is still too lenient and shares the writer's model biases.
   Perfect scores should not be trusted without human calibration.
2. Planner and writer prompts reduce unsupported broadening but do not eliminate
   it. Qwen3 still converts "difficult" into "impossible" and adds generally
   true technical facts that were not in the evidence.
3. This baseline uses one input and one completed sample per prompt version at a
   non-zero temperature. It cannot establish general output consistency.
4. The v2 run needed a higher generation ceiling for the thinking model to
   return evaluator JSON.
5. The v2 final post is closer to publishable, but a human should still shorten
   the ending and narrow the unsupported absolute statements.

## Conclusion

v2 is materially better than v1 in narrative shape, specificity, authenticity,
and practical reader value. It moves BuildLog away from generic AI-generated
LinkedIn formatting and toward a credible engineering reflection.

The answer to "Would a user publish this directly?" is still **not yet**. The v2
post is close enough for light editing, but the evaluator cannot yet reliably
identify the remaining edits.

## Recommended Next Iteration

Keep the architecture frozen. Run v1 and v2 on three to five additional real
development iterations using identical model settings. Collect blind human
ratings before changing prompts again. Use the resulting repeated failure
patterns to calibrate an evaluator v3, with priority on:

- claim-to-evidence strength
- absolute-language detection
- separation of generally true knowledge from supplied evidence
- score calibration against human publishability judgments
