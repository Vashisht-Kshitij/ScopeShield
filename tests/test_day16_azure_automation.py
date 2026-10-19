"""Offline guard regressions: no Azure connection, VM or real SSH/private key."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import struct
import subprocess

import pytest

from deploy.azure import azure_plan as plan
from deploy.azure.host import configure, manage


def parameters(**changes):
    values = dict(location='indonesiacentral', imageVersion='24.04.202610160', administratorSshCidr='', securityType='TrustedLaunch',
                  operatorPrincipalId='00000000-0000-0000-0000-000000000001', operatorStorageCidr='')
    values.update(changes)
    return {'parameters': {k: {'value': v} for k, v in values.items()}}


def public_test_key():
    # Encoding fixture only; no private key is created or used.
    binary = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
    return 'ssh-ed25519 ' + base64.b64encode(binary).decode()


@pytest.mark.parametrize('cidr', ['0.0.0.0/0', '0.0.0.0/24', '8.8.0.0/16', '*', 'Internet', '10.0.0.1/32', '127.0.0.1/32', '8.8.8.8/24', '::/0'])
def test_unsafe_ssh_cidr_refused(cidr):
    with pytest.raises(ValueError): plan.validate_parameters(parameters(administratorSshCidr=cidr))


@pytest.mark.parametrize('cidr', ['8.8.8.8/32', '8.8.8.0/24'])
def test_even_narrow_ssh_remains_closed(cidr):
    with pytest.raises(ValueError, match='SSH must remain closed'):
        plan.validate_parameters(parameters(administratorSshCidr=cidr))


@pytest.mark.parametrize('cidr', ['', '8.8.8.8/32'])
def test_blob_only_operator_ipv4_or_closed(cidr):
    assert plan.validate_parameters(parameters(operatorStorageCidr=cidr))['operatorStorageCidr']['value'] == cidr


@pytest.mark.parametrize('version', ['latest', '', 'REPLACE_WITH_PINNED_IMAGE_VERSION', '24.04; whoami'])
def test_mutable_or_unresolved_image_refused(version):
    with pytest.raises(ValueError): plan.validate_parameters(parameters(imageVersion=version))


def test_unpriced_region_and_extra_parameter_refused():
    with pytest.raises(ValueError): plan.validate_parameters(parameters(location='eastus'))
    with pytest.raises(ValueError): plan.validate_parameters(parameters(adminPassword='forbidden'))


def test_public_key_only_and_comment_removed(tmp_path):
    path = tmp_path / 'operator.pub'; path.write_text(public_test_key() + ' personal-workstation-comment')
    assert plan.public_key(path) == public_test_key()
    private = tmp_path / 'id_ed25519'; private.write_text('PRIVATE KEY MUST NEVER BE READ')
    with pytest.raises(ValueError): plan.public_key(private)


@pytest.mark.parametrize('raw', ['-----BEGIN OPENSSH PRIVATE KEY-----', 'ssh-rsa AAAA', 'ssh-ed25519 invalid==', 'ssh-ed25519 AAAA', public_test_key() + '\nsecond-line'])
def test_private_malformed_or_multiline_key_refused(raw):
    with pytest.raises(ValueError): plan.validate_public_key(raw)


def test_generated_plan_cannot_be_written_into_git(tmp_path):
    with pytest.raises(ValueError): plan.private_output(tmp_path / 'credentials.json')
    with pytest.raises(ValueError): plan.private_output(plan.ROOT / 'deploy/azure/private-plan.json')


def test_no_approval_never_reaches_azure(monkeypatch):
    calls = []
    monkeypatch.setattr(plan, 'azure', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='STOP'):
        plan.deploy('nonexistent', 'nonexistent', 'nonexistent', 'anything', False, True)
    with pytest.raises(ValueError, match='STOP'):
        plan.deploy('nonexistent', 'nonexistent', 'nonexistent', 'anything', True, False)
    assert calls == []


def test_preview_cannot_fall_through_to_resource_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(plan, 'ROOT', tmp_path)
    monkeypatch.setattr(plan, 'load_plan', lambda _: {'planSha256': 'synthetic', 'payload': {'template': {}, 'parameters': {}}})
    monkeypatch.setattr(plan, 'preflight', lambda _: None)
    calls = []
    def fake_azure(subscription, args):
        calls.append(args)
        return {'status': 'Succeeded', 'changes': [{'changeType': 'Create'}]}
    monkeypatch.setattr(plan, 'azure', fake_azure)
    plan.preview('synthetic', 'synthetic-subscription', tmp_path / '.local/preview.json')
    assert len(calls) == 1 and calls[0][:3] == ['deployment', 'sub', 'what-if']
    assert 'create' not in calls[0] and '--confirm-with-what-if' not in calls[0]


@pytest.mark.parametrize('limit', ['Off', 'CurrentPeriodOff'])
def test_credit_spending_limit_cannot_be_disabled(limit, monkeypatch):
    calls = []
    def fake_azure(subscription, args):
        calls.append(args)
        return {'state': 'Enabled', 'subscriptionPolicies': {'spendingLimit': limit}}
    monkeypatch.setattr(plan, 'azure', fake_azure)
    with pytest.raises(ValueError, match='spending limit On'): plan.preflight('synthetic')
    assert len(calls) == 1 and calls[0][:3] == ['rest', '--method', 'get']


def test_unregistered_provider_never_registered_automatically(monkeypatch):
    calls = []
    def fake_azure(subscription, args):
        calls.append(args)
        return {'state': 'Enabled', 'subscriptionPolicies': {'spendingLimit': 'On', 'quotaId': 'AzureForStudents_2018-01-01'}} if args[0] == 'rest' else {'registrationState': 'NotRegistered'}
    monkeypatch.setattr(plan, 'azure', fake_azure)
    with pytest.raises(ValueError, match='not registered'): plan.preflight('synthetic')
    assert calls[1][:2] == ['provider', 'show']
    assert all('register' not in args and 'create' not in args for args in calls)


def review(change='Create', **changes):
    result = dict(planSha256='approved-hash', subscription='00000000-0000-0000-0000-000000000001',
                  createdAt=datetime.now(timezone.utc).isoformat(), result={'status': 'Succeeded', 'changes': [{'changeType': change}]})
    result.update(changes)
    return result


@pytest.mark.parametrize('change', ['Delete', 'Modify', 'Deploy', 'Ignore', 'Unsupported'])
def test_destructive_or_unexpanded_preview_refused(change):
    r = review(change)
    with pytest.raises(ValueError): plan.approve_preview({'planSha256': 'approved-hash'}, r, 'approved-hash', r['subscription'])


def test_preview_hash_subscription_expiry_and_status_bound():
    r = review(); p = {'planSha256': 'approved-hash'}
    plan.approve_preview(p, r, 'approved-hash', r['subscription'])
    for changed in (review(planSha256='different'), review(subscription='different'),
                    review(createdAt=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat()),
                    review(result={'status': 'Failed', 'changes': []})):
        with pytest.raises(ValueError): plan.approve_preview(p, changed, 'approved-hash', r['subscription'])


def test_only_exact_nsg_can_be_modified():
    r = review('Modify'); sub = r['subscription']
    r['result']['changes'][0]['resourceId'] = f'/subscriptions/{sub}/resourceGroups/scopeshield-rg/providers/Microsoft.Network/networkSecurityGroups/scopeshield-nsg'
    plan.approve_preview({'planSha256': 'approved-hash'}, r, 'approved-hash', sub)
    r['result']['changes'][0]['resourceId'] += '-other'
    with pytest.raises(ValueError): plan.approve_preview({'planSha256': 'approved-hash'}, r, 'approved-hash', sub)


def test_public_configuration_preserves_previous_security_profile():
    backend, caddy, frontend = configure.render('scopeshield-test.indonesiacentral.cloudapp.azure.com', 'synthetic-volume-identity')
    for setting in ('SCOPESHIELD_ENV=production', 'SCOPESHIELD_AI_PROVIDER=disabled', 'SCOPESHIELD_AI_ALLOW_LIVE=false',
                    'SCOPESHIELD_AI_APP_DAILY_REQUESTS=0', 'SCOPESHIELD_SECURE_COOKIES=true', 'SCOPESHIELD_TRUSTED_PROXIES=127.0.0.1'):
        assert setting in backend
    assert 'NEXT_PUBLIC_API_BASE_URL=same-origin' in frontend
    assert '?Content-Security-Policy' in caddy and 'header_up -Forwarded' in caddy
    with pytest.raises(ValueError): configure.render('evil.example.com', 'synthetic-volume-identity')


def test_encryption_failure_keeps_private_copy_and_never_publishes_backup(tmp_path, monkeypatch):
    source = tmp_path / 'scopeshield.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE alembic_version(version_num TEXT)')
        db.execute("INSERT INTO alembic_version VALUES ('0008_pdf_provenance')")
    recipient = tmp_path / 'recipient'; recipient.write_text('age1' + 'a' * 58)
    monkeypatch.setattr(manage, 'DATA', tmp_path)
    monkeypatch.setattr(manage, 'RECIPIENT_FILE', recipient)
    monkeypatch.setattr(manage, 'configuration', lambda: (None, None, source))
    def fail(*args, **kwargs): raise subprocess.CalledProcessError(1, ['fake-age'])
    monkeypatch.setattr(manage.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError): manage.backup()
    assert list((tmp_path / 'backups').glob('*.raw'))
    assert not list((tmp_path / 'backups').glob('*.age'))
    with pytest.raises(ValueError, match='queue/staging'): manage.backup()


def test_no_workstation_maintenance(monkeypatch):
    # Runs before engine creation even if local credentials happen to exist.
    monkeypatch.setattr(manage.os, 'name', 'nt')
    with pytest.raises(ValueError, match='Linux target only'): manage.configuration()


def test_edited_plan_and_changed_source_refused(tmp_path, monkeypatch):
    payload = {'template': {}, 'parameters': {**parameters()['parameters'], 'sshPublicKey': {'value': public_test_key()}}, 'sources': {'source': 'original'}}
    p = tmp_path / 'plan.json'
    original = {'payload': payload, 'planSha256': plan.digest(payload)}
    p.write_text(json.dumps(original))
    monkeypatch.setattr(plan, 'source_hashes', lambda: {'source': 'original'})
    monkeypatch.setattr(plan, 'check_policy', lambda _: None)
    assert plan.load_plan(p)['planSha256'] == original['planSha256']
    monkeypatch.setattr(plan, 'source_hashes', lambda: {'source': 'changed'})
    with pytest.raises(ValueError, match='changed'): plan.load_plan(p)
    monkeypatch.setattr(plan, 'source_hashes', lambda: {'source': 'original'})
    original['payload']['parameters']['administratorSshCidr']['value'] = '8.8.8.8/32'
    p.write_text(json.dumps(original))
    with pytest.raises(ValueError, match='changed'): plan.load_plan(p)


def test_successful_encryption_pipeline_and_queue_bound(tmp_path, monkeypatch):
    import hashlib
    source = tmp_path / 'scopeshield.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE alembic_version(version_num TEXT)')
        db.execute("INSERT INTO alembic_version VALUES ('0008_pdf_provenance')")
        db.execute('CREATE TABLE evidence(quote TEXT)')
        db.execute("INSERT INTO evidence VALUES ('synthetic original evidence')")
    before = source.read_bytes()
    recipient = tmp_path / 'recipient'; recipient.write_text('age1' + 'a' * 58)
    monkeypatch.setattr(manage, 'DATA', tmp_path)
    monkeypatch.setattr(manage, 'RECIPIENT_FILE', recipient)
    monkeypatch.setattr(manage, 'configuration', lambda: (None, None, source))
    def fake_age(args, **kwargs):
        # Control-flow fixture, not cryptography or real operator-key verification.
        assert args[:2] == ['age', '--recipient']
        Path(args[args.index('--output') + 1]).write_bytes(b'MOCK-CIPHERTEXT' + Path(args[-1]).read_bytes()[::-1])
    monkeypatch.setattr(manage.subprocess, 'run', fake_age)
    result = manage.backup()
    encrypted = tmp_path / 'backups' / result['file']
    assert hashlib.sha256(encrypted.read_bytes()).hexdigest() == result['encrypted_sha256']
    assert not list(encrypted.parent.glob('*.raw')) and not list(encrypted.parent.glob('*.pending'))
    assert source.read_bytes() == before
    # Even an injected timestamp/UUID collision cannot replace retained evidence.
    import re
    from types import SimpleNamespace
    stamp, identity = re.fullmatch(r'backup-([0-9TZ]+)-([a-f0-9]{8})\.db\.age', encrypted.name).groups()
    fixed = datetime.strptime(stamp, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
    monkeypatch.setattr(manage, 'datetime', SimpleNamespace(now=lambda _: fixed))
    monkeypatch.setattr(manage.uuid, 'uuid4', lambda: SimpleNamespace(hex=identity + '0' * 24))
    retained = encrypted.read_bytes()
    with pytest.raises(FileExistsError): manage.backup()
    assert encrypted.read_bytes() == retained
    for temporary in [*encrypted.parent.glob('*.raw'), *encrypted.parent.glob('*.pending')]: temporary.unlink()
    for index in range(7): (encrypted.parent / f'older-{index}.age').write_bytes(b'older backup retained')
    with pytest.raises(ValueError, match='queue/staging'): manage.backup()
    assert len(list(encrypted.parent.glob('*.age'))) == 8
