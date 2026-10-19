"""Package an exact LOCAL Git commit, never untracked data, credentials or keys."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
PATHS = ['app', 'migrations', 'scripts', 'examples', 'frontend', 'deploy',
         'alembic.ini', 'requirements-day5.lock.txt']


def package(commit, output):
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / '.local').resolve()):
        raise ValueError('Release artifacts must be inside ignored .local/')
    git = ['git', '-c', 'safe.directory=' + ROOT.as_posix()]
    revision = subprocess.check_output([*git, 'rev-parse', '--verify', commit + '^{commit}'], cwd=ROOT, text=True).strip()
    present = set(subprocess.check_output([*git, 'ls-tree', '--name-only', revision], cwd=ROOT, text=True).splitlines())
    notices = [p for p in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'README.md', 'AG_STUDIO_RELEASE_STRATEGY.md') if p in present]
    raw = subprocess.check_output([*git, 'archive', '--format=tar', revision, '--', *PATHS, *notices], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        entries = source.getmembers()
        for member in entries:
            path = Path(member.name)
            if (path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir())
                    or any(p in {'.git', '.local', '.venv', 'node_modules', '.next'} for p in path.parts)
                    or path.name.startswith('.env') and not path.name.endswith('.example')
                    or path.suffix.lower() in {'.pem', '.key', '.pub', '.db', '.sqlite', '.sqlite3'}):
                raise ValueError('Refusing unexpected data/key/link in tracked release: ' + member.name)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as handle, tarfile.open(fileobj=handle, mode='w') as archive:
            for member in entries:
                archive.addfile(member, source.extractfile(member) if member.isfile() else None)
            payload = json.dumps({'commit': revision, 'offlineOnly': True}).encode()
            manifest = tarfile.TarInfo('release-manifest.json'); manifest.size = len(payload); manifest.mode = 0o644
            archive.addfile(manifest, io.BytesIO(payload))
    return {'commit': revision, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': len(entries)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.commit, args.output)))
