# Research program: <goal in one line>

## Goal (since <date>)
<What counts as success, in the user's terms.>
Deliverable: <e.g. a config in the standard pipeline plus predictions in the simulator format>.

## Hard rules from the user
- "<verbatim quote>" (<date>) -> <what it means for the loop>

## Out of scope
- <rejected directions, with a pointer to KNOWLEDGE.md "Rejected">
- Other people's runs and folders are never touched.
- Thresholds and parameters are never tuned on the holdout; it is looked at only as ENVIRONMENT.md says.

## Pipeline
<The project's standard tooling: simulator or backtester, dataset builder, features, model step,
export format. Framework functions to reuse, with paths.>

## Metrics (in order of importance)
- <primary objective, net of what acting costs (money, stock, error, time)>
- <stability: share of cases that improve, per window or scenario>
- <tail: worst case (day, trade, test cycle, segment); limits never crossed>
- <diagnostics: R2, corr; never the goal>

## Data splits (fixed before round 1)
- Instruments / datasets: <...>
- Train: <dates or ids>. Validation (selection, early stopping, thresholds): <dates or ids>.
  Holdout: in ENVIRONMENT.md, never here in numbers.
- Feature or alpha selection period: <dates> (in-sample by selection; outside validation and holdout).
- Evaluation windows inside validation: <e.g. two-week windows W_A, W_B, ...>
- Costs and limits: <fees, spreads, latency; actuator and safety limits; compute>

## Environments
Simulation and verification: ENVIRONMENT.md (requirements S1-S7, V1-V5 with evidence).

## Resource limits
<cores, memory, disk, hours per experiment>

## Project review checklist
<Domain checks the reviewer must run on top of its protocol: leakage traps specific to this
data or simulator, conventions (fill side, sensor placement), units, known quirks.>

## Rounds
One file per round in `KB/rounds/R<NN>.md` (plan, critique, outcome); this program stays short.
