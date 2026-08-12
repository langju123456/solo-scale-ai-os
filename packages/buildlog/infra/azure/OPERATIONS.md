# Hosted operations runbook

## Release

1. Merge only after CI passes tests and builds the production image.
2. Run `Deploy Azure` against `staging`.
3. Verify readiness, dashboard reads, one idempotent job submission, artifact
   mirroring, and sanitized logs.
4. Promote the same commit SHA through the protected `production` environment.

## Rollback

Find the last known-good immutable image tag and update the Container App:

```bash
az containerapp update \
  --resource-group RESOURCE_GROUP \
  --name APP_NAME \
  --image REGISTRY/buildlog:KNOWN_GOOD_SHA
```

Schema changes must be backward compatible with the previous application
revision. Use expand-and-contract migrations for destructive or renamed fields;
do not rely on an automatic downgrade during an incident.

## Backup and recovery

- PostgreSQL point-in-time backups retain seven days in the demo environment.
- Blob objects and deleted containers retain seven days.
- Quarterly, restore PostgreSQL to a temporary server and validate run, job,
  evaluation, and publication-receipt counts.
- Restore Blob artifacts to a temporary container and verify stored SHA-256
  metadata against downloaded content.

## Monitoring

- `/health/live`: process is serving HTTP.
- `/health/ready`: database is reachable and the application can accept reads.
- `/metrics`: request count and latency histograms for scraping.
- Dashboard: run completion, evaluated quality, publications, queue state,
  pipeline latency, and recorded token usage.
- Log Analytics: request IDs, status, latency, worker transitions, retries, and
  artifact-mirror outcomes.

Initial alert policy:

- readiness fails for five minutes;
- HTTP 5xx exceeds 2% over ten minutes;
- p95 API latency exceeds one second for fifteen minutes;
- queued jobs are older than fifteen minutes;
- failed jobs exceed three in fifteen minutes;
- PostgreSQL storage exceeds 75% or connections exceed 80%.

## Cost controls

- Use the Burstable PostgreSQL SKU and one Container App replica for the demo.
- Keep 30 days of application logs and seven days of database/blob recovery.
- Set an Azure budget alert before production deployment.
- Review LLM token usage and cost per successful artifact weekly.
- Increase availability and retention only after an explicit recovery or usage
  requirement justifies the spend.
