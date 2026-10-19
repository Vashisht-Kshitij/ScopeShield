targetScope = 'resourceGroup'

param location string
@secure()
param sshPublicKey string
param imageVersion string
param administratorUsername string = 'azureuser'
@allowed([''])
param administratorSshCidr string = ''
param operatorPrincipalId string
param operatorStorageCidr string = ''
@allowed(['TrustedLaunch'])
param securityType string = 'TrustedLaunch'

var tags = {
  application: 'ScopeShield'
  purpose: 'offline-judge-demo'
}
var webRules = [{
  name: 'allow-caddy-80'
  properties: {
    priority: 120
    access: 'Allow'
    direction: 'Inbound'
    protocol: 'Tcp'
    sourceAddressPrefix: 'Internet'
    sourcePortRange: '*'
    destinationAddressPrefix: '*'
    destinationPortRange: '80'
  }
}, {
  name: 'allow-caddy-443'
  properties: {
    priority: 130
    access: 'Allow'
    direction: 'Inbound'
    protocol: 'Tcp'
    sourceAddressPrefix: 'Internet'
    sourcePortRange: '*'
    destinationAddressPrefix: '*'
    destinationPortRange: '443'
  }
}]
var trustedProfile = {
  securityType: 'TrustedLaunch'
  uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
}
var denyRules = [{
  name: 'deny-other-inbound'
  properties: {
    priority: 4000
    access: 'Deny'
    direction: 'Inbound'
    protocol: '*'
    sourceAddressPrefix: '*'
    sourcePortRange: '*'
    destinationAddressPrefix: '*'
    destinationPortRange: '*'
  }
}]

resource nsg 'Microsoft.Network/networkSecurityGroups@2024-05-01' = {
  name: 'scopeshield-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: concat(webRules, denyRules)
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'scopeshield-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.42.0.0/16']
    }
    subnets: [{
      name: 'scopeshield-subnet'
      properties: {
        addressPrefix: '10.42.0.0/24'
        defaultOutboundAccess: false
        serviceEndpoints: [{ service: 'Microsoft.Storage', locations: [location] }]
        networkSecurityGroup: { id: nsg.id }
      }
    }]
  }
}

resource publicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: 'scopeshield-public-ip'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Regional' }
  properties: {
    publicIPAllocationMethod: 'Static'
    publicIPAddressVersion: 'IPv4'
    dnsSettings: {
      domainNameLabel: 'scopeshield-${uniqueString(subscription().subscriptionId, resourceGroup().name)}'
    }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: 'scopeshield-nic'
  location: location
  tags: tags
  properties: {
    enableIPForwarding: false
    ipConfigurations: [{
      name: 'primary'
      properties: {
        primary: true
        privateIPAllocationMethod: 'Dynamic'
        subnet: { id: '${vnet.id}/subnets/scopeshield-subnet' }
        publicIPAddress: { id: publicIp.id }
      }
    }]
  }
}

resource dataDisk 'Microsoft.Compute/disks@2025-01-02' = {
  name: 'scopeshield-data'
  location: location
  tags: tags
  sku: { name: 'Premium_LRS' }
  properties: {
    creationData: { createOption: 'Empty' }
    diskSizeGB: 32
    tier: 'P4'
    burstingEnabled: false
    encryption: { type: 'EncryptionAtRestWithPlatformKey' }
    networkAccessPolicy: 'DenyAll'
    publicNetworkAccess: 'Disabled'
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2025-04-01' = {
  name: 'scopeshield-vm'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: 'Standard_B2als_v2' }
    priority: 'Regular'
    securityProfile: union(trustedProfile, { securityType: securityType })
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: imageVersion
      }
      diskControllerType: 'SCSI'
      osDisk: {
        name: 'scopeshield-os'
        createOption: 'FromImage'
        diskSizeGB: 30
        caching: 'ReadWrite'
        deleteOption: 'Delete'
        // P4 at 30 GiB allocation (32 GiB billing tier). Paid on-demand
        // bursting is unsupported at <=512 GiB; no paid tier uplift is set.
        managedDisk: { storageAccountType: 'Premium_LRS' }
      }
      dataDisks: [{
        lun: 0
        name: dataDisk.name
        createOption: 'Attach'
        caching: 'None'
        deleteOption: 'Detach'
        managedDisk: { id: dataDisk.id }
      }]
    }
    osProfile: {
      computerName: 'scopeshield-vm'
      adminUsername: administratorUsername
      // Action Run Command requires the agent's extension handler. This does
      // not declare or execute an extension/Run Command in the deployment.
      allowExtensionOperations: true
      linuxConfiguration: {
        disablePasswordAuthentication: true
        provisionVMAgent: true
        ssh: {
          publicKeys: [{ path: '/home/azureuser/.ssh/authorized_keys', keyData: sshPublicKey }]
        }
        patchSettings: { patchMode: 'ImageDefault', assessmentMode: 'ImageDefault' }
      }
    }
    networkProfile: {
      networkInterfaces: [{ id: nic.id, properties: { primary: true, deleteOption: 'Detach' } }]
    }
    // Managed boot logs currently incur no storage charge and allow out-of-band
    // first-connection SSH host-fingerprint verification. No storageUri/account.
    diagnosticsProfile: { bootDiagnostics: { enabled: true } }
  }
}

// Keep role principal references inside this template so what-if can expand
// storage without passing a not-yet-created principal through a module boundary.
var storageSubnetId = '${vnet.id}/subnets/scopeshield-subnet'

var readerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')
var contributorRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')

resource account 'Microsoft.Storage/storageAccounts@2025-01-01' = {
  name: 'scopeshield${uniqueString(subscription().subscriptionId, resourceGroup().name)}'
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    // Selected-network public endpoint, NOT a billed Private Endpoint.
    // Disabling publicNetworkAccess would also block this service-endpoint design.
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
      virtualNetworkRules: [{ id: storageSubnetId, action: 'Allow' }]
      ipRules: empty(operatorStorageCidr) ? [] : [{ value: first(split(operatorStorageCidr, '/')), action: 'Allow' }]
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      services: { blob: { enabled: true, keyType: 'Account' } }
    }
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2025-01-01' = {
  parent: account
  name: 'default'
  properties: {
    isVersioningEnabled: false
    deleteRetentionPolicy: { enabled: true, days: 7 }
    containerDeleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource releases 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobs
  name: 'releases'
  properties: { publicAccess: 'None' }
}
resource backups 'Microsoft.Storage/storageAccounts/blobServices/containers@2025-01-01' = {
  parent: blobs
  name: 'backups'
  properties: { publicAccess: 'None' }
}

resource vmReleaseReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(releases.id, vm.id, readerRole)
  scope: releases
  properties: { principalId: vm.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: readerRole }
}
resource vmBackupContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backups.id, vm.id, contributorRole)
  scope: backups
  properties: { principalId: vm.identity.principalId, principalType: 'ServicePrincipal', roleDefinitionId: contributorRole }
}
resource operatorReleaseContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(releases.id, operatorPrincipalId, contributorRole)
  scope: releases
  properties: { principalId: operatorPrincipalId, principalType: 'User', roleDefinitionId: contributorRole }
}
resource operatorBackupReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(backups.id, operatorPrincipalId, readerRole)
  scope: backups
  properties: { principalId: operatorPrincipalId, principalType: 'User', roleDefinitionId: readerRole }
}


output hostname string = publicIp.properties.dnsSettings.fqdn
output publicIpAddress string = publicIp.properties.ipAddress
output dataDiskId string = dataDisk.id
output sshEnabled bool = !empty(administratorSshCidr)
output storageAccountName string = account.name
