"""Run every variant of every case and record what EXPLAIN ANALYZE says.

For each variant:
    1. drop every index the case may create, then create the variant's own
       (build time and size recorded) and ANALYZE the table
    2. run the query twice to warm the cache
    3. run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ``runs`` times, keep the median
       execution time and the plan of that run
    4. fetch the result and compare it with the first variant's
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field

import psycopg

from .cases import Case, resolve


@dataclass
class Measured:
    label: str
    exec_ms: list[float]
    plan_nodes: list[str]
    shared_hit: int
    shared_read: int
    rows: int
    index_bytes: int = 0
    build_s: float = 0.0
    same_result: bool = True

    @property
    def index_mb(self) -> float:
        return self.index_bytes / 2**20

    @property
    def median_ms(self) -> float:
        return statistics.median(self.exec_ms)


@dataclass
class CaseResult:
    key: str
    title: str
    lesson: str
    variants: list[Measured] = field(default_factory=list)

    @property
    def speedup(self) -> float:
        return self.variants[0].median_ms / self.variants[-1].median_ms


def _nodes(plan: dict) -> list[str]:
    """Node types of the plan, depth first, e.g. ['Limit', 'Index Scan']."""
    out = [plan["Node Type"] + (f" using {plan['Index Name']}" if "Index Name" in plan else "")]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


def _explain(conn: psycopg.Connection, sql: str) -> tuple[float, dict]:
    row = conn.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql}").fetchone()[0]
    doc = row[0] if isinstance(row, list) else json.loads(row)[0]
    return doc["Execution Time"], doc["Plan"]


def _drop(conn: psycopg.Connection, case: Case) -> None:
    for name in case.indexes:
        conn.execute(f"DROP INDEX IF EXISTS shop.{name}")
    conn.commit()


def run_case(conn: psycopg.Connection, case: Case, runs: int = 5) -> CaseResult:
    case = resolve(case)
    result = CaseResult(case.key, case.title, case.lesson)
    baseline = None
    for v in case.variants:
        _drop(conn, case)
        size, build = 0, 0.0
        for ddl in v.ddl:
            t0 = time.perf_counter()
            conn.execute(ddl)
            conn.commit()
            build += time.perf_counter() - t0
            name = ddl.split()[2]
            size += conn.execute(f"SELECT pg_relation_size('shop.{name}')").fetchone()[0]
        conn.execute(f"ANALYZE shop.{case.table}")
        conn.commit()
        for _ in range(2):
            conn.execute(v.sql).fetchall()
        timings, plans = [], []
        for _ in range(runs):
            ms, plan = _explain(conn, v.sql)
            timings.append(ms)
            plans.append((ms, plan))
        conn.commit()
        median_plan = sorted(plans, key=lambda x: x[0])[len(plans) // 2][1]
        rows = conn.execute(v.sql).fetchall()
        conn.commit()
        if baseline is None:
            baseline = rows
        result.variants.append(Measured(
            v.label, timings, _nodes(median_plan),
            median_plan.get("Shared Hit Blocks", 0), median_plan.get("Shared Read Blocks", 0),
            len(rows), size, build, rows == baseline))  # fmt: skip
    _drop(conn, case)
    return result


def write_cost(
    conn: psycopg.Connection, cases: list[Case], n: int = 100_000, runs: int = 3
) -> dict:
    """Time to insert ``n`` orders with only the primary key vs with every orders index above."""
    ddl = [d for c in cases if c.table == "orders" for v in c.variants for d in v.ddl]
    ddl = list(dict.fromkeys(ddl))  # unique, in order
    insert = (f"INSERT INTO shop.orders SELECT order_id + 100000000, customer_id, ordered_at, "
              f"status, channel, subtotal_cents, discount_cents, shipping_cents, total_cents, "
              f"delivered_at FROM shop.orders ORDER BY order_id LIMIT {n}")  # fmt: skip
    out = {}
    for label, indexes in (("primary key only", []), (f"primary key + {len(ddl)} indexes", ddl)):
        for c in cases:
            _drop(conn, c)
        for d in indexes:
            conn.execute(d)
        conn.commit()
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            conn.execute(insert)
            conn.commit()
            times.append(time.perf_counter() - t0)
            conn.execute("DELETE FROM shop.orders WHERE order_id > 100000000")
            conn.commit()
        out[label] = times
    for c in cases:
        _drop(conn, c)
    return out
