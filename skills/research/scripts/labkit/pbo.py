"""The probability of backtest overfitting of a family of runs (`lab.py pbo`): combinatorially symmetric
cross-validation (Bailey, Borwein, Lopez de Prado and Zhu 2017) on the runs' score series, aligned on the
periods they share. The periods are cut into S blocks; for every way to take half of them, the run that
looks best there is ranked among all on the other half. PBO is how often it lands at or below the median:
near 0 when the best run is best for a reason, near 0.5 or above when picking the best is picking noise.
A diagnostic for the reviewer and the brief, not a gate. Each family, block count and statistic is
recorded once, so trying several block counts shows in the record.
"""
from __future__ import annotations

from . import stats
from .ledger import Ledger, sha256_file
from .protocol import bad_id, fail, is_control
from .selection import align, read_series, research_settings, series_file, tested

MAX_BLOCKS = 16  # C(16, 8) = 12870 splits, the paper's choice; the count grows fast beyond it
BINS = (-2.0, -1.0, 0.0, 1.0, 2.0)
QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


def family(lab, ledger: Ledger, ids: list) -> tuple:
    """({id: (run, test)}, [(id, why)]): the named runs, or every registered experiment that is not a control
    and whose latest run has a tested series. A named run that cannot take part is an error."""
    named = bool(ids)
    ids = ids or [e["id"] for e in ledger.find("register") if isinstance(e.get("id"), str)]
    found, apart = {}, []
    for id in dict.fromkeys(ids):
        run, test = tested(ledger, id)
        why = ("it is a control (round 0)" if is_control(lab, id) or (run and run.get("control"))
               else "it has not run" if not run else "its latest run has no tested series"
               if not test or test.get("sharpe") is None else "")
        if why:
            if named:
                raise ValueError(f"{id}: {why}")
            if "control" not in why and "not run" not in why:  # a run without a series is part of the family
                apart.append((id, why))
            continue
        found[id] = (run, test)
    return found, apart


def auto_blocks(periods: int) -> int:
    return max((s for s in range(4, MAX_BLOCKS + 1, 2) if periods // s >= stats.MIN_BLOCK_PERIODS), default=0)


def cmd_pbo(a) -> int:
    for id in a.ids:
        if bad_id(type("A", (), {"id": id})()):
            return 1
    ledger = Ledger(a.lab)
    settings = research_settings(ledger)
    try:
        runs, apart = family(a.lab, ledger, a.ids)
    except ValueError as e:
        return fail(str(e))
    series = {}
    for id, (run, test) in runs.items():
        path = series_file(a.lab, id)
        try:
            if sha256_file(path) != test["daily_sha"]:
                raise ValueError(f"{path.name} changed after its run was tested")
            series[id] = read_series(path, settings["column"])
        except (OSError, ValueError) as e:
            if a.ids:
                return fail(f"{id}: {e}")
            apart.append((id, str(e)))
    order = {e["id"]: e["seq"] for e in ledger.find("register")}
    shared, left = align(series, order)
    apart += left
    kept = sorted((id for id in series if id not in dict(left)), key=lambda i: order.get(i, 0))
    if len(kept) < 2:
        return fail("the probability of backtest overfitting needs at least two runs with tested series on shared "
                    f"periods; {len(kept)} here" + "".join(f"; {id}: {why}" for id, why in apart))
    blocks = a.blocks if a.blocks is not None else auto_blocks(len(shared))
    if blocks < 4 or blocks % 2 or blocks > MAX_BLOCKS or len(shared) // blocks < stats.MIN_BLOCK_PERIODS:
        return fail(f"--blocks {blocks}: an even number from 4 to {MAX_BLOCKS}, with at least "
                    f"{stats.MIN_BLOCK_PERIODS} of the {len(shared)} shared periods in each block "
                    f"(at most {auto_blocks(len(shared))} here)")
    members = sorted([id, runs[id][0]["seq"]] for id in kept)
    done = next((e for e in ledger.find("pbo") if e.get("runs") == members and e.get("blocks") == blocks
                 and e.get("stat") == a.stat and e.get("periods") == len(shared)), None)
    if done:
        print(f"recorded already on {done['ts'][:10]}:")
    else:
        result = stats.pbo([[series[id][k] for k in shared] for id in kept], blocks, a.stat)
        logits = sorted(result["logits"])
        done = ledger.append(
            "pbo", runs=members, periods=len(shared), blocks=blocks, stat=a.stat, splits=result["splits"],
            pbo=result["pbo"], quantiles=[logits[min(len(logits) - 1, int(q * len(logits)))] for q in QUANTILES],
            bins=[sum(1 for x in logits if (lo is None or x > lo) and (hi is None or x <= hi))
                  for lo, hi in zip((None,) + BINS, BINS + (None,))],
            slope=result["slope"], intercept=result["intercept"], loss=result["loss"], is_mean=result["is_mean"],
            oos_mean=result["oos_mean"], left_out=[[id, why] for id, why in apart],
            chosen=sorted(([id, share] for id, share in zip(kept, result["chosen"]) if share), key=lambda c: -c[1]))
    for line in report(done):
        print(line)
    for id, why in apart:
        print(f"  {id} left out: {why}")
    return 0


def report(e: dict) -> list:
    stat = "Sharpe" if e["stat"] == "sharpe" else "mean score"
    lines = [f"PBO over {len(e['runs'])} runs ({', '.join(id for id, _ in e['runs'])}) on {e['periods']} shared "
             f"periods: {e['blocks']} blocks, {e['splits']} splits, by the {stat}",
             f"  probability of backtest overfitting {e['pbo']:.2f}: the run best on half of the periods ranks at or "
             f"below the median on the other half in {e['pbo']:.0%} of the splits",
             "  logit of its rank out of sample (0 is the median; above is better): "
             + ", ".join(f"{int(q * 100)}% {v:+.2f}" for q, v in zip(QUANTILES, e["quantiles"]))]
    edges = ("<= -2", "-2 to -1", "-1 to 0", "0 to 1", "1 to 2", "> 2")
    for edge, count in zip(edges, e["bins"]):
        share = count / e["splits"]
        lines.append(f"    {edge:>9} {'#' * round(share * 40):<40} {share:.0%}")
    lines.append("  chosen in sample: " + ", ".join(f"{id} {share:.0%}" if share >= 0.005 else f"{id} <1%"
                                                 for id, share in e["chosen"]))
    lines.append(f"  degradation: the run chosen in sample has a {stat} of {e['is_mean']:.4g} there and {e['oos_mean']:.4g} "
                 f"out of sample on average, and loses out of sample in {e['loss']:.0%} of the splits")
    if e["slope"] is not None:
        lines.append(f"  across splits, out of sample = {e['intercept']:+.4g} {e['slope']:+.3g} x in sample"
                     + (" (one run is chosen almost always: the two halves of its one series pull the slope toward -1, "
                        "so the averages say more)" if e["chosen"][0][1] >= 0.9 else ""))
    return lines
