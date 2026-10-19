"""Closed-by-default network guard for separately approved Day 10 UI operations.

Only the isolated verification process uses this module. Never installs a gate or
approval API in the normal application. No email, payment, billing or metadata.
"""
import hashlib
import json
from pathlib import Path
import re
import threading

from app.day6_fixture import WALKTHROUGH, SCENARIOS
from app.gemini import API_BASE, DEFAULT_MODEL, MAX_OUTPUT_TOKENS
from app.paypal import BASE_URL, extract_invoice_id, PayPalAPIError
from scripts.verify_gemini_rest import VerifiedRequestBudget
from scripts.verify_gemini_fallback import save

SOURCE_SHA256="f38ef779d57b939d004657564de8257654529566d06a9d302d496a7f91be844f"


def matches_committed_reservation(engine,plan,options):
    """PayPal uses the durable link ID; the browser key is a separate identity."""
    from sqlalchemy.orm import Session
    from app.storage import InvoiceLink
    with Session(engine) as session:
        row=session.get(InvoiceLink,options.get("headers",{}).get("PayPal-Request-Id"))
        return bool(row and row.state=="CREATING" and row.milestone_id==plan.get("milestone_id")
            and row.contract_id==plan.get("contract_id")
            and row.snapshot.get("preview",{}).get("preview_digest")==plan.get("preview_digest")
            and row.invoice_number==options.get("json",{}).get("detail",{}).get("invoice_number"))


class Day10NetworkGuard:
    def __init__(self,directory:Path,original,recipient,reservation_check=None):
        self.directory,self.original,self.recipient=directory,original,recipient
        self.lock=threading.RLock()
        self.reservation_check=reservation_check or (lambda plan,options:False)
        self.budgets={}
        for operation,work in [("gemini-extraction",None),("gemini-scope",tuple(row[0] for row in SCENARIOS))]:
            folder=directory/operation;folder.mkdir(parents=True,exist_ok=True)
            self.budgets[operation]=VerifiedRequestBudget((DEFAULT_MODEL,),1,folder/"attempts.json",original,
                source=WALKTHROUGH,source_sha256=SOURCE_SHA256,requested_changes=work)

    def approval(self,operation):
        try:
            raw=(self.directory/f"{operation}-request.json").read_bytes()
            plan=json.loads(raw)
            grant=json.loads((self.directory/f"{operation}-approval.json").read_text(encoding="utf-8"))
            if (grant.get("approved") is not True or grant.get("operation")!=operation
                    or grant.get("request_sha256")!=hashlib.sha256(raw).hexdigest()):raise ValueError
            return plan
        except (OSError,ValueError,TypeError):
            raise AssertionError("New explicit approval for this exact operation is required") from None

    def claim(self,name,fields):
        with (self.directory/f"{name}.json").open("x",encoding="utf-8") as handle:
            json.dump(fields,handle);handle.flush()
            import os
            os.fsync(handle.fileno())

    def request(self,session,method,url,**kwargs):
        with self.lock:
            if method.upper()=="POST" and url==f"{API_BASE}/{DEFAULT_MODEL}:generateContent":
                payload=kwargs.get("json",{})
                user=json.loads(payload["contents"][0]["parts"][0]["text"])
                operation="gemini-extraction" if "untrusted_contract_text" in user else "gemini-scope"
                plan=self.approval(operation)
                if (plan.get("source_sha256")!=SOURCE_SHA256 or plan.get("model")!=DEFAULT_MODEL
                        or plan.get("max_calls")!=1 or plan.get("max_output_tokens")!=MAX_OUTPUT_TOKENS):
                    raise AssertionError("AI plan differs from the approved bounded request")
                return self.budgets[operation].request(session,method,url,**kwargs)
            plan=self.approval("paypal-draft")
            if plan.get("amount")!="1.00" or plan.get("currency")!="USD" or plan.get("recipient_email")!=self.recipient:
                raise AssertionError("PayPal plan differs from the reviewed USD 1 recipient")
            if method.upper()=="POST" and url==BASE_URL+"/v1/oauth2/token":
                if kwargs.get("data")!={"grant_type":"client_credentials"}:raise AssertionError("Unexpected OAuth request")
                self.claim("paypal-oauth",{"operation":"oauth","calls":1})
                response=self.original(session,method,url,**kwargs)
                save(self.directory/"paypal-oauth.json",{"operation":"oauth","calls":1,"http_status":response.status_code})
                return response
            if method.upper()=="POST" and url==BASE_URL+"/v2/invoicing/invoices":
                body=kwargs.get("json",{});items=body.get("items",[])
                valid=(len(items)==1 and items[0].get("unit_amount")=={"currency_code":"USD","value":"1.00"}
                    and set(items[0])=={"name","quantity","unit_amount","description"}
                    and items[0].get("quantity")=="1" and items[0].get("name")==plan.get("name")
                    and items[0].get("description")==plan.get("description")
                    and body.get("primary_recipients")==[{"billing_info":{"email_address":self.recipient}}]
                    and body.get("detail",{}).get("currency_code")=="USD"
                    and set(body.get("detail",{}))<={"currency_code","note","invoice_number"}
                    and bool(re.fullmatch(r"SS-[a-f0-9]{22}",body.get("detail",{}).get("invoice_number","")))
                    and not body.get("additional_recipients") and set(body)=={"detail","invoicer","primary_recipients","items"})
                if not valid:raise AssertionError("Invoice differs from the reviewed one-item USD 1 draft")
                if not self.reservation_check(plan,kwargs):raise AssertionError("No matching approved milestone reservation")
                intent={"operation":"create_sandbox_draft","amount":"1.00","currency":"USD","http_status":None}
                self.claim("paypal-create",intent)
                response=self.original(session,method,url,**kwargs)
                intent["http_status"]=response.status_code
                try:intent["invoice_id"]=extract_invoice_id(response) if response.status_code==201 else None
                except PayPalAPIError:intent["invoice_id"]=None
                save(self.directory/"paypal-create.json",intent)
                return response
            created=self.directory/"paypal-create.json"
            record=json.loads(created.read_text(encoding="utf-8")) if created.exists() else {}
            identity=record.get("invoice_id")
            if method.upper()=="GET" and identity and url==BASE_URL+f"/v2/invoicing/invoices/{identity}":
                journal=self.directory/"paypal-reads.json"
                rows=json.loads(journal.read_text(encoding="utf-8")) if journal.exists() else []
                if len(rows)>=3:raise AssertionError("Read-only invoice verification budget consumed")
                row={"operation":"read_invoice","invoice_id":identity,"http_status":None};rows.append(row);save(journal,rows)
                response=self.original(session,method,url,**kwargs);row["http_status"]=response.status_code;save(journal,rows)
                return response
            raise AssertionError("Only the separately approved draft and its bounded verification reads are allowed")
