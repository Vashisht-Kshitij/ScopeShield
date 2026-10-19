"""Offline age + streamed fake-Blob + quarantined restore of a >128 MiB DB.

Only NEW synthetic databases and a disposable encryption identity in ignored .local.
Never imports cloud credentials or opens a network connection. Nothing is deleted.
"""
import argparse
from contextlib import closing
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tracemalloc
from unittest.mock import patch
from urllib.error import HTTPError
from uuid import uuid4

from sqlalchemy.orm import Session
from app.database import build_engine, initialize_database, ROOT
from app.storage import User, AuthSession, utc_now
from app.storage_limits import TRANSFER_CHUNK_BYTES
from deploy.azure.host import blob_backup as blob
from scripts.sqlite_backup import backup, restore, file_sha256, inspect_database


def verify(age, keygen):
    age, keygen = (Path(p).resolve(strict=True) for p in (age, keygen))
    directory = ROOT/'.local/day23'/('recovery-'+uuid4().hex)
    directory.mkdir(parents=True)
    source, raw, encrypted, remote, downloaded, decrypted, restored = [directory/n for n in (
        'synthetic.db','snapshot.db','backup-20261019T000000Z-01234567.db.age',
        'fake-remote.age','download.age','decrypted.db','restored.db')]
    engine = build_engine('sqlite:///'+source.as_posix()); initialize_database(engine)
    with Session(engine) as session:
        user = User(username='backup.synthetic', display_name='Synthetic recovery only', role='freelancer', password_hash='not-authenticatable')
        session.add(user);session.flush()
        session.add(AuthSession(token_hash='0'*64,user_id=user.id,expires_at=utc_now()+timedelta(days=1)))
        session.commit()
    with engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE synthetic_large_payload(data BLOB)')
        c.exec_driver_sql('INSERT INTO synthetic_large_payload VALUES(zeroblob(135266304))')
    engine.dispose()
    initial_hash = file_sha256(source)
    snapshot = backup(source, raw)
    identity = directory/'DISPOSABLE-TEST-IDENTITY.txt'
    subprocess.run([str(keygen),'-o',str(identity)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    public = subprocess.check_output([str(keygen),'-y',str(identity)],text=True).strip()
    subprocess.run([str(age),'-r',public,'-o',str(encrypted),str(raw)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert encrypted.stat().st_size > 128*1024*1024
    settings=directory/'blob.json';settings.write_text(json.dumps({'accountName':'scopeshield0123456789abc'}))
    largest=0; calls=[]
    class Reader:
        status=200
        def __enter__(self):self.f=remote.open('rb');return self
        def __exit__(self,*args):self.f.close()
        def read(self,n):
            nonlocal largest
            assert 0<n<=TRANSFER_CHUNK_BYTES;largest=max(largest,n);return self.f.read(n)
    def send(req):
        nonlocal largest
        calls.append(req.method)
        if req.method=='GET':return Reader()
        assert req.get_header('If-none-match')=='*' and req.get_header('Content-length')==str(encrypted.stat().st_size)
        if remote.exists():raise HTTPError(req.full_url,412,'exists',{},None)
        partial=directory/'fake-remote.partial'
        with partial.open('xb') as f:
            for chunk in req.data:
                largest=max(largest,len(chunk));assert len(chunk)<=TRANSFER_CHUNK_BYTES;f.write(chunk)
        os.link(partial,remote)  # Retain partial as audit evidence; fake atomic commit.
        result=Reader();result.status=201;return result
    tracemalloc.start()
    with patch.object(blob,'DATA',directory),patch.object(blob,'SETTINGS',settings),patch.object(blob,'identity',return_value='synthetic-test-token'),patch.object(blob,'send',side_effect=send):
        receipt=blob.upload(encrypted)
        assert blob.upload(encrypted)==receipt  # 412 followed by exact read-back, no overwrite.
    _, python_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert python_peak < 32*1024*1024
    with remote.open('rb') as src,downloaded.open('xb') as dst:shutil.copyfileobj(src,dst,TRANSFER_CHUNK_BYTES)
    assert file_sha256(downloaded)==receipt['encrypted_sha256']
    subprocess.run([str(age),'-d','-i',str(identity),'-o',str(decrypted),str(downloaded)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert file_sha256(decrypted)==snapshot['sha256']
    result=restore(decrypted,restored)
    with closing(sqlite3.connect(restored.as_uri()+'?mode=ro',uri=True)) as db:
        assert db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0]==0
        assert db.execute('SELECT length(data) FROM synthetic_large_payload').fetchone()[0]==135266304
    assert file_sha256(source)==initial_hash and (directory/'.restore-pending').exists()
    try:restore(decrypted,restored)
    except ValueError:pass
    else:raise AssertionError('Restore overwrote an existing database')
    report={'synthetic_only':True,'real_network_calls':0,'source_bytes':source.stat().st_size,
        'encrypted_bytes':encrypted.stat().st_size,'max_transfer_chunk_bytes':largest,
        'python_tracemalloc_peak_bytes':python_peak,'age_version':subprocess.check_output([str(age),'--version'],text=True).strip(),
        'schema':inspect_database(restored),'encrypted_readback_verified':True,'decryption_hash_verified':True,
        'sessions_revoked':result['sessions_revoked'],'restore_quarantined':result['quarantined'],
        'existing_restore_target_refused':True,'original_source_unchanged':True,'fake_http_methods':calls,
        'limits':'Python transfer allocation measured; not whole-host or age subprocess peak RSS'}
    (directory/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'artifact':str(directory/'verification.json'),**report},indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--age',required=True);parser.add_argument('--keygen',required=True)
    args=parser.parse_args();verify(args.age,args.keygen)
