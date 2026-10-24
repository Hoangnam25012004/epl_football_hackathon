// Pundit Copilot on Azure (claude.md section 6.1): one resource group with the app and what it
// uses. `azd up` runs this at subscription scope, then deploys the image (azure.yaml).
// No key is created for the app: it reaches Foundry, Speech and Storage with its managed identity.
targetScope = 'subscription'

@minLength(1)
@maxLength(32)
@description('azd environment name; resource names derive from it')
param environmentName string

@description('Region of the app, storage, registry and monitoring')
param location string = 'southeastasia'

@description('Region of the Foundry resource: one with the model available (East US 2, Sweden Central)')
param foundryLocation string = 'eastus2'

@description('Region of Azure AI Speech (close to Vietnamese viewers)')
param speechLocation string = 'southeastasia'

@description('Model deployed on Foundry for the agents')
param modelName string = 'gpt-4.1-mini'
param modelVersion string = '2025-04-14'

@description('Thousands of tokens per minute of the model deployment')
param modelCapacity int = 30

@description('Run the agents on the mock model (no Foundry call), e.g. while the quota is pending')
param useMockLlm bool = false

@description('Monthly budget in the subscription currency; alerts at 50 % and 80 %')
param budgetAmount int = 50

@description('Where the budget alerts go; empty: no budget')
param budgetEmail string = ''

@description('Image of the first revision; azd deploys the real one')
param containerImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

var tags = { 'azd-env-name': environmentName, project: 'pundit-copilot', data: 'synthetic' }

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: group
  params: {
    environmentName: environmentName
    location: location
    foundryLocation: foundryLocation
    speechLocation: speechLocation
    modelName: modelName
    modelVersion: modelVersion
    modelCapacity: modelCapacity
    useMockLlm: useMockLlm
    budgetAmount: budgetAmount
    budgetEmail: budgetEmail
    containerImage: containerImage
    tags: tags
  }
}

output AZURE_RESOURCE_GROUP string = group.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryEndpoint
output SERVICE_APP_URL string = resources.outputs.appUrl
output FOUNDRY_ENDPOINT string = resources.outputs.foundryEndpoint
output FOUNDRY_MODEL_DEPLOYMENT string = resources.outputs.modelDeployment
output SPEECH_RESOURCE_ID string = resources.outputs.speechResourceId
