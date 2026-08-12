"""Shared human-review policy for generated and publishable artifacts."""

HUMAN_REVIEW_WARNING = (
    "\n\n---\n\n"
    "Human review required before publishing: check for secrets, API keys, "
    "employer-confidential information, customer data, private repository details, "
    "and unpublished business information.\n"
)
