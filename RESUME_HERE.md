# RESUME HERE — 19 August 2026

Written at the end of the session so a cold start needs no archaeology.
Deeper detail: `research/crt-manual/_LABELS.md` (local only, not in git).

---

## STATE: everything is deployed and running

The agent is on the Oracle VM, **scanning every 2 minutes**, paper and
alerts-only. Local git and the VM are byte-identical on every live file.
Nothing is half-shipped.

Live at commit `b30173a`. Today's five changes, all measured before shipping:

| change | effect |
|---|---|
| LTF level gap is per-timeframe (3.0x median LTF candle) | weekly entries +53%, daily +16%, 4h −16% |
| An FVG *beside* the swept level qualifies a setup | fixes the miss the user found by hand |
| Minimum FVG size (0.03x median range) | kills 0.05%-of-price "gaps" |
| Only old high/low, FVG, rejection block qualify | costs 5 setups the user said take — deliberate |
| CISD search starts at C2's CLOSE, not its open | stale alerts 75% → 6%; retest-already-gone 30 → 0 |

---

## THE THREE RESULTS THAT MATTER MOST

**1. The implementation is NOT the gap.** Replaying the live losing week
through the harness — same coins, same window, the same `detect_crt_10` the
agent calls — gives **−0.806%/trade (2W/5L)** against the live **−0.561%
(5W/19L)**. The harness loses too. The agent does what the backtest tests.

**2. The edge is not decaying.** Ten quarters of 1d→1h, 927 trades, **every
quarter positive**, trend slope **+0.0200%/trade per quarter**, r = +0.137. The
two worst quarters are the two oldest. The soft last-90-days figure (+0.378,
t=1.11) is one quarter in a distribution running +0.036 to +1.343.

**3. The bottleneck is sample size, not rule quality.** At t≈1 per quarter,
nine ideas have been closed as measured failures and some may have been fine —
we cannot tell. **More rules will not help. More trades will.**

---

## WHAT TO DO NEXT, IN ORDER

1. **Judge the Telegram alerts as they arrive.** This is the highest-value
   activity in the project. All 81 recorded verdicts were rated with the
   outcome already visible; live approve/decline is the only PROSPECTIVE test
   of the user's selection, and their selection is the only thing that has ever
   separated outcomes (TAKE 67% full target vs SKIP 15%).

2. **Re-run the double-sweep count** — `research/harness/t43_double_sweep.py`.
   It was running when the laptop closed. The user has named the double sweep
   unprompted five times and nothing in the code models it. Worse,
   `crt10_entry` treats a second sweep as a FAILURE: `out = l[j] < wick ...
   break` abandons the candidate exactly where they say the entry is. Count
   first, decide after — their instruction.

3. **Run `journal.py check` around every change.** Baseline **44/72**,
   coverage 72/81. It has already caught two pieces of self-deception.

---

## OPEN, MEASURED, NOT BUILT

* **Wick targets instead of body** — 91% (weekly) and 84% (daily) of setups
  that reach C1's body carry on to the full wick. Three independent samples
  agree. Never tested as an actual target change.
* **`RANGE_TOL`** still labels a 30% crash a "range".
* **FVGs that C1 CREATES** (we only find gaps C1 taps).
* **C1 body size** — rejected by the user five times; we gate on C1's range and
  never its body, and the targets come from the body.
* **Re-check invalidation after C2** — a CRT that dies the next day still reads
  as live.

## OPEN QUESTIONS FOR THE USER

* Does a tap that produces **no reaction** consume an FVG, or only a tap price
  reacts to? Their marking says one thing, their written rule the other.
  `MENTOR_ONLY` currently follows the written rule.
* **4h is 79% of live trades and the weakest pairing** (+0.259%/tr vs daily
  +0.592). Weekly is strongest and has produced no closed trades at all. Should
  the feed be reweighted?

---

## THINGS THAT WILL BITE A FRESH SESSION

* **`research/` is gitignored and local-only.** The journal database, the price
  pickles, the harness and `_LABELS.md` exist ONLY on the laptop. A cloud
  session sees none of it — no backtests, no journal checks, no chart review.
* **There is no git on the VM.** Deploy by scp + restart, always md5-verify,
  and **ask first** — the user has repeatedly wanted fixes held local during a
  review.
* **Windows line endings** make local↔VM md5 comparisons disagree on files
  nobody has touched. Strip `\r` before concluding anything differs.
* **A filter arm that returns EXACTLY the baseline is a bug, not a null.** It
  has happened once — a patch failed to apply silently and three test arms
  measured nothing.
