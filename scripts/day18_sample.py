"""Build a synthetic Evidence Pack in a NEW disposable DB; never use real records.

All network transport is blocked. Review actions below are scripted fixture
simulations, explicitly attributed in their notes. Invoice history is seeded as
simulated data, never created/sent through any payment provider.
"""
from contextlib import ExitStack
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def sample(output):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from app.auth import hash_password
    from app.main import create_app
    from app.storage import User, InvoiceLink, InvoiceEvent, Project
    from app.conversation_fixture import catalog, PATH
    from app.evidence_pack import digest
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / 'output' / 'pdf') or output.exists():
        raise ValueError('Use a new file under output/pdf; existing artifacts are preserved')
    directory = ROOT / '.local' / 'day18' / ('sample-' + uuid4().hex)
    directory.mkdir(parents=True)
    password = secrets.token_urlsafe(24)
    environment = {'SCOPESHIELD_DATABASE_URL': 'sqlite:///' + (directory / 'synthetic.db').as_posix(),
                   'SCOPESHIELD_AI_PROVIDER': 'disabled', 'SCOPESHIELD_AI_ALLOW_LIVE': 'false'}
    def forbidden(*args, **kwargs): raise AssertionError('Sample generation prohibits external providers')
    context = {'reviewer_label': 'Synthetic fixture reviewer', 'note': 'SCRIPTED OFFLINE SIMULATION: demonstrates recorded review states; no real client or human consent is asserted.'}
    def check(response, status=200):
        if response.status_code != status: raise RuntimeError('Synthetic fixture workflow failed: ' + str(response.status_code))
        return response.json()
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch('requests.sessions.Session.request', forbidden))
        stack.enter_context(patch('requests.request', forbidden))
        app = create_app(storage_url=environment['SCOPESHIELD_DATABASE_URL'])
        client = stack.enter_context(TestClient(app, base_url='http://127.0.0.1:8000'))
        with Session(app.state.engine) as db:
            user = User(username='synthetic.report', display_name='Synthetic scripted reviewer', role='freelancer', password_hash=hash_password(password))
            db.add(user); db.commit(); user_id = user.id
        login_csrf = check(client.get('/api/auth/csrf'))['csrf_token']
        login = check(client.post('/api/auth/login', json={'username': 'synthetic.report', 'password': password}, headers={'X-CSRF-Token': login_csrf}))
        client.headers['X-CSRF-Token'] = login['csrf_token']
        path = ROOT / 'examples' / 'day5_sandbox_agreement.txt'
        contract = check(client.post('/api/contracts', files={'file': (path.name, path.read_bytes(), 'text/plain')}), 201)
        project_id = contract['project_id']
        with Session(app.state.engine) as db:
            db.get(Project, project_id).title = 'SYNTHETIC Evidence Pack collection - independent milestone and conversation examples'
            db.commit()
        check(client.post(f"/api/contracts/{contract['id']}/extract", json={'mode': 'offline_demo'}))
        milestones = check(client.get(f"/api/contracts/{contract['id']}/milestones"))['milestones']
        first = milestones[0]
        check(client.post(f"/api/milestones/{first['id']}/approve", json={**context, 'expected_revision': first['revision'], 'expected_contract_revision': first['contract_review_revision'], 'confirmed': True, 'acknowledge_review_flags': True}))
        # Demonstration-only persisted histories. No invoice/payment operation runs.
        with Session(app.state.engine) as db:
            for milestone, state in zip(milestones, ['DRAFT', 'SENT', 'PAID']):
                identity = uuid4()
                link = InvoiceLink(id=str(identity), milestone_id=milestone['id'], contract_id=contract['id'],
                    milestone_revision=milestone['revision'], contract_review_revision=milestone['contract_review_revision'],
                    idempotency_key=str(uuid4()), request_fingerprint='0' * 64, invoice_number='SIM-' + identity.hex[:20],
                    snapshot={'preview': {'provider_mode': 'mocked_paypal_sandbox'}, 'fixture_notice': 'SIMULATED HISTORY ONLY - no provider invoice exists'},
                    amount=Decimal('1.00'), currency='USD', state='CREATED', invoice_id='INV2-SIM-' + identity.hex[:12].upper(),
                    paypal_status=state, verified=False)
                db.add(link); db.flush()
                db.add(InvoiceEvent(actor_id=user_id, milestone_id=milestone['id'], link_id=link.id,
                    action='SIMULATED_HISTORY_IMPORTED', reviewer_label=context['reviewer_label'], note=context['note'],
                    data={'simulated': True, 'paypal_status': state}))
            db.commit()
        for index, scenario in enumerate(catalog()):
            contract = check(client.post('/api/contracts', files={'file': ('synthetic-conversation.txt', PATH.read_bytes(), 'text/plain')}, data={'project_id': project_id}), 201)
            row = check(client.post(f"/api/contracts/{contract['id']}/change-requests", json={**context, 'project_id': project_id, 'title': scenario['title'], 'conversation_messages': scenario['conversation_messages']}), 201)
            row = check(client.post(f"/api/change-requests/{row['id']}/analyze", json={**context, 'expected_revision': row['revision'], 'mode': 'offline_demo'}))
            if index == 1:
                from copy import deepcopy
                findings = deepcopy(row['analysis']['findings'])
                findings[0]['explanation'] = 'Synthetic reviewed interpretation: the FAQ adds a distinct section beyond the agreed sections.'
                row = check(client.patch(f"/api/change-requests/{row['id']}/analysis", json={**context, 'expected_revision': row['revision'], 'analysis': {'findings': findings}}))
                row = check(client.post(f"/api/change-requests/{row['id']}/analysis/approve", json={**context, 'expected_revision': row['revision'], 'confirmed': True, 'acknowledge_review_flags': True}))
                check(client.post(f"/api/change-requests/{row['id']}/change-order", json={**context, 'expected_revision': row['revision'], 'terms': {'proposed_deliverables': ['SYNTHETIC PROPOSAL: additional FAQ section and editing system'], 'proposed_price': '75.00', 'currency': 'USD'}}), 201)
        summary = check(client.get(f'/api/projects/{project_id}/evidence-pack'))
        pdf = client.get(f'/api/projects/{project_id}/evidence-pack.pdf', params={'expected_revision': summary['source_revision']})
        if pdf.status_code != 200 or not pdf.content.startswith(b'%PDF-'): raise RuntimeError('Synthetic report failed')
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('xb') as handle: handle.write(pdf.content)
        # This public manifest contains only synthetic references, never login data.
        manifest = {'synthetic_only': True, 'scripted_review_simulation': True, 'provider_calls': 0,
                    'source_revision': summary['source_revision'], 'counts': summary['counts'],
                    'pdf_sha256': __import__('hashlib').sha256(pdf.content).hexdigest()}
        with output.with_suffix('.manifest.json').open('x') as handle: json.dump(manifest, handle, indent=2)
        return manifest


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'output/pdf/scopeshield-synthetic-evidence-pack.pdf'))
    print(json.dumps(sample(parser.parse_args().output)))
