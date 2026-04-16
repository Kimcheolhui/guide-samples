@description('Name of the Azure AI Search service')
param name string

@description('Location for the resource')
param location string

@description('Tags for the resource')
param tags object = {}

@description('SKU for Azure AI Search')
@allowed(['basic', 'standard', 'standard2', 'standard3'])
param sku string = 'standard'

@description('Number of replicas')
@minValue(1)
@maxValue(12)
param replicaCount int = 2

@description('Number of partitions')
@minValue(1)
@maxValue(12)
param partitionCount int = 1

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    replicaCount: replicaCount
    partitionCount: partitionCount
    hostingMode: 'default'
  }
}

output id string = search.id
output name string = search.name
output endpoint string = 'https://${search.name}.search.windows.net'
