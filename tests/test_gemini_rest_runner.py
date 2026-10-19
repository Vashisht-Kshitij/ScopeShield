"""Real loopback app HTTP; Gemini alone is mocked. No external provider request."""
import json

import pytest
import requests

from app.gemini import API_BASE, DEFAULT_MODEL, GeminiModelRouter, MAX_OUTPUT_TOKENS
from scripts import verify_gemini_rest as runner
from test_gemini import ProviderResponse, envelope, http
from test_gemini_rest_integration import reference_output


def payload():
    return {"contents":[{"parts":[{"text":json.dumps({"untrusted_contract_text":runner.SOURCE.read_text(encoding="utf-8").strip()})}]}],
        "generationConfig":{"responseMimeType":"application/json","maxOutputTokens":MAX_OUTPUT_TOKENS}}


def test_real_http_auth_upload_extraction_and_persistence_with_mocked_gemini(tmp_path,http):
    queue,calls=http
    queue.append(ProviderResponse(body=envelope(reference_output())))
    result=runner.exercise_application(lambda:GeminiModelRouter("dummy_key",models=(DEFAULT_MODEL,),
        free_tier_models=(DEFAULT_MODEL,),allow_live=True,max_model_calls=1),tmp_path)
    assert result["http_transport"]=="real loopback HTTP" and result["application_http_status"]==200
    assert result["anonymous_contract_status"]==result["after_logout_contract_status"]==401
    assert result["all_checks_pass"] and result["api_read_matches"] and result["persisted_in_fresh_connection"]
    assert result["audit_actions"]==["EXTRACTION_SAVED","EXTRACTION_STARTED","CONTRACT_UPLOADED"]
    assert not any(result["financial_and_review_counts"].values()) and len(calls)==1


@pytest.mark.parametrize("change",["wrong_source","schema_field","token_cap","tools","metadata"])
def test_budget_blocks_any_request_outside_the_exact_approved_json_mode_plan(tmp_path,change):
    data=payload(); method="POST"; url=f"{API_BASE}/{DEFAULT_MODEL}:generateContent"
    if change=="wrong_source":data["contents"][0]["parts"][0]["text"]='{"untrusted_contract_text":"different"}'
    if change=="schema_field":data["generationConfig"]["responseJsonSchema"]={}
    if change=="token_cap":data["generationConfig"]["maxOutputTokens"]+=1
    if change=="tools":data["tools"]=[]
    if change=="metadata":method="GET";url=API_BASE
    budget=runner.VerifiedRequestBudget((DEFAULT_MODEL,),1,tmp_path/"attempt.json",lambda *a,**k:pytest.fail("Unexpected external request"))
    with pytest.raises(AssertionError):budget.request(None,method,url,json=data)
    assert not budget.attempts and budget.blocked_requests==1


def test_runner_requires_new_explicit_approval_before_starting_app_or_creating_marker(monkeypatch,tmp_path):
    monkeypatch.setattr(runner,"OUTPUT",tmp_path)
    monkeypatch.setattr("sys.argv",["verify_gemini_rest"])
    with pytest.raises(SystemExit) as caught:runner.main()
    assert caught.value.code==2 and not (tmp_path/"authorization.lock").exists()


def test_failed_schema_keeps_redacted_response_diagnostics_and_failure_token_usage(tmp_path,http,monkeypatch):
    queue,calls=http
    data=reference_output(); data["total_value"]["value"]=1500.0
    body=envelope(data)
    body.update(modelVersion=DEFAULT_MODEL,usageMetadata={"promptTokenCount":17,"candidatesTokenCount":23,"totalTokenCount":40},serverDebug="dummy_key")
    queue.append(ProviderResponse(body=body))
    fake=requests.request
    budget=runner.VerifiedRequestBudget((DEFAULT_MODEL,),1,tmp_path/"attempts.json",lambda session,*args,**kwargs:fake(*args,**kwargs))
    monkeypatch.setattr(requests,"request",lambda method,url,**kwargs:budget.request(None,method,url,**kwargs))
    result=runner.exercise_application(lambda:GeminiModelRouter("dummy_key",models=(DEFAULT_MODEL,),
        free_tier_models=(DEFAULT_MODEL,),allow_live=True,max_model_calls=1),tmp_path)
    captured=json.loads((tmp_path/"provider-response.json").read_text(encoding="utf-8"))
    assert result["application_http_status"]==502 and not result["persisted_in_fresh_connection"]
    assert result["token_usage"]["totalTokenCount"]==40 and result["response_model"]==DEFAULT_MODEL
    assert captured["http_status"]==200 and not captured["truncated"]
    assert "dummy_key" not in json.dumps(captured) and "[redacted]" in captured["body"]
    assert captured["validation"]=={"stage":"schema","error_count":1,"errors":[{"type":"value_error","loc":["total_value","value"]}]}
    assert len(calls)==len(budget.attempts)==1 and not any(result["financial_and_review_counts"].values())


def test_response_capture_is_bounded_and_never_changes_forwarded_stream(tmp_path):
    raw=b"x"*(runner.MAX_RESPONSE_BYTES+20)
    budget=runner.VerifiedRequestBudget((DEFAULT_MODEL,),1,tmp_path/"attempts.json",
        lambda *a,**k:ProviderResponse(raw=raw))
    response=budget.request(None,"POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent",json=payload())
    assert b"".join(response.iter_content(chunk_size=8192))==raw
    captured=json.loads((tmp_path/"provider-response.json").read_text(encoding="utf-8"))
    assert captured["captured_bytes"]==runner.MAX_RESPONSE_BYTES and len(captured["body"])==runner.MAX_RESPONSE_BYTES
    assert captured["truncated"] and captured["validation"]=={"stage":"size_limit"}


def test_schema_diagnostics_never_include_input_context_or_untrusted_field_names():
    data=reference_output(); data["untrusted_private_key_name"]="untrusted_private_input"
    diagnosis=runner.schema_diagnostic(json.dumps(envelope(data)).encode())
    assert diagnosis=={"stage":"schema","error_count":1,"errors":[{"type":"extra_forbidden","loc":["<unexpected_field>"]}]}
    assert "untrusted_private" not in json.dumps(diagnosis)


def test_redaction_expansion_cannot_exceed_capture_limit(tmp_path):
    raw=b"Z"*runner.MAX_RESPONSE_BYTES
    budget=runner.VerifiedRequestBudget((DEFAULT_MODEL,),1,tmp_path/"attempts.json",lambda *a,**k:ProviderResponse(raw=raw))
    response=budget.request(None,"POST",f"{API_BASE}/{DEFAULT_MODEL}:generateContent",json=payload(),headers={"x-goog-api-key":"Z"})
    assert b"".join(response.iter_content(chunk_size=8192))==raw
    captured=json.loads((tmp_path/"provider-response.json").read_text(encoding="utf-8"))
    assert captured["redaction_truncated"] and len(captured["body"].encode())<=runner.MAX_RESPONSE_BYTES
    assert "Z" not in captured["body"]


@pytest.mark.parametrize("identifier",["../outside","verification-../outside","CON","verification-"+"a"*41])
def test_followup_identifier_cannot_escape_or_create_unsafe_directories(monkeypatch,tmp_path,identifier):
    monkeypatch.setattr(runner,"OUTPUT",tmp_path)
    monkeypatch.setattr("sys.argv",["verify_gemini_rest","--approved-free-tier-once","--verification-id",identifier])
    with pytest.raises(SystemExit) as caught:runner.main()
    assert caught.value.code==2 and not list(tmp_path.iterdir())


def test_consumed_authorization_marker_cannot_be_reused_or_overwritten(monkeypatch,tmp_path):
    marker=tmp_path/"authorization.lock"; marker.write_text("spent",encoding="utf-8")
    monkeypatch.setattr(runner,"OUTPUT",tmp_path)
    monkeypatch.setattr("sys.argv",["verify_gemini_rest","--approved-free-tier-once"])
    monkeypatch.setattr(runner,"exercise_application",lambda *a,**k:pytest.fail("Spent authorization reached app"))
    with pytest.raises(FileExistsError):runner.main()
    assert marker.read_text(encoding="utf-8")=="spent"
