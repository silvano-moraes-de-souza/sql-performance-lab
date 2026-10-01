<p align="center">
  <img src="docs/assets/banner.svg" alt="SQL Performance Lab" width="100%">
</p>

<p align="center">
  <a href="https://github.com/silvano-moraes-de-souza/sql-performance-lab/actions/workflows/ci.yml"><img src="https://github.com/silvano-moraes-de-souza/sql-performance-lab/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-2a78d6" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white" alt="PostgreSQL 16">
  <img src="https://img.shields.io/badge/EXPLAIN-ANALYZE%2C%20BUFFERS-ef4444" alt="EXPLAIN ANALYZE">
  <img src="https://img.shields.io/badge/license-MIT-52514e" alt="MIT">
  <a href="https://github.com/silvano-moraes-de-souza/30-days-data-eng"><img src="https://img.shields.io/badge/30%20days-day%2005-0b0b0b" alt="30 Days of Data & Software Engineering"></a>
</p>

> Eight queries that are slow in almost every young database, each with its fix, measured with `EXPLAIN (ANALYZE, BUFFERS)` on a million orders. Every fix is checked to return exactly the same rows as the slow version, and the cost of the indexes on writes is measured too.

@@PANEL@@

<sub>All numbers come from <a href="bench/run.py">bench/run.py</a> and <a href="results/">results/</a>.</sub>

**Contents:** [Problem](#problem) · [The eight cases](#the-eight-cases) · [Quickstart](#quickstart) · [Results](#results) · [How it works](#how-it-works) · [Engineering decisions](#engineering-decisions) · [Tests](#tests) · [Limitations](#limitations)

## Problem

A new application runs fine on a few thousand rows. A year later the same pages take seconds, and the usual reaction is to add indexes until it feels better. Some help, some do nothing, and every one makes writes slower. The way out is to read the plan, fix the specific cause, and measure.

This lab loads [ShopFlow](https://github.com/silvano-moraes-de-souza/shopflow-datagen) data into PostgreSQL 16 with primary keys only, the state most databases start in, and works through eight common slow queries.

## The eight cases

| Case | Query | Fix | Why it works |
|---|---|---|---|
| `customer_history` | last 20 orders of one customer | composite index `(customer_id, ordered_at DESC)` | filter and sort come from the index: 20 entries read, no sort |
| `login_email` | `WHERE lower(email) = ...` | expression index on `lower(email)` | an index on `email` cannot answer `lower(email)` |
| `pending_queue` | oldest 50 pending orders | partial index `WHERE status = 'pending'` | the index holds only the rows the query can return |
| `month_revenue` | revenue of one month | `INCLUDE (status, total_cents)` | index-only scan: the table is never visited |
| `event_log_day` | events of one day in an append-only log | BRIN on `event_at` | the log is written in time order; a block range summary is enough |
| `deep_pagination` | a page halfway through a listing | keyset (`WHERE order_id > last`) | `OFFSET` reads and discards every earlier row |
| `function_on_column` | orders placed on one day | range on the raw column | `date(column) = x` hides the column from its index |
| `items_of_order` | line items of one order | index on the foreign key | Postgres does not index foreign keys by itself |

## Quickstart

```bash
git clone https://github.com/silvano-moraes-de-souza/sql-performance-lab
cd sql-performance-lab
docker compose up --build    # Postgres 16, small dataset, all eight cases
```

Without Docker (Python 3.11 or 3.12; an embedded PostgreSQL 16 starts for the tests):

```bash
uv sync
uv run pytest                                   # @@TESTS@@ tests against a real Postgres
uv run sqllab generate --scale 1 --out data/sf1
uv run sqllab run --data data/sf1 --url postgresql://user:pass@localhost:5432/lab
uv run python -m bench.run                      # rebuilds results/ and the charts
```

@@RESULTS@@

## How it works

### One case, several variants

[`cases.py`](src/sql_performance_lab/cases.py) defines each case as a list of variants. The first is the query as people usually write it, on a table with only its primary key. The others are fixes: an index to create, a rewritten query, or both. Some cases include a fix that does not work (an index on `email` for a `lower(email)` filter, a plain index where a partial one fits), because those are the ones people try first.

### Measuring

[`lab.py`](src/sql_performance_lab/lab.py) runs each variant on its own: it drops every index the case may create, creates the variant's indexes (build time and size recorded), runs `ANALYZE`, warms the cache with two runs and then runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` several times. It keeps the median execution time, the node types of the plan from the median run, and the buffer counts.

### Same rows or it does not count

After timing, each variant's result is fetched and compared with the first variant's, row by row and in order. The benchmark aborts if any fix changes the answer. A faster query that returns different rows is a bug, not an optimization.

### Write cost

[`write_cost`](src/sql_performance_lab/lab.py) inserts 100,000 orders with only the primary key, then with every index the orders cases create, and compares the times.

@@DECISIONS@@

## Tests

@@TESTS@@ tests on Python 3.11, 3.12 and 3.13 in CI against PostgreSQL 16, plus a Docker Compose job that runs all eight cases.

| What | Checked by |
|---|---|
| Every fix returns the same rows as the slow query | one test per case |
| The fix removes the sequential scan | plan node types of the first and last variant |
| An index on `email` does not serve `lower(email)` | the plain index variant still plans a `Seq Scan` |
| The covering index gives an index-only scan | plan node type |
| The partial index is smaller than the full one; BRIN is far smaller than the B-tree | `pg_relation_size` |
| Parameters are deterministic; the write test cleans up rows and indexes | direct queries |

@@LIMITS@@

## Project structure

```
src/sql_performance_lab/
  db.py          ShopFlow into Postgres, primary keys only, plus an append-only event log
  cases.py       the eight cases and their variants
  lab.py         drop, create, ANALYZE, warm up, EXPLAIN ANALYZE, compare rows; write cost
  cli.py         sqllab generate | run
bench/           benchmark at two scales, charts, embedded Postgres helper
results/         benchmark output (JSON with plans, buffers, machine and commit)
tests/
```

## Part of the series

Day 05 of [30 Days of Data & Software Engineering](https://github.com/silvano-moraes-de-souza/30-days-data-eng). Data from [shopflow-datagen](https://github.com/silvano-moraes-de-souza/shopflow-datagen) (day 00), the same tables [ecommerce-data-pipeline](https://github.com/silvano-moraes-de-souza/ecommerce-data-pipeline) (day 01) loads.

## Author

**Silvano Moraes de Souza** · [LinkedIn](https://www.linkedin.com/in/silvano-moraes-de-souza) · [Portfolio](https://silvanomsouza.vercel.app/)
