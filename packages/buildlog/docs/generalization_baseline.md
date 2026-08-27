# BuildLog v0.1 Generalization Baseline

## Objective

This iteration tested one question:

> Can BuildLog handle different types of real engineering iterations
> consistently?

The experiment used five cases from the development history of BuildLog and
the local AI setup around it. It did not change any prompt, pipeline stage,
database table, framework, or product feature.

This baseline measures two different kinds of generalization:

1. Operational generalization: whether the fixed pipeline can validate, plan,
   write, evaluate, revise when required, and persist all five cases.
2. Editorial generalization: whether the resulting post is grounded, useful,
   authentic, and ready to publish without a human edit.

## Fixed Experiment Settings

| Setting | Value |
|---|---|
| Model | `ollama_chat/qwen3:8b` |
| Ollama model digest | `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41` |
| Temperature | `0.4` |
| Maximum output tokens | `4000` |
| Prompt version | `v2` for planner, writer, evaluator, and reviser |
| Revision policy | Existing one-pass threshold policy |
| Persistence | Existing filesystem traces and SQLite metadata |

The architecture case reuses its completed v2 run from the output-quality
baseline because it used these exact settings. The other four cases were run
once with the same settings during this iteration.

This is a cross-case baseline, not a repeatability study. Temperature remained
non-zero, and each case has only one completed sample.

## Real Cases

| Case | Input | Evidence source |
|---|---|---|
| Architecture | `examples/buildlog_architecture_iteration.json` | File-only to SQLite hybrid persistence implementation, tests, and run traces |
| Debugging | `examples/buildlog_debugging_iteration.json` | Incomplete 2200-token evaluator run and completed 4000-token retry |
| Infrastructure | `examples/buildlog_github_push_iteration.json` | Git errors, authentication checks, commits, Desktop push, and remote hash verification |
| Local AI | `examples/buildlog_local_ai_iteration.json` | Ollama command history, installed models, local agent run, and Qwen3 BuildLog runs |
| Developer Workflow | `examples/buildlog_workflow_iteration.json` | v1/v2 prompt files, version setting, SHA-256 records, SQLite links, and tests |

No synthetic project outcome or performance result was added to these inputs.
Some evidence came from direct development observations recorded during the
work, while other evidence is independently inspectable in the repository and
run directories.

## Run Results

| Case | Run | Automated scores | Revision | Words |
|---|---|---|---|---:|
| Architecture | `runs/2026-07-27T15-23-21_buildlog-architecture-001` | `10/10/10/10/10` | No | 282 |
| Debugging | `runs/2026-07-27T15-48-41_buildlog-debugging-001` | `10/9/9/10/9` | No | 225 |
| Infrastructure | `runs/2026-07-27T15-52-26_buildlog-infrastructure-001` | `10/10/10/10/10` | No | 217 |
| Local AI | `runs/2026-07-27T15-55-38_buildlog-local-ai-001` | `7/9/9/9/9` | Yes | 214 |
| Developer Workflow | `runs/2026-07-27T16-09-29_buildlog-workflow-001` | `7/6/7/6/6` | Yes | 272 |

Automated dimensions are technical accuracy, specificity, readability, reader
value, and evidence coverage in that order.

All five runs completed and retained a full trace. Operational generalization
therefore passed for this sample. Two of five cases triggered the existing
one-pass reviser.

## Artifact Hashes

| Case | Final SHA-256 |
|---|---|
| Architecture | `7a9f83151ff283b4e69e4599175452b510398360164b40d4cf5fe76bdf81a57d` |
| Debugging | `2586c4493ac9167c954165a8a9bd25d832b26f4f298a49bf1492fa750fbe30f7` |
| Infrastructure | `c717ec86746e2db9e77d09a1656a770574ce7d31d2059bd2b6ad93ab2b7bee0c` |
| Local AI | `eca28bb15dbd32b5f0ddf3fd29e1cba7e3ad04299d058726be3653b54c94748c` |
| Developer Workflow | `d18ea57aacf3d0ead0a28920c4559cdf37b78de361f781a7d637d04e290839f2` |

Hashes identify the exact local output files. They do not validate the claims
inside those files.

## Human Review Method

Each post received an editorial score from 1 to 10 on eight equally weighted
criteria:

- factual accuracy and grounding
- technical specificity
- clarity
- usefulness to software and AI engineers
- authenticity
- absence of exaggerated claims
- opening quality
- transferable ending

The reported human score is the arithmetic mean of those eight judgments.
`Publish?` means publish the generated post directly, without editing beyond
the standard secret and confidentiality review.

## Human Review Summary

| Case | Human Score | Publish? | Biggest Weakness |
|---|---:|---|---|
| Architecture | 8.1 | No, light edit | Converts difficulty into impossibility and adds unsupported SQLite framing |
| Debugging | 8.1 | No, light edit | Treats one successful retry as confirmation of a single root cause |
| Infrastructure | 7.1 | No | Places the clean synchronized Git status before the failed push |
| Local AI | 7.1 | No | Claims a model digest ensured output consistency |
| Developer Workflow | 7.1 | No | Overstates reproducibility and prompt/model isolation |

Zero of five generated posts were ready for direct publishing under the strict
definition above. Two were close enough for a light factual edit. Three needed
a more substantive correction.

## Case Critiques

### Architecture

The post has a clear architecture tension, useful implementation detail, and a
credible trade-off. It explains hybrid persistence, domain separation, the
repository boundary, and the startup table-creation limitation well.

It still says file-only traces made comparison "impossible" when the input said
"difficult." It adds `ACID-compliant` as generally true SQLite knowledge rather
than supplied iteration evidence. It also assumes a SQLite-only design would
lose relationship metadata even though a different schema could store it.

The evaluator reported no unsupported claims and assigned five perfect scores.

### Debugging

This is the strongest causal story in the set. The opening is concrete, the
missing `04_evaluation.json` localizes the failed stage, and changing one
runtime variable gives readers a useful debugging method.

The final post says the trace would otherwise make localization "impossible"
and that the successful retry confirmed the token ceiling as the root cause.
The retry is strong evidence, but one successful run does not exclude model
variance or another transient condition. "Supported explanation" would be more
accurate than "confirmed root cause."

The evaluator requested two additions but did not identify either overstatement.

### Infrastructure

The post avoids becoming a command-by-command log and correctly focuses on the
authentication boundary. The concrete error and remote hash make the story
specific.

Its chronology is wrong. It says the local status showed no divergence from
`origin/master` before the push failed. That synchronized status was observed
after the GitHub Desktop push. Before an unpushed commit is published, the
branch cannot simultaneously have no divergence from the remote. The input
contained both observations but did not attach an explicit timestamp to the
final status, so this is partly an input-sequencing weakness.

The post also spends too much reader attention on two full commit hashes. The
evaluator assigned five perfect scores and missed the chronology problem.

### Local AI

The initial draft invented a resource explanation for the connection reset:
it attributed the failure to persistent memory allocation and resource
constraints. The evaluator correctly rejected that claim, and the reviser
removed it.

The revised post still says recording the Qwen3 digest "ensured output
consistency across runs." A digest identifies the exact model artifact; it
cannot make generation deterministic, especially at temperature `0.4`. The
post also presents the adapter counterfactual more confidently than the
available run evidence supports.

Revision improved the result materially, but the one-pass pipeline did not
evaluate the revised draft again.

### Developer Workflow

The post covers the important product decision: prompt text is versioned,
hashable behavior rather than disposable prose. It includes the unequal token
ceiling and non-zero-temperature limitations.

It says duplicate prompt files introduced test-suite complexity without
evidence. It also claims `BUILDLOG_PROMPT_VERSION` isolates prompt behavior from
model changes, when it only selects a prompt version. Finally, calling the
comparison "reproducible" is too broad unless reproducibility means provenance,
not identical generated prose.

The evaluator identified a hash-related problem and triggered revision, but the
revision mainly shortened the ending. It retained the broader reproducibility
and isolation claims.

## Repeated Failure Patterns

### 1. Evidence becomes causal certainty

Four of five posts strengthen the supplied evidence:

- difficult comparison becomes impossible in Architecture
- one controlled retry becomes a confirmed root cause in Debugging
- a digest becomes output consistency in Local AI
- traceable prompt selection becomes reproducible and isolated behavior in
  Developer Workflow

This is the clearest cross-case writer failure.

### 2. Unsupported claims enter at the planning stage

The final writer is not the only source of drift. Story plans introduced:

- `ACID-compliant` framing and an absolute file-only limitation
- confirmed root-cause language
- a memory-allocation explanation for the connection reset
- test-suite complexity and model-isolation claims

The writer then expanded claims already present in the plan. Groundedness work
should therefore examine Planner and Evaluator behavior, not only Writer.

### 3. Evaluator scores are not calibrated to publishability

The evaluator gave perfect scores to Architecture and Infrastructure and near
perfect scores to Debugging. Human review found a meaningful factual or causal
edit in each.

The evaluator did catch unsupported claims in Local AI and Developer Workflow.
However, the one-pass reviser left a different unsupported claim in each final
post, and the revised drafts were not evaluated again.

Automated completion and automated scores are useful trace signals. They are
not yet reliable publishability judgments.

### 4. The prose uses a repeated engineering-post template

Four posts follow nearly the same sequence: concrete opening, rejected
alternative, "the trade-off," result, generalized lesson, hashtags. The
structure is readable, but across a feed it would begin to feel generated.
This is less urgent than groundedness and score calibration.

## Case-Specific Problems

These should not be treated as universal prompt failures:

- Infrastructure needs clearer timestamps in its input evidence.
- Local AI has weaker historical evidence than repository-backed cases; the
  connection-reset observation is recorded from the development session while
  the current installation now contains both models.
- Developer Workflow uses "reproducible" in a provenance sense while also
  acknowledging non-deterministic generation. The input should define that term
  more narrowly in future experiments.
- Full paths and long hashes help traceability in Debugging, Infrastructure,
  and Local AI but are not always good LinkedIn details.

## Prompt Decision

No prompt was changed in this iteration.

The repeated evidence-strength and evaluator-calibration failures are now
supported by multiple cases. They justify a future groundedness iteration, but
changing prompts inside this experiment would invalidate the baseline.

The v2 prompt hashes remain:

| Prompt | SHA-256 |
|---|---|
| Planner | `f150eb8a53ccdcc74ba8ce2597db9eaecec0c414c6eef6418747384700a7e277` |
| Writer | `8eab760630075a4151a30bcdd72c8d9d350fe7459cdaf3262843683e516d1623` |
| Evaluator | `ab2e5f6bfee4ee072b0960c679a351c43787e2e8e492b1dcee20f31ffd108eeb` |
| Reviser | `338990c02cb4748efebaf19047d62a31ea489b20647dfe04c707103b11e6ef8c` |

## Conclusion

BuildLog generalized operationally across architecture, debugging,
infrastructure, local AI, and developer workflow stories: five of five inputs
completed through the fixed pipeline with complete trace artifacts.

It did not yet generalize to direct publishability. The system consistently
turns structured evidence into readable engineering stories, but it also
consistently strengthens evidence into certainty. The evaluator is not
calibrated to catch that behavior reliably.

The current product answer is:

> BuildLog can produce a useful first editorial draft across different
> engineering story types, but a human still needs to verify causal strength
> and chronology before publishing.

## Recommended Next Iteration

Do not add few-shot retrieval yet.

Create a narrowly scoped groundedness-calibration iteration using these five
fixed cases:

1. Define claim-to-evidence labels for the observed failures.
2. Blind-review planner claims separately from writer claims.
3. Design Planner and Evaluator v3 changes only around repeated failure
   patterns.
4. Keep Writer changes minimal unless the same drift originates in Writer
   rather than Planner.
5. Re-run all five cases with identical model settings.
6. Require a second evaluation after revision before treating a revised draft
   as publishable.

Few-shot selection should wait until BuildLog has a larger set of reviewed,
publishable iterations rather than examples that still contain known claim
strength problems.
