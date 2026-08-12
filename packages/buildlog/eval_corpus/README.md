# Evaluation Corpus

`eval_corpus/` is the future boundary for reviewed, cleaned, and intentionally
selected BuildLog evaluation records.

Raw runs remain local under `runs/` and are ignored by Git. Nothing should be
promoted from `runs/` automatically. A sample belongs here only after deliberate
human review confirms that it is:

- grounded in the supplied iteration evidence
- sanitized of secrets, private paths, and confidential information
- useful for a named evaluation purpose
- accompanied by enough provenance to interpret its scores and conclusions

Reviewed samples may later support regression tests, evaluator calibration,
prompt and model comparisons, failure-pattern analysis, and carefully selected
few-shot examples.

This directory does not contain an evaluation dataset yet. It establishes the
asset boundary without exposing unreviewed traces or adding retrieval,
embedding, RAG, or dataset tooling.
