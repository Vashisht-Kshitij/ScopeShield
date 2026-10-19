"""Create/serve only NEW isolated synthetic workspaces. No external HTTP or payment.

Passwords use getpass. `create` starts empty; --complete explicitly scripts review
decisions and a MOCK draft for rehearsal, never real client approval or an invoice.
"""
import argparse
from contextlib import ExitStack, contextmanager
from getpass import getpass
import json
import os
from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
NOTICE = 'SCRIPTED SYNTHETIC REHEARSAL: no real client acceptance, AI inference, invoice or payment.'
REVIEW = {'reviewer_label': 'Synthetic rehearsal reviewer', 'note': NOTICE}


def location(workspace):
    base = ROOT / '.local/day24'
    workspace = Path(workspace).resolve()
    if (not base.resolve().is_relative_to(ROOT.resolve()) or workspace.parent != base.resolve()
            or not re.fullmatch(r'demo-[a-f0-9]{32}', workspace.name)):
        raise ValueError('Use only a new Day 24 synthetic workspace under .local/day24')
    return workspace


@contextmanager
def offline_application(workspace):
    from app.database import build_engine
    from app.main import create_app, get_paypal
    from app.storage import InvoiceLink
    from scripts.mock_paypal import MockPayPal
    workspace = location(workspace)
    marker_path = workspace / 'synthetic.json'
    database = workspace / 'demo.db'
    if marker_path.is_symlink() or database.is_symlink() or not database.is_file():
        raise ValueError('A marked synthetic database is required')
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    if marker != {'synthetic_only': True, 'policy': 'day24-offline-v1'}:
        raise ValueError('A marked synthetic database is required')
    def forbidden(*args, **kwargs):
        raise AssertionError('Day 24 offline demo prohibits external provider HTTP')
    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'SCOPESHIELD_DATABASE_URL': 'sqlite:///'+database.as_posix(),
            'SCOPESHIELD_ENV': 'local', 'SCOPESHIELD_AI_PROVIDER': 'disabled', 'SCOPESHIELD_AI_ALLOW_LIVE': 'false'}))
        stack.enter_context(patch('requests.sessions.Session.request', forbidden))
        app = create_app(storage_url='sqlite:///'+database.as_posix())
        paypal = MockPayPal()
        # Restore only this fixture's mocked DRAFT/SENT records after restart.
        # Never import real identifiers or simulate payment confirmation.
        history = build_engine('sqlite:///'+database.as_posix())
        try:
            with Session(history) as db:
                for link in db.scalars(select(InvoiceLink)):
                    value = link.snapshot.get('preview', {})
                    if (value.get('provider_mode') != 'mocked_paypal_sandbox'
                            or not (link.invoice_id or '').startswith('INV2-MOCK-')
                            or link.paypal_status not in {'DRAFT', 'SENT'}):
                        raise ValueError('Refuse non-mock or unsupported invoice history')
                    paypal.invoices[link.invoice_id] = {'invoice_number': link.invoice_number, 'name': value['name'],
                        'description': value['description'], 'amount': link.amount, 'status': link.paypal_status}
        finally:
            history.dispose()
        app.dependency_overrides[get_paypal] = lambda: paypal
        try:
            yield app, paypal
        finally:
            if hasattr(app.state, 'engine'): app.state.engine.dispose()


def create_workspace(username, password, *, complete=False):
    from app.auth import hash_password, normalize_username
    from app.database import build_engine, initialize_database
    from app.storage import User, SecurityEvent
    # Validate credentials before creating any files. Never accept an existing DB.
    username, encoded = normalize_username(username), hash_password(password)
    workspace = location(ROOT / '.local/day24' / ('demo-'+uuid4().hex))
    workspace.mkdir(parents=True, exist_ok=False)
    database = workspace / 'demo.db'
    descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    (workspace / 'synthetic.json').write_text(json.dumps({'synthetic_only': True, 'policy': 'day24-offline-v1'}))
    engine = build_engine('sqlite:///'+database.as_posix())
    try:
        initialize_database(engine)
        with Session(engine) as db:
            user = User(username=username, display_name='Synthetic release reviewer', role='freelancer', password_hash=encoded)
            db.add(user); db.flush()
            db.add(SecurityEvent(actor_id=user.id, action='ACCOUNT_CREATED'))
            db.commit()
    finally:
        engine.dispose()
    if complete:
        with offline_application(workspace) as (app, paypal):
            replay(app, username, password, workspace)
            assert len(paypal.creates) == 1 and not paypal.sends
    return workspace


def replay(app, username, password, workspace):
    """Explicit fake complete fixture through real auth, validators and actions."""
    from fastapi.testclient import TestClient
    from app.day24_fixture import scenario
    def check(response, status=200):
        if response.status_code != status:
            raise RuntimeError('Synthetic workflow failed with HTTP '+str(response.status_code))
        return response.json()
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        csrf = check(client.get('/api/auth/csrf'))['csrf_token']
        signed = check(client.post('/api/auth/login', json={'username': username, 'password': password}, headers={'X-CSRF-Token': csrf}))
        client.headers['X-CSRF-Token'] = signed['csrf_token']
        example = scenario()
        contract = check(client.post('/api/contracts', files={'file': (example['filename'], example['text'].encode(), 'text/plain')}), 201)
        contract = check(client.post(f"/api/contracts/{contract['id']}/extract", json={'mode': 'offline_demo'}))
        first = contract['milestones'][0]
        check(client.post(f"/api/milestones/{first['id']}/approve", json={**REVIEW, 'expected_revision': first['revision'],
            'expected_contract_revision': contract['review_revision'], 'confirmed': True, 'acknowledge_review_flags': True}))
        request = check(client.post(f"/api/contracts/{contract['id']}/change-requests", json={**REVIEW,
            'project_id': contract['project_id'], 'title': 'Synthetic Cedar launch conversation', 'conversation_messages': example['conversation_messages']}), 201)
        request = check(client.post(f"/api/change-requests/{request['id']}/analyze", json={**REVIEW, 'expected_revision': request['revision'], 'mode': 'offline_demo'}))
        findings = request['analysis']['findings']
        findings[1].update(classification='AMBIGUOUS', explanation='Clarify whether FAQ means an additional section or replacement content.',
            uncertainties=['Ask the fictional client to confirm whether an existing section would be replaced.'])
        request = check(client.patch(f"/api/change-requests/{request['id']}/analysis", json={**REVIEW,
            'expected_revision': request['revision'], 'analysis': {'findings': findings}}))
        request = check(client.post(f"/api/change-requests/{request['id']}/analysis/approve", json={**REVIEW,
            'expected_revision': request['revision'], 'confirmed': True, 'acknowledge_review_flags': True}))
        check(client.post(f"/api/change-requests/{request['id']}/change-order", json={**REVIEW,
            'expected_revision': request['revision'], 'terms': {'proposed_deliverables': ['SYNTHETIC PROPOSAL: CMS editor; FAQ, dates and support remain separate negotiation items.'],
            'proposed_price': '300.00', 'currency': 'USD'}}), 201)
        preview = check(client.get(f"/api/milestones/{first['id']}/invoice/preview"))
        invoice = check(client.post(f"/api/milestones/{first['id']}/invoice", json={**REVIEW,
            'expected_revision': preview['milestone_revision'], 'expected_contract_revision': preview['contract_review_revision'],
            'contract_id': contract['id'], 'idempotency_key': str(uuid4()), 'preview_digest': preview['preview_digest'],
            'confirmed': True, 'acknowledge_discrepancies': True, 'discrepancy_note': NOTICE}), 201)
        assert invoice['provider_mode'] == 'mocked_paypal_sandbox' and invoice['paypal_status'] == 'DRAFT'
        preview = check(client.get(f"/api/projects/{contract['project_id']}/evidence-pack"))
        with (workspace / 'evidence-preview.json').open('x', encoding='utf-8') as handle:
            json.dump(preview, handle)
        for executive, name in ((True, 'executive-brief.pdf'), (False, 'complete-evidence.pdf')):
            response = client.get(f"/api/projects/{contract['project_id']}/evidence-pack.pdf", params={'expected_revision': preview['source_revision'], 'executive': executive})
            if response.status_code != 200 or not response.content.startswith(b'%PDF-'):
                raise RuntimeError('Synthetic evidence export failed')
            with (workspace / name).open('xb') as handle: handle.write(response.content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create'); create.add_argument('--username', required=True)
    create.add_argument('--complete', action='store_true', help='Explicitly script synthetic review and one MOCK draft; no live operations')
    serve = sub.add_parser('serve'); serve.add_argument('--workspace', required=True)
    args = parser.parse_args()
    if args.action == 'create':
        password = getpass('Individual demo password (12-128 characters, hidden): ')
        if password != getpass('Confirm password (hidden): '): raise ValueError('Passwords do not match')
        workspace = create_workspace(args.username, password, complete=args.complete)
        del password
        print(json.dumps({'workspace': str(workspace), 'synthetic_only': True, 'live_provider_calls': 0,
            'notice': NOTICE if args.complete else 'Empty synthetic account; all decisions remain manual.'}))
    else:
        import uvicorn
        with offline_application(args.workspace) as (app, _):
            print('Synthetic offline demo only. Mocked PayPal; live provider HTTP blocked. Loopback port 8000.')
            uvicorn.run(app, host='127.0.0.1', port=8000, workers=1, access_log=False)


if __name__ == '__main__':
    main()
