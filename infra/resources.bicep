// The resources of Pundit Copilot in one resource group (see main.bicep).
param environmentName string
param location string
param foundryLocation string
param speechLocation string
param modelName string
param modelVersion string
param modelCapacity int
param useMockLlm bool
param budgetAmount int
param budgetEmail string
param containerImage string
param tags object

var token = toLower(uniqueString(subscription().id, resourceGroup().id, environmentName))
var appName = 'ca-pundit-${token}'
var modelDeployment = modelName

// Built-in roles given to the app's identity
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  speechUser: 'f2dc8367-1007-4938-bd23-fe263f013447'
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-pundit-${token}'
  location: location
  tags: tags
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-pundit-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-pundit-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'crpundit${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stpundit${take(token, 15)}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // the app uses its identity, never a key
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource audio 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'audio' // recap and commentary clips (docs/day5-design.md section 8.8)
  properties: { publicAccess: 'None' }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-pundit-${take(token, 12)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'spch-pundit-${token}'
  location: speechLocation
  tags: tags
  kind: 'SpeechServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'spch-pundit-${token}' // needed for Entra ID tokens
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: 'ais-pundit-${token}'
  location: foundryLocation
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: 'ais-pundit-${token}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource model 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = {
  parent: foundry
  name: modelDeployment
  sku: { name: 'GlobalStandard', capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-pundit-${token}'
  location: location
  tags: tags
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

var appHost = '${appName}.${environment.properties.defaultDomain}'

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: union(tags, { 'azd-service-name': 'app' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto' // HTTP and WebSocket
      }
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: containerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: [
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'USE_MOCK_LLM', value: useMockLlm ? 'true' : 'false' }
            { name: 'LLM_PROVIDER', value: 'foundry' }
            { name: 'FOUNDRY_ENDPOINT', value: 'https://${foundry.name}.openai.azure.com' }
            { name: 'FOUNDRY_MODEL_DEPLOYMENT', value: modelDeployment }
            { name: 'TTS_PROVIDER', value: 'azure' }
            { name: 'STT_PROVIDER', value: 'azure' }
            { name: 'SPEECH_ENDPOINT', value: 'https://${speech.name}.cognitiveservices.azure.com' }
            { name: 'SPEECH_RESOURCE_ID', value: speech.id }
            { name: 'SPEECH_REGION', value: speechLocation }
            { name: 'AUDIO_STORE', value: 'blob' }
            { name: 'AUDIO_BLOB_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
            { name: 'AUDIO_BLOB_CONTAINER', value: audio.name }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
            { name: 'MCP_ALLOWED_HOSTS', value: appHost }
            { name: 'TRUST_PROXY', value: 'true' }
            { name: 'CORS_ORIGINS', value: 'https://${appHost}' }
          ]
        }
      ]
      // Sessions live in memory (docs/day6-design.md section 6): one replica, always on, so the
      // judges never meet a cold start (claude.md section 6.1)
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull]
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, roles.acrPull)
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource blobAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, roles.blobDataContributor)
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', roles.blobDataContributor
    )
  }
}

resource vaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, roles.keyVaultSecretsUser)
  scope: vault
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser
    )
  }
}

resource modelAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, identity.id, roles.openAiUser)
  scope: foundry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
  }
}

resource speechAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(speech.id, identity.id, roles.speechUser)
  scope: speech
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.speechUser)
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = if (!empty(budgetEmail)) {
  name: 'budget-pundit-${token}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: '2026-10-01' }
    notifications: {
      half: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        contactEmails: [budgetEmail]
      }
      most: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        contactEmails: [budgetEmail]
      }
    }
  }
}

output registryEndpoint string = registry.properties.loginServer
output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
output foundryEndpoint string = 'https://${foundry.name}.openai.azure.com'
output modelDeployment string = model.name
output speechResourceId string = speech.id
