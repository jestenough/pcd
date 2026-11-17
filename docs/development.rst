Development and verification
============================

Local checks
------------

Install Python and Poetry first, then run from the checkout::

    poetry install
    poetry run ruff format --check .
    poetry run ruff check .
    poetry run mypy src tests benchmarks
    poetry run pytest
    poetry build

With make and a POSIX shell, the equivalent quality/coverage and build targets
are::

    make install
    make check
    make build-check

``make check`` includes branch coverage with a 95% minimum. ``make build-check``
builds wheel/sdist and smoke-tests the installed wheel in an isolated environment.
Makefile cleanup and wheel smoke commands use POSIX paths and shell utilities;
use the direct Poetry commands above in native PowerShell.

Benchmarks and builds
---------------------

Run benchmarks separately with ``make bench`` or::

    poetry run pytest benchmarks -q -s

See the `benchmark guide <../benchmarks/README.md>`_ for CLI comparisons and
measurement limits. Built distributions are in ``./dist``; install the wheel
with your Python package manager.

See also: the `roadmap <../ROADMAP.txt>`_ and the `README <../README.txt>`_.
