targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the environment')
param environmentName string

@description('Primary location for all resources')
param location string

@description('Azure AI Search SKU')
@allowed(['basic', 'standard', 'standard2', 'standard3'])
param searchSku string = 'standard'

@description('Number of replicas for Azure AI Search')
@minValue(1)
@maxValue(12)
param searchReplicaCount int = 3

@description('Number of partitions for Azure AI Search')
@minValue(1)
@maxValue(12)
param searchPartitionCount int = 1

var abbrs = loadJsonContent('abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: '${abbrs.resourceGroup}${environmentName}'
  location: location
  tags: tags
}

module search 'search.bicep' = {
  name: 'search'
  scope: rg
  params: {
    name: '${abbrs.searchService}${resourceToken}'
    location: location
    tags: tags
    sku: searchSku
    replicaCount: searchReplicaCount
    partitionCount: searchPartitionCount
  }
}

output AZURE_AI_SEARCH_ENDPOINT string = search.outputs.endpoint
output AZURE_AI_SEARCH_NAME string = search.outputs.name
output AZURE_RESOURCE_GROUP string = rg.name
