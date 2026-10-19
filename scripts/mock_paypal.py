"""Explicit in-memory PayPal fixture for isolated browser QA. No HTTP/email/payment."""
from uuid import uuid4
from app.paypal import PayPalAPIError


class MockPayPal:
    mocked = True
    recipient_email = "buyer@personal.example.com"

    def __init__(self):
        self.invoices = {}
        self.creates = []
        self.sends = []

    def create_invoice(self, **payload):
        self.creates.append(payload)
        identity = "INV2-MOCK-" + uuid4().hex[:16].upper()
        self.invoices[identity] = {"invoice_number": payload.get("invoice_number"), "name": payload["milestone"],
            "description": payload.get("description"), "amount": payload["amount_usd"], "status": "DRAFT"}
        return identity

    def get_invoice(self, invoice_id):
        row = self.invoices.get(invoice_id)
        if row is None: raise PayPalAPIError("RESOURCE_NOT_FOUND", operation="get_invoice", http_status=404)
        return {"invoice_id": invoice_id, "status": row["status"], "currency_code": "USD",
                "total_amount": format(row["amount"], ".2f"), "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#MOCK-ONLY"}

    def verify_milestone_invoice(self, invoice_id, **expected):
        row = self.invoices.get(invoice_id)
        if row is None: raise PayPalAPIError("RESOURCE_NOT_FOUND", operation="get_invoice", http_status=404)
        if any(row[key] != expected[key] for key in ("invoice_number", "name", "description", "amount")) or expected.get("recipient_email", self.recipient_email) != self.recipient_email:
            raise PayPalAPIError("INVOICE_LINK_MISMATCH", operation="verify_invoice", http_status=409)
        return self.get_invoice(invoice_id)

    def find_milestone_invoice(self, **expected):
        for identity, row in self.invoices.items():
            if row["invoice_number"] == expected["invoice_number"]:
                return self.verify_milestone_invoice(identity, **expected)
        return None

    def send_milestone_invoice(self, invoice_id, **expected):
        self.verify_milestone_invoice(invoice_id, **expected)
        self.send_invoice(invoice_id)

    def send_invoice(self, invoice_id):
        row = self.invoices[invoice_id]
        if row["status"] != "DRAFT": raise PayPalAPIError("INVOICE_NOT_DRAFT", operation="send_invoice", http_status=409)
        self.sends.append(invoice_id)
        row["status"] = "SENT"
