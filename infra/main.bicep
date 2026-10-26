// Training log on Azure: Container Apps (scale to zero) + PostgreSQL Flexible Server (B1ms)
// + Log Analytics + a monthly cost budget. Deployed into an existing resource group by
// .github/workflows/deploy.yml; safe to re-run (idempotent).

param location string = resourceGroup().location
param appName string = 'training-log'

@description('Container image, e.g. ghcr.io/owner/repo:sha')
param image string

@secure()
param postgresAdminPassword string
@secure()
param garminApiKey string
@description('Expiry date of the Garmin API key (YYYY-MM-DD), shown as a warning on the dashboard')
param garminKeyExpires string = ''
@secure()
param googleClientId string
@secure()
param googleClientSecret string
@secure()
param allowedEmail string
@secure()
param sessionSecret string

@description('Email address for budget alerts')
param budgetEmail string
param budgetAmount int = 25
@description('First day of the month the budget starts; must stay fixed once created')
param budgetStartDate string = '2026-10-01'

@description('Optional: your public IP, to reach the database from your laptop (e.g. for the MCP server)')
param clientIpAddress string = ''

var suffix = uniqueString(resourceGroup().id)
var pgAdmin = 'garminadmin'
var dbName = 'garmin'

resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: 'log-${appName}-${suffix}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json('0.2') } // stays inside the 5 GB/month free allowance
  }
}

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'pg-${appName}-${suffix}'
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    administratorLogin: pgAdmin
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' } // TLS is required; firewall limits who can connect
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: dbName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// 0.0.0.0 is Azure's special rule for "services inside Azure", which covers Container Apps.
resource pgAllowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource pgAllowClient 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (!empty(clientIpAddress)) {
  parent: pg
  name: 'AllowClientIp'
  properties: { startIpAddress: clientIpAddress, endIpAddress: clientIpAddress }
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

var databaseUrl = 'postgresql+psycopg://${pgAdmin}:${postgresAdminPassword}@${pg.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
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
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'garmin-api-key', value: garminApiKey }
        { name: 'google-client-id', value: googleClientId }
        { name: 'google-client-secret', value: googleClientSecret }
        { name: 'allowed-email', value: allowedEmail }
        { name: 'session-secret', value: sessionSecret }
      ]
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
            { name: 'DATABASE_URL', secretRef: 'database-url' }
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
  dependsOn: [pgDb, pgAllowAzure]
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
output postgresHost string = pg.properties.fullyQualifiedDomainName
