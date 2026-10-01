"""Command line.

sqllab generate --scale 1 --out data/sf1
sqllab run --data data/sf1 --url postgresql://...      (or DATABASE_URL)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg

from .cases import cases, params
from .db import load
from .lab import run_case


def table(results) -> str:
    lines = ["| case | variant | median ms | plan | index MB | same rows |",
             "|---|---|---:|---|---:|---|"]  # fmt: skip
    for r in results:
        for v in r.variants:
            lines.append(f"| {r.key} | {v.label} | {v.median_ms:.2f} | {' > '.join(v.plan_nodes[:3])} "
                         f"| {v.index_mb:.1f} | {'yes' if v.same_result else 'NO'} |")  # fmt: skip
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="sqllab", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--scale", type=float, default=1)
    g.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", type=Path, required=True)
    r.add_argument("--url", default=os.environ.get("DATABASE_URL"))
    r.add_argument("--runs", type=int, default=5)
    a = p.parse_args(argv)
    if a.cmd == "generate":
        from shopflow_datagen import GenConfig, write  # noqa: PLC0415

        write(GenConfig(scale=a.scale), a.out)
        print(a.out)
        return
    if not a.url:
        p.error("set DATABASE_URL or pass --url")
    with psycopg.connect(a.url) as conn:
        load(conn, a.data)
        print(table([run_case(conn, c, runs=a.runs) for c in cases(params(conn))]))


if __name__ == "__main__":
    main()
