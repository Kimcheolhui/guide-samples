targetScope = 'subscription'

@description('Azure region for all resources')
param location string = 'swedencentral'

@description('Resource group name')
param resourceGroupName string = 'rg-localdns-test'

@description('AKS cluster name')
param clusterName string = 'aks-localdns-test'

@description('Kubernetes version')
param kubernetesVersion string = '1.33.7'

@description('System node pool VM size')
param systemNodeVmSize string = 'Standard_D16as_v6'

@description('System node pool count')
param systemNodeCount int = 2

@description('User node pool VM size')
param userNodeVmSize string = 'Standard_D16as_v6'

@description('User node pool count')
param userNodeCount int = 5

@description('Max pods per node (user pool)')
param maxPods int = 60

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module aks 'aks.bicep' = {
  name: 'aks-deployment'
  scope: rg
  params: {
    location: location
    clusterName: clusterName
    kubernetesVersion: kubernetesVersion
    systemNodeVmSize: systemNodeVmSize
    systemNodeCount: systemNodeCount
    userNodeVmSize: userNodeVmSize
    userNodeCount: userNodeCount
    maxPods: maxPods
  }
}

output AZURE_AKS_CLUSTER_NAME string = aks.outputs.clusterName
output AZURE_RESOURCE_GROUP string = rg.name
