targetScope = 'subscription'

@description('Azure region for the disposable demo resources.')
param location string = 'eastus2'

@description('Short environment name used in resource names. Use lowercase letters, numbers, and hyphens.')
@minLength(2)
@maxLength(20)
param environmentName string = 'demo'

@description('Dedicated resource group that is safe to delete during teardown.')
param resourceGroupName string = 'rg-dbai-${environmentName}'

@description('Resource group used only for Azure Databricks managed resources.')
param managedResourceGroupName string = 'rg-dbai-${environmentName}-managed'

@description('Azure Databricks workspace name.')
param workspaceName string = 'dbai-${environmentName}'

var tags = {
  Application: 'dbai'
  Environment: environmentName
  ManagedBy: 'bicep'
  Purpose: 'disposable-demo'
}

resource demoResourceGroup 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module databricksWorkspace 'br/public:avm/res/databricks/workspace:0.12.0' = {
  name: 'databricksWorkspace-${environmentName}'
  scope: demoResourceGroup
  params: {
    name: workspaceName
    location: location
    skuName: 'premium'
    defaultCatalog: {
      initialType: 'UnityCatalog'
    }
    managedResourceGroupResourceId: subscriptionResourceId(
      'Microsoft.Resources/resourceGroups',
      managedResourceGroupName
    )
    publicNetworkAccess: 'Enabled'
    tags: tags
  }
}

output resourceGroupName string = demoResourceGroup.name
output managedResourceGroupName string = managedResourceGroupName
output workspaceName string = databricksWorkspace.outputs.name
output workspaceUrl string = databricksWorkspace.outputs.workspaceUrl
