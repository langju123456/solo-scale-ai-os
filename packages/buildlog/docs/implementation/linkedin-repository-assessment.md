# LinkedIn Publishing Repository Assessment

## Scope

This assessment records the extension points used by the BuildLog v0.2
LinkedIn Publishing Baseline. The working tree was clean before assessment,
and the existing test baseline was 25 passing tests.

## Existing System

### Pipeline entry point

`buildlog.main` accepts one iteration JSON path, loads environment-backed
settings, initializes `SQLAlchemyRunRepository`, and invokes
`buildlog.pipeline.run_pipeline`.

### Final artifact

A completed run stores its final artifact as:

```text
runs/<run-id>/06_final.md
```

The same artifact is indexed in SQLite with `artifact_type="final"`. The file
contains the generated post followed by a fixed human-review warning. That
warning is part of the v0.1 artifact contract and must remain visible in the
run, but it is not post content and must be removed by the downstream
publication-content resolver.

### CLI

The CLI uses the Python standard library `argparse`. The existing command must
remain valid:

```bash
python -m buildlog.main <iteration.json>
```

LinkedIn subcommands can be added by dispatching the `linkedin` command before
the legacy parser, without introducing another CLI framework.

### Persistence

BuildLog uses SQLAlchemy 2.0 and SQLite. Domain records and repository
protocols are independent of ORM tables. Filesystem JSON and Markdown remain
the payload source of truth; SQLite stores metadata and query projections.
Tables are created idempotently on startup with `Base.metadata.create_all`.

### Observability

Each run has an append-only `events.jsonl` stream validated through
`ObservationEvent`. `RunObserver` currently owns event sequencing and file
appends. Publishing happens after the generation observer has finalized, so a
small reusable append-only event writer is the appropriate extension point.
Publishing telemetry must append safe metadata to the existing run stream and
must never contain credentials or full post content.

### Configuration

Generation configuration is loaded from `.env` through `python-dotenv` into an
immutable dataclass. LinkedIn configuration should use the same approach but
remain separately loadable so generation and all tests continue to work
without LinkedIn credentials.

### Tests

Tests use `pytest`, temporary directories, direct repository construction, and
mocked LLM behavior. There is no configured formatter, linter, type checker,
or lock file. LinkedIn network tests must use a mock HTTP transport and require
no real credentials.

## Existing Integration Inventory

The repository contains no publishing, OAuth, token-store, HTTP adapter,
external credential, or platform adapter abstraction. It does contain stable
boundaries for:

- immutable configuration
- typed project errors
- domain records
- repository protocols
- SQLAlchemy persistence
- filesystem artifact resolution
- validated append-only observation events

## Selected Extension Points

1. Keep generation unchanged and resolve an already-completed `final`
   artifact through the repository.
2. Add a small publishing domain and service boundary independent of LinkedIn
   HTTP details.
3. Add LinkedIn OAuth, identity, token storage, and API adapter modules under
   the existing `buildlog` package.
4. Add an independent publishing-receipt repository implemented by the
   existing SQLAlchemy repository.
5. Reuse the existing `events.jsonl` schema with a reusable append-only writer.
6. Preserve the final artifact contract and strip only the exact known
   human-review footer when producing publication content.
7. Preserve legacy CLI behavior while adding a minimal
   `buildlog linkedin ...` flow.

## Boundaries Preserved

- Publishing does not rerun generation.
- LinkedIn code does not enter planner, writer, evaluator, or reviser modules.
- No prompts, thresholds, model settings, revision behavior, or artifact names
  change.
- A failed publication does not change the completed run status.
- Credentials remain outside `runs/` and outside SQLite.
- No real LinkedIn request is made by tests or autonomous implementation.
