"""Isolated browser QA: real auth/storage/fixtures and explicitly mocked PayPal.

All external HTTP is blocked. Never use this module as the normal app entry point.
"""
import requests
from app.main import create_app, get_paypal
from scripts.mock_paypal import MockPayPal


def forbidden(*args, **kwargs):
    raise AssertionError("Browser QA cannot call any external provider")


requests.sessions.Session.request = forbidden
app = create_app()
paypal = MockPayPal()
app.dependency_overrides[get_paypal] = lambda: paypal
