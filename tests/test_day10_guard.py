"""No external traffic: prove the live browser plan is closed until separately approved."""
import hashlib
import json
from pathlib import Path

import pytest
from app.gemini import API_BASE,DEFAULT_MODEL,MAX_OUTPUT_TOKENS
from app.extraction import build_messages
from app.day6_fixture import WALKTHROUGH,SCENARIOS
from app.paypal import BASE_URL
from scripts.day10_guard import Day10NetworkGuard,SOURCE_SHA256,matches_committed_reservation
from test_gemini import ProviderResponse,envelope
from test_gemini_rest_integration import reference_output
from test_milestone_invoices import api,approve,creation


def grant(folder,operation,plan):
    raw=json.dumps(plan).encode();(folder/f"{operation}-request.json").write_bytes(raw)
    (folder/f"{operation}-approval.json").write_text(json.dumps({"approved":True,"operation":operation,"request_sha256":hashlib.sha256(raw).hexdigest()}),encoding="utf-8")


def ai_payload():
    messages=build_messages(WALKTHROUGH.read_text(encoding="utf-8").strip())
    return {"systemInstruction":{"parts":[{"text":messages[0]["content"]}]},
        "contents":[{"parts":[{"text":messages[1]["content"]}]}],
        "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":MAX_OUTPUT_TOKENS}}


def ai_plan():return {"source_sha256":SOURCE_SHA256,"model":DEFAULT_MODEL,"max_calls":1,"max_output_tokens":MAX_OUTPUT_TOKENS}
def paypal_plan():return {"amount":"1.00","currency":"USD","recipient_email":"buyer@personal.example.com","name":"Wireframe","description":"Reviewed synthetic work"}
def draft():return {"detail":{"currency_code":"USD","invoice_number":"SS-"+"a"*22},"invoicer":{},
    "primary_recipients":[{"billing_info":{"email_address":"buyer@personal.example.com"}}],
    "items":[{"name":"Wireframe","description":"Reviewed synthetic work","quantity":"1","unit_amount":{"currency_code":"USD","value":"1.00"}}]}


@pytest.mark.parametrize("method,url",[("POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent"),("GET",API_BASE),
    ("POST",BASE_URL+"/v1/oauth2/token"),("POST",BASE_URL+"/v2/invoicing/invoices")])
def test_no_operation_reaches_network_without_exact_new_approval(tmp_path,method,url):
    gate=Day10NetworkGuard(tmp_path,lambda *a,**k:pytest.fail("Unapproved network request"),paypal_plan()["recipient_email"])
    with pytest.raises(AssertionError):gate.request(None,method,url,json=ai_payload())


def test_ai_budget_survives_backend_restart_and_never_repeats(tmp_path):
    calls=[]
    def actual(*args,**kwargs):calls.append(args);return ProviderResponse(body=envelope(reference_output()))
    grant(tmp_path,"gemini-extraction",ai_plan())
    gate=Day10NetworkGuard(tmp_path,actual,paypal_plan()["recipient_email"])
    response=gate.request(None,"POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent",json=ai_payload())
    list(response.iter_content(chunk_size=8192))
    restarted=Day10NetworkGuard(tmp_path,actual,paypal_plan()["recipient_email"])
    with pytest.raises(AssertionError):restarted.request(None,"POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent",json=ai_payload())
    assert len(calls)==1


def test_changed_approval_plan_hash_is_rejected(tmp_path):
    grant(tmp_path,"gemini-extraction",ai_plan())
    (tmp_path/"gemini-extraction-request.json").write_text("{}",encoding="utf-8")
    gate=Day10NetworkGuard(tmp_path,lambda *a,**k:pytest.fail("Changed plan forwarded"),paypal_plan()["recipient_email"])
    with pytest.raises(AssertionError):gate.request(None,"POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent",json=ai_payload())


@pytest.mark.parametrize("change",["amount","recipient","tax","extra_item","send","pay"])
def test_invoice_amount_recipient_and_forbidden_actions_cannot_bypass_approval(tmp_path,change):
    grant(tmp_path,"paypal-draft",paypal_plan());body=draft();url=BASE_URL+"/v2/invoicing/invoices"
    if change=="amount":body["items"][0]["unit_amount"]["value"]="2.00"
    if change=="recipient":body["primary_recipients"][0]["billing_info"]["email_address"]="other@example.com"
    if change=="tax":body["items"][0]["tax"]={"percent":"5"}
    if change=="extra_item":body["items"].append(body["items"][0].copy())
    if change in {"send","pay"}:url+="/INV2-TEST-1234/"+change
    gate=Day10NetworkGuard(tmp_path,lambda *a,**k:pytest.fail("Unsafe financial request forwarded"),paypal_plan()["recipient_email"])
    with pytest.raises(AssertionError):gate.request(None,"POST",url,json=body)


def test_invoice_create_intent_blocks_duplicate_even_after_restart(tmp_path):
    grant(tmp_path,"paypal-draft",paypal_plan());calls=[]
    response=type("Response",(),{"status_code":201,"json":lambda self:{"id":"INV2-TEST-1234-5678-ABCD"},"headers":{}})()
    def actual(*a,**k):calls.append(a);return response
    gate=Day10NetworkGuard(tmp_path,actual,paypal_plan()["recipient_email"],lambda *a:True)
    gate.request(None,"POST",BASE_URL+"/v2/invoicing/invoices",json=draft())
    restarted=Day10NetworkGuard(tmp_path,actual,paypal_plan()["recipient_email"],lambda *a:True)
    with pytest.raises(FileExistsError):restarted.request(None,"POST",BASE_URL+"/v2/invoicing/invoices",json=draft())
    assert len(calls)==1 and json.loads((tmp_path/"paypal-create.json").read_text())["http_status"]==201


def test_real_application_commits_link_id_before_guarded_provider_call(api):
    app,client,cid,paypal=api
    row=approve(client,cid)
    preview=client.get(f"/api/milestones/{row['id']}/invoice/preview").json()
    payload=creation(client,row)
    observed=[]
    def verify_committed():
        call=paypal.creates[-1]
        options={"headers":{"PayPal-Request-Id":call["request_id"]},
            "json":{"detail":{"invoice_number":call["invoice_number"]}}}
        # A new connection sees the committed reservation while the API is creating.
        assert call["request_id"]!=payload["idempotency_key"]
        assert matches_committed_reservation(app.state.engine,preview,options)
        assert not matches_committed_reservation(app.state.engine,preview,
            {**options,"headers":{"PayPal-Request-Id":payload["idempotency_key"]}})
        for field in ("milestone_id","contract_id","preview_digest"):
            assert not matches_committed_reservation(app.state.engine,{**preview,field:"changed"},options)
        assert not matches_committed_reservation(app.state.engine,preview,
            {**options,"json":{"detail":{"invoice_number":"SS-"+"0"*22}}})
        observed.append(options)
    paypal.after_create=verify_committed
    result=client.post(f"/api/milestones/{row['id']}/invoice",json=payload)
    assert result.status_code==201,result.json()
    assert len(observed)==1
    # An already completed reservation never authorizes another provider create.
    assert not matches_committed_reservation(app.state.engine,preview,observed[0])
