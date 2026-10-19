targetScope = 'subscription'

@description('Policy-permitted Indonesia Central candidate; allocation is not guaranteed.')
@allowed(['indonesiacentral'])
param location string = 'indonesiacentral'

@secure()
@description('Existing SSH PUBLIC key only. Never supply a private key.')
@minLength(40)
param sshPublicKey string

@description('Pinned Canonical Ubuntu 24.04 image version, obtained read-only. Never latest.')
@minLength(8)
param imageVersion string

@description('Must remain empty. This release does not permit SSH, even from an administrator IP.')
@allowed([''])
param administratorSshCidr string = ''

@description('Existing operator Entra USER object UUID for container-scoped data access; never credentials.')
param operatorPrincipalId string

@description('Optional public operator IPv4 /32 for Blob HTTPS only. Empty blocks operator transfers.')
param operatorStorageCidr string = ''

@description('Trusted Launch only; unsupported image/size must stop for review.')
@allowed(['TrustedLaunch'])
param securityType string = 'TrustedLaunch'

var groupName = 'scopeshield-rg'

resource group 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: groupName
  location: location
  tags: {
    application: 'ScopeShield'
    purpose: 'offline-judge-demo'
    accessRequiredThrough: '2026-12-15T16:00:00Z'
    retentionReview: '2026-12-19T00:00:00Z'
  }
}

module host './host.bicep' = {
  name: 'scopeshield-host'
  scope: group
  params: {
    location: location
    sshPublicKey: sshPublicKey
    imageVersion: imageVersion
    administratorSshCidr: administratorSshCidr
    securityType: securityType
    operatorPrincipalId: operatorPrincipalId
    operatorStorageCidr: operatorStorageCidr
  }
}

output resourceGroupName string = group.name
output hostname string = host.outputs.hostname
output publicIpAddress string = host.outputs.publicIpAddress
output dataDiskId string = host.outputs.dataDiskId
output sshEnabled bool = host.outputs.sshEnabled
output storageAccountName string = host.outputs.storageAccountName
