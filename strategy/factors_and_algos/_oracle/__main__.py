"""The oracle mode's own entry — the isolated confidence-scale
optimizer (2026-11-20).

    python -m strategy.factors_and_algos._oracle [--sec-type index]
                                                 [--codes 000001,...]
                                                 [--out-csv PATH]

Sweeps the replay's confidence-scaling grid against direct profit/risk
metrics (see optimizer.py for the isolation contract — no Omega/Calmar,
no trainer, no persistence) over the 10 study indices by default, and
prints the ranked matrix + champions. The champion is then pinned BY
HAND into ``algo.py`` ``DEFAULT_PARAMS["conf_scale"]`` (never upserted).
"""
# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate

activate()

import argparse  # noqa: E402
import csv  # noqa: E402
import sys  # noqa: E402

DEFAULT_OUT_CSV = "temp_scripts/oracle_conf_scale_results.csv"


def _default_codes() -> list:
    """The mandatory study scope — the 10 named indices (single source
    of truth: the oracle analysis tier's own scope constant)."""
    from analysis.oracle_signals.config import STUDY_INDEX_CODES

    return list(STUDY_INDEX_CODES)


def main() -> int:
    from strategy.factors_and_algos._oracle.optimizer import (
        DD_TOL,
        OracleScaleOptimizer,
        build_pnl_grid,
        build_scale_grid,
    )

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sec-type", default="index")
    ap.add_argument("--codes", default=None,
                    help="comma-separated codes (default: the 10 study "
                         "indices)")
    ap.add_argument("--grid", default="scale", choices=("scale", "pnl"),
                    help="scale = the return%% grid; pnl = the creative "
                         "side-split grid ranked by ABSOLUTE realized "
                         "PnL (naive + identity reference cells first)")
    ap.add_argument("--out-csv", default=DEFAULT_OUT_CSV)
    args = ap.parse_args()

    codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.codes else _default_codes()
    )
    opt = OracleScaleOptimizer(args.sec_type, codes)
    grid = build_pnl_grid() if args.grid == "pnl" else None
    rows = opt.run(grid=grid)
    write_csv(args.out_csv, rows)
    if args.grid == "pnl":
        report_pnl(rows)
    else:
        report(rows)
    return 0


def write_csv(path: str, rows: list) -> None:
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV -> {path}")


def _fmt_cfg(r: dict) -> str:
    return (f"bp={r['buy_power'] or r['grade_power']} "
            f"bf={r['buy_floor'] or 0} "
            f"sp={r['sell_power'] or r['grade_power']} "
            f"sf={r['sell_floor'] or 0} ex={r['exit_mode']}")


def report_pnl(rows: list) -> None:
    """Rank by ABSOLUTE realized PnL, with the naive flat ±100 replay
    and the raw-grade identity as standing reference lines."""
    def _ref(name: str) -> dict | None:
        for r in rows:
            if name == "naive" and r["buy_floor"] == 100.0 \
                    and r["sell_floor"] == 100.0:
                return r
            if name == "identity" and r["identity"]:
                return r
        return None

    print("\n=== ranked by FULL realized PnL (absolute units) ===")
    for r in sorted(rows, key=lambda r: r["full_pnl"], reverse=True)[:20]:
        print(f"{_fmt_cfg(r):<48} pnl {r['full_pnl']:+9.1f} "
              f"(ret {r['full_ret']:+.1%}, cap {r['full_capital']:.0f}, "
              f"dd {r['full_dd']:.1%}) | OOS ret {r['oos_ret']:+.1%}")
    for name in ("naive", "identity"):
        r = _ref(name)
        if r is not None:
            print(f"REF {name:<8} {_fmt_cfg(r):<40} pnl {r['full_pnl']:+9.1f} "
                  f"(ret {r['full_ret']:+.1%}, cap {r['full_capital']:.0f}, "
                  f"dd {r['full_dd']:.1%}) | OOS ret {r['oos_ret']:+.1%}")


def report(rows: list) -> None:
    """Ranked tables: full matrix by FULL return, the champion (max
    return within DD_TOL of identity), and the min-DD champion."""
    from strategy.factors_and_algos._oracle.optimizer import DD_TOL

    ident_dd = next(
        (r["full_dd"] for r in rows if r["identity"]), None,
    )
    print("\n=== ranked by FULL total_return ===")
    for r in sorted(rows, key=lambda r: r["full_ret"], reverse=True):
        print(
            f"p={r['grade_power']:<4} fl={r['grade_floor']:<4} "
            f"null={r['null_conf']:<5} ex={r['exit_mode']:<5} "
            f"FULL {r['full_ret']:+.1%} (dd {r['full_dd']:.1%}, "
            f"ret/dd {r['full_rodd']:+.2f}, codes+ "
            f"{r['full_codes_pos']}/{r['full_codes']}) | "
            f"IS {r['is_ret']:+.1%} | OOS {r['oos_ret']:+.1%} "
            f"(dd {r['oos_dd']:.1%})"
        )

    if ident_dd is not None:
        dd_cap = ident_dd + DD_TOL
        eligible = [r for r in rows if r["full_dd"] <= dd_cap]
        champ = (
            max(eligible, key=lambda r: r["full_ret"])
            if eligible else None
        )
        print(
            f"\nCHAMPION (max FULL return, dd <= identity "
            f"{ident_dd:.1%} + {DD_TOL:.1%}): "
            + (
                f"p={champ['grade_power']} fl={champ['grade_floor']} "
                f"null={champ['null_conf']} ex={champ['exit_mode']} "
                f"-> FULL {champ['full_ret']:+.1%} "
                f"(dd {champ['full_dd']:.1%}) | OOS "
                f"{champ['oos_ret']:+.1%} (dd {champ['oos_dd']:.1%})"
                if champ else "none eligible"
            )
        )
    min_dd = min(rows, key=lambda r: r["full_dd"]) if rows else None
    if min_dd is not None:
        print(
            f"MIN-DD: p={min_dd['grade_power']} fl={min_dd['grade_floor']} "
            f"null={min_dd['null_conf']} ex={min_dd['exit_mode']} "
            f"-> FULL {min_dd['full_ret']:+.1%} "
            f"(dd {min_dd['full_dd']:.1%})"
        )


if __name__ == "__main__":
    sys.exit(main())
