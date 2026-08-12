# Azure Container Apps deployment

This deployment keeps the modular-monolith boundary while adding managed
PostgreSQL, Alembic migrations, Azure Blob artifact mirroring through managed
identity, a private container registry, health probes, database backups, and
Log Analytics. The template keeps one replica until migration execution and
rate limiting are separated into globally coordinated deployment/runtime
boundaries.

## Deploy

Build and push an immutable image to a registry, then deploy the resource group:

```bash
az group create --name buildlog-rg --location eastus
az deployment group create \
  --resource-group buildlog-rg \
  --template-file infra/azure/main.bicep \
  --parameters \
    namePrefix=buildlogdemo \
    containerImage=REGISTRY/IMAGE:IMMUTABLE_TAG \
    webApiKey='A_RANDOM_VALUE_OF_AT_LEAST_24_CHARACTERS' \
    postgresAdminPassword='A_RANDOM_DATABASE_PASSWORD' \
    llmModel='openai/YOUR_MODEL' \
    llmApiKey='YOUR_PROVIDER_KEY'
```

Store the web, database, and model keys in a protected CI environment or secret
manager. Do not commit parameter files containing their values. PostgreSQL
retains seven days of backups; Blob soft deletion retains deleted artifacts for
seven days. The low-cost demo SKU deliberately disables zone redundancy and
geo-redundant backup.

The template uses the API key until Container Apps built-in Microsoft Entra ID
authentication is configured and tested. Only after the platform is set to
reject unauthenticated requests should `BUILDLOG_TRUST_AZURE_AUTH=true` be
enabled; otherwise a client-supplied identity header must not be trusted. Keep
the API key as a controlled automation fallback.

## Scale-out trigger

Before enabling multiple replicas, move migrations into a one-shot deployment
job and move per-replica rate limits to a shared gateway or Redis policy. The
PostgreSQL queue already uses row locking outside SQLite, but an external queue
becomes preferable when independent worker autoscaling, long visibility
timeouts, or dead-letter operations become real requirements.
