import pytest

from sql_performance_lab.cases import cases, params
from sql_performance_lab.lab import run_case, write_cost

KEYS = [c.key for c in cases({k: 1 for k in ("customer", "email", "order", "offset", "after_id",
                                                "month_start", "month_end", "day")})]  # fmt: skip


@pytest.fixture(scope="module")
def results(conn, p):
    return {c.key: run_case(conn, c, runs=1) for c in cases(p)}


@pytest.mark.parametrize("key", KEYS)
def test_every_fix_returns_the_same_rows(results, key):
    r = results[key]
    assert all(v.same_result for v in r.variants)
    assert r.variants[0].rows > 0


@pytest.mark.parametrize("key", ["customer_history", "login_email", "month_revenue",
                                 "function_on_column", "items_of_order"])  # fmt: skip
def test_the_fix_removes_the_sequential_scan(results, key):
    first, last = results[key].variants[0], results[key].variants[-1]
    assert any("Seq Scan" in n for n in first.plan_nodes)
    assert not any("Seq Scan" in n for n in last.plan_nodes)


def test_an_index_on_email_does_not_serve_lower_email(results):
    plain = results["login_email"].variants[1]
    assert plain.label == "index (email)"
    assert any("Seq Scan" in n for n in plain.plan_nodes)


def test_covering_index_gives_an_index_only_scan(results):
    assert any("Index Only Scan" in n for n in results["month_revenue"].variants[-1].plan_nodes)


def test_partial_index_is_smaller_than_the_full_one(results):
    full, partial = results["pending_queue"].variants[1:]
    assert 0 < partial.index_mb < full.index_mb


def test_brin_is_much_smaller_than_btree(results):
    btree, brin = results["event_log_day"].variants[1:]
    assert brin.index_mb * 10 < btree.index_mb


def test_params_are_deterministic(conn, p):
    assert params(conn) == p


def test_write_cost_cleans_up(conn, p):
    before = conn.execute("SELECT count(*) FROM shop.orders").fetchone()[0]
    out = write_cost(conn, cases(p), n=500, runs=1)
    assert len(out) == 2
    assert conn.execute("SELECT count(*) FROM shop.orders").fetchone()[0] == before
    leftovers = conn.execute(
        "SELECT count(*) FROM pg_indexes WHERE indexname LIKE 'lab_%'"
    ).fetchone()[0]
    assert leftovers == 0


def test_cli_table_marks_every_variant(results):
    from sql_performance_lab.cli import table  # noqa: PLC0415

    out = table(list(results.values()))
    assert out.count("| yes |") == sum(len(r.variants) for r in results.values())
