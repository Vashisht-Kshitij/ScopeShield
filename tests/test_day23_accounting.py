"""Deterministic duplicate accounting cannot be changed by model prose/effects."""
import json
from types import SimpleNamespace
from uuid import uuid4

from app.conversation_fixture import PATH, catalog, compare
from app.conversations import message_accounting, requested_work
from app.scope_comparison import validate_comparison
from app.project_intelligence import intelligence
from app.evidence_pack import capture
from app.storage import ChangeRequest
from sqlalchemy.orm import Session
from test_scope_changes import api, analyze
from test_conversations import create


def test_duplicate_modelled_individual_is_excluded_by_source_identity(api):
    app,client,_=api
    c,r=create(client,0)
    a=analyze(client,r).json()['analysis']
    a['findings'][2]['conversation_effect']='INDIVIDUAL'
    a['findings'][2]['message_evidence']=[e for e in a['findings'][2]['message_evidence'] if e['message_id']==r['conversation_messages'][2]['id']]
    # Review prose flags are not authoritative for accounting.
    a['review_flags']=[]
    with Session(app.state.engine) as s:
        s.get(ChangeRequest,r['id']).analysis=a;s.commit()
    response=client.get(f"/api/projects/{c['project_id']}/evidence-pack")
    assert response.status_code==200
    actual=response.json()['intelligence']
    assert actual['requests'][0]['individual_classifications']=={'IN_SCOPE':2}
    assert sum(actual['classifications'].values())==1 and actual['outstanding']['value'] is None
    validated=validate_comparison(json.dumps({'findings':a['findings']}),c['text'],r['requested_changes'],provider=SimpleNamespace(name='test',live_ai=False),mode='offline_demo',conversation=r['conversation_messages'])
    accounted=client.get(f"/api/change-requests/{r['id']}").json()['message_accounting']
    assert accounted[2]['duplicate_of_message_id']==accounted[0]['message_id']
    assert not accounted[2]['counts_as_individual']
    assert sum(m['counts_as_individual'] for m in accounted)==2
    # Existing strict stored-analysis JSON shape remains backward compatible.
    assert 'message_accounting' not in validated.model_dump(mode='json')


def test_conflicting_requests_and_shared_clause_are_not_exact_duplicates():
    messages=[{'id':str(uuid4()),'sequence':i+1,'role':'client','content':content} for i,content in enumerate(['Keep the layout.','Replace the layout.','Keep the layout.'])]
    findings=[SimpleNamespace(ordinal=i+1,conversation_effect=effect) for i,effect in enumerate(['INDIVIDUAL','CONFLICTING','INDIVIDUAL'])]
    accounted=message_accounting(messages,findings)
    assert [r['counts_as_individual'] for r in accounted]==[True,True,False]
    assert accounted[2]['duplicate_of_message_id']==messages[0]['id']
    # Supplied chronological order determines canonical ID after an edit; IDs persist.
    reordered=[{**m,'sequence':i+1} for i,m in enumerate(reversed(messages))]
    again=message_accounting(reordered,findings)
    assert again[2]['duplicate_of_message_id']==messages[2]['id']
