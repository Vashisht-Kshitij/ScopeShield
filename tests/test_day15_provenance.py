"""Original PDF regressions, strict physical-page references and isolated demo data."""
import hashlib
from io import BytesIO
import json

from alembic import command
from fastapi.testclient import TestClient
import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.contracts import contract_response
from app.database import build_engine, migration_config, initialize_database
from app.ingestion import ingest
from app.pdf_provenance import page_provenance, verified_provenance
from app.storage import AIUsage, ChangeRequest, Contract, ContractDocument, InvoiceLink, Milestone, ScopeEvent, User
from scripts.provision_judge import provision
from scripts.sqlite_backup import backup, restore
from test_day14_deployment import public_app, public_config, login, password


@pytest.fixture(autouse=True)
def authenticated_legacy_clients():
    yield  # Real authentication/authorization; no convenience login fixture.


def synthetic_pdf(pages, *, unusual=False):
    """Small content streams with explicit non-BMP ToUnicode mappings.

    Unusual mode writes lower text before visually higher text. The Unicode font
    mapping intentionally tests extraction, not glyph rendering or visual fidelity.
    """
    writer = PdfWriter()
    non_ascii = sorted(set("".join(pages)) - set(chr(n) for n in range(128)))
    codes = {char: n + 128 for n, char in enumerate(non_ascii)}
    mapping = DecodedStreamObject()
    entries = [f'<{code:02X}> <{char.encode("utf-16-be").hex()}>' for char, code in codes.items()]
    mapping.set_data(('''/CIDInit /ProcSet findresource begin 12 dict begin begincmap
/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def
/CMapName /TestUnicode def /CMapType 2 def
1 begincodespacerange <00> <FF> endcodespacerange
1 beginbfrange <00> <7F> <0000> endbfrange
''' + f'{len(entries)} beginbfchar\n' + '\n'.join(entries) + '\nendbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end').encode('ascii'))
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        NameObject('/ToUnicode'): writer._add_object(mapping)})
    font_ref = writer._add_object(font)
    for content in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font_ref})})
        commands = []
        for index, line in enumerate(content.splitlines()):
            encoded = bytes(codes[char] if char in codes else ord(char) for char in line)
            y = 100 + index * 20 if unusual else 740 - index * 14
            commands.append(f'BT /F1 10 Tf 40 {y} Td <{encoded.hex()}> Tj ET')
        stream = DecodedStreamObject()
        stream.set_data('\n'.join(commands).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize('pages', [
    ['  Multiline paragraph\nSecond line with payment USD 100.\nThird line.  '],
    ['Same clause USD 100.', 'Same clause USD 100.'],
    ['😀 Café Ω payment USD 100.', '🧪 Second page: payment USD 100.'],
    ['Payment is due only after', 'written approval and delivery.'],
    ['', 'Physical page two has the clause.', ''],
])
def test_actual_pdf_pages_preserve_exact_text_and_each_occurrence(pages):
    document = ingest('provenance.pdf', 'application/pdf', synthetic_pdf(pages))
    provenance = verified_provenance(document.text, len(pages), document.pdf_provenance)
    assert provenance is not None
    for number, original in enumerate(pages, 1):
        span = provenance['pages'][number - 1]
        excerpt = document.text[span['start']:span['end']]
        assert ' '.join(excerpt.split()) == ' '.join(original.split())
        assert span['page'] == number
    if '😀' in ''.join(pages):
        assert '😀' in document.text and '🧪' in document.text
    if len(pages) == 2 and pages[0] == pages[1]:
        assert provenance['pages'][0]['end'] < provenance['pages'][1]['start']


def test_unusual_content_stream_order_maps_pages_without_claiming_visual_order():
    document = ingest('order.pdf', 'application/pdf', synthetic_pdf(['Lower visual line\nHigher visual line', 'Another page'], unusual=True))
    assert document.text.index('Lower') < document.text.index('Higher')
    assert len(document.pdf_provenance['pages']) == 2
    assert document.text[document.pdf_provenance['pages'][1]['start']:].strip() == 'Another page'


def test_pdf_pages_do_not_authorize_wrong_milestone_amount_association():
    from app.day4_fixture import PATH, fixture_output
    from app.extraction import validate_extraction
    from contract_fixtures import MockExtractionProvider
    pages = PATH.read_text(encoding='utf-8').split('\n\n')
    doc = ingest('agreement.pdf', 'application/pdf', synthetic_pdf(pages))
    data = json.loads(fixture_output())
    first, second = data['milestones'][:2]
    first['amount'], second['amount'] = second['amount'], first['amount']
    provider = MockExtractionProvider(data)
    result = validate_extraction(provider.output, doc.text, provider=provider, mode='configured')
    assert [row.amount.value for row in result.data.milestones[:2]] == [None, None]
    assert result.totals_match is None
    assert doc.pdf_provenance and any(i.code == 'MILESTONE_EVIDENCE_MISMATCH' for i in result.review_issues)


def test_cross_page_quote_has_two_distinct_page_intersections():
    document = ingest('split.pdf', 'application/pdf', synthetic_pdf(['Start payment clause', 'continues after approval']))
    start = document.text.index('payment')
    end = document.text.index('approval') + len('approval')
    assert [p['page'] for p in document.pdf_provenance['pages'] if p['start'] < end and p['end'] > start] == [1, 2]


@pytest.mark.parametrize('mutation', ['digest', 'overlap', 'invented_page', 'missing_content', 'missing'])
def test_invalid_mapping_has_truthful_fallback(mutation):
    source = 'One\n\nTwo'
    mapping = page_provenance(['One', 'Two'])
    if mutation == 'digest': mapping['text_sha256'] = '0' * 64
    if mutation == 'overlap': mapping['pages'][1]['start'] = 0
    if mutation == 'invented_page': mapping['pages'][1]['page'] = 99
    if mutation == 'missing_content': mapping['pages'][1]['start'] += 1
    if mutation == 'missing': mapping = None
    assert verified_provenance(source, 2, mapping) is None


def test_original_download_requires_owner_and_preserves_exact_bytes(public_app):
    app, client = public_app
    assert client.get('/api/contracts/00000000-0000-0000-0000-000000000000/original').status_code == 401
    login(client)
    raw = synthetic_pdf(['Repeat clause', '😀 Repeat clause'])
    response = client.post('/api/contracts', files={'file': ('untrusted.pdf', raw, 'application/pdf')})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['original_pdf_available'] and saved['pdf_provenance']['pages'][1]['page'] == 2
    cid = saved['id']
    original = client.get(f'/api/contracts/{cid}/original')
    assert original.status_code == 200 and original.content == raw
    assert hashlib.sha256(original.content).hexdigest() == saved['sha256']
    assert original.headers['content-disposition'] == f'attachment; filename="agreement-{cid}.pdf"'
    assert original.headers['cache-control'] == 'no-store'
    assert original.headers['x-content-type-options'] == 'nosniff'
    assert original.headers['content-security-policy'].startswith('sandbox;')
    with Session(app.state.engine) as db:
        contract = db.get(Contract, cid)
        assert 'data' in inspect(contract.document).unloaded
        assert contract_response(contract)['pdf_provenance']
        assert 'data' in inspect(contract.document).unloaded
        owner = db.scalar(select(User).where(User.username == 'judge.one'))
        owner.role = 'client'
        db.commit()
    assert client.get(f'/api/contracts/{cid}/original').status_code == 403
    with Session(app.state.engine) as db:
        owner = db.scalar(select(User).where(User.username == 'judge.one')); owner.role = 'freelancer'; db.commit()
    client.post('/api/auth/logout', json={})
    login(client, 'judge.two')
    assert client.get(f'/api/contracts/{cid}/original').status_code == 404


def test_legacy_and_malformed_pdfs_never_gain_invented_originals(public_app):
    app, client = public_app
    login(client)
    cid = client.get('/api/contracts').json()[0]['id']
    legacy = client.get(f'/api/contracts/{cid}').json()
    assert not legacy['original_pdf_available'] and legacy['pdf_provenance'] is None
    assert client.get(f'/api/contracts/{cid}/original').status_code == 404
    malformed = client.post('/api/contracts', files={'file': ('broken.pdf', b'%PDF-1.7\nnot a document', 'application/pdf')})
    assert malformed.status_code == 400
    with Session(app.state.engine) as db:
        assert db.scalar(select(func.count()).select_from(ContractDocument)) == 0


def test_original_integrity_failure_does_not_serve_wrong_document(public_app):
    app, client = public_app
    login(client)
    raw = synthetic_pdf(['Original text'])
    saved = client.post('/api/contracts', files={'file': ('source.pdf', raw, 'application/pdf')}).json()
    with Session(app.state.engine) as db:
        db.get(ContractDocument, saved['id']).data = synthetic_pdf(['Altered text'])
        db.commit()
    assert client.get(f'/api/contracts/{saved["id"]}/original').status_code == 409


def test_pdf_retention_and_download_budgets_preserve_saved_data(public_app, monkeypatch):
    app, client = public_app
    login(client)
    raw = synthetic_pdf(['Retained source'])
    saved = client.post('/api/contracts', files={'file': ('source.pdf', raw, 'application/pdf')}).json()
    monkeypatch.setenv('SCOPESHIELD_PDF_USER_STORAGE_BYTES', str(len(raw)))
    monkeypatch.setenv('SCOPESHIELD_RATE_DOCUMENT_DOWNLOAD', '1')
    assert client.post('/api/contracts', files={'file': ('extra.pdf', raw, 'application/pdf')}).status_code == 507
    path = f'/api/contracts/{saved["id"]}/original'
    assert client.get(path).content == raw
    assert client.get(path).status_code == 429
    with Session(app.state.engine) as db:
        assert db.scalar(select(func.count()).select_from(ContractDocument)) == 1
        assert db.scalar(select(func.count()).select_from(Contract)) == 3  # Two seeds + one successful upload.


def test_prepared_judges_have_validated_candidates_and_no_financial_or_live_records(public_app):
    app, client = public_app
    with Session(app.state.engine) as db:
        user_id = provision(db, 'judge.prepared', password(), prepared=True)
        contract = db.scalar(select(Contract).join(Contract.project).where(Contract.project.has(owner_id=user_id)))
        milestones = db.scalars(select(Milestone).where(Milestone.contract_id == contract.id)).all()
        assert len(milestones) == 3 and all(row.status == 'REVIEW_REQUIRED' and row.reviewed_at is None for row in milestones)
        assert contract.extraction['mode'] == 'offline_demo' and contract.extraction['live_ai'] is False
        request = db.scalar(select(ChangeRequest).where(ChangeRequest.contract_id == contract.id))
        assert {f['classification'] for f in request.analysis['findings']} == {'IN_SCOPE', 'OUT_OF_SCOPE', 'AMBIGUOUS'}
        for finding in request.analysis['findings']:
            for quote in finding['contract_evidence']:
                assert contract.text[quote['source_start']:quote['source_end']] == quote['source_excerpt']
        assert db.scalar(select(ScopeEvent).where(ScopeEvent.change_request_id == request.id)).actor_id == user_id
        assert db.scalar(select(func.count()).select_from(InvoiceLink)) == db.scalar(select(func.count()).select_from(AIUsage)) == 0
        with pytest.raises(ValueError, match='already exists'): provision(db, 'judge.prepared', password(), prepared=True)
    login(client, 'judge.prepared')
    assert len(client.get('/api/contracts').json()) == 1


def test_pdf_migration_backup_restore_preserves_originals_and_refuses_loss(tmp_path):
    engine = build_engine('sqlite:///' + (tmp_path / 'seed.db').as_posix())
    with engine.begin() as connection:
        config = migration_config(); config.attributes['connection'] = connection
        command.upgrade(config, '0007_resource_controls')
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO projects(id,status,created_at) VALUES ('legacy','DRAFT',CURRENT_TIMESTAMP)"))
    initialize_database(engine)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM projects WHERE id='legacy'")) == 1
    with Session(engine) as db:
        user_id = provision(db, 'isolated.judge', password(), prepared=True)
        seed = db.scalar(select(Contract).where(Contract.filename == 'day10_sandbox_agreement.txt'))
        raw = synthetic_pdf(['Private original', 'Second physical page'])
        doc = ingest('source.pdf', 'application/pdf', raw)
        contract = Contract(project_id=seed.project_id, filename=doc.filename, media_type=doc.media_type,
            text=doc.text, sha256=doc.sha256, size_bytes=doc.size_bytes, page_count=doc.page_count)
        db.add(contract); db.flush()
        db.add(ContractDocument(contract_id=contract.id, data=raw, provenance=doc.pdf_provenance))
        db.commit()
        cid = contract.id
    with pytest.raises(RuntimeError, match='original PDF documents'):
        with engine.begin() as connection:
            config = migration_config(); config.attributes['connection'] = connection
            command.downgrade(config, '0007_resource_controls')
    engine.dispose()
    snapshot = tmp_path / 'backup.db'
    backup(tmp_path / 'seed.db', snapshot)
    restored = tmp_path / 'restored.db'
    restore(snapshot, restored)
    restored_engine = build_engine('sqlite:///' + restored.as_posix())
    with restored_engine.begin() as connection:
        config = migration_config(); config.attributes['connection'] = connection
        command.check(config)
    with Session(restored_engine) as db:
        assert db.get(ContractDocument, cid).data == raw
        assert db.get(ContractDocument, cid).provenance == doc.pdf_provenance
        assert db.get(User, user_id)
    restored_engine.dispose()
