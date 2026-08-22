# RESUME HERE — last updated 23 August 2026

Deeper detail: `research/crt-manual/_LABELS.md` (local only, gitignored).

---

## DO THIS FIRST — MONDAY 24 AUGUST, 00:00 UTC

```
cd research/harness && python weekly_capture.py
```

**Rate each setup BEFORE the week trades.** All 81 verdicts in the journal were
given with the outcome already visible on the chart, which is exactly why none
of them can prove an edge. A Monday-morning verdict can. Expect a handful — 3
across all 150 symbols at the 17 Aug close.

Weekly silence between Mondays is CORRECT: a weekly bar closes once a week and
`create_pending` dedupes on `signal_ts`.

---

## THE RESEARCH QUEUE IS EMPTY

`journal.py brief` shows **no untested rules left**. Everything the corpus and
the user's own verdicts suggested has been encoded and measured. What remains is
not another rule — it is data. Specifically, PROSPECTIVE verdicts.

---

## STATE

Live at `4e84f3d`, scanning every 2 minutes, paper and alerts-only. Local git and
the VM byte-identical on every live file. Live record: **CRT 1.0 5W / 20L** —
far too few trades to read.

Daily backtest: **+0.692%/trade, t=6.34**, both walk-forward halves strong, on
1,231 trades. Strongest result the project has produced.

---

## THE FIVE RESULTS THAT ANCHOR EVERYTHING

**1. The implementation is NOT the gap.** Replaying the live losing week through
the harness — same coins, same window, the same `detect_crt_10` the agent calls
— gives −0.806%/trade against live −0.561%. The harness loses too.

**2. The edge is not decaying.** Ten quarters of 1d→1h, 927 trades, every
quarter positive, trend slope +0.0200%/trade per quarter. The two worst quarters
are the two oldest.

**3. FILTERS FAIL, ADDITIVE RULES WORK.** Eight filters failed — trend, sweep
depth, key-level proximity, C2 delivery, double sweep, C1 body, Rule A,
sweep-wick stop. Three additive changes worked — FVGs beside the level, FVGs C1
creates, seeing recent swings. **CIA is better at being told what to look for
than what to ignore.**

**4. The LTF entry gate EARNS ITS KEEP** — the one exception to (3). It converts
a worse subset (14.5% full target vs 38.0%), but that is mechanical, and the
setups it rejects cannot be traded at C2's close: −0.190%/tr t=−4.55 on daily
**with a 54% win rate**. Its 92% rejection rate is the price of admission.

**5. The bottleneck is sample size.** At t≈1 per quarter, some of the nine closed
ideas may have been fine. More rules will not help. More trades will.

---

## OPEN QUESTIONS FOR THE USER

* Does a tap producing **no reaction** consume an FVG, or only a tap price
  reacts to? Their marking says one thing, their written rule the other; the
  code follows the written rule. It is the last thing between us and the BNB
  2023-06-14 setup.
* **4h is 79% of live trades**; weekly is strongest on paper and essentially
  unmeasurable (one half-year in the whole history clears 20 trades). Reweight?

---

## TRAPS THAT WILL BITE A FRESH SESSION

* **`research/` is gitignored and local-only.** Journal DB, price pickles,
  harness and `_LABELS.md` exist ONLY on the laptop. A cloud session has none of
  it — no backtests, no journal checks, no chart review.
* **No git on the VM.** Deploy by scp + restart, md5-verify, and **ask first**.
* **CRLF makes local↔VM md5 comparisons lie.** Strip `\r` before concluding.
* **A test arm returning EXACTLY another arm is a failed patch, not a null.**
* **A filter that raises the agreement rate by DELETING labelled setups is a
  regression.** `journal.py check` prints coverage first and names lost TAKEs.
* **Re-measure before re-rejecting.** `kl_swing_lookback_1` was rejected once
  and is now live and the best change of the week — the baseline had moved.
* **Watch holding windows across timeframes.** A test reported the C2-close
  entry at +1.463%/tr on t=+29 because it held 400 HTF bars — over a year —
  where the same constant means 400 LTF bars, about two weeks, in pit_backtest.
* **Watch for survivorship.** Another said Rule-A setups hit target 76% vs 40%,
  cleanly, on both pairings — meaningless, because the loop broke on a stop-hit
  before it could check the condition.
* **`journal.py brief` statuses drift.** They have gone stale twice. Update the
  rule when the test finishes, not later.
