# SQLAlchemy Dremio


![PyPI](https://img.shields.io/pypi/v/sqlalchemy_dremio.svg)

A SQLAlchemy dialect for Dremio via ODBC and Flight interfaces.

<!--ts-->
   * [Installation](#installation)
   * [Usage](#usage)
   * [Testing](#testing)
   * [Superset Integration](#superset-integration)
<!--te-->

Installation
------------

From pip:
-----------

`pip install sqlalchemy_dremio`

Or from conda:
--------------
`conda install sqlalchemy-dremio`

To install from source:
`python setup.py install`

Usage
-----

Connection String example:

Dremio Software:

`dremio+flight://user:password@host:port/dremio`

Dremio Cloud:

`dremio+flight://data.dremio.cloud:443/?Token=<TOKEN>UseEncryption=true&disableCertificateVerification=true`

Options:

Schema - (Optional) The schema to use

TLS:

UseEncryption=true|false - (Optional) Enables TLS connection. Must be enabled on Dremio to use it.
DisableCertificateVerification=true|false - (Optional) Disables certificate verification.
TrustedCerts=<path to PEM> - (Optional) Trust these CA certificates instead of the system store.

WLM:

https://docs.dremio.com/software/advanced-administration/workload-management/#query-tagging--direct-routing-configuration


routing_queue - (Optional) The queue in which queries should run
routing_tag - (Optional) Routing tag to use.
routing_engine - (Optional) The engine in which the queries should run

Testing
-------

You can run the integration tests with the Dremio community edition Docker image.

```bash
docker run -d -p 9047:9047 -p 31010:31010 -p 32010:32010 --name dremio dremio/dremio-oss:latest
export DREMIO_CONNECTION_URL="dremio+flight://dremio:dremio123@localhost:32010/dremio?UseEncryption=false"
pytest
```

The workflow in `.github/workflows/dremio.yml` demonstrates how to run these tests automatically on GitHub Actions.
The CI badge at the top of this file shows the current test status.

Superset Integration
-------------

The ODBC connection to superset is now deprecated. Please update sqlalchemy_dremio to 3.0.2 to use the flight connection.

Release Notes
-------------

3.0.5.1
-------
- SQLAlchemy 2: reflection (`get_schema_names`, `get_table_names`, `get_view_names`,
  `has_table`, `get_columns`) executes `text()` statements, the dialect implements
  `import_dbapi` and enables the statement cache. Still works with SQLAlchemy 1.4.
- Results are converted directly from Arrow, not through pandas: TIMESTAMP columns of any
  unit are `datetime` (previously a `KeyError` for `datetime64[ms]`), integers stay
  `int` next to NULLs, DATE is `date`, DECIMAL is `Decimal`, NULL is `None`.
  `cursor.description` has seven items with the Dremio type name as `type_code`.
- `get_columns` reads `INFORMATION_SCHEMA."COLUMNS"`: DECIMAL keeps precision and scale,
  nullability is reported, and unknown types become `NullType` instead of a `KeyError`.
  Views are listed by `get_view_names` and no longer by `get_table_names`. Names are
  escaped in catalog queries. Autoloading an absent table raises `NoSuchTableError`.
- Bound parameters (qmark) are rendered client-side as typed SQL literals; previously they
  were silently dropped. `?` inside string literals, quoted identifiers and comments is
  left alone, and values are rendered exactly once.
- Arrow Flight errors are raised as DB-API exceptions (`ProgrammingError`,
  `OperationalError`, ...), so SQLAlchemy wraps them and invalidates dead connections.
- Connection properties are passed as keyword arguments, so passwords may contain `;`
  and `=`. Closing a connection closes its Flight client.
- Packaging: requires `SQLAlchemy>=1.4,<3` and `pyarrow>=10.0.0` (3.0.5 pinned
  `SQLAlchemy~=2.0.41` and `pyarrow~=20.0.0`); pandas is no longer used.

3.0.4
-----
- Addressing issue #34 and #37: Add driver name to dialects

3.0.3
-----
- Add back missing routing_engine property.

3.0.2
-----
- Add implementations of has_table and get_view_names.

3.0.1
-----
- Made connection string property keys case-insensitive
- Fix incorrect lookup of the token property
- Fix incorrect lookup of the DisableCertificateVerification property
