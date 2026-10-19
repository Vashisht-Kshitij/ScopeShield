"""Future root-only, first configuration. No app start, DB creation or network API."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def render(hostname, storage_id):
    from app.deployment import public_origin
    origin = public_origin('https://' + hostname)
    if not re.fullmatch(r'[a-z0-9-]+\.indonesiacentral\.cloudapp\.azure\.com', hostname) or not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', storage_id):
        raise ValueError('Use the exact approved Indonesia Central FQDN and existing volume identity')
    environment = (ROOT / 'deploy/production.env.example').read_text(encoding='utf-8')
    environment = environment.replace('https://demo.example.com', origin).replace('replace-with-volume-identity', storage_id)
    caddy = (ROOT / 'deploy/Caddyfile').read_text(encoding='utf-8').replace('demo.example.com', hostname)
    return environment, caddy, f'SCOPESHIELD_FRONTEND_PROFILE=production\nNEXT_PUBLIC_API_BASE_URL=same-origin\nNEXT_PUBLIC_FRONTEND_URL={origin}\nNEXT_TELEMETRY_DISABLED=1\n'


def main():
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('Configure only as root on the approved Linux target')
    directory = Path('/srv/scopeshield-data')
    if not os.path.ismount(directory) or (directory / '.restore-pending').exists():
        raise ValueError('Verified non-quarantined data mount required')
    settings = json.loads(Path('/etc/scopeshield/settings.json').read_text())
    if not re.fullmatch(r'scopeshield[a-z0-9]{13}', settings['storageAccountName']):
        raise ValueError('Supply the exact deployed private Blob account name')
    recipient = settings['ageRecipient']
    if not re.fullmatch(r'age1[a-z0-9]{58}', recipient):
        raise ValueError('Supply the operator backup PUBLIC age recipient, never a private key')
    subprocess.run(['age', '--recipient', recipient], input=b'', stdout=subprocess.DEVNULL, check=True)
    env, caddy, frontend = render(settings['hostname'], (directory / '.scopeshield-volume').read_text().strip())
    files = {
        '/etc/scopeshield/backend.env': (env, 0o600),
        '/etc/scopeshield/frontend.env': (frontend, 0o600),
        '/etc/scopeshield/backup.recipient': (recipient + '\n', 0o644),
        '/etc/scopeshield/blob.json': (json.dumps({'accountName': settings['storageAccountName']}) + '\n', 0o644),
        '/etc/scopeshield/Caddyfile.candidate': (caddy, 0o600),
    }
    if any(Path(p).exists() for p in files):
        raise ValueError('Configuration exists; refuse overwrite, inspect/resume explicitly')
    for path, (content, mode) in files.items():
        with open(path, 'x', encoding='utf-8') as handle:
            os.chmod(path, mode)
            handle.write(content)
    # The service user can read only this non-secret recipient, not backend settings.
    os.chmod('/etc/scopeshield', 0o711)
    subprocess.run(['caddy', 'validate', '--config', '/etc/scopeshield/Caddyfile.candidate', '--adapter', 'caddyfile'], check=True)
    old = Path('/etc/caddy/Caddyfile')
    if old.exists():
        with open('/etc/scopeshield/Caddyfile.distribution-before', 'x') as handle:
            os.chmod(handle.name, 0o600)
            handle.write(old.read_text())
    old.write_text(caddy, encoding='utf-8'); old.chmod(0o644)
    print('Configuration created and Caddy parsed. No process started or database initialized.')


if __name__ == '__main__':
    main()
