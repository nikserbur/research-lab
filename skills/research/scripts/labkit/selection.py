"""Selection: what may be called the best result once many things have been tried.

Every `lab.py run` is a test. When it ends, the run's `results/<id>/scores.tsv` (`daily.tsv` in
older researches: a header, then one row per case, a day, a test cycle or an episode, with a `period`
column and the score column) is tested at once, under the ledger's lock: deflated Sharpe against the best Sharpe that as many tries of noise would show, a
block-bootstrap p-value and interval, and the LORD++ level for this place in the sequence of runs.
The noise benchmark uses the variance of a Sharpe estimate whose true value is 0 (1/n), not the spread
of the trials' Sharpe ratios: one trial that loses heavily to costs would otherwise raise the bar for
every later one. The trial count only grows, so `best` and `holdout open` deflate again with the count
of the day: a candidate run early is not judged against the few trials there were then. A run without a usable
series is a test with p = 1, so untested runs still spend the error budget. The settings (value
column, block length, alpha) are fixed for the whole research before its first run. Nothing here
can be recomputed with friendlier settings: stats only shows the record, and compare is recorded
once per pair of runs.
"""
from __future__ import annotations

import math
import pathlib
import re
import statistics

from . import stats
from .ledger import Ledger, read_metrics, sha256_file, text_lines
from .protocol import (bad_id, current_best, fail, is_control, journal, judged_latest, latest_run, protocol_problem,
                       trial_count)

MIN_PERIODS = 20
MIN_DSR = 0.95
MAX_ALPHA = 0.1
DEFAULTS = {"column": "pnl", "block": "auto", "alpha": 0.05, "run_timeout": None, "max_trials": None, "max_controls": 10,
            "scenarios": "", "select_by": ""}
VERDICTS = ("PASS", "BLOCK")


def research_settings(ledger: Ledger) -> dict:
    found = ledger.last("settings")
    return {k: found.get(k, v) for k, v in DEFAULTS.items()} if found else dict(DEFAULTS)


def read_series(path: pathlib.Path, column: str) -> dict:
    """{period: value} in file order. Duplicate periods, blank or non-finite values are errors: a
    series with gaps chosen after the fact could be made to look better."""
    lines = text_lines(path)
    if not lines:
        raise ValueError(f"{path.name} is empty")
    head = lines[0].split("\t")
    if column not in head:
        raise ValueError(f"no column {column!r} in {path.name} (columns: {', '.join(head)})")
    out = {}
    for n, line in enumerate(lines[1:], 2):
        row = dict(zip(head, line.split("\t")))
        key = row.get("period") or str(n - 1)
        try:
            value = float(row.get(column, ""))
        except ValueError:
            raise ValueError(f"{path.name} line {n}: {column} is {row.get(column)!r}, not a number")
        if not math.isfinite(value):
            raise ValueError(f"{path.name} line {n}: {column} is {value}")
        if key in out:
            raise ValueError(f"{path.name} line {n}: period {key} appears twice")
        out[key] = value
    return out


def series_file(lab: pathlib.Path, id: str) -> pathlib.Path:
    """The run's score per case: scores.tsv, or daily.tsv as older researches and trading projects name it."""
    out = lab / "results" / id
    return out / "scores.tsv" if (out / "scores.tsv").exists() or not (out / "daily.tsv").exists() else out / "daily.tsv"


def noise_benchmark(trials: int, n: int) -> float:
    """The per-period Sharpe the best of `trials` strategies without an edge is expected to show over n periods."""
    return stats.expected_max_sharpe(trials, 1 / n)


def dsr_now(e: dict, trials: int) -> float:
    """A recorded test's deflated Sharpe against the trials run so far (never above the recorded one)."""
    if e.get("sharpe") is None or not e.get("n"):
        return 0.0
    return min(e["dsr"], stats.psr(e["sharpe"], noise_benchmark(max(trials, e["trials"]), e["n"]), e["n"],
                                   e["skew"], e["kurt"]))


def block_for(values: list, setting) -> float:
    return stats.auto_block(values) if setting == "auto" else min(max(float(setting), 1.0), len(values) / 4)


def configs_of(lab: pathlib.Path, id: str) -> int:
    """Configurations a run tried inside itself (a `configs` metric), so a sweep counts as its size."""
    try:
        value = read_metrics(lab / "results" / id / "metrics.tsv").get("configs", 1)
    except (OSError, ValueError):
        return 1
    return max(1, int(value))


def record_test(lab: pathlib.Path, ledger: Ledger, id: str, run: dict, exit_code: int) -> dict:
    """Test one finished run and append the result; called by `lab.py run` as it ends."""
    path, configs = series_file(lab, id), configs_of(lab, id)
    row = journal(lab).get(id, {})

    def compute(led: Ledger) -> dict:
        settings = research_settings(led)
        control = bool(run.get("control"))
        prior = [e for e in led.find("stats") if not e.get("control")]  # controls test the environment, not ideas
        trials = trial_count(led) + configs - 1  # this run's own sweep is not in the record yet
        fields = {"id": id, "run_seq": run["seq"], "candidate": run["candidate"], "round": row.get("round", ""),
                  "configs": configs, "trials": trials, "control": control,
                  **{f"setting_{k}": v for k, v in settings.items()}}
        try:
            if exit_code == 124:
                raise ValueError("the run was stopped by the research's time budget")
            if exit_code == -15 or exit_code == -1:
                raise ValueError("the run was interrupted")
            if exit_code:
                raise ValueError(f"the run exited with code {exit_code}")
            series = read_series(path, settings["column"])
            if len(series) < MIN_PERIODS:
                raise ValueError(f"{len(series)} periods; at least {MIN_PERIODS} are needed")
        except (OSError, ValueError) as e:
            fields.update(p=1.0, sharpe=None, dsr=0.0, note=f"not tested: {e}")
        else:
            values = list(series.values())
            n = len(values)
            mean, sd, skew, kurt = stats.moments(values)
            sr = stats.sharpe(values)
            pool = {e["candidate"]: e["sharpe"] for e in prior if e.get("sharpe") is not None}
            pool[run["candidate"]] = sr
            benchmark = noise_benchmark(trials, n)
            block = block_for(values, settings["block"])
            boot = stats.bootstrap(values, block)
            n95 = stats.min_track_length_95(sr, trials, skew, kurt)
            fields.update(n=n, mean=mean, sd=sd, skew=skew, kurt=kurt, sharpe=sr, pool=len(pool),
                          sharpe_noise_best=benchmark, psr=stats.psr(sr, 0.0, n, skew, kurt),
                          dsr=stats.psr(sr, benchmark, n, skew, kurt), block=block, p=boot["p"], lo=boot["lo"],
                          hi=boot["hi"], periods_for_dsr95=n95 if math.isfinite(n95) else None,
                          daily_sha=sha256_file(path))
        if control:  # outside the error budget: a control spends no LORD++ position
            fields.update(lord_level=0.0, discovery=False)
        else:
            level, found = stats.lord_levels([e["p"] for e in prior] + [fields["p"]], settings["alpha"])[-1]
            fields.update(lord_level=level, discovery=found)
        return fields

    return ledger.append("stats", compute=compute)


def describe(e: dict) -> list:
    if e.get("sharpe") is None:
        return [f"{e['id']}: {e.get('note', 'not tested')}; " + ("a control, so it spends no trial or LORD++ position"
                if e.get("control") else f"counts as a test with p = 1 (trial {e['trials']})")]
    alpha = e.get("setting_alpha") or DEFAULTS["alpha"]
    lines = [f"{e['id']}: {e['n']} periods, mean {e['mean']:.4g} (95% interval {e['lo']:.4g} to {e['hi']:.4g}, "
             f"block {e['block']:.1f}), Sharpe {e['sharpe']:.3f} per period",
             f"  {e['trials']} trials so far: noise alone would show a best Sharpe of {e['sharpe_noise_best']:.3f}; "
             f"deflated Sharpe {e['dsr']:.3f} (against 0: {e['psr']:.3f})",
             (f"  a control: it tests the environment and spends no LORD++ position; p(mean > 0) {e['p']:.2e}" +
              (f"\n  ! below alpha {alpha:g}: right for a known case, a leak or a bug for doing nothing or a "
               "shuffled target" if e["p"] < alpha else "") if e.get("control") else
              f"  p(mean > 0) {e['p']:.2e}; LORD++ level {e['lord_level']:.2e}: "
              f"{'a discovery' if e['discovery'] else 'not a discovery'}")]
    if e["dsr"] < MIN_DSR:
        need = e.get("periods_for_dsr95")
        lines.append(f"  deflated Sharpe below {MIN_DSR}: " + (f"about {math.ceil(need)} periods would be needed "
                     f"at this Sharpe" if need else "no positive Sharpe to build on"))
    return lines


def cmd_settings(a) -> int:
    ledger = Ledger(a.lab)
    current = research_settings(ledger)
    if all(getattr(a, k) is None for k in DEFAULTS):
        print("  ".join(f"{k} {v}" for k, v in current.items()) + ("" if ledger.last("settings") else "  (defaults)"))
        return 0
    if ledger.find("stats"):
        return fail("the settings are fixed: this research has tested runs already")
    new = {"column": a.column or current["column"], "block": a.block or current["block"],
           "alpha": a.alpha if a.alpha is not None else current["alpha"],
           "run_timeout": a.run_timeout if a.run_timeout is not None else current["run_timeout"],
           "max_trials": a.max_trials if a.max_trials is not None else current["max_trials"],
           "max_controls": a.max_controls if a.max_controls is not None else current["max_controls"],
           "scenarios": a.scenarios if a.scenarios is not None else current["scenarios"],
           "select_by": a.select_by if a.select_by is not None else current["select_by"]}
    if new["run_timeout"] is not None and not 1 <= new["run_timeout"] <= 7 * 86400:
        return fail("run-timeout: seconds per run, from 1 to a week")
    if new["max_trials"] is not None and not 1 <= new["max_trials"] <= 100_000:
        return fail("max-trials: from 1 to 100000")
    if new["select_by"] and not re.fullmatch(r"-?[A-Za-z_][\w.]*", new["select_by"]):
        return fail("select-by: a metric of metrics.tsv, higher is better; -metric for lower is better")
    if new["scenarios"] and not re.fullmatch(r"[a-z][a-z0-9]*(,[a-z][a-z0-9]*)*", new["scenarios"]):
        return fail("scenarios: names like stress or stress,borrow (lower case, comma-separated)")
    if not 0 <= new["max_controls"] <= 100:
        return fail("max-controls: from 0 to 100 (controls spend no trial, so they are capped apart)")
    if not re.fullmatch(r"[A-Za-z_][\w.]*", new["column"]):
        return fail(f"column {new['column']!r}: a plain column name")
    if new["block"] != "auto":
        try:
            if not 1 <= float(new["block"]) <= 250:
                raise ValueError
        except ValueError:
            return fail("block: 'auto' or a number of periods from 1 to 250")
    if not 0 < new["alpha"] <= MAX_ALPHA:
        return fail(f"alpha must be above 0 and at most {MAX_ALPHA}")
    ledger.append("settings", **new)
    print("  ".join(f"{k} {v}" for k, v in new.items()))
    return 0


def tested(ledger: Ledger, id: str):
    run = latest_run(ledger, id)
    if not run:
        return None, None
    return run, next((e for e in reversed(ledger.find("stats", id)) if e["run_seq"] == run["seq"]), None)


def cmd_stats(a) -> int:
    if bad_id(a):
        return 1
    run, e = tested(Ledger(a.lab), a.id)
    if not e:
        return fail(f"{a.id} has no tested run: every `lab.py run` is tested as it ends")
    for line in describe(e):
        print(line)
    trials = trial_count(Ledger(a.lab))
    now = dsr_now(e, trials)
    if e.get("sharpe") is not None and trials > e["trials"]:
        print(f"  deflated Sharpe now {now:.3f}, after {trials} trials")
    return 0 if e.get("discovery") and now >= MIN_DSR else 1


def cmd_compare(a) -> int:
    for id in (a.new, a.old):
        if bad_id(type("A", (), {"id": id})()):
            return 1
    ledger = Ledger(a.lab)
    (new_run, new_test), (old_run, old_test) = tested(ledger, a.new), tested(ledger, a.old)
    for id, t in ((a.new, new_test), (a.old, old_test)):
        if not t or t.get("sharpe") is None:
            return fail(f"{id} has no tested series on its latest run")
    done = next((e for e in ledger.find("compare", a.new)
                 if e["old"] == a.old and e["run_seq"] == new_run["seq"] and e["old_run_seq"] == old_run["seq"]), None)
    if not done:
        settings = research_settings(ledger)
        try:
            new = read_series(series_file(a.lab, a.new), settings["column"])
            old = read_series(series_file(a.lab, a.old), settings["column"])
        except (OSError, ValueError) as e:
            return fail(str(e))
        if sha256_file(series_file(a.lab, a.new)) != new_test["daily_sha"] or \
                sha256_file(series_file(a.lab, a.old)) != old_test["daily_sha"]:
            return fail("a scores file changed after its run was tested")
        if set(new) != set(old):
            return fail(f"{a.new} and {a.old} cover different periods ({len(set(new) - set(old))} only in {a.new}, "
                        f"{len(set(old) - set(new))} only in {a.old}): compare them on the same days")
        diffs = [new[k] - old[k] for k in new]
        boot = stats.bootstrap(diffs, block_for(diffs, settings["block"]))
        done = ledger.append("compare", id=a.new, old=a.old, run_seq=new_run["seq"], old_run_seq=old_run["seq"],
                             n=len(diffs), mean=statistics.fmean(diffs), lo=boot["lo"], hi=boot["hi"],
                             verdict="BETTER" if boot["lo"] > 0 else "NOT BETTER")
    print(f"{a.new} against {a.old} on {done['n']} shared periods: mean difference {done['mean']:.4g}, "
          f"95% interval {done['lo']:.4g} to {done['hi']:.4g}: {done['verdict']}")
    return 0 if done["verdict"] == "BETTER" else 1


def cmd_review(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    run = latest_run(ledger, a.id)
    if not run:
        return fail(f"{a.id} has not run")
    ledger.append("review", id=a.id, run_seq=run["seq"], verdict=a.verdict, round=a.round, by=a.by)
    print(f"{a.id}: review {a.round} {a.verdict} recorded for its run {run['seq']}")
    return 0


def reviewed(ledger: Ledger, id: str) -> bool:
    run, review = latest_run(ledger, id), ledger.last("review", id)
    return bool(run and review and review["run_seq"] == run["seq"] and review["verdict"] == "PASS")


def problems_of(lab: pathlib.Path, ledger: Ledger, id: str, trials: int) -> list:
    """Why `id` cannot take part in the selection of BEST (empty when it can)."""
    run, test = tested(ledger, id)
    if is_control(lab, id) or (run and run.get("control")):
        return ["it is a control (round 0): it tests the environment and is never a candidate"]
    problems = []
    if not judged_latest(ledger, id):
        problems.append("its latest run is not judged PASS")
    if not reviewed(ledger, id):
        problems.append("no reviewer PASS for its latest run (`lab.py review`)")
    if not test or test.get("sharpe") is None:
        problems.append("its latest run has no tested series")
    else:
        if not test["discovery"]:
            problems.append(f"not a discovery under LORD++ (p {test['p']:.2e} against level {test['lord_level']:.2e})")
        if dsr_now(test, trials) < MIN_DSR:
            problems.append(f"deflated Sharpe {dsr_now(test, trials):.3f} is below {MIN_DSR} after {max(trials, test['trials'])} trials")
    return problems


def eligible(lab: pathlib.Path, ledger: Ledger) -> dict:
    """{id: (run, test)} of every candidate the selection of BEST takes."""
    trials = trial_count(ledger)
    ids = [e["id"] for e in ledger.find("register") if isinstance(e.get("id"), str)]
    return {id: tested(ledger, id) for id in ids if not problems_of(lab, ledger, id, trials)}


def choose(lab: pathlib.Path, ledger: Ledger, cands: dict, settings: dict):
    """(winner, members, dropped, value) or (None, error): the model confidence set of the candidates'
    score series on their shared periods, then the highest `select_by` within it (the mean score unless
    the research named a metric before its first run; a leading - means lower is better)."""
    series = {}
    for id, (run, test) in cands.items():
        path = series_file(lab, id)
        if sha256_file(path) != test["daily_sha"]:
            return None, f"{path} changed after its run was tested"
        series[id] = read_series(path, settings["column"])
    shared = [k for k in series[next(iter(series))] if all(k in v for v in series.values())]
    if len(shared) < MIN_PERIODS:
        return None, f"the candidates share {len(shared)} periods; at least {MIN_PERIODS} are needed to choose between them"
    values = {id: [v[k] for k in shared] for id, v in series.items()}
    spread = [x - fmean_of(values, t) for id in values for t, x in enumerate(values[id])]
    block = block_for(spread, settings["block"]) if len(cands) > 1 else 1.0
    members, dropped = stats.confidence_set(values, block, settings["alpha"])
    key = (settings.get("select_by") or "").strip()
    def value(id):
        if not key:
            return statistics.fmean(values[id])
        metrics = read_metrics(lab / "results" / id / "metrics.tsv")
        name, sign = (key[1:], -1) if key.startswith("-") else (key, 1)
        return sign * metrics.get(name, float("-inf") * sign)
    order = {e["id"]: e["seq"] for e in ledger.find("register")}
    winner = max(members, key=lambda id: (value(id), -order.get(id, 0)))
    return winner, (members, dropped, value(winner), len(shared))


def fmean_of(values: dict, t: int) -> float:
    return statistics.fmean(v[t] for v in values.values())


def cmd_best(a) -> int:
    ledger = Ledger(a.lab)
    if protocol_problem(ledger):
        return fail(protocol_problem(ledger))
    trials = trial_count(ledger)
    if a.id:
        if bad_id(a):
            return 1
        problems = problems_of(a.lab, ledger, a.id, trials)
        if problems:
            return fail(f"{a.id} cannot be BEST: " + "; ".join(problems))
    cands = eligible(a.lab, ledger)
    if not cands:
        return fail("no candidate can be BEST yet: each needs a judge PASS and a reviewer PASS on its latest run, "
                    f"a LORD++ discovery and a deflated Sharpe of {MIN_DSR} after the trials so far")
    settings = research_settings(ledger)
    winner, detail = choose(a.lab, ledger, cands, settings)
    if winner is None:
        return fail(detail)
    members, dropped, value, shared = detail
    if a.id and a.id != winner:
        return fail(f"{a.id} cannot be BEST: the selection over all {len(cands)} candidates picks {winner} "
                    f"(confidence set {', '.join(sorted(members))}; chosen by {settings.get('select_by') or 'the mean score'})")
    best = current_best(ledger)
    run = cands[winner][0]
    pool = sorted([id, cands[id][0]["seq"]] for id in cands)
    print(f"{len(cands)} candidates on {shared} shared periods: {', '.join(sorted(cands))}")
    for id, p in dropped:
        print(f"  {id} left the confidence set (p {p:.3f})")
    print(f"  confidence set at alpha {settings['alpha']:g}: {', '.join(sorted(members))}; chosen by "
          f"{settings.get('select_by') or 'the mean score'}")
    if best and best["id"] == winner and best["run_seq"] == run["seq"] and best.get("candidates") == pool:
        print(f"{winner} is BEST already")
        return 0
    ledger.append("best", id=winner, run_seq=run["seq"], previous=best["id"] if best else "", candidates=pool,
                  members=sorted(members), dropped=[[id, p] for id, p in dropped],
                  select_by=settings.get("select_by") or "mean", value=value, periods=shared, trials=trials)
    print(f"{winner} is BEST" + (f" (was {best['id']})" if best and best["id"] != winner else "")
          + ". Mark its row BEST and the old one KEEP.")
    return 0


def cmd_fdr(a) -> int:
    ledger = Ledger(a.lab)
    tests = [e for e in ledger.find("stats") if not e.get("control")]
    alpha = research_settings(ledger)["alpha"]
    if not tests:
        print("no tested runs yet")
        return 0
    rounds = {}
    for e in tests:
        rounds.setdefault(e.get("round", ""), []).append(e)
    within = {}
    for group in rounds.values():
        for e, keep in zip(group, stats.benjamini_yekutieli([e["p"] for e in group], alpha)):
            within[e["seq"]] = keep
    print("id\tround\tp\tLORD++ level\tdiscovery\tBY in round")
    for e in tests:
        print(f"{e['id']}\t{e.get('round', '')}\t{e['p']:.2e}\t{e['lord_level']:.2e}\t"
              f"{'yes' if e['discovery'] else 'no'}\t{'yes' if within[e['seq']] else 'no'}")
    print(f"{len(tests)} tests (one per run), {sum(e['discovery'] for e in tests)} discoveries under LORD++ "
          f"at alpha {alpha:g}; each level was fixed when its run ended")
    return 0
