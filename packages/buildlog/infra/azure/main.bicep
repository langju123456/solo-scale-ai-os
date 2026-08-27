targetScope = 'resourceGroup'

@description('Globally unique prefix for Azure resources.')
@minLength(3)
@maxLength(18)
param namePrefix string

@description('OCI image including an immutable tag or digest.')
param containerImage string

@description('Region for the hosted application.')
param location string = resourceGroup().location

@description('Private API key used by the internal application.')
@secure()
@minLength(24)
param webApiKey string

@description('LiteLLM model identifier used by the generation worker.')
param llmModel string = 'openai/gpt-4o-mini'

@description('Optional model-provider API key.')
@secure()
param llmApiKey string = ''

@description('Optional model-provider API base URL.')
param llmApiBase string = ''

@description('PostgreSQL administrator login for the managed demo database.')
param postgresAdminUser string = 'buildlogowner'

@description('PostgreSQL administrator password. Store it only in a protected deployment environment.')
@secure()
@minLength(12)
param postgresAdminPassword string

var normalizedPrefix = toLower(replace(namePrefix, '-', ''))
var storageName = take('${normalizedPrefix}${uniqueString(resourceGroup().id)}', 24)
var applicationName = '${namePrefix}-app'
var postgresName = '${namePrefix}-pg-${take(uniqueString(resourceGroup().id), 8)}'
var databaseUrl = 'postgresql+psycopg://${postgresAdminUser}:${uriComponent(postgresAdminPassword)}@${postgresName}.postgres.database.azure.com:5432/buildlog?sslmode=require'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${namePrefix}-logs'
  location: location
  properties: {
    retentionInDays: 30
    sku: {
      name: 'PerGB2018'
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${namePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: take('${normalizedPrefix}${uniqueString(subscription().id, resourceGroup().id)}', 50)
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'buildlog-artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-03-01-preview' = {
  name: postgresName
  location: location
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    administratorLogin: postgresAdminUser
    administratorLoginPassword: postgresAdminPassword
    authConfig: {
      activeDirectoryAuth: 'Disabled'
      passwordAuth: 'Enabled'
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
    network: {
      publicNetworkAccess: 'Enabled'
    }
    storage: {
      autoGrow: 'Enabled'
      storageSizeGB: 32
    }
    version: '16'
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-03-01-preview' = {
  parent: postgres
  name: 'buildlog'
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-03-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource application 'Microsoft.App/containerApps@2024-03-01' = {
  name: applicationName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        allowInsecure: false
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: 'system'
        }
      ]
      secrets: concat([
        {
          name: 'web-api-key'
          value: webApiKey
        }
        {
          name: 'database-url'
          value: databaseUrl
        }
      ], empty(llmApiKey) ? [] : [
        {
          name: 'llm-api-key'
          value: llmApiKey
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'buildlog'
          image: containerImage
          env: concat([
            {
              name: 'BUILDLOG_ENV'
              value: 'production'
            }
            {
              name: 'BUILDLOG_WEB_API_KEY'
              secretRef: 'web-api-key'
            }
            {
              name: 'BUILDLOG_MODEL'
              value: llmModel
            }
            {
              name: 'BUILDLOG_API_BASE'
              value: llmApiBase
            }
            {
              name: 'BUILDLOG_DATABASE_URL'
              secretRef: 'database-url'
            }
            {
              name: 'BUILDLOG_RUNS_DIR'
              value: '/tmp/buildlog/runs'
            }
            {
              name: 'BUILDLOG_WEB_JOBS_DIR'
              value: '/tmp/buildlog/jobs'
            }
            {
              name: 'BUILDLOG_PROMPTS_DIR'
              value: '/app/prompts'
            }
            {
              name: 'BUILDLOG_SCHEMA_MANAGEMENT'
              value: 'migrations'
            }
            {
              name: 'BUILDLOG_OBJECT_STORE_BACKEND'
              value: 'azure_blob'
            }
            {
              name: 'BUILDLOG_AZURE_STORAGE_ACCOUNT_URL'
              value: 'https://${storage.name}.blob.core.windows.net'
            }
            {
              name: 'BUILDLOG_AZURE_STORAGE_CONTAINER'
              value: artifactContainer.name
            }
            {
              name: 'BUILDLOG_TRUST_AZURE_AUTH'
              value: 'false'
            }
          ], empty(llmApiKey) ? [] : [
            {
              name: 'OPENAI_API_KEY'
              secretRef: 'llm-api-key'
            }
          ])
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
              }
              initialDelaySeconds: 15
              periodSeconds: 20
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
              }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 1
        rules: []
      }
    }
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, application.id, 'Storage Blob Data Contributor')
  scope: storage
  properties: {
    principalId: application.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

resource registryPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, application.id, 'AcrPull')
  scope: registry
  properties: {
    principalId: application.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

output applicationUrl string = 'https://${application.properties.configuration.ingress.fqdn}'
output applicationName string = application.name
output storageAccountName string = storage.name
output registryName string = registry.name
output registryServer string = registry.properties.loginServer
output postgresServerName string = postgres.name
