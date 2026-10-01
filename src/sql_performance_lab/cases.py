"""Eight slow queries and their fixes.

Each case is a list of variants. The first is the query as it is usually written
on a database with primary keys only; the others are fixes. Every variant runs on
its own: the indexes of the other variants are dropped first. Every variant must
return exactly the rows of the first one.

Parameters (a customer, a month, an offset) are picked from the data by
``params``, so the cases work at any scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg


@dataclass
class Variant:
    label: str
    sql: str
    ddl: list[str] = field(default_factory=list)  # run before, timed separately


@dataclass
class Case:
    key: str
    title: str
    lesson: str
    table: str
    variants: list[Variant]
    indexes: list[str] = field(default_factory=list)  # every index any variant creates


def params(conn: psycopg.Connection) -> dict:
    """Deterministic parameters taken from the loaded data."""
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    p = {
        "customer": q(
            "SELECT customer_id FROM shop.orders GROUP BY customer_id "
            "ORDER BY count(*) DESC, customer_id LIMIT 1"
        ),  # fmt: skip
        "email": q(
            "SELECT upper(email) FROM shop.customers ORDER BY customer_id "
            "OFFSET (SELECT count(*) / 2 FROM shop.customers) LIMIT 1"
        ),  # fmt: skip
        "order": q(
            "SELECT order_id FROM shop.orders ORDER BY order_id "
            "OFFSET (SELECT count(*) / 3 FROM shop.orders) LIMIT 1"
        ),  # fmt: skip
        "month_start": "2025-06-01 00:00:00-03",
        "month_end": "2025-07-01 00:00:00-03",
        "day": "2025-06-15",
        "offset": q("SELECT (count(*) / 2)::int FROM shop.orders"),
    }
    p["after_id"] = q(f"SELECT order_id FROM shop.orders ORDER BY order_id "
                      f"OFFSET {p['offset'] - 1} LIMIT 1")  # fmt: skip
    conn.commit()
    return p


def cases(p: dict) -> list[Case]:
    order_cols = "order_id, customer_id, ordered_at, status, total_cents"
    return [
        Case(
            "customer_history",
            "Last 20 orders of one customer",
            "A composite index matches both the filter and the sort, so Postgres reads 20 "
            "index entries instead of sorting a million rows.",
            "orders",
            [
                Variant(
                    "no index",
                    f"SELECT {order_cols} FROM shop.orders WHERE customer_id = "
                    f"{p['customer']} ORDER BY ordered_at DESC, order_id DESC LIMIT 20",
                ),  # fmt: skip
                Variant(
                    "index (customer_id)",
                    "",
                    ["CREATE INDEX lab_o_cust ON shop.orders (customer_id)"],
                ),
                Variant(
                    "index (customer_id, ordered_at DESC)",
                    "",
                    [
                        "CREATE INDEX lab_o_cust_time ON shop.orders "
                        "(customer_id, ordered_at DESC, order_id DESC)"
                    ],
                ),  # fmt: skip
            ],
            ["lab_o_cust", "lab_o_cust_time"],
        ),
        Case(
            "login_email",
            "Login by e-mail, case insensitive",
            "An index on email does not help lower(email) = ...; an index on the expression does.",
            "customers",
            [
                Variant(
                    "no index",
                    "SELECT customer_id, full_name FROM shop.customers "
                    f"WHERE lower(email) = lower('{p['email']}')",
                ),  # fmt: skip
                Variant(
                    "index (email)", "", ["CREATE INDEX lab_c_email ON shop.customers (email)"]
                ),
                Variant(
                    "index (lower(email))",
                    "",
                    ["CREATE INDEX lab_c_lower_email ON shop.customers (lower(email))"],
                ),
            ],
            ["lab_c_email", "lab_c_lower_email"],
        ),
        Case(
            "pending_queue",
            "Oldest 50 pending orders (a work queue)",
            "A partial index holds only the rows the query can return: a fraction of the size.",
            "orders",
            [
                Variant(
                    "no index",
                    f"SELECT {order_cols} FROM shop.orders WHERE status = 'pending' "
                    "ORDER BY ordered_at, order_id LIMIT 50",
                ),  # fmt: skip
                Variant(
                    "index (status, ordered_at)",
                    "",
                    [
                        "CREATE INDEX lab_o_status_time ON shop.orders (status, ordered_at, order_id)"
                    ],
                ),
                Variant(
                    "partial index WHERE status = 'pending'",
                    "",
                    [
                        "CREATE INDEX lab_o_pending ON shop.orders (ordered_at, order_id) "
                        "WHERE status = 'pending'"
                    ],
                ),  # fmt: skip
            ],
            ["lab_o_status_time", "lab_o_pending"],
        ),
        Case(
            "month_revenue",
            "Revenue of one month",
            "INCLUDE puts the summed columns in the index: an index-only scan never visits the table.",
            "orders",
            [
                Variant(
                    "no index",
                    "SELECT count(*), sum(total_cents) FROM shop.orders "
                    f"WHERE ordered_at >= '{p['month_start']}' AND ordered_at < '{p['month_end']}' "
                    "AND status IN ('shipped', 'delivered')",
                ),  # fmt: skip
                Variant(
                    "index (ordered_at)",
                    "",
                    ["CREATE INDEX lab_o_time ON shop.orders (ordered_at)"],
                ),
                Variant(
                    "index (ordered_at) INCLUDE (status, total_cents)",
                    "",
                    [
                        "CREATE INDEX lab_o_time_cover ON shop.orders (ordered_at) "
                        "INCLUDE (status, total_cents)"
                    ],
                ),  # fmt: skip
            ],
            ["lab_o_time", "lab_o_time_cover"],
        ),
        Case(
            "event_log_day",
            "Events of one day in an append-only log",
            "On a table written in time order, a BRIN index is a few pages and still skips "
            "almost everything; a B-tree is fast too but hundreds of times bigger.",
            "order_events",
            [
                Variant(
                    "no index",
                    "SELECT status, count(*) FROM shop.order_events "
                    f"WHERE event_at >= '{p['day']} 00:00:00-03' "
                    f"AND event_at < '{p['day']} 00:00:00-03'::timestamptz + interval '1 day' "
                    "GROUP BY status ORDER BY status",
                ),  # fmt: skip
                Variant(
                    "B-tree (event_at)",
                    "",
                    ["CREATE INDEX lab_e_btree ON shop.order_events (event_at)"],
                ),
                Variant(
                    "BRIN (event_at)",
                    "",
                    ["CREATE INDEX lab_e_brin ON shop.order_events USING brin (event_at)"],
                ),
            ],
            ["lab_e_btree", "lab_e_brin"],
        ),
        Case(
            "deep_pagination",
            "A page halfway through a listing (50 per page)",
            "OFFSET reads and throws away every row before the page; a keyset condition "
            "starts right where the last page ended.",
            "orders",
            [
                Variant(
                    "OFFSET",
                    f"SELECT {order_cols} FROM shop.orders ORDER BY order_id "
                    f"LIMIT 50 OFFSET {p['offset']}",
                ),  # fmt: skip
                Variant(
                    "keyset (order_id > last seen)",
                    f"SELECT {order_cols} FROM shop.orders WHERE order_id > {p['after_id']} "
                    "ORDER BY order_id LIMIT 50",
                ),  # fmt: skip
            ],
        ),
        Case(
            "function_on_column",
            "Orders placed on one day",
            "Wrapping the column in a function hides it from the index; a range on the "
            "raw column uses it. Both variants have the same index.",
            "orders",
            [
                Variant(
                    "date(ordered_at) = day",
                    f"SELECT {order_cols} FROM shop.orders "
                    f"WHERE (ordered_at AT TIME ZONE INTERVAL '-03:00')::date = '{p['day']}' "
                    "ORDER BY order_id",
                    ["CREATE INDEX lab_o_time2 ON shop.orders (ordered_at)"],
                ),  # fmt: skip
                Variant(
                    "ordered_at in [day, day + 1)",
                    f"SELECT {order_cols} FROM shop.orders "
                    f"WHERE ordered_at >= '{p['day']} 00:00:00-03' "
                    f"AND ordered_at < '{p['day']} 00:00:00-03'::timestamptz + interval '1 day' "
                    "ORDER BY order_id",
                    ["CREATE INDEX lab_o_time2 ON shop.orders (ordered_at)"],
                ),  # fmt: skip
            ],
            ["lab_o_time2"],
        ),
        Case(
            "items_of_order",
            "Line items of one order",
            "Postgres does not index foreign key columns by itself. Without one, every order "
            "page scans the whole items table.",
            "order_items",
            [
                Variant(
                    "no index",
                    "SELECT order_item_id, product_id, quantity, line_total_cents "
                    f"FROM shop.order_items WHERE order_id = {p['order']} ORDER BY order_item_id",
                ),  # fmt: skip
                Variant(
                    "index (order_id)",
                    "",
                    ["CREATE INDEX lab_i_order ON shop.order_items (order_id)"],
                ),
            ],
            ["lab_i_order"],
        ),
    ]


def resolve(case: Case) -> Case:
    """Variants with an empty SQL reuse the first variant's query (index-only fixes)."""
    base = case.variants[0].sql
    for v in case.variants:
        if not v.sql:
            v.sql = base
    return case
