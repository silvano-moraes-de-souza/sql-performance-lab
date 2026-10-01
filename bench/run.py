"""Every case at two scales, plus what the indexes cost on writes.

    uv run python -m bench.run

Writes results/*.json and the charts in docs/assets/. Loading is not timed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import matplotlib
import psycopg
from shopflow_datagen import GenConfig, write

from bench.harness import CaseResult as Bench
from bench.harness import save
from bench.pg import server_url
from sql_performance_lab.cases import cases, params
from sql_performance_lab.db import load
from sql_performance_lab.lab import run_case, write_cost

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SCALES = [1, 10]
RUNS = 7
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SLOW, FAST = "#c2410c", "#2a78d6"


def run_scale(url: str, scale: float, where: str) -> tuple[Path, list, dict]:
    data = Path(tempfile.mkdtemp(prefix=f"sqllab-sf{scale:g}-"))
    write(GenConfig(scale=scale), data)
    with psycopg.connect(url) as conn:
        counts = load(conn, data)
        p = params(conn)
        all_cases = cases(p)
        results = [run_case(conn, c, runs=RUNS) for c in all_cases]
        writes = write_cost(conn, all_cases) if scale == max(SCALES) else None
    rows = []
    for r in results:
        assert all(v.same_result for v in r.variants), f"{r.key}: a fix changed the result"
        for v in r.variants:
            rows.append(Bench(f"{r.key} · {v.label}", {"case": r.key, "variant": v.label,
                                                      "scale": scale},
                              len(v.exec_ms), [ms / 1000 for ms in v.exec_ms], [0.0],
                              {"plan": v.plan_nodes, "shared_hit": v.shared_hit,
                               "shared_read": v.shared_read, "rows": v.rows,
                               "index_bytes": v.index_bytes, "index_mb": round(v.index_mb, 3), "build_s": round(v.build_s, 3),
                               "same_result": v.same_result}))  # fmt: skip
        print(
            f"scale {scale} {r.key}: {r.variants[0].median_ms:.1f} ms -> "
            f"{r.variants[-1].median_ms:.2f} ms ({r.speedup:.0f}x)"
        )
    path = save(f"cases_scale_{scale:g}", rows, notes=(
        f"ShopFlow scale {scale:g}: {counts}. EXPLAIN (ANALYZE, BUFFERS) execution time, "
        f"median of {RUNS} after 2 warm-up runs, warm cache. Parameters: {p}. {where}."))  # fmt: skip
    return path, results, writes


def chart(results: list, scale: float, out: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 0.62 * len(results) + 1.4), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    labels = [r.title for r in results][::-1]
    slow = [r.variants[0].median_ms for r in results][::-1]
    fast = [r.variants[-1].median_ms for r in results][::-1]
    y = range(len(results))
    ax.barh([i + 0.2 for i in y], slow, height=0.38, color=SLOW, label="as written", zorder=2)
    ax.barh([i - 0.2 for i in y], fast, height=0.38, color=FAST, label="with the fix", zorder=2)
    ax.set_xscale("log")
    for i, (s, f) in enumerate(zip(slow, fast, strict=True)):
        ax.text(s * 1.15, i + 0.2, f"{s:.3g} ms", va="center", fontsize=7.5, color=INK)
        ax.text(
            f * 1.15, i - 0.2, f"{f:.3g} ms  ({s / f:.0f}x)", va="center", fontsize=7.5, color=INK
        )
    ax.set_yticks(list(y), labels, fontsize=8.5, color=INK)
    ax.set_xlim(min(fast) / 2, max(slow) * 12)
    ax.set_xlabel("Median execution time, ms (log scale)", color=MUTED, fontsize=9)
    ax.tick_params(axis="x", colors=MUTED, labelsize=8)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8, zorder=0)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    ax.set_title(f"Eight slow queries and their fixes, ShopFlow scale {scale:g}", loc="left",
                 color=INK, fontsize=11, pad=12)  # fmt: skip
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    url, where = server_url()
    for scale in SCALES:
        path, results, writes = run_scale(url, scale, where)
        print(path)
        chart(results, scale, Path(f"docs/assets/cases_scale_{scale:g}.png"))
        if writes:
            rows = [
                Bench(label, {"rows_inserted": 100_000}, len(t), t, [0.0])
                for label, t in writes.items()
            ]
            base = rows[0].median_s
            for r in rows:
                r.extra["vs_primary_key_only_pct"] = round(100 * (r.median_s / base - 1), 1)
            print(save("write_cost", rows, notes=(
                f"INSERT ... SELECT of 100,000 orders at scale {scale:g}, with only the primary key "
                f"vs with every index the orders cases create. {where}.")))  # fmt: skip


if __name__ == "__main__":
    main()
