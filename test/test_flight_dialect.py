import sqlalchemy.engine.url as url

from sqlalchemy_dremio.flight import DremioDialect_flight


def _connect_args(connect_url: str) -> dict:
    dialect = DremioDialect_flight()
    args, kwargs = dialect.create_connect_args(url.make_url(connect_url))
    assert args == []
    return kwargs


def test_create_connect_args_basic():
    properties = _connect_args("dremio+flight://localhost:31010")
    assert properties == {"HOST": "localhost", "PORT": 31010}


def test_create_connect_args_with_user_and_db():
    properties = _connect_args(
        "dremio+flight://user:pass@localhost:32010/dremio"
    )
    assert properties["UID"] == "user"
    assert properties["PWD"] == "pass"
    assert properties["Schema"] == "dremio"
    assert properties["HOST"] == "localhost"
    assert properties["PORT"] == 32010


def test_create_connect_args_query_options_case_insensitive():
    properties = _connect_args(
        "dremio+flight://localhost:12345/db?useencryption=false&routing_engine=myeng"
    )
    assert properties["UseEncryption"] == "false"
    assert properties["routing_engine"] == "myeng"
