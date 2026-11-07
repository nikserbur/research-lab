# Second end-to-end run and its evaluation (2026-11-07..08)

The second run used `/research` by the skill, with the subagents, on a problem built so the answer
is known: daily prints of 60 stocks over 2012-2021 from a generator kept outside the project, with
500 later days as the holdout. An independent evaluator then compared the run with the generator.
The research folder and the evaluator's scripts stay in the session's scratch space; this page
keeps what they showed and what changed because of it.

## The world

- A real momentum effect: 0.015 * sigma * z of the sector-relative 120-day sum of shocks.
- A short-term reversal of non-news moves that lasts one session (realised open to close the next
  day), larger for illiquid stocks and doubled in a stress regime (16% of the simulation, 33% of
  the holdout).
- Traps: `trend5` in the project's feature pipeline averages two later days; closing and opening
  prints carry bid-ask bounce; the backtester's defaults (fill at the close, 1 bp flat) are a
  vendor's quick backtest.

## What the loop did

| Step | Result |
|---|---|
| Recon | `trend5` rank IC +0.371 flagged as implausible |
| S1 proof | the probe control decided differently with the prints cut at 2018-06-29 and the features rebuilt; leaving out one feature at a time isolated `trend5` |
| Sources | live institutional costs about 7 times the backtester's default impact; reversal mostly does not survive costs; residual momentum, bands, partial trading |
| Environment | next-open fills, 1.5 x half spread + 75 bp * sqrt(participation); proven again on the controls after each change |
| Round 1 | critique cut two hypotheses at no trial; momentum E2 (net 3.00 bps/day, Sharpe 1.28) and E5 with a band (3.53, 1.52) passed; a slow reversal book lost before costs |
| Round 2 | critique cut two more; E6, momentum whose swaps wait a day when they go with the day's move, beat E2 by +0.27 bps/day; the reviewer showed the gain is the one-session reversal |
| Stop | the cost gate written before E6 ran (twice the costs, 3%/yr borrow) failed; no holdout look |

The holdout file stayed locked (mode 000) and intact throughout; no agent read it.

## What the evaluation found

Correct against the truth: the look-ahead, momentum as within-sector selection over six months, the
reversal's one-session shape and its liquidity and regime dependence. Wrong: E5 is truly better than
E6 (+0.47 bps/day, se 0.16), the "momentum break in 2020" is noise (-3.2 se), "E5's gain is one
episode" came from a one-sided post-hoc check. On the holdout regenerated from the truth E6 would
have passed (Sharpe 2.15), E5 even more clearly (2.79): stopping was a false negative.

Causes in the protocol, and what changed:

| Finding | Change |
|---|---|
| The deflated Sharpe's noise benchmark used the spread of all trials' Sharpe ratios: one cost-sunk trial would have made the holdout unreachable (found by the critique before compute) | the variance of a Sharpe estimate under the null, 1/n (0.11.1+) |
| The deflated Sharpe was frozen when a run ended | `best`, `holdout open`, `stats` and `status` deflate again with the trials so far |
| BEST had to beat the current best in a paired test: near-copies of the incumbent win, a different and better candidate is locked out | `lab.py best` selects once over every eligible candidate: a model confidence set of their scores, then a metric declared before the first run (`--select-by`) |
| About 60 configurations were judged outside `lab.py run`; the ledger counted 4 trials | `lab.py explore --n N --what` adds them to the trial count; skill and reviewer record them |
| The decisive cost gate lived in a round file, with flags mistranslated from the critique | `settings --scenarios stress`: every criterion needs a clause on the simulator's `*_stress` metrics, judged by `lab.py judge` and at the holdout |
| The stop had no ledger entry | `lab.py close --reason`, with `--without-look` required when the BEST was never looked at |
| The protocol code changed four times during the research | the first protocol command fixes the hash of `lab.py` and `labkit`; another is taken only with `lab.py upgrade --reason` |
| Post-hoc subperiod checks were one-sided and became decisions | the reviewer drops the best and the worst periods alike, compares with a placebo, and proposes gates only as registered scenarios |
| The holdout guard refused any command naming `data/raw/prices.csv`, since the holdout file had the same name | the guard matches paths, not bare file names |
| `trace` called an outcome stamped when it is known "time going back"; `truncate` compared fills that need the next price | time order per stage; `truncate --stages` |
| `verify` split wrapped sentences into fragments and let a brief's copy in the KB support itself (0.81 against 0.39) | paragraphs are joined first; the verified text is never its own evidence |

Not done, and why: combinatorial purged cross-validation and the probability of backtest overfitting
(they need a family of runs over the same splits, a larger change to how runs are defined); e-LOND in
place of LORD++ (it needs e-values from every test); a power report at registration (it needs a model
of each metric's distribution).

Sources the evaluation leaned on: Bailey and Lopez de Prado (2014) on the deflated Sharpe; Hansen,
Lunde and Nason (2011) on the model confidence set; Hansen (2005) and Romano and Wolf (2005) on
joint selection; Arnott, Harvey and Markowitz (2019) on recording every trial; Ramdas et al. (2017)
and Xu and Ramdas (2024) on online FDR under dependence.
