"""Validated local API contracts; payment status always comes from PayPal."""
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

INVOICE_ID_PATTERN = r"^INV2-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
InvoiceID = Annotated[str, Field(pattern=INVOICE_ID_PATTERN, min_length=10, max_length=85)]


class DraftInvoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    milestone: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)
    ] = Field(examples=["Website design milestone 1"])
    amount_usd: Decimal = Field(
        gt=Decimal("0"), le=Decimal("1000"), decimal_places=2,
        allow_inf_nan=False, examples=["1.00"],
    )


class InvoiceResponse(BaseModel):
    invoice_id: InvoiceID
    status: str
    currency_code: str | None = None
    total_amount: str | None = None
    recipient_view_url: str | None = None
