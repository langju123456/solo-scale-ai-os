# Architecture Output Showcase

This showcase comes from a real BuildLog development iteration: evolving the
project from filesystem-only traces to hybrid filesystem and SQLite
persistence.

## Source and Outputs

1. [Architecture iteration input](../../buildlog_architecture_iteration.json)
2. [Generated LinkedIn post with v1 prompts](linkedin_v1.md)
3. [Generated LinkedIn post with v2 prompts](linkedin_v2.md)
4. [Full output-quality baseline](../../../docs/output_quality_baseline.md)

Both posts were generated locally with the same
`ollama_chat/qwen3:8b` model. The iteration evidence and model stayed the same;
the planner, writer, evaluator, and reviser prompt versions changed from v1 to
v2.

The files were selected from complete local run traces. Their generated post
content is preserved without rewriting. The v2 result improved technical
specificity, narrative structure, authenticity, and reader value, but the
human review still found claims that should be narrowed before publishing.

These Markdown files are public showcase artifacts, not complete raw traces.
Raw runs remain local because they include intermediate outputs, debugging
context, local paths, and potentially private development evidence.
