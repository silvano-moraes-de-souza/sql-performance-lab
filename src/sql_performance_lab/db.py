"""Load ShopFlow into PostgreSQL with primary keys only, the state most slow databases start in."""

from __future__ import annotations

import io
from pathlib import Path

import psycopg
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

COLUMNS = {
    "customers": ["customer_id", "full_name", "email", "state", "signup_at"],
    "products": ["product_id", "sku", "name", "category", "brand", "price_cents", "cost_cents",
                 "active"],
    "orders": ["order_id", "customer_id", "ordered_at", "status", "channel", "subtotal_cents",
               "discount_cents", "shipping_cents", "total_cents", "delivered_at"],
    "order_items": ["order_item_id", "order_id", "product_id", "quantity", "unit_price_cents",
                    "line_total_cents"],
}  # fmt: skip

SCHEMA = """
DROP SCHEMA IF EXISTS shop CASCADE;
CREATE SCHEMA shop;
CREATE TABLE shop.customers (
    customer_id bigint PRIMARY KEY, full_name text, email text, state text, signup_at timestamptz);
CREATE TABLE shop.products (
    product_id bigint PRIMARY KEY, sku text, name text, category text, brand text,
    price_cents bigint, cost_cents bigint, active boolean);
CREATE TABLE shop.orders (
    order_id bigint PRIMARY KEY, customer_id bigint, ordered_at timestamptz, status text,
    channel text, subtotal_cents bigint, discount_cents bigint, shipping_cents bigint,
    total_cents bigint, delivered_at timestamptz);
CREATE TABLE shop.order_items (
    order_item_id bigint PRIMARY KEY, order_id bigint, product_id bigint, quantity bigint,
    unit_price_cents bigint, line_total_cents bigint);
"""

# An append-only log, written in time order like the change feed of day 03:
# every status an order went through, one row each.
EVENTS = """
CREATE TABLE shop.order_events (
    event_id bigserial PRIMARY KEY, order_id bigint, status text, event_at timestamptz);
INSERT INTO shop.order_events (order_id, status, event_at)
SELECT order_id, status, event_at FROM (
    SELECT order_id, 'pending' AS status, ordered_at AS event_at FROM shop.orders
    UNION ALL SELECT order_id, 'shipped', ordered_at + interval '1 day' FROM shop.orders
        WHERE status IN ('shipped', 'delivered', 'returned')
    UNION ALL SELECT order_id, 'canceled', ordered_at + interval '6 hours' FROM shop.orders
        WHERE status = 'canceled'
    UNION ALL SELECT order_id, 'delivered', delivered_at FROM shop.orders
        WHERE status IN ('delivered', 'returned')
) e ORDER BY event_at;
"""


def _copy(conn: psycopg.Connection, folder: Path, table: str) -> int:
    cols = COLUMNS[table]
    n = 0
    with conn.cursor().copy(f"COPY shop.{table} ({', '.join(cols)}) FROM STDIN (FORMAT csv)") as cp:
        for part in sorted(folder.glob("part-*.parquet")):
            for batch in pq.ParquetFile(part).iter_batches(batch_size=200_000, columns=cols):
                buf = io.BytesIO()
                pacsv.write_csv(pa.Table.from_batches([batch]), buf,
                                pacsv.WriteOptions(include_header=False))  # fmt: skip
                cp.write(buf.getvalue())
                n += batch.num_rows
    return n


def load(conn: psycopg.Connection, data: Path) -> dict[str, int]:
    """Create the schema, bulk load, build the event log, then VACUUM ANALYZE."""
    conn.execute(SCHEMA)
    counts = {t: _copy(conn, data / t, t) for t in COLUMNS}
    conn.execute(EVENTS)
    counts["order_events"] = conn.execute("SELECT count(*) FROM shop.order_events").fetchone()[0]
    conn.commit()
    # VACUUM sets the visibility map, which index-only scans depend on.
    conn.autocommit = True
    conn.execute("VACUUM (ANALYZE) ")
    conn.autocommit = False
    return counts
