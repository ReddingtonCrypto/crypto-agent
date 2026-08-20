# RESUME HERE — last updated 20 August 2026

Deeper detail lives in `research/crt-manual/_LABELS.md` (local only, gitignored).

---

## STATE

Agent live on the Oracle VM at commit `b30173a`, **scanning every 2 minutes**,
paper and alerts-only. Local git and the VM are byte-identical on every live
file. Healthy: 351 scans in the last 22 hours, zero errors.

**Live record: CRT 1.0 = 5W / 20L.** Still far too few trades to read.

---

## THE THREE RESULTS THAT ANCHOR EVERYTHING

**1. The implementation is NOT the gap.** Replaying the live losing week through
the harness — same coins, same window, the same `detect_crt_10` the agent calls
— gives **−0.806%/trade (2W/5L)** against live **−0.561% (5W/19L)**. The harness
loses too. The agent does what the backtest tests.

**2. The edge is not decaying.** Ten quarters of 1d→1h, 927 trades, **every
quarter positive**, trend slope **+0.0200%/trade per quarter**, r = +0.137. The
two worst quarters are the two oldest.

**3. The bottleneck is sample size, not rule quality.** At t≈1 per quarter, nine
ideas are closed as measured failures and some may have been fine. **More rules
will not help. More trades will.**

---

## DO THIS FIRST

**Monday 24 August, 00:00 UTC** — the weekly bar closes and the sweep feed fires
its one burst of the week. Run:

```
cd research/harness && python weekly_capture.py
```

Rate each setup **before the week trades**. All 81 verdicts in the journal were
given with the outcome already on the chart; a Monday-morning verdict is the
only kind that can settle whether the user's selection is real. Expect a
handful — at the 17 Aug close there were 3 across all 150 symbols.

**Weekly silence between Mondays is CORRECT, not a bug.** A weekly bar closes
once a week and `create_pending` dedupes on `signal_ts`.

---

## JOBS THAT MAY HAVE DIED WITH THE SESSION

* `t45_double_tight.py` → `t45.log` — the double sweep with the window taken
  from a histogram, plus the SL arm. **Re-run it.**
* `t44_targets.py` → `t44.log` — body vs wick targets. First arms only:
  daily BODY +0.592%/tr t=+5.20 edge +0.567 · WICK +0.547 t=+4.84 edge +0.581.
  A wash so far. FAR arm and the 1w/4h pairings unfinished.

---

## THE OPEN LIST (`journal.py brief` is authoritative)

1. **`wick_targets`** — 91% weekly / 84% daily / 83% of the 2023 batch of
   setups that reach C1's BODY carry on to its WICK. Three independent samples.
   Being tested now; first arms say wash.
2. **`double_sweep`** — named unprompted 5x. `crt10_entry` ABANDONS the setup
   at exactly that bar (`out = l[j] < wick ... break`). The loose encoding does
   not replicate (daily t=−5.47, 4h flat, weekly +1.023) because 70% of setups
   qualified. Test 45 tightens it.
3. **`small_c1_body`** — rejected for it 5x. We gate on C1's RANGE, never its
   BODY, and the targets come FROM the body.
4. **`range_needs_price`** — labelled a 30% crash a "range".
5. **`invalidation_after_c2`** — a CRT that dies the day after the alert still
   reads as live.
6. **`fvg_created_by_c1`** — we only find gaps C1 taps, never ones it forms.

---

## OPEN QUESTIONS FOR THE USER

* Does a tap producing **no reaction** consume an FVG, or only a tap price
  reacts to? Their marking says one, their written rule the other; the code
  follows the written rule.
* **4h is 79% of live trades and the weakest pairing** (+0.259%/tr vs daily
  +0.592). Weekly is strongest and essentially unmeasurable. Reweight?

---

## TRAPS THAT WILL BITE A FRESH SESSION

* **`research/` is gitignored and local-only.** The journal DB, price pickles,
  harness and `_LABELS.md` exist ONLY on the laptop. A cloud session has none of
  it — no backtests, no journal checks, no chart review.
* **No git on the VM.** Deploy by scp + restart, always md5-verify, and **ask
  first**.
* **CRLF makes local↔VM md5 comparisons lie.** Strip `\r` before concluding
  anything differs.
* **A test arm that returns EXACTLY the baseline is a failed patch, not a null.**
  Happened once; three arms measured nothing.
* **A filter that raises the agreement rate by DELETING labelled setups is a
  regression.** `journal.py check` now prints coverage first and names any lost
  setup the user said TAKE.
* **Absolute numbers from `t43`/`t45` are not comparable to the harness** — they
  skip the live gates deliberately. Only their internal contrasts are valid.
