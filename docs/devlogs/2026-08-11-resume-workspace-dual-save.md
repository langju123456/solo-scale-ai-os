# Resume Workspace dual-save

## Problem

Resume Workspace runs were complete and auditable under ignored
`.soloscale/resume-runs/`, but the operator's job-application documents lived in a separate
library. A successful run therefore required a manual copy step to keep the resume and its
controlling JD together.

## Decision

Preserve the nine internal run artifacts unchanged and add one optional, explicitly scoped
application-library sink. The local UI enables it by default at
`~/Documents/Resume Applications`; direct Python callers must opt in with
`application_library_root`.

The external bundle contains only:

- `JD.md`;
- the generated Markdown resume; and
- `application.json` with job identity, source, run ID, and review status.

Repeated runs never overwrite an existing application directory. The first run uses the
human-readable date/company/role/job-ID name; a later collision receives the unique
SoloScale run ID suffix. Internal and external directories remain private on POSIX systems.

## Boundary

This change does not generate DOCX, call a network service, apply to a job, publish data,
or copy Conversation RAG evidence bodies into the application library. The DOCX template
workflow remains separately human-reviewed.

## Verification

Targeted tests cover both destinations, non-overwrite behavior, private modes, UI wiring,
and the visible external path. Full repository verification is recorded in the task result.
