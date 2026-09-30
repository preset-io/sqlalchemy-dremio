#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""The setup script."""

import re

from setuptools import setup, find_packages

with open('README.md') as readme_file:
    readme = readme_file.read()

# The package's __version__ is the single source of the distribution version.
with open('sqlalchemy_dremio/__init__.py') as init_file:
    version = re.search(r"^__version__ = '([^']+)'", init_file.read(), re.M).group(1)

# Lower bounds only: the dialect supports SQLAlchemy 1.4 and 2.x, and uses
# pyarrow's Flight client and Arrow-to-Python conversion, which have been
# stable since pyarrow 10. Upper caps here conflict with applications that
# pin newer releases (for example pyarrow 25).
requirements = [
    'SQLAlchemy>=1.4,<3',
    'pyarrow>=10.0.0',
]

setup_requirements = [
]

test_requirements = [
]

setup(
    name='sqlalchemy_dremio',
    version=version,
    description="A SQLAlchemy dialect for Dremio via the Flight interface.",
    long_description=readme,
    long_description_content_type='text/markdown',
    author="Naren",
    author_email='me@narendran.info',
    url='https://github.com/narendrans/sqlalchemy_dremio',
    packages=find_packages(include=['sqlalchemy_dremio']),
    python_requires='>=3.8',
    entry_points={
        'sqlalchemy.dialects': [
            'dremio.flight = sqlalchemy_dremio.flight:DremioDialect_flight',
        ]
    },
    include_package_data=True,
    install_requires=requirements,
    license="Apache Software License",
    zip_safe=False,
    keywords='sqlalchemy_dremio',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: Apache Software License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8'
    ]
)
