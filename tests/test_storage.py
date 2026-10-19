"""Exact money encoding and the database type chosen for PostgreSQL."""
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql, sqlite

from app.database import ExactMoney


@pytest.mark.parametrize("value", [0.1, "0.10", True, Decimal("NaN"), Decimal("-1"), Decimal("0.001")])
def test_money_storage_refuses_floats_or_invalid_values(value):
    with pytest.raises(ValueError):
        ExactMoney().process_bind_param(value, sqlite.dialect())


def test_money_storage_has_exact_sqlite_and_postgresql_representations():
    column_type = ExactMoney()
    assert column_type.process_bind_param(Decimal("0.10"), sqlite.dialect()) == "0.10"
    assert column_type.process_result_value("0.10", sqlite.dialect()) == Decimal("0.10")
    assert column_type.process_bind_param(Decimal("0.10"), postgresql.dialect()) == Decimal("0.10")
    assert str(column_type.load_dialect_impl(postgresql.dialect())) == "NUMERIC(18, 2)"
