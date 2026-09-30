# 3.0.5.1

SQLAlchemy 1.4/2.x compatibility, exact Arrow-to-Python results, typed qmark
binding, DB-API error mapping and delimiter-safe connection properties.

Review fixes prevent negative numeric binds from commenting out predicates,
use exact DECIMAL casts (precision/scale up to 38; larger values fail clearly),
and preserve case-insensitive reflection/autoload, including nested schemas.
Reflection uses `exec_driver_sql()` so colons in catalog names are not binds.
The parameter scanner skips `--` and `//` line comments through CR or LF,
and block comments. SQLAlchemy binds inside comments now raise a placeholder-count
`ProgrammingError` before transport rather than inserting values into comments.
The Python classifier now agrees with the minimum supported version, 3.8.

See [the Dremio 26.0.5 replay](docs/review-20260929.md) for regression tests,
baseline controls and reproduction steps.

# Stable publication prerequisite

Before publishing 3.0.5.1, the publisher must remove these legacy PR wheels
from the stable `preset-pypi` bucket prefix (new PR builds use
`pr/sqlalchemy-dremio/1/`):

- `sqlalchemy-dremio/sqlalchemy_dremio-3.0.5.1+pr.1.1147ce753a4d-py3-none-any.whl`
- `sqlalchemy-dremio/sqlalchemy_dremio-3.0.5.1+pr.1.4be64255e8bb-py3-none-any.whl`

PEP 440 `==3.0.5.1` also matches local versions, so leaving these in the stable
prefix can select an obsolete PR build over the stable wheel. First check
consumer URL/hash pins, including superset-shell master. If a consumer still
pins either artifact, migrate that pin before deleting it. Use an authorized
publisher account to delete only the two keys above, verify they are absent
from the stable prefix and any served index, then publish stable. Do not remove
PR artifacts from the separate `pr/` prefix.

# Release Commands

```
pip install twine
bumpversion patch --allow-dirty
python setup.py sdist
twine upload -u ${{ secrets.PYPI_USERNAME }} -p ${{ secrets.PYPI_PASSWORD }} dist/*
```
