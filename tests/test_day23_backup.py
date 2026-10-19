"""Bounded transfer failures and SQLite's enforced recoverable envelope."""
import hashlib
from io import BytesIO
import json
import sqlite3
import time
from urllib.error import HTTPError

import pytest
from sqlalchemy.exc import OperationalError

from app import database
from app.storage_limits import MAX_DATABASE_BYTES, MAX_ENCRYPTED_BACKUP_BYTES, TRANSFER_CHUNK_BYTES
from deploy.azure.host import blob_backup as blob


class Response(BytesIO):
    status = 200


def test_sqlite_ceiling_is_enforced_on_independent_connections(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'MAX_DATABASE_BYTES', 2*1024*1024)
    engine = database.build_engine('sqlite:///'+(tmp_path/'bounded.db').as_posix())
    with engine.begin() as c: c.exec_driver_sql('CREATE TABLE sample (data BLOB)')
    for _ in range(2):
        with engine.connect() as c:
            assert c.exec_driver_sql('PRAGMA max_page_count').scalar()*c.exec_driver_sql('PRAGMA page_size').scalar() == 2*1024*1024
            with pytest.raises(OperationalError) as e: c.exec_driver_sql('INSERT INTO sample VALUES(zeroblob(3000000))')
            assert e.value.orig.sqlite_errorcode == sqlite3.SQLITE_FULL
        engine.dispose()
    assert (tmp_path/'bounded.db').stat().st_size <= 2*1024*1024


def test_database_limit_and_ciphertext_envelope_allow_age_chunk_overhead():
    assert MAX_DATABASE_BYTES == 1024**3
    assert MAX_ENCRYPTED_BACKUP_BYTES > MAX_DATABASE_BYTES + (MAX_DATABASE_BYTES//65536+1)*16+4096
    assert TRANSFER_CHUNK_BYTES <= 4*1024*1024


def setup_backup(tmp_path, monkeypatch, size):
    p = tmp_path/'backup-20261019T000000Z-01234567.db.age'
    with p.open('wb') as f:
        f.write(b'age-encryption.org/v1\nSYNTHETIC-OPAQUE-TRANSPORT-FIXTURE')
        f.truncate(size)
    settings = tmp_path/'settings.json';settings.write_text(json.dumps({'accountName':'scopeshield0123456789abc'}))
    monkeypatch.setattr(blob,'DATA',tmp_path);monkeypatch.setattr(blob,'SETTINGS',settings)
    monkeypatch.setattr(blob,'identity',lambda:'synthetic-test-token')
    return p


@pytest.mark.parametrize('failure',['interrupt','short','long','corrupt','expired','denied','conflict'])
def test_failed_streams_never_publish_receipt_or_remove_local_file(tmp_path,monkeypatch,failure):
    p = setup_backup(tmp_path,monkeypatch, 1024)
    original = p.read_bytes()
    def send(req):
        if req.method=='PUT':
            if failure=='expired':raise HTTPError(req.full_url,401,'unauthorized',{},None)
            if failure=='denied':raise HTTPError(req.full_url,403,'denied',{},None)
            if failure=='conflict':raise HTTPError(req.full_url,412,'exists',{},None)
            for block in req.data:
                if failure=='interrupt':raise OSError('synthetic connection interruption')
            r=Response();r.status=201;return r
        return Response(original[:-1] if failure=='short' else original+b'x' if failure=='long' else b'x'*len(original))
    monkeypatch.setattr(blob,'send',send)
    with pytest.raises(ValueError,match='local evidence retained'):blob.upload(p)
    assert p.read_bytes()==original and not list(tmp_path.glob('*.uploaded.json'))


def test_upload_larger_than_old_boundary_uses_bounded_streams(tmp_path,monkeypatch):
    p = setup_backup(tmp_path,monkeypatch,129*1024*1024)
    maximum = {'upload':0,'download':0};calls=[]
    class Reader:
        status=200
        def __enter__(self):self.f=p.open('rb');return self
        def __exit__(self,*args):self.f.close()
        def read(self,n):
            assert 0<n<=TRANSFER_CHUNK_BYTES
            maximum['download']=max(maximum['download'],n);return self.f.read(n)
    def send(req):
        calls.append(req.method)
        if req.method=='GET':return Reader()
        assert req.get_header('If-none-match')=='*'
        assert int(req.get_header('Content-length'))==p.stat().st_size
        received=0;digest=hashlib.sha256()
        for block in req.data:
            maximum['upload']=max(maximum['upload'],len(block));received+=len(block);digest.update(block)
        with p.open('rb') as f:assert digest.hexdigest()==hashlib.file_digest(f,'sha256').hexdigest()
        assert received==p.stat().st_size
        r=Response();r.status=201;return r
    monkeypatch.setattr(blob,'send',send)
    receipt=blob.upload(p)
    assert receipt['remoteReadbackVerified'] and calls==['PUT','GET']
    assert max(maximum.values())<=TRANSFER_CHUNK_BYTES and p.exists()


def test_transfer_deadline_and_truncated_source_fail_closed():
    with pytest.raises(ValueError,match='deadline'):list(blob.chunks(BytesIO(b'abc'),3,time.monotonic()-1))
    with pytest.raises(ValueError,match='changed'):list(blob.chunks(BytesIO(b'ab'),3,time.monotonic()+10))


def test_real_urllib_transport_uses_content_length_without_chunked_encoding(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    p=setup_backup(tmp_path,monkeypatch,8192);received={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_PUT(self):
            received['transfer_encoding']=self.headers.get('Transfer-Encoding')
            received['body']=self.rfile.read(int(self.headers['Content-Length']))
            received['exclusive']=self.headers.get('If-None-Match')
            self.send_response(201);self.send_header('Content-Length','0');self.end_headers()
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Length',str(len(received['body'])));self.end_headers();self.wfile.write(received['body'])
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        monkeypatch.setattr(blob,'endpoint',lambda *args:f'http://127.0.0.1:{server.server_port}/synthetic-only')
        assert blob.upload(p)['remoteReadbackVerified']
    finally:server.shutdown();server.server_close();thread.join(5)
    assert received['transfer_encoding'] is None and received['exclusive']=='*'
    assert received['body']==p.read_bytes()
