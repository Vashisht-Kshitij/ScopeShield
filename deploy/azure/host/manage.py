"""Future unprivileged target maintenance; disabled providers and mounted data required."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
DATA = Path('/srv/scopeshield-data')
RECIPIENT_FILE = Path('/etc/scopeshield/backup.recipient')


def configuration():
    from app.database import database_url
    from app.deployment import Deployment, value
    if os.name != 'posix' or (ROOT / '.env').exists():
        raise ValueError('Linux target only; release must contain no workstation .env')
    cfg = Deployment.load()
    if not cfg.production:
        raise ValueError('Maintenance requires the mandatory public offline production profile')
    for name in ('PASSWORD_CONCURRENCY', 'PDF_CONCURRENCY', 'AI_CONCURRENCY'):
        if value(name) != '1': raise ValueError('Keep resource concurrency one')
    for name in ('AI_USER_DAILY_REQUESTS', 'AI_APP_DAILY_REQUESTS'):
        if value(name) != '0': raise ValueError('Keep public AI budgets zero')
    if any(os.environ.get(k) for k in ('PAYPAL_CLIENT_ID', 'PAYPAL_CLIENT_SECRET', 'GEMINI_API_KEY', 'GOOGLE_API_KEY')):
        raise ValueError('Provider credentials must not be installed in the public demo')
    url = database_url()
    cfg.validate_storage(url)
    from sqlalchemy.engine import make_url
    path = Path(make_url(url).database).resolve()
    if path.parent != DATA: raise ValueError('Expected the approved dedicated mount')
    return cfg, url, path


def release_schema(root=ROOT):
    """Read this release's single Alembic head; never assume an old schema."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(root / 'alembic.ini'))
    config.set_main_option('script_location', str(root / 'migrations'))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1: raise ValueError('Release must have one reviewed migration head')
    return heads[0]


def require_release_policy(prior):
    """Same Alembic head alone cannot promise safe evidence/backup semantics."""
    from app.storage_limits import MAX_DATABASE_BYTES, MAX_ENCRYPTED_BACKUP_BYTES
    expected = {'policy': 'day23-evidence-backup-v1', 'sqliteMaximumBytes': MAX_DATABASE_BYTES,
                'encryptedBackupMaximumBytes': MAX_ENCRYPTED_BACKUP_BYTES,
                'contractQuotes': 'validated-range-or-unique-occurrence'}
    for release in (ROOT, Path(prior)):
        path = release / 'deploy/azure/host/release-policy.json'
        if not path.is_file() or json.loads(path.read_text(encoding='utf-8')) != expected:
            raise ValueError('Rollback requires the current evidence and backup policy; no switch permitted')


def compatible_release(prior):
    from scripts.sqlite_backup import inspect_database
    _, _, path = configuration()
    prior = Path(prior)
    if prior.is_symlink() or not re.fullmatch(r'/opt/scopeshield-releases/[a-f0-9]{40}', str(prior)) or prior.resolve() != prior:
        raise ValueError('Expected a reviewed immutable local release')
    require_release_policy(prior)
    current = inspect_database(path)
    if current != release_schema() or release_schema(prior) != current:
        raise ValueError('Rollback release cannot read the current schema; no downgrade or switch permitted')
    return {'schema': current, 'compatible': True, 'database_changed': False}


def migrate():
    from alembic import command
    from app.database import build_engine, initialize_database, migration_config
    from app.resources import single_instance
    from scripts.sqlite_backup import inspect_database
    _, url, path = configuration()
    expected = release_schema()
    if path.is_file(): backup(upload=True)
    engine = build_engine(url)
    try:
        with single_instance(engine):
            initialize_database(engine)
            with engine.begin() as connection:
                config = migration_config(); config.attributes['connection'] = connection
                command.check(config)
            if inspect_database(path) != expected: raise ValueError('Unexpected release schema')
    finally:
        engine.dispose()
    return {'schema': expected, 'integrity': 'ok'}


def backup(upload=False):
    from scripts.sqlite_backup import backup as copy_database, sync_directory
    _, _, path = configuration()
    recipient = RECIPIENT_FILE.read_text().strip()
    if not re.fullmatch(r'age1[a-z0-9]{58}', recipient): raise ValueError('Approved public backup recipient required')
    directory = DATA / 'backups'; directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.resolve().parent != DATA:
        raise ValueError('Backup staging must remain on the approved data mount')
    if len(list(directory.glob('*.age'))) >= 8 or list(directory.glob('*.pending')) or list(directory.glob('*.raw')):
        raise ValueError('Backup queue/staging needs operator review; no automatic evidence deletion')
    usage = shutil.disk_usage(DATA)
    if usage.free - path.stat().st_size * 2 < usage.total * 0.30:
        raise ValueError('Backup would violate 30% disk reserve')
    name = 'backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    raw, pending, target = (directory / (name + suffix) for suffix in ('.raw', '.pending', '.db.age'))
    original = copy_database(path, raw)
    subprocess.run(['age', '--recipient', recipient, '--output', str(pending), str(raw)], check=True)
    pending.chmod(0o600)
    with pending.open('r+b') as handle: os.fsync(handle.fileno())
    os.link(pending, target)  # Exclusive publication: never replace an existing backup.
    sync_directory(directory)
    pending.unlink()
    # Only our exclusive temporary plaintext copy; preserve the encrypted backup.
    raw.unlink()
    with target.open('rb') as encrypted:
        encrypted_hash = hashlib.file_digest(encrypted, 'sha256').hexdigest()
    manifest = {**original, 'encrypted_sha256': encrypted_hash, 'file': target.name}
    with (directory / (name + '.json')).open('x') as handle:
        os.chmod(handle.name, 0o600)
        json.dump(manifest, handle)
        handle.flush(); os.fsync(handle.fileno())
    sync_directory(directory)
    if upload:
        from deploy.azure.host.blob_backup import upload as transfer
        transfer(target)
    return manifest


def inspect():
    from scripts.sqlite_backup import inspect_database
    _, _, path = configuration()
    schema = inspect_database(path)
    if schema != release_schema(): raise ValueError('Database does not match this release; run reviewed migration before activation')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        counts = {name: db.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
                  for name in ('users', 'contracts', 'milestones', 'invoice_links', 'ai_usage', 'contract_documents', 'change_requests')}
    if counts['invoice_links'] or counts['ai_usage']:
        raise ValueError('Unexpected provider history in this fresh offline demo; quarantine and review, never erase it')
    return {'schema': schema, 'integrity': 'ok', 'counts': counts}


def restore(source, target):
    from scripts.sqlite_backup import restore as restore_database
    from app.database import build_engine
    from app.resources import single_instance
    _, url, _ = configuration()
    source, target = Path(source).resolve(), Path(target).resolve()
    if not source.is_relative_to(DATA / 'backups') or target.parent != DATA or not re.fullmatch(r'scopeshield-recovery-[a-f0-9-]+\.db', target.name):
        raise ValueError('Use the approved backup directory and a NEW recovery database directly on the data mount')
    # Services must already be stopped by ops.sh. Helper refuses overwrites and revokes sessions.
    engine = build_engine(url)
    try:
        with single_instance(engine):
            return restore_database(source, target)
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['validate', 'migrate', 'backup', 'inspect', 'restore', 'compatible'])
    parser.add_argument('--release')
    parser.add_argument('--source'); parser.add_argument('--target')
    args = parser.parse_args()
    if args.action == 'validate': configuration(); result = {'configuration': 'safe public offline profile'}
    elif args.action == 'migrate': result = migrate()
    elif args.action == 'backup': result = backup(upload=True)
    elif args.action == 'inspect': result = inspect()
    elif args.action == 'compatible': result = compatible_release(args.release)
    else: result = restore(args.source, args.target)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
