// Training log on Azure: Container Apps (scale to zero) + PostgreSQL Flexible Server (B1ms)
// + Log Analytics + a monthly cost budget. Deployed into an existing resource group by
// .github/workflows/deploy.yml; safe to re-run (idempotent).
//
// Secrets never pass through this template or GitHub: the Key Vault, its secrets and the
// app's managed identity are created once by scripts/azure_bootstrap.sh, and the container
// app reads the secrets from Key Vault with that identity.

// Sweden Central rather than the resource group's region (Germany West Central): there, Postgres
// Flexible Server is restricted for this subscription and Container Apps had no capacity, and
// West Europe is not accepting new customers (Oct 2026).
// The Key Vault and managed identity from the bootstrap stay in the resource group's region.
param location string = 'swedencentral'
param appName string = 'training-log'

@description('Container image, e.g. ghcr.io/owner/repo:sha')
param image string

@description('Key Vault created by scripts/azure_bootstrap.sh')
param keyVaultName string
@description('User-assigned identity created by scripts/azure_bootstrap.sh, with read access to the vault')
param identityName string = 'id-${appName}'

@description('Expiry date of the Garmin API key (YYYY-MM-DD), shown as a warning on the dashboard')
param garminKeyExpires string = ''

@description('Email address for budget alerts')
param budgetEmail string
param budgetAmount int = 25
@description('First day of the month the budget starts; must stay fixed once created')
param budgetStartDate string = '2026-10-01'

@description('Optional: your public IP, to reach the database from your laptop (e.g. for the MCP server)')
param clientIpAddress string = ''

var suffix = uniqueString(resourceGroup().id)
var pgAdmin = 'garminadmin'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: identityName
}

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${appName}-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.2') } // stays inside the 5 GB/month free allowance
  }
}

module postgres 'postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    name: 'pg-${appName}-${suffix}'
    adminLogin: pgAdmin
    databaseName: 'garmin'
    adminPassword: kv.getSecret('postgres-admin-password')
    clientIpAddress: clientIpAddress
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${appName}-${suffix}'
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

// Container App secret name -> Key Vault secret name. Unversioned references always resolve
// to the latest version; restart the revision after changing a secret to pick it up.
var kvSecrets = [
  'postgres-admin-password'
  'garmin-api-key'
  'google-client-id'
  'google-client-secret'
  'allowed-email'
  'session-secret'
]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [for s in kvSecrets: {
        name: s
        keyVaultUrl: '${kv.properties.vaultUri}secrets/${s}'
        identity: identity.id
      }]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: image
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          env: [
            { name: 'APP_ENV', value: 'production' }
            { name: 'GARMIN_KEY_EXPIRES', value: garminKeyExpires }
            { name: 'POSTGRES_HOST', value: postgres.outputs.host }
            { name: 'POSTGRES_USER', value: pgAdmin }
            { name: 'POSTGRES_DB', value: postgres.outputs.databaseName }
            { name: 'POSTGRES_PASSWORD', secretRef: 'postgres-admin-password' }
            { name: 'GARMIN_API_KEY', secretRef: 'garmin-api-key' }
            { name: 'GOOGLE_CLIENT_ID', secretRef: 'google-client-id' }
            { name: 'GOOGLE_CLIENT_SECRET', secretRef: 'google-client-secret' }
            { name: 'ALLOWED_EMAIL', secretRef: 'allowed-email' }
            { name: 'SESSION_SECRET', secretRef: 'session-secret' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/login', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // scale to zero: no cost when idle, ~5-15 s cold start
        maxReplicas: 1
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '20' } } }
        ]
      }
    }
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-05-01' = {
  name: 'budget-${appName}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [budgetEmail]
      }
      forecast100: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: [budgetEmail]
      }
    }
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output postgresHost string = postgres.outputs.host
