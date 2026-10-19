"""The Day 22 occurrence counterexample through real PDF parsing and review."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.ingestion import ingest
from app.pdf_provenance import verified_provenance
from app.report_content import quote_reference
from app.scope_comparison import validate_comparison
from app.storage import Contract
from test_day15_provenance import synthetic_pdf
from test_scope_changes import api, MockScope, configured, create, analyze, body

QUOTE = 'Up to two revisions are included.'
WORK = 'Please revise the deployment milestone.'
PROVIDER = SimpleNamespace(name='offline_test', live_ai=False)


def output(quote=QUOTE, start=None, end=None):
    return {'findings': [{'ordinal': 1, 'requested_work': WORK, 'classification': 'IN_SCOPE',
        'explanation': 'The cited clause includes the requested revision.',
        'contract_evidence': [{'source_excerpt': quote, 'relationship': 'COVERAGE',
                              'source_start': start, 'source_end': end}],
        'uncertainties': [], 'recommended_action': 'FULFILL_EXISTING_SCOPE', 'requires_human_review': True}]}


def check(raw, text):
    return validate_comparison(json.dumps(raw), text, [WORK], provider=PROVIDER, mode='configured')


@pytest.mark.parametrize('pages,quote,expected_pages', [
    (['Design milestone.\n'+QUOTE, 'Deployment milestone.\n'+QUOTE], QUOTE, [2]),
    ([QUOTE+'\nDeployment milestone.\n'+QUOTE], QUOTE, [1]),
    (['Earlier.\n'+QUOTE, '😀 Café 👩🏽‍💻\n'+QUOTE], QUOTE, [2]),
    (['First line\nSecond line', 'First line\nSecond line'], 'First line\nSecond line', [2]),
    (['One page ends with this', 'clause continuing on the next page'], 'this\n\nclause continuing', [1, 2]),
])
def test_correct_supplied_occurrence_survives_parsing_validation_and_report(pages, quote, expected_pages):
    raw = synthetic_pdf(pages)
    doc = ingest('synthetic.pdf', 'application/pdf', raw)
    start = doc.text.rindex(quote); end = start + len(quote)
    result = check(output(quote, start, end), doc.text)
    e = result.findings[0].contract_evidence[0].model_dump()
    assert (e['source_start'], e['source_end']) == (start, end)
    provenance = verified_provenance(doc.text, doc.page_count, doc.pdf_provenance,
                                     document_sha256=hashlib.sha256(raw).hexdigest())
    assert quote_reference(doc.text, e, provenance)['physical_pages'] == expected_pages
    # Re-export/revalidation must not shift a saved or human-selected occurrence.
    again = check({'findings': [f.model_dump(mode='json') for f in result.findings]}, doc.text)
    assert again.findings[0].contract_evidence[0] == result.findings[0].contract_evidence[0]


@pytest.mark.parametrize('start,end', [(None, None), (0, 1), (1, None), (None, 10), (9999, 10000)])
def test_repeated_missing_fabricated_or_partial_ranges_cannot_claim_a_page(start, end):
    text = 'Design. '+QUOTE+'\n\nDeployment. '+QUOTE
    result = check(output(start=start, end=end), text)
    finding = result.findings[0]
    assert finding.classification == 'AMBIGUOUS' and not finding.contract_evidence
    assert any('CONTRACT_QUOTE_RANGE_UNRESOLVED' in flag for flag in result.review_flags)
    assert not quote_reference(text, output(start=start, end=end)['findings'][0]['contract_evidence'][0])['verified']


def test_unique_offset_free_multiline_quote_preserves_whitespace_normalization():
    text = 'Intro.\nFirst line\n  Second line.\nEnd.'
    result = check(output('First line Second line.'), text)
    e = result.findings[0].contract_evidence[0]
    assert e.source_excerpt == 'First line\n  Second line.'
    assert text[e.source_start:e.source_end] == e.source_excerpt


def test_overlapping_contract_occurrences_are_ambiguous_without_a_range():
    text = 'very very very'
    quote = 'very very'
    finding = check(output(quote), text).findings[0]
    assert finding.classification == 'AMBIGUOUS' and not finding.contract_evidence
    assert not quote_reference(text, {'source_excerpt': quote})['verified']
    for start, end in [(0, 9), (5, 14)]:
        result = check(output(quote, start, end), text)
        evidence = result.findings[0].contract_evidence[0].model_dump()
        assert (evidence['source_start'], evidence['source_end']) == (start, end)
        assert quote_reference(text, evidence)['verified']


@pytest.mark.parametrize('mutation', ['document', 'text', 'missing-document-hash'])
def test_stale_document_or_text_binding_has_no_page_claim(mutation):
    doc = ingest('test.pdf', 'application/pdf', synthetic_pdf([QUOTE, QUOTE]))
    p = deepcopy(doc.pdf_provenance); text = doc.text; digest = doc.sha256
    if mutation == 'document': digest = 'f'*64
    elif mutation == 'text': text += 'changed'
    else: p.pop('document_sha256')
    assert verified_provenance(text, doc.page_count, p, document_sha256=digest) is None


def test_real_pdf_request_edit_preview_and_stale_contract_guard(api, monkeypatch):
    app, client, _ = api
    raw = synthetic_pdf(['Design.\n'+QUOTE, 'Deployment.\n'+QUOTE])
    c = client.post('/api/contracts', files={'file': ('test.pdf', raw, 'application/pdf')}).json()
    start = c['text'].rindex(QUOTE); data = output(start=start, end=start+len(QUOTE))
    configured(monkeypatch, MockScope(data))
    r = analyze(client, create(client, c, [WORK]), 'configured').json()
    assert r['analysis']['findings'][0]['contract_evidence'][0]['source_start'] == start
    edited = client.patch(f"/api/change-requests/{r['id']}/analysis", json={**body(r['revision']), 'analysis': data})
    assert edited.status_code == 200
    preview = client.get(f"/api/projects/{c['project_id']}/evidence-pack")
    assert preview.status_code == 200
    pdf = client.get(f"/api/projects/{c['project_id']}/evidence-pack.pdf", params={'expected_revision': preview.json()['source_revision']})
    assert pdf.status_code == 200
    from io import BytesIO
    from pypdf import PdfReader
    assert 'physical original PDF page(s) 2' in '\n'.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    with Session(app.state.engine) as session:
        session.get(Contract, c['id']).sha256 = 'f'*64; session.commit()
    stale = client.patch(f"/api/change-requests/{r['id']}/analysis", json={**body(edited.json()['revision']), 'analysis': data})
    assert stale.status_code == 409
