"""Future target only: encrypted backups via VM identity and selected-network Blob.

No CLI keys, SAS, public blobs, redirects, deletion or plaintext DB transfer.
Never prints authentication responses, headers or credentials. Not invoked by IaC.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from email.utils import formatdate
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from app.storage_limits import MAX_ENCRYPTED_BACKUP_BYTES, TRANSFER_CHUNK_BYTES

DATA = Path('/srv/scopeshield-data/backups')
SETTINGS = Path('/etc/scopeshield/blob.json')
MAX_BYTES = MAX_ENCRYPTED_BACKUP_BYTES
NAME = r'backup-[0-9TZ]+-[a-f0-9]{8}\.db\.age'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Storage authentication must never follow redirects')


def send(request):
    return build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=60)


def identity():
    url = 'http://169.254.169.254/metadata/identity/oauth2/token?' + urlencode({
        'api-version': '2018-02-01', 'resource': 'https://storage.azure.com/'})
    try:
        with send(Request(url, headers={'Metadata': 'true'})) as response:
            result = json.loads(response.read(65537))
        token = result['access_token']
        if not isinstance(token, str) or not token or '\n' in token or '\r' in token:
            raise ValueError('Invalid identity response')
        return token
    except Exception:
        raise ValueError('VM managed identity authentication failed; no authentication details logged') from None


def endpoint(account, name):
    if not re.fullmatch(r'scopeshield[a-z0-9]{13}', account) or not re.fullmatch(NAME, name):
        raise ValueError('Use only the exact approved account and encrypted backup name')
    return f'https://{account}.blob.core.windows.net/backups/{name}'


def request(url, token, method='GET', body=None, *, size=None):
    headers = {'Authorization': 'Bearer ' + token, 'x-ms-version': '2023-11-03', 'x-ms-date': formatdate(usegmt=True)}
    if method == 'PUT':
        headers.update({'x-ms-blob-type': 'BlockBlob', 'If-None-Match': '*', 'Content-Type': 'application/octet-stream'})
        headers['Content-Length'] = str(size)
    return Request(url, data=body, headers=headers, method=method)


def chunks(handle, size, deadline):
    """Known-length HTTP body, never chunked encoding or a whole-file allocation."""
    remaining = size
    while remaining:
        if time.monotonic() > deadline:
            raise ValueError('Backup transfer deadline exceeded')
        block = handle.read(min(TRANSFER_CHUNK_BYTES, remaining))
        if not block: raise ValueError('Backup changed during transfer')
        remaining -= len(block)
        yield block


def remote_hash(url, token, size, deadline):
    result, received = hashlib.sha256(), 0
    with send(request(url, token)) as response:
        if response.status != 200: raise ValueError('Unexpected read-back status')
        while True:
            if time.monotonic() > deadline: raise ValueError('Backup read-back deadline exceeded')
            block = response.read(min(TRANSFER_CHUNK_BYTES, size - received + 1))
            if not block: break
            received += len(block)
            if received > size: raise ValueError('Remote backup length mismatch')
            result.update(block)
    if received != size: raise ValueError('Remote backup length mismatch')
    return result.hexdigest()


def upload(path):
    path = Path(path)
    if path.is_symlink() or path.resolve().parent != DATA.resolve() or not re.fullmatch(NAME, path.name):
        raise ValueError('Only retained encrypted backups from the dedicated staging directory may be uploaded')
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise ValueError('Backup must be nonempty and within the 1 GiB plus age-overhead envelope')
    account = json.loads(SETTINGS.read_text(encoding='utf-8'))['accountName']
    url = endpoint(account, path.name)
    with path.open('rb') as handle:
        size = os.fstat(handle.fileno()).st_size
        if not 0 < size <= MAX_BYTES: raise ValueError('Backup size changed before transfer')
        if handle.read(22) != b'age-encryption.org/v1\n':
            raise ValueError('Refuse mislabeled plaintext; expected an age encrypted file')
        handle.seek(0)
        digest = hashlib.sha256()
        for block in chunks(handle, size, time.monotonic()+300): digest.update(block)
        if handle.read(1): raise ValueError('Backup grew during validation')
        expected = digest.hexdigest()
        handle.seek(0)
        token = identity()
        try:
            try:
                with send(request(url, token, 'PUT', chunks(handle, size, time.monotonic()+300), size=size)) as response:
                    if response.status != 201: raise ValueError('Unexpected upload status')
            except HTTPError as error:
                if error.code != 412: raise
            if remote_hash(url, token, size, time.monotonic()+300) != expected:
                raise ValueError('Remote encrypted checksum mismatch')
        except Exception:
            raise ValueError('Encrypted Blob upload/read-back verification failed; local evidence retained') from None
        finally:
            token = None
    receipt = {'file': path.name, 'encrypted_sha256': expected, 'remoteReadbackVerified': True}
    marker = path.with_suffix(path.suffix + '.uploaded.json')
    if marker.exists():
        if json.loads(marker.read_text(encoding='utf-8')) != receipt:
            raise ValueError('Conflicting local upload receipt; preserve and investigate')
    else:
        with marker.open('x', encoding='utf-8') as handle:
            os.chmod(marker, 0o600)
            json.dump(receipt, handle); handle.flush(); os.fsync(handle.fileno())
    # Retain backup and manifest locally; operator copy and restore are separate.
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['upload'])
    parser.add_argument('--name', required=True)
    args = parser.parse_args()
    if os.name != 'posix': raise ValueError('Approved Linux target only')
    print(json.dumps(upload(DATA / args.name)))
