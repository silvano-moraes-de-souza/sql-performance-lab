"""Tests run against a real PostgreSQL (see bench/pg.py)."""

from __future__ import annotations

import psycopg
import pytest
from shopflow_datagen import GenConfig, write

from bench.pg import NoServerError, server_url
from sql_performance_lab.cases import params
from sql_performance_lab.db import load


@pytest.fixture(scope="session")
def conn(tmp_path_factory):
    try:
        url = server_url()[0]
    except NoServerError:
        pytest.skip("no embedded PostgreSQL for this Python version; set TEST_DATABASE_URL")
    data = tmp_path_factory.mktemp("shopflow")
    write(GenConfig(scale=0.05, chunk_size=5_000), data)
    with psycopg.connect(url) as c:
        load(c, data)
        yield c


@pytest.fixture(scope="session")
def p(conn):
    return params(conn)
