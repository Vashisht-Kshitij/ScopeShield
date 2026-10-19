"""Offline API/transfer guard regressions; all Azure and identity I/O is mocked."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from deploy.azure import azure_plan as plan, verify_account, pull_backup, costs
from deploy.azure.host import blob_backup as blob
from tests.test_day16_azure_automation import parameters
from tests.test_day16_azure_automation import review


@pytest.mark.parametrize('field,value',[('error',{'code':'SyntheticError'}),('potentialChanges',[{'changeType':'Create'}]),
                                     ('diagnostics',[{'message':'Unexpanded module'}])])
def test_separate_azure_pending_diagnostics_cannot_bypass_preview_gate(field,value):
    r=review();r['result'][field]=value
    with pytest.raises(ValueError,match='incomplete expansion'):
        plan.approve_preview({'planSha256':'approved-hash'},r,'approved-hash',r['subscription'])


def test_create_with_unsupported_reason_is_not_accepted():
    r=review();r['result']['changes'][0]['unsupportedReason']='Synthetic unresolved provider'
    with pytest.raises(ValueError,match='Unsupported'):
        plan.approve_preview({'planSha256':'approved-hash'},r,'approved-hash',r['subscription'])


@pytest.mark.parametrize('cidr', ['0.0.0.0/0', '8.8.8.0/24', '10.0.0.1/32', '127.0.0.1/32', '*', '::/0', '8.8.8.8'])
def test_storage_firewall_cannot_be_broad_or_private(cidr):
    with pytest.raises(ValueError): plan.validate_parameters(parameters(operatorStorageCidr=cidr))


@pytest.mark.parametrize('change', [dict(location='centralindia'), dict(securityType='Standard'), dict(operatorPrincipalId=''), dict(operatorPrincipalId='00000000-0000-0000-0000-000000000000')])
def test_blocked_region_launch_downgrade_and_missing_operator_refused(change):
    with pytest.raises(ValueError): plan.validate_parameters(parameters(**change))


def sku(restrictions=None):
    return {'name': 'Standard_B2als_v2', 'resourceType': 'virtualMachines', 'locations': ['indonesiacentral'],
            'family': 'standardBasv2Family', 'restrictions': restrictions or [], 'capabilities': []}


def test_exact_sku_and_restrictions_are_not_capacity_proof():
    assert verify_account.sku_result([sku()])['eligibleByMetadata']
    assert not verify_account.sku_result([sku()])['allocationCapacityGuaranteed']
    blocked = sku([{'type': 'Location', 'values': ['indonesiacentral'], 'reasonCode': 'NotAvailableForSubscription'}])
    assert not verify_account.sku_result([blocked])['eligibleByMetadata']
    blocked['name'] = 'Standard_B2als_v2_extra'
    assert not verify_account.sku_result([blocked])['eligibleByMetadata']
    assert not verify_account.sku_result([sku(), sku()])['eligibleByMetadata']


@pytest.mark.parametrize('date,expected,higher,fits', [('2026-11-05T00:00:00Z','71.00','79.48',True),
    ('2026-11-04T00:00:00Z','72.49','80.62',False), ('2026-11-01T00:00:00Z','76.98','86.07',False),
    ('2026-10-17T00:00:00Z','99.39','107.27',False)])
def test_earlier_launch_cannot_silently_consume_reserve(date,expected,higher,fits):
    result = costs.estimate(date)
    assert (result['expectedUsd'],result['higherUsd'],result['meets80DollarForecast']) == (expected,higher,fits)
    assert not result['hardSpendingCap']


def test_even_approval_flags_cannot_create_resources_using_early_start_quote(monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime,timezone
    monkeypatch.setattr(plan,'datetime',SimpleNamespace(now=lambda _:datetime(2026,11,4,tzinfo=timezone.utc)))
    calls=[]
    monkeypatch.setattr(plan,'azure',lambda *args:calls.append(args))
    with pytest.raises(ValueError,match='current start exceeds'):
        plan.deploy('nonexistent','synthetic','nonexistent','synthetic',True,True)
    assert calls==[]


def test_zone_restriction_is_reported_without_inventing_regional_capacity():
    row = sku([{'type': 'Zone', 'values': ['indonesiacentral'], 'restrictionInfo': {'zones': ['1']}}])
    result = verify_account.sku_result([row])
    assert result['eligibleByMetadata'] and result['restrictions'] == row['restrictions']
    assert not result['allocationCapacityGuaranteed']


def test_actual_api_region_casing_and_zone_only_restrictions():
    row = sku([{'type':'Zone','values':['IndonesiaCentral'],'reasonCode':'NotAvailableForSubscription',
                'restrictionInfo':{'locations':['IndonesiaCentral'],'zones':['1','2']}}])
    row['locations'] = ['IndonesiaCentral']
    assert verify_account.sku_result([row])['eligibleByMetadata']
    row['restrictions'][0]['type'] = 'Location'
    assert not verify_account.sku_result([row])['eligibleByMetadata']


def test_account_inspector_never_mutates_or_exposes_raw_auth_errors(monkeypatch):
    calls = []
    def fake(subscription, args):
        calls.append(args)
        raise RuntimeError('SYNTHETIC-SECRET-ERROR-MUST-NOT-LOG')
    monkeypatch.setattr(verify_account, 'azure', fake)
    result = verify_account.inspect('synthetic')
    assert result['errors'] and 'SYNTHETIC-SECRET' not in json.dumps(result)
    assert all(not {'create', 'register', 'update', 'set', 'get-access-token', 'invoke', 'login'} & set(args) for args in calls)


@pytest.mark.parametrize('locations,passes', [(['indonesiacentral'],True), (['centralindia'],False), ([],False)])
def test_preview_preflight_preserves_live_region_policy_and_registration(locations,passes,monkeypatch):
    calls=[]
    def fake(subscription,args):
        calls.append(args)
        if args[0]=='provider': return {'registrationState':'Registered'}
        if 'policyAssignments/sys.regionrestriction' in args[-1]:
            return {'properties':{'parameters':{'allowedLocations':{'value':locations}}}}
        return {'state':'Enabled','subscriptionPolicies':{'quotaId':'AzureForStudents_2018-01-01','spendingLimit':'On'}}
    monkeypatch.setattr(plan,'azure',fake)
    if passes: plan.preflight('synthetic')
    else:
        with pytest.raises(ValueError,match='region policy'): plan.preflight('synthetic')
    assert all(not {'create','register','update','set'} & set(args) for args in calls)


class Response(BytesIO):
    def __init__(self, value=b'', status=200): super().__init__(value); self.status = status


@pytest.fixture
def staged(tmp_path, monkeypatch):
    name = 'backup-20261017T000000Z-01234567.db.age'
    path = tmp_path / name; path.write_bytes(b'age-encryption.org/v1\nSYNTHETIC-CIPHERTEXT')
    settings = tmp_path / 'blob.json'; settings.write_text(json.dumps({'accountName': 'scopeshield0123456789abc'}))
    monkeypatch.setattr(blob, 'DATA', tmp_path)
    monkeypatch.setattr(blob, 'SETTINGS', settings)
    monkeypatch.setattr(blob, 'identity', lambda: 'synthetic-opaque-test-value')
    return path


def test_backup_put_is_encrypted_exclusive_authenticated_and_readback_verified(staged, monkeypatch):
    requests = []
    original = staged.read_bytes()
    def fake(req):
        requests.append(req)
        if req.method == 'PUT':
            assert b''.join(req.data) == original
            assert req.get_header('Content-length') == str(len(original))
            assert req.get_header('If-none-match') == '*'
            assert req.get_header('X-ms-blob-type') == 'BlockBlob'
            assert req.get_header('Authorization') == 'Bearer synthetic-opaque-test-value'
            return Response(status=201)
        return Response(original)
    monkeypatch.setattr(blob, 'send', fake)
    receipt = blob.upload(staged)
    assert receipt['remoteReadbackVerified']
    assert receipt['encrypted_sha256'] == hashlib.sha256(original).hexdigest()
    assert [r.method for r in requests] == ['PUT', 'GET']
    assert staged.read_bytes() == original
    assert all('.blob.core.windows.net/backups/' in r.full_url and '?' not in r.full_url for r in requests)
    assert 'synthetic-opaque-test-value' not in json.dumps(receipt)


@pytest.mark.parametrize('failure', ['denied', 'corrupt', 'identity', 'conflicting-name'])
def test_transfer_failure_retains_backup_and_never_claims_success(staged, monkeypatch, failure):
    original = staged.read_bytes()
    def fake(req):
        if failure == 'denied': raise HTTPError(req.full_url, 403, 'Denied', {}, None)
        if req.method == 'PUT':
            if failure == 'conflicting-name': raise HTTPError(req.full_url, 412, 'Exists', {}, None)
            return Response(status=201)
        return Response(b'corrupt')
    monkeypatch.setattr(blob, 'send', fake)
    if failure == 'identity': monkeypatch.setattr(blob, 'identity', lambda: (_ for _ in ()).throw(ValueError('identity unavailable')))
    with pytest.raises(ValueError): blob.upload(staged)
    assert staged.read_bytes() == original
    assert not list(staged.parent.glob('*.uploaded.json'))


def test_identical_remote_object_is_idempotent_without_overwrite(staged, monkeypatch):
    def fake(req):
        if req.method == 'PUT': raise HTTPError(req.full_url, 412, 'Exists', {}, None)
        return Response(staged.read_bytes())
    monkeypatch.setattr(blob, 'send', fake)
    first = blob.upload(staged)
    assert blob.upload(staged) == first


def test_plaintext_oversize_and_untrusted_destination_blocked_before_auth(staged, monkeypatch):
    monkeypatch.setattr(blob, 'identity', lambda: pytest.fail('No identity request permitted'))
    with pytest.raises(ValueError): blob.upload(staged.parent / 'scopeshield.db')
    monkeypatch.setattr(blob, 'MAX_BYTES', 1)
    with pytest.raises(ValueError): blob.upload(staged)
    for account in ('evil.example.com', 'scopeshield0123456789abc/path', 'https://other'):
        with pytest.raises(ValueError): blob.endpoint(account, staged.name)
    with pytest.raises(ValueError): blob.NoRedirect().redirect_request(None, None, None, None, None, None)


def test_identity_errors_never_log_response_or_secret(monkeypatch):
    monkeypatch.setattr(blob, 'send', lambda _: Response(b'{"access_token":"SYNTHETIC\\nSECRET"}'))
    with pytest.raises(ValueError) as e: blob.identity()
    assert 'SYNTHETIC' not in str(e.value) and 'SECRET' not in str(e.value)


def test_operator_download_uses_entra_and_preserves_conflicting_files(tmp_path, monkeypatch):
    body = b'synthetic encrypted data'
    expected = hashlib.sha256(body).hexdigest()
    name = 'backup-20261017T000000Z-01234567.db.age'
    calls = []
    monkeypatch.setattr(pull_backup.shutil, 'which', lambda _: 'synthetic-az')
    def run(args, **kwargs):
        calls.append(args)
        Path(args[args.index('--file') + 1]).write_bytes(body)
    monkeypatch.setattr(pull_backup.subprocess, 'run', run)
    assert pull_backup.pull('scopeshield0123456789abc', name, expected, tmp_path) == expected
    assert calls[0][1:4] == ['storage', 'blob', 'download']
    assert calls[0][calls[0].index('--auth-mode') + 1] == 'login'
    assert '--account-key' not in calls[0] and '--sas-token' not in calls[0]
    with pytest.raises(ValueError): pull_backup.pull('scopeshield0123456789abc', name, expected, tmp_path)
    assert len(calls) == 1 and (tmp_path / name).read_bytes() == body
