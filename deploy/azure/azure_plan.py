"""Local compilation and explicit, separate Azure preview/provisioning gates.

No default action, login, provider registration, subscription update or SSH-key
generation. Generated artifacts must remain under the ignored repository .local/.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import uuid

try:
    from .check_compiled import check as check_policy
    from .costs import estimate as estimate_cost
except ImportError:  # Direct operator CLI invocation.
    from check_compiled import check as check_policy
    from costs import estimate as estimate_cost

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BICEP_HASHES = {
    '398af294cf16ac4becdbd008a77d148d0e8e91492309a6b60ec911b7e1cab082',
    'b09ec25a9d376c1f8e33ede6ed22b587f915ad68488d5db77a6f9541748c7f6e',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def private_output(path):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT / '.local').resolve()):
        raise ValueError('Generated artifacts must be inside the ignored repository .local directory')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_new(path, value):
    path = private_output(path)
    with path.open('x', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        json.dump(value, handle, indent=2)
        handle.write('\n')


def checked(args, *, env=None):
    result = subprocess.run([str(x) for x in args], check=True, capture_output=True, text=True, env=env, timeout=600)
    return result.stdout


def compile_template(compiler):
    compiler = Path(compiler).resolve(strict=True)
    if hashlib.sha256(compiler.read_bytes()).hexdigest() not in BICEP_HASHES:
        raise ValueError('Use the SHA-256-verified official Bicep 0.48.1 binary documented in Day 16')
    directory = private_output(ROOT / '.local/day16' / ('build-' + uuid.uuid4().hex))
    directory.mkdir()
    environment = dict(os.environ, DOTNET_BUNDLE_EXTRACT_BASE_DIR=str(ROOT / '.local/day16/dotnet'))
    version = checked([compiler, '--version'], env=environment).strip()
    output = directory / 'main.json'
    result = subprocess.run([str(compiler), 'build', str(HERE / 'main.bicep'), '--no-restore', '--outfile', str(output)],
                            capture_output=True, text=True, env=environment)
    (directory / 'compile.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or ': Warning ' in result.stderr:
        raise ValueError('Compiler/linter diagnostics require review: ' + str(directory / 'compile.log'))
    template = json.loads(output.read_text(encoding='utf-8'))
    check_policy(template)
    return template, version, output


def public_key(path):
    path = Path(path)
    if path.suffix != '.pub' or path.stat().st_size > 16384:
        raise ValueError('Supply an existing .pub PUBLIC key file only')
    return validate_public_key(path.read_text(encoding='ascii').strip())


def validate_public_key(raw):
    if '\n' in raw or '\r' in raw or 'PRIVATE' in raw:
        raise ValueError('Expected one OpenSSH public-key line')
    pieces = raw.split()
    if len(pieces) < 2 or pieces[0] != 'ssh-ed25519':
        raise ValueError('This reviewed plan accepts an existing Ed25519 public key only')
    binary = base64.b64decode(pieces[1], validate=True)
    expected_header = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(binary) != len(expected_header) + 32 or not binary.startswith(expected_header):
        raise ValueError('Invalid Ed25519 public-key encoding')
    # Comments can identify a workstation/person and are unnecessary for deployment.
    return pieces[0] + ' ' + pieces[1]


def validate_parameters(document):
    parameters = document['parameters']
    expected = {'location', 'imageVersion', 'administratorSshCidr', 'securityType', 'operatorPrincipalId', 'operatorStorageCidr'}
    if set(parameters) != expected or any(set(v) != {'value'} for v in parameters.values()):
        raise ValueError('Unexpected parameter or secret/reference; use the supplied non-secret example')
    values = {k: v['value'] for k, v in parameters.items()}
    if values['location'] != 'indonesiacentral' or values['securityType'] != 'TrustedLaunch':
        raise ValueError('Unreviewed region/security type')
    if not re.fullmatch(r'\d+\.\d+\.\d+', values['imageVersion']):
        raise ValueError('Pin a real Canonical image version obtained read-only; latest/placeholders are refused')
    if values['administratorSshCidr'] != '':
        raise ValueError('SSH must remain closed in this release')
    if str(uuid.UUID(values['operatorPrincipalId'])) != values['operatorPrincipalId'].lower() or uuid.UUID(values['operatorPrincipalId']).int == 0:
        raise ValueError('Existing operator User object UUID required')
    cidr = values['operatorStorageCidr']
    if cidr:
        network = ipaddress.ip_network(cidr, strict=True)
        if network.version != 4 or network.prefixlen != 32 or not network.is_global or str(network) != cidr:
            raise ValueError('Blob operator access needs one canonical public IPv4 /32; never all public networks')
    return parameters


def source_hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(HERE.rglob('*')) if p.is_file() and p.suffix in {'.py', '.bicep', '.sh', '.json', '.service', '.timer'}
            and '__pycache__' not in p.parts}


def prepare(compiler, parameters, key, output):
    template, version, _ = compile_template(compiler)
    params = validate_parameters(json.loads(Path(parameters).read_text(encoding='utf-8')))
    params['sshPublicKey'] = {'value': public_key(key)}
    payload = {'template': template, 'parameters': params, 'sources': source_hashes(), 'compiler': version}
    result = {'planSha256': digest(payload), 'payload': payload}
    write_new(output, result)
    return result['planSha256']


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    if digest(plan['payload']) != plan['planSha256'] or plan['payload']['sources'] != source_hashes():
        raise ValueError('Plan/source changed; prepare and review a fresh plan')
    params = plan['payload']['parameters']
    validate_parameters({'parameters': {k: v for k, v in params.items() if k != 'sshPublicKey'}})
    validate_public_key(params['sshPublicKey']['value'])
    check_policy(plan['payload']['template'])
    return plan


def azure(subscription, args):
    command = shutil.which('az')
    if not command:
        raise ValueError('Azure CLI is not installed; do not install/login/provision implicitly')
    if str(uuid.UUID(subscription)) != subscription.lower():
        raise ValueError('Supply the intended subscription UUID explicitly')
    return json.loads(checked([command, *args, '--subscription', subscription, '--output', 'json', '--only-show-errors']) or '{}')


def preflight(subscription):
    state = azure(subscription, ['rest', '--method', 'get', '--url',
        f'https://management.azure.com/subscriptions/{subscription}?api-version=2022-12-01'])
    if state['state'] != 'Enabled' or state['subscriptionPolicies']['spendingLimit'] != 'On':
        raise ValueError('Enabled student subscription with spending limit On required; never remove the limit')
    if state['subscriptionPolicies'].get('quotaId') != 'AzureForStudents_2018-01-01':
        raise ValueError('Verify the existing student offer; no trial or upgrade is assumed')
    for provider in ('Microsoft.Compute', 'Microsoft.Network', 'Microsoft.Storage', 'Microsoft.Resources', 'Microsoft.Authorization'):
        result = azure(subscription, ['provider', 'show', '--namespace', provider])
        if result['registrationState'] != 'Registered':
            raise ValueError('Required provider is not registered; registration is a separate unapproved change')
    policy = azure(subscription, ['rest', '--method', 'get', '--url',
        f'https://management.azure.com/subscriptions/{subscription}/providers/Microsoft.Authorization/policyAssignments/sys.regionrestriction?api-version=2022-06-01'])
    values = [p.get('value') for p in policy['properties'].get('parameters', {}).values()]
    if not any(isinstance(v,list) and 'indonesiacentral' in [x.lower() for x in v] for v in values):
        raise ValueError('Indonesia must remain in the existing enforced region policy; never bypass it')
    # Credit coverage, SKU, pinned image, quotas and capacity require separate review.


def parameters_file(plan, directory):
    template, params = directory / 'template.json', directory / 'parameters.json'
    write_new(template, plan['payload']['template'])
    write_new(params, {'$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#',
                       'contentVersion': '1.0.0.0', 'parameters': plan['payload']['parameters']})
    return template, params


def preview(plan_path, subscription, output):
    plan = load_plan(plan_path)
    preflight(subscription)
    directory = private_output(ROOT / '.local/day16' / ('preview-' + uuid.uuid4().hex))
    directory.mkdir()
    template, params = parameters_file(plan, directory)
    result = azure(subscription, ['deployment', 'sub', 'what-if', '--location', 'indonesiacentral',
        '--name', 'scopeshield-preview', '--template-file', template, '--parameters', '@' + str(params),
        '--validation-level', 'ProviderNoRbac', '--no-pretty-print', '--result-format', 'FullResourcePayloads'])
    record = {'planSha256': plan['planSha256'], 'subscription': subscription,
              'createdAt': datetime.now(timezone.utc).isoformat(), 'result': result}
    write_new(output, record)


def approve_preview(plan, preview_record, approved_hash, subscription):
    if plan['planSha256'] != approved_hash or preview_record['planSha256'] != approved_hash or preview_record['subscription'] != subscription:
        raise ValueError('Human-approved exact plan/subscription and its preview must match')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(preview_record['createdAt'])).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError('Preview expired; recheck current resources and credit')
    result = preview_record['result']
    if result.get('status') != 'Succeeded' or not result.get('changes'):
        raise ValueError('A complete successful, nonempty Azure what-if is required')
    if result.get('error') or result.get('potentialChanges') or result.get('diagnostics'):
        raise ValueError('What-if errors, diagnostics or pending changes require review; never approve incomplete expansion')
    nsg_id = f'/subscriptions/{subscription}/resourceGroups/scopeshield-rg/providers/Microsoft.Network/networkSecurityGroups/scopeshield-nsg'.lower()
    for change in result['changes']:
        if change.get('unsupportedReason'):
            raise ValueError('Unsupported what-if expansion requires review')
        if change.get('changeType') in {'Create', 'NoChange'}: continue
        if change.get('changeType') == 'Modify' and change.get('resourceId', '').lower() == nsg_id: continue
        raise ValueError('Only initial Create/NoChange or the exact reviewed NSG update is supported; other modifications/deletions/unknown expansion stop')


def deploy(plan_path, subscription, preview_path, approved_hash, approval, credit_review):
    if not approval or not credit_review:
        raise ValueError('STOP: explicit human approval of resource creation AND current complete credit forecast is required')
    if not estimate_cost(datetime.now(timezone.utc).isoformat())['meets80DollarForecast']:
        raise ValueError('STOP: current start exceeds the USD 80 forecast; never provision early using the November 5 quote')
    plan = load_plan(plan_path)
    review = json.loads(Path(preview_path).read_text(encoding='utf-8'))
    approve_preview(plan, review, approved_hash, subscription)
    preflight(subscription)
    directory = private_output(ROOT / '.local/day16' / ('approved-deployment-' + uuid.uuid4().hex))
    directory.mkdir()
    template, params = parameters_file(plan, directory)
    # FIRST RESOURCE-CREATING COMMAND. Never reached by compile, prepare or preview.
    result = azure(subscription, ['deployment', 'sub', 'create', '--location', 'indonesiacentral',
        '--name', 'scopeshield-' + approved_hash[:12], '--template-file', template, '--parameters', '@' + str(params)])
    write_new(directory / 'result.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    c = sub.add_parser('compile'); c.add_argument('--bicep', required=True)
    p = sub.add_parser('prepare')
    for name in ('bicep', 'parameters', 'public-key', 'output'): p.add_argument('--' + name, required=True)
    p = sub.add_parser('preview')
    for name in ('plan', 'subscription', 'output'): p.add_argument('--' + name, required=True)
    d = sub.add_parser('deploy')
    for name in ('plan', 'subscription', 'preview', 'approved-plan-sha256'): d.add_argument('--' + name, required=True)
    d.add_argument('--approve-resource-creation', action='store_true')
    d.add_argument('--approve-current-credit-forecast', action='store_true')
    args = parser.parse_args()
    if args.action == 'compile':
        _, version, path = compile_template(args.bicep); print(version); print('Local template:', path)
    elif args.action == 'prepare': print('Review plan SHA-256:', prepare(args.bicep, args.parameters, args.public_key, args.output))
    elif args.action == 'preview': preview(args.plan, args.subscription, args.output); print('Preview saved; no resources created')
    else:
        deploy(args.plan, args.subscription, args.preview, args.approved_plan_sha256,
               args.approve_resource_creation, args.approve_current_credit_forecast)


if __name__ == '__main__':
    main()
