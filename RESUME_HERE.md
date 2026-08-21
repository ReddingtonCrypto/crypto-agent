# RESUME HERE — last updated 20 August 2026

Deeper detail: `research/crt-manual/_LABELS.md` (local only, gitignored).

---

## DO THIS FIRST — MONDAY 24 AUGUST, 00:00 UTC

The weekly bar closes and the sweep feed fires its one burst of the week.

```
cd research/harness && python weekly_capture.py
```

**Rate each setup BEFORE the week trades.** This is the whole point: all 81
verdicts in the journal were given with the outcome already visible on the
chart, which is exactly why none of them can prove an edge. A Monday-morning
verdict can. Expect a handful — 3 across all 150 symbols at the 17 Aug close.

Weekly silence between Mondays is CORRECT: a weekly bar closes once a week and
`create_pending` dedupes on `signal_ts`.

---

## STATE

Live at `4e84f3d`, scanning every 2 minutes, paper and alerts-only. Local git
and the VM byte-identical on every live file. Live record: **CRT 1.0 5W / 20L**
— far too few trades to read.

---

## THE FOUR RESULTS THAT ANCHOR EVERYTHING

**1. The implementation is NOT the gap.** Replaying the live losing week through
the harness — same coins, same window, the same `detect_crt_10` the agent calls
— gives −0.806%/trade against live −0.561%. The harness loses too.

**2. The edge is not decaying.** Ten quarters of 1d→1h, 927 trades, every
quarter positive, trend slope +0.0200%/trade per quarter. The two worst quarters
are the two oldest.

**3. FILTERS FAIL, ADDITIVE RULES WORK.** Eight filters have now failed — trend,
sweep depth, key-level proximity, C2 delivery, double sweep, C1 body, Rule A,
sweep-wick stop. Three additive changes have worked — FVGs beside the level,
FVGs C1 creates, and seeing recent swings. **CIA is better at being told what to
look for than what to ignore.**

**4. The bottleneck is sample size.** At t≈1 per quarter, some of the nine
closed ideas may have been fine. More rules will not help. More trades will.

---

## WHERE THE DETECTOR NOW STANDS (daily 2023, same window as Batch 2)

| | before today | now |
|---|---|---|
| entries | 81 | 45 |
| fill rate | 64% | 64% |
| retest already gone before the alert | 30 | **0** |
| order block / equal-highs-lows as the key level | many | **zero** |

Daily P&L is the strongest this project has recorded: **+0.692%/trade, t=6.34**,
both walk-forward halves strong, on 1,231 trades.

---

## THE ONE OPEN LEAD

**The LTF entry gate is the largest untested filter left.** Only 8% of HTF
setups now produce an entry (45 of 573) and `stale / stop breached` blocks 398
of them. Every encoded filter has failed; this one has never been measured.

---

## OPEN QUESTIONS FOR THE USER

* Does a tap producing **no reaction** consume an FVG, or only a tap price
  reacts to? Their marking says one thing, their written rule the other; the
  code follows the written rule. This is the last thing standing between us and
  the BNB 2023-06-14 setup.
* **4h is 79% of live trades**; weekly is strongest on paper and essentially
  unmeasurable. Reweight the feed?

---

## TRAPS THAT WILL BITE A FRESH SESSION

* **`research/` is gitignored and local-only.** Journal DB, price pickles,
  harness and `_LABELS.md` exist ONLY on the laptop. A cloud session has none of
  it — no backtests, no journal checks, no chart review.
* **No git on the VM.** Deploy by scp + restart, md5-verify, and **ask first**.
* **CRLF makes local↔VM md5 comparisons lie.** Strip `\r` before concluding
  anything differs.
* **A test arm returning EXACTLY the baseline is a failed patch, not a null.**
* **A filter that raises the agreement rate by DELETING labelled setups is a
  regression.** `journal.py check` prints coverage first and names lost TAKEs.
* **Re-measure before re-rejecting.** `kl_swing_lookback_1` was rejected days
  ago and is now live and the best change of the week — the baseline had moved.
  The journal records the commit each measurement was taken at for this reason.
* **Watch for survivorship.** A measurement said Rule-A setups hit target 76% vs
  40%, cleanly, on both pairings — and was meaningless, because the loop broke
  on a stop-hit before it could check the condition.
