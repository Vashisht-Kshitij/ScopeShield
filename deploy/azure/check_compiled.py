"""Release-specific security policy checks on ACTUAL Bicep compiler output.

These do not simulate Azure, reserve capacity, or establish account eligibility.
"""
import argparse
import json
from pathlib import Path


def check(template):
    def require(condition, message):
        if not condition: raise ValueError(message)
    resources = template['resources']
    require(len(resources) == 2, 'Only resource group plus host module permitted')
    require(template['parameters']['location']['allowedValues'] == ['indonesiacentral'], 'Blocked/unreviewed region')
    require(template['parameters']['sshPublicKey']['type'] == 'securestring' and 'defaultValue' not in template['parameters']['sshPublicKey'], 'Unsafe SSH key parameter')
    require('defaultValue' not in template['parameters']['imageVersion'], 'Image version must be supplied explicitly')
    module = next(r for r in resources if r['type'] == 'Microsoft.Resources/deployments')
    require(module['properties']['mode'] == 'Incremental', 'Destructive deployment mode')
    host = module['properties']['template']
    allowed = {'Microsoft.Network/networkSecurityGroups', 'Microsoft.Network/virtualNetworks',
               'Microsoft.Network/publicIPAddresses', 'Microsoft.Network/networkInterfaces',
               'Microsoft.Compute/disks', 'Microsoft.Compute/virtualMachines', 'Microsoft.Storage/storageAccounts',
               'Microsoft.Storage/storageAccounts/blobServices', 'Microsoft.Storage/storageAccounts/blobServices/containers', 'Microsoft.Authorization/roleAssignments'}
    rows = host['resources']
    require(len(rows) == 14 and {r['type'] for r in rows} == allowed, 'Unexpected/expensive resource')
    by_type = {r['type']: r for r in rows}
    expected_names = dict(zip(['Microsoft.Network/networkSecurityGroups', 'Microsoft.Network/virtualNetworks',
                              'Microsoft.Network/publicIPAddresses', 'Microsoft.Network/networkInterfaces',
                              'Microsoft.Compute/disks', 'Microsoft.Compute/virtualMachines'],
                             ['scopeshield-nsg', 'scopeshield-vnet', 'scopeshield-public-ip', 'scopeshield-nic', 'scopeshield-data', 'scopeshield-vm']))
    require(all(by_type[k]['name'] == v for k, v in expected_names.items()), 'Unexpected resource names')
    require(template['variables']['groupName'] == 'scopeshield-rg', 'Wrong resource group')
    vm = by_type['Microsoft.Compute/virtualMachines']['properties']
    require(vm['hardwareProfile']['vmSize'] == 'Standard_B2als_v2' and vm['priority'] == 'Regular', 'Wrong size or Spot')
    require('zones' not in by_type['Microsoft.Compute/virtualMachines'] and 'availabilitySet' not in vm, 'Unexpected redundancy')
    require(host['parameters']['administratorUsername']['defaultValue'] == 'azureuser', 'Wrong administrator')
    require(vm['osProfile']['linuxConfiguration']['disablePasswordAuthentication'] is True, 'Password SSH enabled')
    require(vm['osProfile']['allowExtensionOperations'] is True and vm['osProfile']['linuxConfiguration']['provisionVMAgent'] is True and 'customData' not in vm['osProfile'], 'Run Command agent must be enabled without automatic bootstrap')
    require(by_type['Microsoft.Compute/virtualMachines']['identity'] == {'type': 'SystemAssigned'}, 'Use only the VM managed identity')
    require(all(t['parameters']['securityType']['defaultValue'] == 'TrustedLaunch' and t['parameters']['securityType']['allowedValues'] == ['TrustedLaunch'] for t in (host,template)), 'Trusted launch must be enforced')
    require(host['variables']['trustedProfile'] == {'securityType': 'TrustedLaunch', 'uefiSettings': {'secureBootEnabled': True, 'vTpmEnabled': True}}, 'Missing boot safeguards')
    require(vm['securityProfile'] == "[union(variables('trustedProfile'), createObject('securityType', parameters('securityType')))]", 'Unreviewed VM boot profile')
    require(vm['diagnosticsProfile']['bootDiagnostics'] == {'enabled': True}, 'Use only free managed boot diagnostics, no custom account')
    storage = vm['storageProfile']; os_disk = storage['osDisk']; data = storage['dataDisks']
    require(storage['imageReference'] == {'publisher': 'Canonical', 'offer': 'ubuntu-24_04-lts', 'sku': 'server', 'version': "[parameters('imageVersion')]"}, 'Wrong/unpinned image')
    require(os_disk['diskSizeGB'] == 30 and os_disk['managedDisk'] == {'storageAccountType': 'Premium_LRS'} and os_disk['deleteOption'] == 'Delete', 'OS must use the 32-GiB P4 billing tier; no paid tier uplift')
    require(len(data) == 1 and data[0]['lun'] == 0 and data[0]['deleteOption'] == 'Detach' and data[0]['caching'] == 'None', 'Unsafe data disk attachment')
    disk = by_type['Microsoft.Compute/disks']
    require(disk['name'] == 'scopeshield-data' and disk['sku']['name'] == 'Premium_LRS' and disk['properties']['diskSizeGB'] == 32 and disk['properties']['tier'] == 'P4' and disk['properties']['burstingEnabled'] is False, 'Wrong data disk or paid bursting')
    require(disk['properties']['encryption']['type'] == 'EncryptionAtRestWithPlatformKey' and disk['properties']['networkAccessPolicy'] == 'DenyAll' and disk['properties']['publicNetworkAccess'] == 'Disabled', 'Unsafe data disk encryption/export')
    ip = by_type['Microsoft.Network/publicIPAddresses']
    require(ip['sku'] == {'name': 'Standard', 'tier': 'Regional'} and ip['properties']['publicIPAllocationMethod'] == 'Static' and ip['properties']['publicIPAddressVersion'] == 'IPv4', 'Wrong public IP')
    require(all(t['parameters']['administratorSshCidr']['defaultValue'] == '' and t['parameters']['administratorSshCidr']['allowedValues'] == [''] for t in (host, template)), 'SSH must remain closed')
    web = host['variables']['webRules']
    require({r['properties']['destinationPortRange'] for r in web} == {'80', '443'} and len(web) == 2, 'Unexpected public application/SSH port')
    require(all(r['properties']['access'] == 'Allow' and r['properties']['direction'] == 'Inbound' and r['properties']['protocol'] == 'Tcp' and r['properties']['sourceAddressPrefix'] == 'Internet' for r in web), 'Unexpected web NSG behavior')
    rules = by_type['Microsoft.Network/networkSecurityGroups']['properties']['securityRules']
    require(rules == "[concat(variables('webRules'), variables('denyRules'))]", 'Unreviewed NSG expression')
    require(host['variables']['denyRules'] == [{'name': 'deny-other-inbound', 'properties': {'priority': 4000, 'access': 'Deny', 'direction': 'Inbound', 'protocol': '*', 'sourceAddressPrefix': '*', 'sourcePortRange': '*', 'destinationAddressPrefix': '*', 'destinationPortRange': '*'}}], 'Missing explicit deny before Azure default VNet allows')
    subnet = by_type['Microsoft.Network/virtualNetworks']['properties']['subnets'][0]['properties']
    require(subnet['serviceEndpoints'] == [{'service': 'Microsoft.Storage', 'locations': ["[parameters('location')]"]}] and subnet['defaultOutboundAccess'] is False, 'Missing same-region Storage service endpoint')
    require(host['variables']['storageSubnetId'] == "[format('{0}/subnets/scopeshield-subnet', resourceId('Microsoft.Network/virtualNetworks', 'scopeshield-vnet'))]", 'Backup network must be the exact VM subnet')
    require(all(t['parameters']['operatorStorageCidr']['defaultValue'] == '' for t in (host,template)), 'Operator storage IP must remain unset by default')
    check_storage(host, require)
    return {'policy': 'passed', 'hostResources': len(rows), 'publicPorts': [80, 443], 'sshDefault': 'closed', 'dataDiskDeleteOption': 'Detach', 'storageFirewall': 'Deny except subnet and optional operator /32', 'containerRoleAssignments': 4}


def check_storage(template, require):
    rows = [r for r in template['resources'] if r['type'].startswith(('Microsoft.Storage/', 'Microsoft.Authorization/'))]
    from collections import Counter
    require(Counter(r['type'] for r in rows) == {'Microsoft.Storage/storageAccounts': 1, 'Microsoft.Storage/storageAccounts/blobServices': 1, 'Microsoft.Storage/storageAccounts/blobServices/containers': 2, 'Microsoft.Authorization/roleAssignments': 4}, 'Unexpected storage resources/roles')
    account = next(r for r in rows if r['type'] == 'Microsoft.Storage/storageAccounts')
    account_expr = "format('scopeshield{0}', uniqueString(subscription().subscriptionId, resourceGroup().name))"
    require(account['name'] == '[' + account_expr + ']', 'Unreviewed storage account name')
    p = account['properties']
    require(account['sku'] == {'name': 'Standard_LRS'} and account['kind'] == 'StorageV2' and p['accessTier'] == 'Hot', 'Wrong/expensive backup storage')
    require(p['supportsHttpsTrafficOnly'] is True and p['minimumTlsVersion'] == 'TLS1_2' and p['allowSharedKeyAccess'] is False and p['allowBlobPublicAccess'] is False and p['defaultToOAuthAuthentication'] is True, 'Unsafe storage authentication')
    acl = p['networkAcls']
    require(p['publicNetworkAccess'] == 'Enabled' and acl['defaultAction'] == 'Deny' and acl['bypass'] == 'None' and acl['virtualNetworkRules'] == [{'id': "[variables('storageSubnetId')]", 'action': 'Allow'}], 'Backup network path blocked or too broad')
    require(acl['ipRules'] == "[if(empty(parameters('operatorStorageCidr')), createArray(), createArray(createObject('value', first(split(parameters('operatorStorageCidr'), '/')), 'action', 'Allow')))]", 'Unreviewed operator firewall rule')
    containers = [r for r in rows if r['type'].endswith('/containers')]
    require(all(r['properties'] == {'publicAccess': 'None'} for r in containers) and {r['name'].split("'")[-2] for r in containers} == {'releases', 'backups'}, 'Containers must be private and exact')
    service = next(r for r in rows if r['type'].endswith('/blobServices'))['properties']
    require(service == {'isVersioningEnabled': False, 'deleteRetentionPolicy': {'enabled': True, 'days': 7}, 'containerDeleteRetentionPolicy': {'enabled': True, 'days': 7}}, 'Backup retention or cost changed')
    require({k:template['variables'][k] for k in ('readerRole','contributorRole')} == {'readerRole': "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1')]", 'contributorRole': "[subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')]"}, 'Unreviewed role definition')
    assignments = [r for r in rows if r['type'] == 'Microsoft.Authorization/roleAssignments']
    actual = {(r['scope'], r['properties']['principalId'], r['properties']['principalType'], r['properties']['roleDefinitionId']) for r in assignments}
    vm_principal = "[reference(resourceId('Microsoft.Compute/virtualMachines', 'scopeshield-vm'), '2025-04-01', 'full').identity.principalId]"
    expected = {("[resourceId('Microsoft.Storage/storageAccounts/blobServices/containers', " + account_expr + ", 'default', '" + container + "')]", principal, kind, "[variables('%s')]" % role) for container, principal, kind, role in [
        ('releases', vm_principal, 'ServicePrincipal', 'readerRole'), ('backups', vm_principal, 'ServicePrincipal', 'contributorRole'),
        ('releases', "[parameters('operatorPrincipalId')]", 'User', 'contributorRole'), ('backups', "[parameters('operatorPrincipalId')]", 'User', 'readerRole')]}
    require(actual == expected and all(r['scope'].startswith("[resourceId('Microsoft.Storage/storageAccounts/blobServices/containers',") for r in assignments), 'Roles must remain scoped to the exact containers and principals')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compiled_template')
    args = parser.parse_args()
    print(json.dumps(check(json.loads(Path(args.compiled_template).read_text(encoding='utf-8')))))
