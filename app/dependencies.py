"""Shared sandbox dependency; constructing it does not make a provider call."""
from functools import lru_cache

from app.paypal import PayPalSandbox


@lru_cache(maxsize=1)
def get_paypal() -> PayPalSandbox:
    return PayPalSandbox.from_environment()
