# Expected behaviour: <experiment id>

Written before the run; never edited after it. The reviewer compares every row with the trace.
Hypothesis: <H id, one line>. Pass criterion: <copied from EXPERIMENTS.tsv>.

| Stage | Should look at | Should do | Shows in (trace field or metric) | Expected |
|---|---|---|---|---|
| <stage> | <inputs, e.g. order-book imbalance over the last 5 s> | <e.g. go long above 0.3> | <stage=<stage>, inputs, action> | <e.g. 40-60 signals a day> |
| | | | | |

One row per stage the system goes through, in order. For example:
- a trading system: signal -> decision -> order -> fill -> position -> exit -> result;
- a business process: demand forecast -> decision (order, price, schedule) -> execution -> outcome;
- a model or a controller: data or sensing -> features -> prediction or command -> outcome.
A stage shown only in a metric (an outcome over the whole case) says so in "Shows in", and the trace
check does not look for it.

## Must not happen
- <e.g. a feature from the future; an order above the limit; a decision using demand not yet known>

## Neighbours to watch
Parts not under test that this change may still move: <other operating regions, cases, segments,
limits, latency>.
