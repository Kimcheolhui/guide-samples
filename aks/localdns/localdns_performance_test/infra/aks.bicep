@description('Azure region')
param location string

@description('AKS cluster name')
param clusterName string

@description('Kubernetes version')
param kubernetesVersion string

@description('System node pool VM size')
param systemNodeVmSize string

@description('System node pool count')
param systemNodeCount int

@description('User node pool VM size')
param userNodeVmSize string

@description('User node pool count')
param userNodeCount int

@description('Max pods per node (user pool)')
param maxPods int

resource aks 'Microsoft.ContainerService/managedClusters@2025-01-01' = {
  name: clusterName
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: kubernetesVersion
    dnsPrefix: '${clusterName}-dns'
    networkProfile: {
      networkPlugin: 'azure'
      networkPluginMode: 'overlay'
      networkDataplane: 'cilium'
      podCidr: '10.244.0.0/16'
      serviceCidr: '10.0.0.0/16'
      dnsServiceIP: '10.0.0.10'
    }
    agentPoolProfiles: [
      {
        name: 'system'
        count: systemNodeCount
        vmSize: systemNodeVmSize
        osType: 'Linux'
        mode: 'System'
      }
      {
        name: 'userpool'
        count: userNodeCount
        vmSize: userNodeVmSize
        osType: 'Linux'
        mode: 'User'
        maxPods: maxPods
      }
    ]
  }
}

output clusterName string = aks.name
