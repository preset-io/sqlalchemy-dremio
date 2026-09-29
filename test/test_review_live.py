"""Review regressions against Dremio 26.0.5 (opt in with DREMIO_REVIEW_URL).

Create the Review space and Review.Sub folder first, then run this file.
Only the disposable view Review.Sub.Mixed is created and dropped.
"""
import os
from decimal import Decimal

import pytest
import sqlalchemy as sa


@pytest.fixture(scope='module')
def live_engine():
    url = os.environ.get('DREMIO_REVIEW_URL')
    if not url:
        pytest.skip('set DREMIO_REVIEW_URL for the disposable Dremio server')
    engine = sa.create_engine(url, pool_pre_ping=True, future=True)
    yield engine
    engine.dispose()


@pytest.mark.parametrize('value', [1, -1, Decimal('-1'), Decimal('-0'), Decimal('-0.00'), -0.5])
@pytest.mark.parametrize('bound_tenant', [False, True])
def test_tenant_filter_survives_subtraction(live_engine, value, bound_tenant):
    tenant = ':tenant' if bound_tenant else "'x'"
    sql = ("SELECT * FROM (VALUES (1,'x'),(20,'y')) t(a,tenant) "
           "WHERE a < 100-:n AND tenant = " + tenant)
    params = {'n': value}
    if bound_tenant:
        params['tenant'] = 'x'
    with live_engine.connect() as conn:
        assert [tuple(row) for row in conn.execute(sa.text(sql), params)] == [(1, 'x')]
        assert conn.execute(sa.text('SELECT 1-:n'), {'n': value}).scalar() == 1 - value


@pytest.mark.parametrize('value', [
    '12345678901234567890.123456789', '-12345678901234567890.123456789',
    '99999999999999999999999999999999999999', '1E-38', '1E+20', '0.00100', '-0.00',
])
def test_exact_decimal_roundtrip(live_engine, value):
    value = Decimal(value)
    with live_engine.connect() as conn:
        result = conn.execute(sa.text('SELECT :value'), {'value': value}).scalar()
    assert isinstance(result, Decimal)
    assert result == value
    assert result.as_tuple().exponent == min(value.as_tuple().exponent, 0)


@pytest.mark.parametrize('value', ['1E-40', '1E+38'])
def test_unrepresentable_decimal_rejected(live_engine, value):
    with live_engine.connect() as conn:
        with pytest.raises(sa.exc.ProgrammingError, match='Dremio DECIMAL.*up to 38'):
            conn.execute(sa.text('SELECT :value'), {'value': Decimal(value)})
        assert conn.execute(sa.text('SELECT 1')).scalar() == 1


def test_mixed_case_nested_schema_autoload(live_engine):
    with live_engine.connect() as conn:
        conn.exec_driver_sql('CREATE OR REPLACE VIEW "Review"."Sub"."Mixed" AS '
                             "SELECT CAST('12345678901234567890.123456789' AS DECIMAL(29,9)) AS amount")
    try:
        inspector = sa.inspect(live_engine)
        for schema in ('Review.Sub', 'review.sub', 'REVIEW.SUB'):
            assert inspector.has_table('mixed', schema=schema)
            assert 'Mixed' in inspector.get_view_names(schema=schema)
            assert 'Mixed' not in inspector.get_table_names(schema=schema)
            table = sa.Table('mixed', sa.MetaData(), schema=schema, autoload_with=live_engine)
            assert (table.c.amount.type.precision, table.c.amount.type.scale) == (29, 9)
            with pytest.raises(sa.exc.NoSuchTableError):
                inspector.get_columns('absent', schema=schema)
    finally:
        with live_engine.connect() as conn:
            conn.exec_driver_sql('DROP VIEW "Review"."Sub"."Mixed"')
