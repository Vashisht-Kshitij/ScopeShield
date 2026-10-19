"""Explicit read-only Azure catalog/offer/quota verification. No login or mutations.

Writes a sanitized report without account IDs, user IDs, credentials or tokens.
CLI authentication remains opaque; no get-access-token or cache inspection.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

try:
    from .azure_plan import azure, write_new
except ImportError:
    from azure_plan import azure, write_new

LOCATION = 'indonesiacentral'
SIZE = 'Standard_B2als_v2'


def sku_result(rows):
    exact = [r for r in rows if r.get('name') == SIZE and r.get('resourceType') == 'virtualMachines']
    if len(exact) != 1: return {'exactMatchCount': len(exact), 'eligibleByMetadata': False}
    row = exact[0]
    restrictions = row.get('restrictions', [])
    blocked = []
    for restriction in restrictions:
        locations = restriction.get('values', []) + restriction.get('restrictionInfo', {}).get('locations', [])
        if restriction.get('type') == 'Location' and (not locations or LOCATION in [v.lower() for v in locations]):
            blocked.append(restriction)
    capabilities = {v['name']: v['value'] for v in row.get('capabilities', [])}
    return {'exactMatchCount': 1, 'name': row['name'], 'family': row.get('family'),
            'locations': row.get('locations', []), 'restrictions': restrictions,
            'capabilities': {k: capabilities.get(k) for k in ('vCPUs', 'MemoryGB', 'PremiumIO', 'CpuArchitectureType', 'HyperVGenerations', 'TrustedLaunchDisabled')},
            'eligibleByMetadata': LOCATION in [v.lower() for v in row.get('locations', [])] and not blocked and all(r.get('type') in {'Location','Zone'} for r in restrictions),
            'allocationCapacityGuaranteed': False}


def inspect(subscription):
    result = {'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'location': LOCATION,
              'vmSize': SIZE, 'readOnly': True, 'checks': {}, 'errors': [], 'allocationCapacityGuaranteed': False}
    def get(name, args):
        try:
            print('Read-only check: ' + name, flush=True)
            return azure(subscription, args)
        except Exception:
            # Do not expose CLI error bodies, URLs containing account IDs or auth output.
            result['errors'].append(name + ': read failed; no account identifiers or authentication details logged')
            return None
    account = get('subscription', ['rest', '--method', 'get', '--url',
        f'https://management.azure.com/subscriptions/{subscription}?api-version=2022-12-01'])
    if account:
        p = account['subscriptionPolicies']
        result['checks']['subscription'] = {'state': account['state'], 'offer': p.get('quotaId'), 'spendingLimit': p.get('spendingLimit')}
    policy = get('regionPolicy', ['rest', '--method', 'get', '--url',
        f'https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.Authorization/policyAssignments/sys.regionrestriction?api-version=2022-06-01'])
    if policy:
        p = policy['properties']
        values = [v.get('value') for v in p.get('parameters', {}).values()]
        lists = [v for v in values if isinstance(v, list)]
        result['checks']['regionPolicy'] = {'enforcementMode': p.get('enforcementMode', 'Default'), 'locationLists': lists,
                                           'indonesiaListed': any(LOCATION in v for v in lists)}
    for provider in ('Microsoft.Compute', 'Microsoft.Network', 'Microsoft.Storage', 'Microsoft.Resources'):
        value = get(provider, ['provider', 'show', '--namespace', provider])
        if value: result['checks'][provider] = value['registrationState']
    rows = get('sku', ['vm', 'list-skus', '--location', LOCATION, '--resource-type', 'virtualMachines', '--size', SIZE, '--all'])
    if rows is not None: result['checks']['sku'] = sku_result(rows)
    usage = get('quota', ['vm', 'list-usage', '--location', LOCATION])
    if usage is not None:
        family = result['checks'].get('sku', {}).get('family', 'standardBasv2Family').lower()
        result['checks']['quota'] = [{'name': u['name']['value'], 'used': int(u['currentValue']), 'limit': int(u['limit'])}
            for u in usage if u['name']['value'].lower() in {'cores', family}]
    images = get('imageList', ['vm', 'image', 'list', '--location', LOCATION, '--publisher', 'Canonical', '--offer', 'ubuntu-24_04-lts', '--sku', 'server', '--all'])
    if images:
        pinned = max(images, key=lambda x: tuple(int(p) for p in x['version'].split('.')))
        image = get('imageShow', ['vm', 'image', 'show', '--location', LOCATION, '--urn', pinned['urn']])
        if image:
            result['checks']['image'] = {'urn': pinned['urn'], 'version': pinned['version'], 'architecture': image.get('architecture'),
                'hyperVGeneration': image.get('hyperVGeneration'), 'features': image.get('features', []),
                'osDiskSizeGiB': image.get('osDiskImage', {}).get('sizeInGb', image.get('osDiskImage', {}).get('sizeInGB')), 'plan': image.get('plan')}
    for label, role in [('blobReader', '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'), ('blobContributor', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')]:
        roles = get(label, ['role', 'definition', 'list', '--name', role])
        if roles: result['checks'][label] = {'name': roles[0]['roleName'], 'permissions': roles[0]['permissions']}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subscription', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = inspect(args.subscription)
    write_new(Path(args.output), report)
    print(json.dumps(report, indent=2))
