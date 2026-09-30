"""SQLAlchemy 2 / DB-API regressions, run without a Dremio server.

Only the Arrow Flight transport is replaced: a fake client answers
``get_flight_info``/``do_get`` with Arrow tables and records the SQL text it
receives, so the dialect, compiler, DB-API module, parameter binding and
Arrow-to-Python conversion all run unmodified.
"""

import datetime
import decimal
import warnings

import pyarrow as pa
import pytest
import sqlalchemy as sa
from pyarrow import flight
from sqlalchemy.engine import URL

from sqlalchemy_dremio import db
from sqlalchemy_dremio.exceptions import OperationalError, ProgrammingError


class _Info:
    def __init__(self, sql):
        self.endpoints = [type("Endpoint", (), {"ticket": sql})()]


class _Reader:
    def __init__(self, table):
        self.table = table
        self.batches = list(table.to_batches())

    def read_all(self):
        return self.table

    def read_chunk(self):
        # FlightStreamReader's chunk API, used by earlier releases.
        if not self.batches:
            raise StopIteration
        return self.batches.pop(0), None


class FakeFlightClient:
    """Answers by exact SQL text, or by the first registered prefix."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.sent = []
        self.closed = False

    def get_flight_info(self, descriptor, options=None):
        sql = descriptor.command.decode()
        self.sent.append(sql)
        answer = self.answers.get(sql)
        if answer is None:
            for prefix, value in self.answers.items():
                if sql.startswith(prefix):
                    answer = value
                    break
        if isinstance(answer, BaseException):
            raise answer
        if answer is None:
            raise AssertionError("unexpected SQL: %r" % sql)
        return _Info(sql)

    def do_get(self, ticket, options=None):
        answer = self.answers.get(ticket)
        if answer is None:
            for prefix, value in self.answers.items():
                if ticket.startswith(prefix):
                    answer = value
                    break
        return _Reader(answer)

    def authenticate_basic_token(self, user, password):
        self.credentials = (user, password)
        return (b"authorization", b"Bearer token")

    def close(self):
        self.closed = True


@pytest.fixture
def make_engine(monkeypatch):
    """An engine whose real DB-API connections talk to a FakeFlightClient."""

    def factory(client):
        monkeypatch.setattr(flight, "FlightClient", lambda *args, **kwargs: client)
        return sa.create_engine("dremio+flight://user:pass@localhost:32010/?UseEncryption=false")

    return factory


def one_column(name, values, arrow_type):
    return pa.table({name: pa.array(values, type=arrow_type)})


# --- engine and dialect ---


def test_create_engine_and_compile_emit_no_deprecation_or_cache_warnings(make_engine):
    client = FakeFlightClient({"SELECT ": one_column("id", [1], pa.int32())})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        engine = make_engine(client)
        with engine.connect() as conn:
            stmt = sa.select(sa.table("t", sa.column("id"), schema="$scratch"))
            conn.execute(stmt).all()
    assert engine.dialect.supports_statement_cache is True
    assert engine.dialect.paramstyle == "qmark"


def test_import_dbapi_is_native():
    from sqlalchemy_dremio.flight import DremioDialect_flight

    assert "import_dbapi" in DremioDialect_flight.__dict__
    assert DremioDialect_flight.import_dbapi() is db


def test_password_with_separator_characters_reaches_flight_intact(monkeypatch):
    created = []

    def factory(location, middleware=None, **kwargs):
        client = FakeFlightClient()
        client.location = location
        created.append(client)
        return client

    monkeypatch.setattr(flight, "FlightClient", factory)
    engine = sa.create_engine(
        URL.create(
            "dremio+flight",
            username="u",
            password="p;w=d@x",
            host="localhost",
            port=32010,
            query={"UseEncryption": "false"},
        )
    )
    engine.raw_connection()
    assert created[0].credentials == ("u", "p;w=d@x")
    assert created[0].location == "grpc+tcp://localhost:32010"


# --- reflection ---


def _information_schema_answers():
    return {
        "SHOW SCHEMAS": one_column("SCHEMA_NAME", ["$scratch", "@dremio", "sys"], pa.string()),
        'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_TYPE <> \'VIEW\'': one_column(
            "TABLE_NAME", ["t"], pa.string()
        ),
        'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_TYPE = \'VIEW\'': one_column(
            "TABLE_NAME", ["v"], pa.string()
        ),
        'SELECT COUNT(*) FROM INFORMATION_SCHEMA."TABLES"': one_column("EXPR$0", [1], pa.int64()),
        "SELECT COLUMN_NAME, DATA_TYPE": pa.table(
            {
                "COLUMN_NAME": ["id", "amount", "ts", "d", "txt", "f", "dbl", "bin", "arr"],
                "DATA_TYPE": [
                    "INTEGER",
                    "DECIMAL",
                    "TIMESTAMP",
                    "DATE",
                    "CHARACTER VARYING",
                    "FLOAT",
                    "DOUBLE",
                    "BINARY VARYING",
                    "ARRAY",
                ],
                "NUMERIC_PRECISION": pa.array([32, 12, None, None, None, 24, 53, None, None], pa.int32()),
                "NUMERIC_SCALE": pa.array([0, 2, None, None, None, None, None, None, None], pa.int32()),
                "IS_NULLABLE": ["NO", "YES", "YES", "YES", "YES", "YES", "YES", "YES", "YES"],
            }
        ),
    }


def test_reflection_executes_textual_sql_under_sqlalchemy_2(make_engine):
    client = FakeFlightClient(_information_schema_answers())
    inspector = sa.inspect(make_engine(client))
    assert inspector.get_schema_names() == ["$scratch", "@dremio", "sys"]
    assert inspector.get_table_names(schema="$scratch") == ["t"]
    assert inspector.get_view_names(schema="@dremio") == ["v"]
    assert inspector.has_table("t", schema="$scratch") is True
    assert (
        'SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_TYPE <> \'VIEW\''
        " AND UPPER(TABLE_SCHEMA) = UPPER('$scratch')"
    ) in client.sent


def test_get_columns_types_decimal_scale_and_nullability(make_engine):
    client = FakeFlightClient(_information_schema_answers())
    columns = sa.inspect(make_engine(client)).get_columns("t", schema="$scratch")
    by_name = {c["name"]: c for c in columns}
    assert [c["name"] for c in columns] == ["id", "amount", "ts", "d", "txt", "f", "dbl", "bin", "arr"]
    assert isinstance(by_name["id"]["type"], sa.INTEGER)
    assert by_name["id"]["nullable"] is False
    assert isinstance(by_name["amount"]["type"], sa.DECIMAL)
    assert (by_name["amount"]["type"].precision, by_name["amount"]["type"].scale) == (12, 2)
    assert isinstance(by_name["ts"]["type"], sa.TIMESTAMP)
    assert isinstance(by_name["d"]["type"], sa.DATE)
    assert isinstance(by_name["txt"]["type"], sa.VARCHAR)
    assert isinstance(by_name["f"]["type"], sa.Float)
    assert isinstance(by_name["dbl"]["type"], sa.Float)
    assert isinstance(by_name["bin"]["type"], sa.types.VARBINARY)
    assert isinstance(by_name["arr"]["type"], sa.types.NullType)
    assert client.sent[-1] == (
        "SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE "
        'FROM INFORMATION_SCHEMA."COLUMNS" '
        "WHERE UPPER(TABLE_SCHEMA) = UPPER('$scratch') AND UPPER(TABLE_NAME) = UPPER('t') ORDER BY ORDINAL_POSITION"
    )


def test_reflection_escapes_quotes_in_names(make_engine):
    answers = _information_schema_answers()
    answers['SELECT COUNT(*) FROM INFORMATION_SCHEMA."TABLES"'] = one_column("EXPR$0", [0], pa.int64())
    client = FakeFlightClient(answers)
    assert sa.inspect(make_engine(client)).has_table("x' OR '1'='1", schema="s'") is False
    assert client.sent[-1].endswith("WHERE UPPER(TABLE_NAME) = UPPER('x'' OR ''1''=''1') AND UPPER(TABLE_SCHEMA) = UPPER('s''')")


def test_autoload_of_absent_table_raises_no_such_table(make_engine):
    answers = _information_schema_answers()
    answers["SELECT COLUMN_NAME, DATA_TYPE"] = pa.table(
        {
            "COLUMN_NAME": pa.array([], pa.string()),
            "DATA_TYPE": pa.array([], pa.string()),
            "NUMERIC_PRECISION": pa.array([], pa.int32()),
            "NUMERIC_SCALE": pa.array([], pa.int32()),
            "IS_NULLABLE": pa.array([], pa.string()),
        }
    )
    with pytest.raises(sa.exc.NoSuchTableError):
        sa.Table("missing", sa.MetaData(), schema="$scratch", autoload_with=make_engine(FakeFlightClient(answers)))


# --- results ---


def test_results_keep_exact_python_types(make_engine):
    table = pa.table(
        {
            "big": pa.array([9007199254740993, None], pa.int64()),
            "amount": pa.array([decimal.Decimal("12345.67"), None], pa.decimal128(12, 2)),
            "d": pa.array([datetime.date(2024, 2, 29), None], pa.date32()),
            "ts_ms": pa.array([datetime.datetime(2024, 2, 29, 13, 14, 15, 123000), None], pa.timestamp("ms")),
            "ts_us": pa.array([datetime.datetime(2024, 2, 29, 13, 14, 15, 123456), None], pa.timestamp("us")),
            "t": pa.array([datetime.time(13, 14, 15), None], pa.time32("ms")),
            "flag": pa.array([True, None], pa.bool_()),
            "txt": pa.array(["é", None], pa.string()),
        }
    )
    client = FakeFlightClient({"SELECT": table})
    with make_engine(client).connect() as conn:
        result = conn.exec_driver_sql("SELECT * FROM t")
        description = result.cursor.description
        rows = [tuple(r) for r in result]
    assert rows == [
        (
            9007199254740993,
            decimal.Decimal("12345.67"),
            datetime.date(2024, 2, 29),
            datetime.datetime(2024, 2, 29, 13, 14, 15, 123000),
            datetime.datetime(2024, 2, 29, 13, 14, 15, 123456),
            datetime.time(13, 14, 15),
            True,
            "é",
        ),
        (None,) * 8,
    ]
    assert [type(v) for v in rows[0]] == [
        int,
        decimal.Decimal,
        datetime.date,
        datetime.datetime,
        datetime.datetime,
        datetime.time,
        bool,
        str,
    ]
    assert [d[1] for d in description] == [
        "BIGINT",
        "DECIMAL",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMP",
        "TIME",
        "BOOLEAN",
        "VARCHAR",
    ]
    assert description[1][4:6] == (12, 2)
    assert all(len(d) == 7 for d in description)
    assert description[3][1] == db.DATETIME and description[1][1] == db.NUMBER


# --- parameters ---


def test_bound_parameters_are_rendered_as_typed_literals(make_engine):
    client = FakeFlightClient({"SELECT": one_column("x", [1], pa.int32())})
    with make_engine(client).connect() as conn:
        conn.execute(
            sa.text("SELECT :s, :i, :f, :d, :dt, :day, :n, :b, '?' AS q, \"a?\" FROM t -- ?\nWHERE x = :s"),
            {
                "s": "it's ? :x",
                "i": 42,
                "f": 0.1,
                "d": decimal.Decimal("12345.67"),
                "dt": datetime.datetime(2024, 2, 29, 13, 14, 15, 123000),
                "day": datetime.date(2024, 2, 29),
                "n": None,
                "b": True,
            },
        ).all()
    assert client.sent[-1] == (
        "SELECT 'it''s ? :x', 42, CAST(0.1 AS DOUBLE), CAST('12345.67' AS DECIMAL(7,2)), "
        "TIMESTAMP '2024-02-29 13:14:15.123000', DATE '2024-02-29', NULL, TRUE, "
        "'?' AS q, \"a?\" FROM t -- ?\nWHERE x = 'it''s ? :x'"
    )


def test_core_limit_is_rendered(make_engine):
    client = FakeFlightClient({"SELECT": one_column("id", [1], pa.int32())})
    table = sa.table("t", sa.column("id"), schema="$scratch")
    with make_engine(client).connect() as conn:
        conn.execute(sa.select(table.c.id).where(table.c.id > 5).limit(1)).all()
    assert client.sent[-1] == 'SELECT "$scratch".t.id \nFROM "$scratch"."t" \nWHERE "$scratch".t.id > 5\n LIMIT 1'


@pytest.mark.parametrize(
    "value, literal",
    [
        (None, "NULL"),
        (False, "FALSE"),
        (-7, "(-7)"),
        (float("nan"), "CAST('NaN' AS DOUBLE)"),
        (float("-inf"), "CAST('-Infinity' AS DOUBLE)"),
        (decimal.Decimal("1E-10"), "CAST('0.0000000001' AS DECIMAL(10,10))"),
        (b"\x00\xff", "FROM_HEX('00ff')"),
        (datetime.time(1, 2, 3, 4), "TIME '01:02:03.000004'"),
        (
            datetime.datetime(2024, 1, 1, 12, tzinfo=datetime.timezone(datetime.timedelta(hours=2))),
            "TIMESTAMP '2024-01-01 10:00:00'",
        ),
        ("back\\slash", "'back\\slash'"),
    ],
)
def test_render_literal(value, literal):
    from sqlalchemy_dremio.params import render_literal

    assert render_literal(value) == literal


def test_bind_rejects_mismatched_or_unsupported_parameters():
    from sqlalchemy_dremio.params import bind

    with pytest.raises(ProgrammingError):
        bind("SELECT ?, ?", [1])
    with pytest.raises(ProgrammingError):
        bind("SELECT ?", [object()])
    with pytest.raises(ProgrammingError):
        bind("SELECT ?", {"a": 1})
    assert bind("SELECT '?'", ()) == "SELECT '?'"
    assert bind("SELECT ?", None) == "SELECT ?"


# --- errors and connection lifecycle ---


def test_flight_errors_become_dbapi_errors_and_sqlalchemy_wraps_them(make_engine):
    client = FakeFlightClient(
        {
            "SELEC": pa.ArrowInvalid("Non-query expression encountered"),
            "SELECT down": flight.FlightUnavailableError("connection refused"),
        }
    )
    engine = make_engine(client)
    with engine.connect() as conn:
        with pytest.raises(sa.exc.ProgrammingError) as info:
            conn.exec_driver_sql("SELEC 1")
    assert isinstance(info.value.orig, ProgrammingError)
    with engine.connect() as conn:
        with pytest.raises(sa.exc.OperationalError) as info:
            conn.exec_driver_sql("SELECT down")
    assert isinstance(info.value.orig, OperationalError)
    assert info.value.connection_invalidated is True


def test_close_releases_flight_client(make_engine):
    client = FakeFlightClient()
    connection = make_engine(client).raw_connection().dbapi_connection
    connection.close()
    assert client.closed is True
    with pytest.raises(db.InterfaceError):
        connection.cursor()


def test_distribution_does_not_cap_sqlalchemy_or_pyarrow():
    import importlib.metadata as im

    requires = {r.split(";")[0].replace(" ", "") for r in im.requires("sqlalchemy_dremio")}
    assert requires == {"SQLAlchemy<3,>=1.4", "pyarrow>=10.0.0"}


@pytest.mark.parametrize('value', [-1, decimal.Decimal('-1'), decimal.Decimal('-0'), decimal.Decimal('-0.00')])
def test_review_negative_binding_cannot_start_comment(value, make_engine):
    from sqlalchemy_dremio.params import bind

    assert '--' not in bind('SELECT 1-?', [value])
    client = FakeFlightClient({'SELECT': one_column('a', [1], pa.int32())})
    with make_engine(client).connect() as conn:
        conn.execute(sa.text("SELECT * FROM t WHERE a < 100-:n AND tenant = :tenant"),
                     {'n': value, 'tenant': 'x'}).all()
    assert '--' not in client.sent[-1]
    assert client.sent[-1].endswith(" AND tenant = 'x'")


@pytest.mark.parametrize('value, precision, scale', [
    ('12345678901234567890.123456789', 29, 9),
    ('-12345678901234567890.123456789', 29, 9),
    ('99999999999999999999999999999999999999', 38, 0),
    ('1E-38', 38, 38), ('1E+20', 21, 0), ('0.00100', 5, 5),
    ('-0.00', 2, 2), ('0', 1, 0),
])
def test_review_decimal_exact_cast(value, precision, scale):
    from sqlalchemy_dremio.params import render_literal

    value = decimal.Decimal(value)
    assert render_literal(value) == "CAST('%s' AS DECIMAL(%d,%d))" % (
        format(value, 'f'), precision, scale)


@pytest.mark.parametrize('value', ['1E-40', '1E+38', '1E+1000000', 'NaN', '-Infinity'])
def test_review_decimal_out_of_range_fails_before_transport(value, make_engine):
    client = FakeFlightClient()
    with make_engine(client).connect() as conn:
        with pytest.raises(sa.exc.ProgrammingError, match='Dremio DECIMAL'):
            conn.execute(sa.text('SELECT :value'), {'value': decimal.Decimal(value)})
    assert client.sent == []


@pytest.mark.parametrize('table_name, schema', [('mixed', 'nas.sub'), ('typed', 'NAS'), ("x'", "s'")])
def test_review_case_insensitive_reflection(table_name, schema, make_engine):
    # Require the actual SQL predicate, not a fake that accepts either spelling.
    quote = lambda value: "'" + value.replace("'", "''") + "'"
    columns_sql = (
        'SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE, IS_NULLABLE '
        'FROM INFORMATION_SCHEMA."COLUMNS" '
        'WHERE UPPER(TABLE_SCHEMA) = UPPER(%s) AND UPPER(TABLE_NAME) = UPPER(%s) '
        'ORDER BY ORDINAL_POSITION' % (quote(schema), quote(table_name)))
    has_sql = ('SELECT COUNT(*) FROM INFORMATION_SCHEMA."TABLES" '
               'WHERE UPPER(TABLE_NAME) = UPPER(%s) AND UPPER(TABLE_SCHEMA) = UPPER(%s)'
               % (quote(table_name), quote(schema)))
    client = FakeFlightClient({
        columns_sql: pa.table({'COLUMN_NAME': ['amount'], 'DATA_TYPE': ['DECIMAL'],
                              'NUMERIC_PRECISION': [29], 'NUMERIC_SCALE': [9],
                              'IS_NULLABLE': ['YES']}),
        has_sql: one_column('n', [1], pa.int64()),
    })
    engine = make_engine(client)
    assert sa.inspect(engine).has_table(table_name, schema=schema)
    table = sa.Table(table_name, sa.MetaData(), schema=schema, autoload_with=engine)
    assert (table.c.amount.type.precision, table.c.amount.type.scale) == (29, 9)


def test_review_python_classifiers_match_requires(monkeypatch):
    import runpy
    import setuptools
    from pathlib import Path
    from packaging.specifiers import SpecifierSet

    metadata = {}
    monkeypatch.setattr(setuptools, 'setup', lambda **kwargs: metadata.update(kwargs))
    runpy.run_path(str(Path(__file__).resolve().parents[1] / 'setup.py'))
    requires = SpecifierSet(metadata['python_requires'])
    versions = [c.rsplit(' :: ', 1)[-1] for c in metadata['classifiers']
                if c.startswith('Programming Language :: Python :: ') and '.' in c]
    assert versions
    assert all(version in requires for version in versions)


@pytest.mark.parametrize('views', [False, True])
def test_review_table_listing_schema_is_case_insensitive(views, make_engine):
    sql = ('SELECT TABLE_NAME FROM INFORMATION_SCHEMA."TABLES" WHERE TABLE_TYPE %s '
           "'VIEW' AND UPPER(TABLE_SCHEMA) = UPPER('NAS.Sub')" % ('=' if views else '<>'))
    client = FakeFlightClient({sql: one_column('TABLE_NAME', ['Mixed'], pa.string())})
    inspector = sa.inspect(make_engine(client))
    method = inspector.get_view_names if views else inspector.get_table_names
    assert method(schema='NAS.Sub') == ['Mixed']


@pytest.mark.parametrize('comment', ['--', '//'])
@pytest.mark.parametrize('ending', ['\n', '\r', '\r\n'])
def test_review_single_line_comment_boundaries(comment, ending):
    from sqlalchemy_dremio.params import bind

    sql = 'SELECT 1 ' + comment + ' ?' + ending + ', ?'
    assert bind(sql, ['x']) == sql[:-1] + "'x'"
    assert bind('-- c\r?', [7]) == '-- c\r7'
    with pytest.raises(ProgrammingError, match='0 placeholder'):
        bind('SELECT 1 ' + comment + ' ?', [7])


@pytest.mark.parametrize('comment', ['--', '//'])
@pytest.mark.parametrize('ending', ['\n', '\r'])
def test_review_comment_bind_injection_fails_before_transport(comment, ending, make_engine):
    client = FakeFlightClient()
    with make_engine(client).connect() as conn:
        with pytest.raises(sa.exc.ProgrammingError, match='1 placeholder.*2 parameter'):
            conn.execute(sa.text(
                "SELECT * FROM (VALUES (1,'x'),(20,'y')) t(a,tenant) "
                "WHERE a > 0 " + comment + " scoped to :tenant" + ending +
                " AND tenant = :tenant"), {'tenant': '\nOR TRUE --'})
    assert client.sent == []


def test_review_numeric_subclasses_use_builtin_rendering():
    from enum import IntEnum
    import numpy as np
    from sqlalchemy_dremio.params import render_literal

    class Status(IntEnum):
        ACTIVE = 1

        def __str__(self):
            return 'Status.ACTIVE'

    class NegativeInt(int):
        def __str__(self):
            return 'not_a_number'

    class Float(float):
        def __repr__(self):
            return 'not_a_number'

    assert render_literal(Status.ACTIVE) == '1'
    assert render_literal(NegativeInt(-2)) == '(-2)'
    assert render_literal(Float(1.5)) == 'CAST(1.5 AS DOUBLE)'
    assert render_literal(np.float64(1.5)) == 'CAST(1.5 AS DOUBLE)'


def test_review_reflection_colons_are_not_bind_parameters(make_engine):
    answers = _information_schema_answers()
    answers['DESCRIBE "tag (:v1)"'] = pa.table({
        'COLUMN_NAME': ['id'], 'DATA_TYPE': ['INTEGER']})
    client = FakeFlightClient(answers)
    engine = make_engine(client)
    inspector = sa.inspect(engine)
    name = 'tag (:v1)'
    assert inspector.has_table(name, schema=name)
    assert inspector.get_columns(name, schema=name)
    assert inspector.get_columns(name)[0]['name'] == 'id'
    assert inspector.get_table_names(schema=name) == ['t']
    assert inspector.get_view_names(schema=name) == ['v']
    assert all('?' not in sql for sql in client.sent)
    assert sum(name in sql for sql in client.sent) == 5
    engine.dispose()


def test_review_expired_token_pre_ping_reconnects(monkeypatch):
    stale = FakeFlightClient({'SELECT 1': flight.FlightUnauthenticatedError('expired token')})
    fresh = FakeFlightClient({'SELECT 1': one_column('n', [1], pa.int32())})
    clients = iter([stale, fresh])
    monkeypatch.setattr(flight, 'FlightClient', lambda *args, **kwargs: next(clients))
    engine = sa.create_engine(
        'dremio+flight://user:pass@localhost:32010/?UseEncryption=false', pool_pre_ping=True)
    with engine.connect():
        pass
    with engine.connect() as conn:
        assert conn.exec_driver_sql('SELECT 1').scalar() == 1
    assert stale.closed
    assert stale.sent == ['SELECT 1']
    assert fresh.credentials == ('user', 'pass')
    engine.dispose()
    assert fresh.closed
