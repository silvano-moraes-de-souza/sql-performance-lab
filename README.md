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

<table>
<tr>
<td align="center"><b>8 of 8</b><br/>fixes return exactly the rows<br/>of the slow query</td>
<td align="center"><b>5,141x</b><br/>keyset vs OFFSET halfway<br/>through 1M orders</td>
<td align="center"><b>24 KB vs 63 MB</b><br/>BRIN vs B-tree on a 2.9M-row<br/>log, 1.8 ms vs 0.9 ms</td>
<td align="center"><b>20x</b><br/>slower inserts with 7 indexes<br/>than with the primary key only</td>
</tr>
</table>

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
uv run pytest                                   # 25 tests against a real Postgres
uv run sqllab generate --scale 1 --out data/sf1
uv run sqllab run --data data/sf1 --url postgresql://user:pass@localhost:5432/lab
uv run python -m bench.run                      # rebuilds results/ and the charts
```

## Results

Measured on a laptop (Intel 11th gen Tiger Lake, 6 cores, 24 GB RAM, Windows 11) against an embedded PostgreSQL 16 with default settings (`shared_buffers` 128 MB). Execution time from `EXPLAIN (ANALYZE, BUFFERS)`, median of 7 runs after 2 warm-up runs. Every JSON in [`results/`](results/) has the plans, buffer counts, parameters, machine and commit.

ShopFlow scale 10: 1,000,000 orders, 1,700,433 order items, 200,000 customers, 2,920,432 order events.

![Eight slow queries and their fixes](docs/assets/cases_scale_10.png)

| Case | As written | Fix | Speedup | Plan after the fix |
|---|---:|---:|---:|---|
| Page halfway through a listing | 108 ms | 0.021 ms | 5,141x | Index Scan on the primary key, 5 pages instead of 8,110 |
| Login by e-mail, case insensitive | 67.2 ms | 0.034 ms | 1,978x | Index Scan on `lower(email)` |
| Line items of one order | 65.0 ms | 0.045 ms | 1,444x | Index Scan on `order_id` |
| Oldest 50 pending orders | 56.8 ms | 0.053 ms | 1,072x | Index Scan on the partial index |
| Last 20 orders of one customer | 52.7 ms | 0.088 ms | 599x | Bitmap Heap Scan + Sort |
| Events of one day in the log | 123 ms | 1.82 ms (BRIN) | 68x | Bitmap Heap Scan over 130 pages |
| Orders placed on one day | 45.9 ms | 1.27 ms | 36x | Bitmap Index Scan on `ordered_at` |
| Revenue of one month | 55.9 ms | 10.2 ms | 5x | Index Only Scan, 264 pages instead of 13,512 |

### The fixes that did not work, or not the way expected

| Case | Variant | Time | Index size | What happened |
|---|---|---:|---:|---|
| Login by e-mail | index on `email` | 69.7 ms | 9.9 MB | Still a sequential scan: the index cannot answer `lower(email)` |
| Pending queue | index `(status, ordered_at)` | 0.17 ms | 47.2 MB | Fast, but the partial index is 0.14 MB and faster (0.05 ms) |
| Last orders of a customer | index `(customer_id)` | 0.10 ms | 9.6 MB | As fast as the composite one at this scale |
| Last orders of a customer | index `(customer_id, ordered_at DESC)` | 0.09 ms | 38.7 MB | Postgres still chose bitmap + sort at scale 10; at scale 1 it read the index in order with no sort (0.02 ms) |
| Revenue of one month | index on `ordered_at` | 24.0 ms | 21.4 MB | 2.3x: it still visits 13,083 table pages; the covering index visits 264 |
| Events of one day | B-tree on `event_at` | 0.89 ms | 62.6 MB | Twice as fast as BRIN (1.82 ms), and 2,670 times bigger (BRIN: 24 KB) |

The composite index result is worth a second look. The customer picked is the one with the most orders, and even so the planner judged that fetching that customer's rows and sorting them was cheaper than walking the composite index. The composite index pays off when the rows to sort grow, which the scale 1 run shows the other way round: there it was used in order and skipped the sort.

### What the indexes cost

Inserting 100,000 orders, 3 runs each.

| Indexes on `orders` | Runs (s) | Median |
|---|---|---:|
| primary key only | 0.32 to 0.40 | 0.40 s |
| primary key + 7 indexes | 5.38 to 10.0 | 7.97 s (20x) |

The 7 are every index the orders cases create, including the alternatives that lost (the plain index next to the covering one, the full index next to the partial one). That is what "add indexes until it feels better" ends up with, and it is why the losing variants are worth dropping.

### Scale 1 vs scale 10

| Case | Scale 1 (100k orders) | Scale 10 (1M orders) |
|---|---|---|
| Page halfway, OFFSET | 14.1 ms | 108 ms |
| Login by e-mail, no index | 14.9 ms | 67.2 ms |
| Line items of one order, no index | 7.1 ms | 65.0 ms |
| Line items of one order, with the index | 0.01 ms | 0.04 ms |

The slow versions grow with the table; the fixed ones barely move. Charts for scale 1: [`cases_scale_1.png`](docs/assets/cases_scale_1.png).

## How it works

### One case, several variants

[`cases.py`](src/sql_performance_lab/cases.py) defines each case as a list of variants. The first is the query as people usually write it, on a table with only its primary key. The others are fixes: an index to create, a rewritten query, or both. Some cases include a fix that does not work (an index on `email` for a `lower(email)` filter, a plain index where a partial one fits), because those are the ones people try first.

### Measuring

[`lab.py`](src/sql_performance_lab/lab.py) runs each variant on its own: it drops every index the case may create, creates the variant's indexes (build time and size recorded), runs `ANALYZE`, warms the cache with two runs and then runs `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` several times. It keeps the median execution time, the node types of the plan from the median run, and the buffer counts.

### Same rows or it does not count

After timing, each variant's result is fetched and compared with the first variant's, row by row and in order. The benchmark aborts if any fix changes the answer. A faster query that returns different rows is a bug, not an optimization.

### Write cost

[`write_cost`](src/sql_performance_lab/lab.py) inserts 100,000 orders with only the primary key, then with every index the orders cases create, and compares the times.

## Engineering decisions

| Decision | Alternative | Why |
|---|---|---|
| `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` | Wall clock around `execute` | Execution time without network and client overhead, plus the plan and the pages touched, which explain the time. |
| Median of 7 after 2 warm-up runs | One cold run | Cold runs measure the disk, not the plan. The warm cache is the normal state of a hot query. |
| Each variant alone, other indexes dropped | Add indexes cumulatively | Otherwise the planner may pick an index from an earlier variant and the comparison is meaningless. |
| Compare result rows of every variant | Trust the rewrite | Keyset pagination and range rewrites are easy to get subtly wrong (off by one, time zone). The benchmark refuses to save a fix that changes the answer. |
| Parameters picked from the data (the busiest customer, the middle e-mail) | Hard-coded ids | Works at any scale and avoids picking an easy case by accident. |
| `VACUUM (ANALYZE)` after loading | `ANALYZE` only | Index-only scans need the visibility map, which only VACUUM sets. |
| Default Postgres settings | Tuned `work_mem`, `shared_buffers` | The point is query and index design; tuning would blur which change did what. |


## Tests

25 tests on Python 3.11, 3.12 and 3.13 in CI against PostgreSQL 16, plus a Docker Compose job that runs all eight cases.

| What | Checked by |
|---|---|
| Every fix returns the same rows as the slow query | one test per case |
| The fix removes the sequential scan | plan node types of the first and last variant |
| An index on `email` does not serve `lower(email)` | the plain index variant still plans a `Seq Scan` |
| The covering index gives an index-only scan | plan node type |
| The partial index is smaller than the full one; BRIN is far smaller than the B-tree | `pg_relation_size` |
| Parameters are deterministic; the write test cleans up rows and indexes | direct queries |

## Limitations

- Warm cache only. On cold disk the slow variants would be much slower and the gaps larger; not measured here.
- One query at a time. Under concurrency, sequential scans also compete for I/O and buffers with everything else.
- Default settings on a laptop. A tuned server changes the absolute numbers, and sometimes the plan.
- Parallel query is on (Postgres default) and the slow variants use it (`Gather` in their plans). Runs with it off were not measured.
- The write cost uses `INSERT ... SELECT` in one transaction. Many small inserts from an application pay per-statement overhead too.
- `pg_trgm` for `LIKE '%term%'` search is not covered: the embedded server used locally does not ship the extension.


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
