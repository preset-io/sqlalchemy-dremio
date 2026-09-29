# 3.0.5.1

SQLAlchemy 1.4/2.x compatibility, exact Arrow-to-Python results, typed qmark
binding, DB-API error mapping and delimiter-safe connection properties.

Review fixes prevent negative numeric binds from commenting out predicates,
use exact DECIMAL casts (precision/scale up to 38; larger values fail clearly),
and preserve case-insensitive reflection/autoload, including nested schemas.
The Python classifier now agrees with the minimum supported version, 3.8.

See [the Dremio 26.0.5 replay](docs/review-20260929.md) for regression tests,
baseline controls and reproduction steps.

# Release Commands

```
pip install twine
bumpversion patch --allow-dirty
python setup.py sdist
twine upload -u ${{ secrets.PYPI_USERNAME }} -p ${{ secrets.PYPI_PASSWORD }} dist/*
```
