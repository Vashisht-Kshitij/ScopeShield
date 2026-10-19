"""Future operator-only Blob download using existing CLI Entra authentication.

Requires Data Reader plus an explicitly approved temporary storage /32 rule.
No SSH, account keys, SAS, implicit login or resource mutation. Never run by IaC.
"""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess


def pull(account, name, expected, directory):
    if not re.fullmatch(r'scopeshield[a-z0-9]{13}', account): raise ValueError('Use the exact approved Blob account')
    if not re.fullmatch(r'backup-[0-9TZ]+-[a-f0-9]{8}\.db\.age', name): raise ValueError('Use an exact encrypted backup filename')
    if not re.fullmatch(r'[a-f0-9]{64}', expected): raise ValueError('Verified encrypted checksum required')
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir(): raise ValueError('Existing operator storage is required')
    cli = shutil.which('az')
    if not cli: raise ValueError('Existing authenticated Azure CLI required; no implicit login')
    target, partial = directory / name, directory / (name + '.partial')
    if target.exists() or partial.exists(): raise ValueError('Never overwrite an existing backup or partial transfer')
    with partial.open('xb') as handle:
        os.chmod(partial, 0o600)
    # Only our newly reserved partial file can be overwritten by the CLI.
    subprocess.run([cli, 'storage', 'blob', 'download', '--account-name', account,
                    '--container-name', 'backups', '--name', name, '--file', str(partial),
                    '--auth-mode', 'login', '--overwrite', 'true', '--output', 'none', '--only-show-errors'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with partial.open('rb') as handle:
        if hashlib.file_digest(handle, 'sha256').hexdigest() != expected: raise ValueError('Transfer checksum mismatch; partial retained')
    with partial.open('r+b') as handle: os.fsync(handle.fileno())
    os.link(partial, target)  # Atomic exclusive publication; refuses an existing name.
    partial.unlink()         # Only our temporary hard link; encrypted backup retained.
    return expected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('account', 'name', 'sha256', 'directory'): parser.add_argument('--' + flag, required=True)
    args = parser.parse_args()
    print('Encrypted backup verified:', pull(args.account, args.name, args.sha256, args.directory))
