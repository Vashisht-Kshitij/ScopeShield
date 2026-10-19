"""Prepare a local, reviewable source ZIP. NEVER creates a repository or pushes.

An allowlist omits historical QA media, exported PDFs, workstation reports and
Git history. The original checkout/history/data are preserved. A staged-tree
preview is not a signed-off release; rebuild from the approved commit afterward.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
from uuid import uuid4
import zipfile

from scripts.scan_source_secrets import findings, TEXT

ROOT = Path(__file__).resolve().parents[1]
TOP = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'AG_STUDIO_RELEASE_STRATEGY.md',
       'INSTALL_GUIDE.md', 'DAY24_RELEASE_READINESS.md', 'DEMO_SCRIPT.md', 'DEMO_STORYBOARD.md',
       'DEVPOST_SUBMISSION_DRAFT.md', 'RELEASE_CHECKLIST.md', '.gitignore', '.env.example', '.env.offline.example',
       '.gitattributes', 'PUBLICATION_NOTES.md', 'RELEASE_MANIFEST.json', 'alembic.ini', 'pytest.ini'}
DIRECTORIES = {'app', 'migrations', 'scripts', 'examples', 'tests', 'frontend', 'deploy'}
PRIVATE_REPORTS = {
    'deploy/HOSTING_COST_EVIDENCE.json',
    'deploy/TARGET_HOST_CHECKLIST.md',
    'deploy/azure/ACCOUNT_VERIFICATION.json',
    'deploy/azure/COST_ESTIMATE.json',
    'deploy/azure/PREDEPLOYMENT_COST_REVIEW.json',
    'deploy/azure/PROVIDER_REGISTRATION_REVIEW.json',
}
PRIVATE_PARTS = {'.git', '.local', '.venv', 'node_modules', '.next', '.ssh', '.azure', '__pycache__'}
PRIVATE_SUFFIXES = {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.pub', '.p12', '.pfx', '.exe', '.dll', '.so', '.node', '.whl'}


def include(name):
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or not p.parts:
        raise ValueError('Unsafe source archive path')
    if (set(p.parts) & PRIVATE_PARTS or p.suffix.lower() in PRIVATE_SUFFIXES
            or p.name.startswith('.env') and not p.name.endswith('.example')
            or p.name in {'.npmrc', 'gemini_contract_test.py', 'gemini_simple_test.py'}):
        raise ValueError('Private/runtime path must never be tracked in a release: '+name)
    if name in PRIVATE_REPORTS: return False
    if p.suffix.lower() in {'.pdf', '.log', '.pyc'}: return False
    if p.parts[0] == 'docs':
        return name == 'docs/sponsors/USAGE_EVIDENCE.md' or name in {
            'docs/day24/contract-review.png', 'docs/day24/conversation-review.png',
            'docs/day24/mock-invoice.png', 'docs/day24/project-overview.png'}
    return (name in TOP or p.parts[0] in DIRECTORIES
            or len(p.parts) == 1 and p.name.startswith('requirements') and p.suffix == '.txt')


def prepare(revision='HEAD', *, staged=False):
    git = ['git', '-c', 'safe.directory='+ROOT.as_posix()]
    tree = (subprocess.check_output([*git, 'write-tree'], cwd=ROOT, text=True).strip() if staged
            else subprocess.check_output([*git, 'rev-parse', '--verify', revision+'^{tree}'], cwd=ROOT, text=True).strip())
    raw = subprocess.check_output([*git, 'archive', '--format=tar', tree], cwd=ROOT)
    output = ROOT / '.local/day24' / ('public-source-'+uuid4().hex+'.zip')
    output.parent.mkdir(parents=True, exist_ok=True)
    payloads, omitted = {}, []
    with tarfile.open(fileobj=io.BytesIO(raw)) as source:
        for member in source.getmembers():
            if not (member.isfile() or member.isdir()): raise ValueError('Links/devices not allowed in source export')
            selected = include(member.name.rstrip('/'))
            if member.isdir(): continue
            if not selected:
                omitted.append(member.name); continue
            data = source.extractfile(member).read()
            if data.startswith(b'SQLite format 3\x00'): raise ValueError('Database disguised as source: '+member.name)
            if PurePosixPath(member.name).suffix.lower() in TEXT:
                matches = findings(member.name, data.decode('utf-8', errors='replace'))
                if matches: raise ValueError('Unresolved secret pattern in source export: '+member.name)
            payloads[member.name] = data
    if not TOP <= payloads.keys(): raise ValueError('Missing required release documentation')
    manifest = {'source_tree': tree, 'staged_preview': staged, 'publication_approved': False,
        'contains_git_history': False, 'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())},
        'omitted_paths': sorted(omitted), 'licensing': 'MIT original code only; dependency and ownership gates remain'}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payloads.items()): archive.writestr(name, data)
        archive.writestr('release-source-manifest.json', json.dumps(manifest, indent=2))
    return {'archive': str(output), 'source_tree': tree, 'files': len(payloads), 'omitted': len(omitted),
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'staged_preview': staged, 'publication_approved': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    print(json.dumps(prepare(args.revision, staged=args.staged)))
