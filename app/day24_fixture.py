"""One fixed release walkthrough; output is simulation, never general inference."""
import json
from pathlib import Path

from app.conversation_fixture import COVERAGE, LIMIT, DEADLINE, MAINTENANCE, finding
from app.conversations import requested_work
from app.extraction import ExtractionError, normalize

PATH = Path(__file__).resolve().parents[1] / 'examples/day24_cedar_agreement.txt'
MESSAGES = (
    'Please update the headline wording during the first revision round.',
    'Could you also add a small FAQ section?',
    'Please give our team a content management system to edit the page.',
    'Can you deliver on 2026-11-12 instead?',
    'Please keep the page updated after launch and cover weekend support.',
)


def scenario():
    return {'id': 'release-cedar', 'title': 'Cedar launch: one agreement, growing requests',
        'description': 'Synthetic USD 1,500 agreement, five ordered messages, grounded offline analysis and separate human decisions. No real Gemini or PayPal call.',
        'filename': PATH.name, 'text': PATH.read_text(encoding='utf-8'),
        'supports_extraction': True, 'requested_changes': [], 'mode': 'offline_demo', 'live_ai': False,
        'conversation_messages': [{'sequence': i, 'sender': 'Alex (synthetic client)', 'role': 'client',
            'content': content, 'sent_at': None} for i, content in enumerate(MESSAGES, 1)]}


def require_contract(text):
    if normalize(text) != normalize(PATH.read_text(encoding='utf-8')):
        raise ExtractionError('OFFLINE_DEMO_CONTRACT_MISMATCH', 'Use the unchanged synthetic Cedar agreement', 422)


def extract(text):
    require_contract(text)
    from app.day4_fixture import fixture_output
    result = json.loads(fixture_output(PATH))
    # This fixed contract supplies every milestone field; the photography fixture
    # reader's deliberately missing-field flags do not apply to this agreement.
    result['review_required_fields'] = []
    return json.dumps(result)


def compare(text, messages):
    require_contract(text)
    fields = ('sequence', 'sender', 'role', 'content', 'sent_at')
    if [{k: m[k] for k in fields} for m in messages] != scenario()['conversation_messages']:
        raise ExtractionError('OFFLINE_DEMO_CONTRACT_MISMATCH', 'Use the unchanged Cedar conversation', 422)
    work = requested_work(messages)
    clauses = (COVERAGE, LIMIT, LIMIT, DEADLINE, MAINTENANCE)
    relationships = ('COVERAGE', 'EXCLUSION', 'EXCLUSION', 'LIMIT', 'EXCLUSION')
    kinds = ('REVISIONS', 'DELIVERABLE', 'DELIVERABLE', 'DEADLINE', 'MAINTENANCE')
    findings = [finding(i, work[i-1], 'IN_SCOPE' if i == 1 else 'OUT_OF_SCOPE',
        'The requested work is assessed against the exact quoted agreement clause.',
        clauses[i-1], relationships[i-1], [message], [kinds[i-1]], 'INDIVIDUAL')
        for i, message in enumerate(messages, 1)]
    findings.append(finding(len(messages)+1, work[-1], 'OUT_OF_SCOPE',
        'The included headline revision does not authorize extra sections, an editing system, an earlier schedule or ongoing support. Review those additions together before negotiating separate terms.',
        LIMIT, 'EXCLUSION', messages, ['DELIVERABLE', 'DEADLINE', 'MAINTENANCE'], 'CUMULATIVE'))
    return json.dumps({'findings': findings})
