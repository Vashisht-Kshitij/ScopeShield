"""Offline negative tests against the actual compiled ARM graph, never Azure."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

try:
    from .check_compiled import check
except ImportError:
    from check_compiled import check


def verify(template):
    check(template)
    def host(t): return next(r for r in t['resources'] if r['type'] == 'Microsoft.Resources/deployments')['properties']['template']
    def resource(t, kind): return next(r for r in host(t)['resources'] if r['type'] == kind)
    def vm(t): return resource(t,'Microsoft.Compute/virtualMachines')['properties']
    def disk(t): return resource(t,'Microsoft.Compute/disks')
    def storage(t): return resource(t,'Microsoft.Storage/storageAccounts')['properties']
    def role(t): return next(r for r in host(t)['resources'] if r['type'] == 'Microsoft.Authorization/roleAssignments')
    cases = [
        ('blocked region',lambda t:t['parameters']['location'].update(allowedValues=['centralindia'])),
        ('SSH allowed',lambda t:t['parameters']['administratorSshCidr'].update(allowedValues=['8.8.8.8/32'])),
        ('public application port',lambda t:host(t)['variables']['webRules'][0]['properties'].update(destinationPortRange='8000')),
        ('missing inbound deny',lambda t:host(t)['variables'].update(denyRules=[])),
        ('Standard OS disk',lambda t:vm(t)['storageProfile']['osDisk']['managedDisk'].update(storageAccountType='StandardSSD_LRS')),
        ('data deletion',lambda t:vm(t)['storageProfile']['dataDisks'][0].update(deleteOption='Delete')),
        ('paid bursting',lambda t:disk(t)['properties'].update(burstingEnabled=True)),
        ('data export',lambda t:disk(t)['properties'].update(publicNetworkAccess='Enabled')),
        ('broken Run Command',lambda t:vm(t)['osProfile'].update(allowExtensionOperations=False)),
        ('missing VM identity',lambda t:resource(t,'Microsoft.Compute/virtualMachines').update(identity={'type':'None'})),
        ('shared storage keys',lambda t:storage(t).update(allowSharedKeyAccess=True)),
        ('anonymous Blob',lambda t:storage(t).update(allowBlobPublicAccess=True)),
        ('all networks',lambda t:storage(t)['networkAcls'].update(defaultAction='Allow')),
        ('storage endpoint disabled',lambda t:storage(t).update(publicNetworkAccess='Disabled')),
        ('missing subnet rule',lambda t:storage(t)['networkAcls'].update(virtualNetworkRules=[])),
        ('bypass firewall',lambda t:storage(t)['networkAcls'].update(bypass='AzureServices')),
        ('unreviewed storage IP',lambda t:storage(t)['networkAcls'].update(ipRules=[{'value':'0.0.0.0/0','action':'Allow'}])),
        ('broad RBAC scope',lambda t:role(t).update(scope="[subscription().id]")),
        ('wrong RBAC principal',lambda t:role(t)['properties'].update(principalId='unreviewed')),
        ('public container',lambda t:resource(t,'Microsoft.Storage/storageAccounts/blobServices/containers')['properties'].update(publicAccess='Blob')),
        ('extra extension',lambda t:host(t)['resources'].append({'type':'Microsoft.Compute/virtualMachines/extensions'})),
        ('Trusted Launch downgrade',lambda t:t['parameters']['securityType'].update(allowedValues=['TrustedLaunch','Standard'])),
        ('expensive Blob redundancy',lambda t:resource(t,'Microsoft.Storage/storageAccounts')['sku'].update(name='Standard_GRS')),
    ]
    for name, mutate in cases:
        changed = deepcopy(template); mutate(changed)
        try: check(changed)
        except ValueError: continue
        raise AssertionError('Unsafe compiled graph accepted: ' + name)
    return {'actualCompiledGraph':'passed','unsafeMutationsRejected':len(cases),'cloudCalls':0}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('compiled_template')
    args=parser.parse_args()
    print(json.dumps(verify(json.loads(Path(args.compiled_template).read_text(encoding='utf-8')))))
