// PostgreSQL Flexible Server. A separate module so main.bicep can pass the admin password
// straight from Key Vault (getSecret only works for module parameters).

param location string
param name string
param adminLogin string
param databaseName string
@secure()
param adminPassword string
@description('Optional: a public IP allowed to connect (e.g. your laptop for the MCP server)')
param clientIpAddress string = ''

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: name
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' } // covered by the 12-month free account offer
  properties: {
    version: '16'
    administratorLogin: adminLogin
    administratorLoginPassword: adminPassword
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' } // TLS is required; the firewall limits who can connect
  }
}

resource db 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: databaseName
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

// 0.0.0.0 is Azure's special rule for "services inside Azure", which covers Container Apps.
resource allowAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource allowClient 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = if (!empty(clientIpAddress)) {
  parent: pg
  name: 'AllowClientIp'
  properties: { startIpAddress: clientIpAddress, endIpAddress: clientIpAddress }
}

output host string = pg.properties.fullyQualifiedDomainName
output databaseName string = db.name
