# Environments: <research name>

Written in round 0, before any hypothesis. The environments are requirements, not software:
research-lab brings no simulator. The project's own one (a backtester, a model of a business process,
a plant model, a training and evaluation harness) becomes the **simulation environment**
once it meets S1-S8 with evidence; there candidates are tried and models learn. A candidate judged
PASS there is judged once in the **verification environment**: held-out data, held-out scenarios or
operating conditions, or the real system itself (a paper-trading or shadow run, a pilot site, a test
bench). Status is ✅
(with evidence: a file, a `lab.py` output, a ledger entry), ❌, or n/a (with the reason in Evidence).
`lab.py env --stage sim` lists what is still unproven; a ❌ blocks round 1 unless the user accepts
the risk in PROGRAM.md (quoted, dated). V1-V5 are proven for a candidate, before its look.

Words used below: a **decision** is what the system under test does at one step (an order, a price,
a replenishment, a schedule, a command, a prediction); a **case** is one unit the score is counted
over (a day, a week of a store, a test cycle, an example); **costs and limits** are what acting pays
and what the real system cannot do.

## Simulation environment
- Command: `<exact command, run from a frozen copy of the script>`
- Data, scenarios and models: `<files>`, sha256 `<...>`; code: commit `<...>`; seeds: `<...>`
- Environment version: `<commit or hash of the simulator>`. Results compare only within one version;
  a change to the simulator is an environment change: S1-S8 are proven again on the controls.
- Output per experiment: `results/<id>/metrics.tsv` (one row of totals), `results/<id>/scores.tsv`
  (one row per case: `period` and the score column fixed by `lab.py settings --column`),
  `results/<id>/trace.tsv` (one row per decision)
- Limits: `<cores, memory, hours>`

| # | Requirement | Status | Evidence |
|---|---|---|---|
| S1 | Point in time: every decision uses only what the real system would know at that moment; nothing from later in the case, and nothing from the verification cases (`lab.py truncate`: the same decisions with the case cut at T and everything derived from it rebuilt) | | |
| S2 | Costs and limits of acting are explicit and conservative: what each decision pays and what the real system cannot do | | |
| S3 | Reproducible: the same command, data, commit and seed give the same files (`lab.py repro`) | | |
| S4 | Isolated: nothing real is driven (no live orders, no hardware, no network), source data read-only, limits respected | | |
| S5 | Controls hold: doing nothing (or the baseline) gives its known value, a known case gives its known numbers, a shuffled input gives no gain | | |
| S6 | Decision trace: every decision, doing nothing and the last step included, with ts, stage, inputs looked at (with inputs_ts), action, reason, outcome (`lab.py trace`) | | |
| S7 | Units and conventions written down: units, time base, signs, what is measured where | | |
| S8 | Fidelity: where the real system has been measured, the simulation reproduces those measurements within a stated error, over the region the candidates will use; where it has not, say so | | |

How the requirements read in a few domains:

| # | Trading | A business process (pricing, inventory, scheduling) | A machine or a trained model |
|---|---|---|---|
| S1 | features from data up to the decision's time; rebuild them from data cut at T | demand, prices and stock known at that moment; no later orders inside a forecast; cut the history at T and rebuild | commands from sensor values up to t, no preview; no validation or test case in training or in feature statistics |
| S2 | fees, spread, impact, partial fills; a signal from a close does not trade at that close | lead times, capacity, minimum orders, the cost of a stock-out and of holding stock, the delay before a change acts | actuator ranges and sensor lag; inference cost, latency, the cost of each kind of error |
| S5 | a flat book gives 0; a shuffled signal earns nothing before costs | the current policy reproduces its recorded outcome; a random policy does not beat it | the baseline gives its measured numbers; shuffled labels give chance |
| S8 | the backtest reproduces the realised fills and costs of a live or paper period | a replay of a past period under the current policy matches its recorded costs and service level | the model matches bench measurements; the offline metric tracks the online one |

## Verification environment
- Holdout: `<held-out dates, entities, scenarios or operating conditions; or the real system>`, sha256
  `<...>`, protected with `lab.py holdout protect` (files, or the model of the real system). Never
  used for training, selection, tuning or plots. Size it so the pass criterion can be met by a real
  effect.
- Runs it: `rl-hypothesis-reviewer`, mode `verify`, fresh context, on a frozen candidate.
- Look budget: `<1>` for the whole research, fixed by the first `lab.py holdout open`; every look is
  a ledger entry, and any failure spends the holdout.
- Pass criterion: the candidate's own criterion, registered before its first run.

| # | Requirement | Status | Evidence |
|---|---|---|---|
| V1 | Holdout is apart from all data and cases the loop has seen, by time, entity, scenario or operating condition, with a gap where they overlap | | |
| V2 | The candidate is frozen before the look: its `lab.py run` recorded code, config and data hashes | | |
| V3 | Independent: the reviewer runs it, the controller does not; nothing in the candidate changes after a look | | |
| V4 | The same environment as simulation (S1-S8 hold), only the cases differ; or, when verification is the real system, every difference from the simulation is written down | | |
| V5 | Looks are counted in the ledger; over the budget, or after a failure, the result is SPOILED, not PASS | | |
