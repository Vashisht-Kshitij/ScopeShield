import unittest
from app.paypal import extract_invoice_id


class FakeResponse:
    def __init__(self, body=None, headers=None):
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


class InvoiceParserTests(unittest.TestCase):
    def test_location(self):
        response = FakeResponse(headers={"Location": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-ABCDE-12345"})
        self.assertEqual(extract_invoice_id(response), "INV2-ABCDE-12345")

    def test_id(self):
        self.assertEqual(extract_invoice_id(FakeResponse(body={"id": "INV2-ABC-DEF"})), "INV2-ABC-DEF")

    def test_links(self):
        response = FakeResponse(body={"links": [{"href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-XXX-YYY"}]})
        self.assertEqual(extract_invoice_id(response), "INV2-XXX-YYY")


if __name__ == "__main__":
    unittest.main()
