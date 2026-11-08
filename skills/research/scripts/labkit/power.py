"""The power of a pass criterion, shown by `lab.py register` (and `register --dry-run`, which records
nothing): how often the criterion passes a run without an edge, and a run whose edge is the reference
run's in-sample estimate shrunk toward zero. The reference is the parent's latest tested run, else the
current BEST's, else the research's latest tested run. Both rates come from resampling its scores in
stationary blocks, so the series keeps its dependence and its tails: the mean is taken out for the null
and the shrunk mean put back for the other. A clause is modelled when its metric is, in the reference
run's own files, the mean score (in units of 1, 100 or 10000, per period or times a yearly factor), its
Sharpe ratio (per period or times the square root of one) or the number of periods. Other clauses are
taken to pass, so both rates are upper bounds.
"""
from __future__ import annotations

import math

from . import stats
from .ledger import OPS, Ledger, read_metrics, sha256_file
from .protocol import current_best
from .selection import block_for, read_series, research_settings, series_file, tested

YEARLY = (1, 12, 52, 252, 365)
UNITS = (1, 100, 10_000)


def reference(ledger: Ledger, parent: str):
    """(id, test, why) of the run whose scores model the criterion, or (None, None, why)."""
    best = (current_best(ledger) or {}).get("id")
    for id, why in ((parent, "the parent"), (best, "the current BEST")):
        test = tested(ledger, id)[1] if id else None
        if test and test.get("sharpe") is not None and not test.get("control"):
            return id, test, why
    test = next((e for e in reversed(ledger.find("stats")) if e.get("sharpe") is not None and not e.get("control")), None)
    if test:
        return test["id"], test, "the latest tested run"
    return None, None, "no run is tested yet"


def close(a: float, b: float, absolute: float = 0.0) -> bool:
    return abs(a - b) <= 2e-3 * max(abs(a), abs(b)) or abs(a - b) <= absolute


def model(value: float, values: list) -> tuple:
    """What a metric is in terms of the scores: ('periods',), ('mean', factor) or ('sharpe', factor, ddof), else ()."""
    n, (mean, sd, _, _) = len(values), stats.moments(values)
    if value == n:
        return ("periods",)
    for factor in sorted(u * y for u in UNITS for y in YEARLY):
        if close(value, factor * mean, 1e-6 * factor):
            return ("mean", factor)
    for ddof in (0, 1):
        spread = sd * math.sqrt(n / (n - ddof))
        for y in YEARLY:
            if spread > 0 and close(value, math.sqrt(y) * mean / spread):
                return ("sharpe", math.sqrt(y), ddof)
    return ()


def describe(name: str, kind: tuple) -> str:
    if kind[0] == "periods":
        return f"{name} is the number of periods"
    what = "mean score" if kind[0] == "mean" else "Sharpe ratio"
    return f"{name} is the {what}" + (f" times {kind[1]:g}" if kind[1] != 1 else "")


def metric(kind: tuple, mean: float, sd: float, n: int) -> float:
    if kind[0] == "periods":
        return n
    if kind[0] == "mean":
        return kind[1] * mean
    spread = sd * math.sqrt(n / (n - kind[2]))
    return kind[1] * mean / spread if spread > 0 else 0.0


def power(lab, ledger: Ledger, parent: str, clauses: list) -> dict:
    """The power report as a ledger-ready dict; {'note': why} when there is nothing to model it on."""
    id, test, why = reference(ledger, parent)
    if not id:
        return {"note": f"no power report: {why}, so nothing models the scores"}
    path, settings = series_file(lab, id), research_settings(ledger)
    try:
        if sha256_file(path) != test["daily_sha"]:
            raise ValueError(f"{path.name} changed after its run was tested")
        values = list(read_series(path, settings["column"]).values())
        metrics = read_metrics(lab / "results" / id / "metrics.tsv")
    except (OSError, ValueError) as e:
        return {"note": f"no power report: {id}'s scores cannot be read ({e})"}
    kinds = {name: model(metrics[name], values) for name, _, _ in clauses if name in metrics}
    modelled = {name: kind for name, kind in kinds.items() if kind}
    unmodelled = sorted({name for name, _, _ in clauses} - set(modelled))
    if not modelled:
        return {"note": f"no power report: no clause of the criterion is the mean score, the Sharpe ratio or the "
                        f"number of periods in {id}'s run ({', '.join(unmodelled)})"}
    n, mean = len(values), stats.moments(values)[0]
    block = block_for(values, settings["block"])
    draws = stats.resampled_moments([x - mean for x in values], block, max(400, min(2000, int(2e6 * block / n))))
    zs = [stats.NORMAL.inv_cdf(1 - min(max(e["p"], 1e-15), 1 - 1e-15)) for e in ledger.find("stats")
          if e.get("sharpe") is not None and not e.get("control")]
    z = stats.NORMAL.inv_cdf(1 - min(max(test["p"], 1e-15), 1 - 1e-15))
    factor = stats.shrink_factor(z, zs)

    def rate(shift: float) -> float:
        passed = sum(all(OPS[op](metric(modelled[name], m + shift, sd, n), bound)
                         for name, op, bound in clauses if name in modelled) for m, sd in draws)
        return passed / len(draws)

    return {"reference": id, "run_seq": test["run_seq"], "why": why, "periods": n, "block": block,
            "draws": len(draws), "modelled": {name: describe(name, kind) for name, kind in modelled.items()},
            "unmodelled": unmodelled, "null": rate(0.0), "mean": mean, "factor": factor,
            "shrunk_mean": mean * factor, "tests": len(zs), "shrunk": rate(mean * factor)}


def lines(report: dict, alpha: float) -> list:
    if "note" in report:
        return [report["note"]]
    how = (f"James-Stein over the research's {report['tests']} tests" if report["tests"] >= 3
           else "1 - 1/z^2 while fewer than three runs are tested")
    out = [f"power of this criterion, modelled on {report['reference']}'s run ({report['why']}; {report['periods']} "
           f"periods, block {report['block']:.1f}, {report['draws']} resamples):",
           "  " + "; ".join(report["modelled"].values()),
           f"  it passes {report['null']:.1%} of runs without an edge, and {report['shrunk']:.1%} of runs whose mean "
           f"score is {report['reference']}'s {report['mean']:.4g} shrunk to {report['shrunk_mean']:.4g} "
           f"(factor {report['factor']:.2f}, {how})"]
    if report["unmodelled"]:
        out.append(f"  not modelled, taken to pass (so both rates are upper bounds): {', '.join(report['unmodelled'])}")
    if report["shrunk"] < 0.5:
        out.append(f"  an edge of that shrunk size fails it {1 - report['shrunk']:.0%} of the time")
    if report["null"] > alpha:
        out.append(f"WARN  noise passes this criterion {report['null']:.1%} of the time, above alpha {alpha:g}: make it "
                   "stricter (a registered row is fixed; a stricter criterion gets a new id)")
    return out
