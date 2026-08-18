# crypto-agent — standing instructions

## THE ONE HABIT THAT MATTERS: run the journal around every change

```bash
cd research/journal && python journal.py check
```

**Before touching detector or strategy code, run it. After the change, run it
again. Do not ship anything that REGRESSES the number.**

`check` replays every verdict the user has ever given on a real setup against
the current code and reports IMPROVED / REGRESSED against the last run. The
baseline at commit `5103fec` is **22/36 (61%)**.

Also worth running:

| | |
|---|---|
| `journal.py brief` | what is already tested and FAILED — read this before proposing a test |
| `journal.py drift` | rules whose evidence was measured at an older commit |
| `journal.py outcomes` | how the labelled setups actually resolved, by the user's reasons |
| `journal.py sync` | pull the live paper-trade results off the VM |

After any chart-review session, add the new verdicts to `research/journal/seed.py`.
**Only use tags the user has actually said** — inventing a measure and
reporting it back as their rule has already happened once.

## Mistakes this project keeps repeating — check for them by name

1. **PER-TIMEFRAME CONSTANTS.** Made four times: the CISD window, the trigger
   age, the old-high/low proximity, and `crt10_entry(max_level_gap=0.02)` which
   is still wrong on weekly (42% of blocked weekly entries). **Any bar count or
   percentage calibrated on one timeframe is wrong on the others.** Prefer a
   multiple of the median candle range over a fixed percentage.
2. **ASSUMING A RESULT GENERALISES.** Rule B was the best daily result the
   project ever produced and fails on 1w and 4h. Measure on every pairing.
3. **LOOK-AHEAD AND UNFILLED LIMITS.** Both made in one week. A limit price
   never touched is not a trade. See the mandatory checks below.
4. **INVENTING A MEASURE AND ATTRIBUTING IT TO THE USER.** The "% of the C1
   range" rule was mine, not theirs; their rule is `c2_delivered` (C2's own
   wick already reached TP1), which matches their objection 17/19 on weekly.
5. **BACKTESTS DO NOT FIND DETECTOR DEFECTS.** 36 of them found zero. Three
   chart-review sessions with the user found twelve. **The method that works:
   list every detector setup in a window, ask take/skip WITH A REASON, verify
   each reason against the OHLC, fix what turns out to be ours.**

## Mandatory checks before any strategy result is reportable

base rate · returns not win rate · buy-and-hold over the SAME bars and holding
period · walk-forward both halves · |t| > 2 · fees both sides · same-bar tie =
loss · strict point-in-time · **model the FILL** · breadth across assets ·
outlier and top-decile concentration.

## Deploying

There is **no git on the VM**. Deploy by scp + restart, then verify:

```bash
scp -i D:/oracle-keys/crypto_agent_vm.key <file> ubuntu@140.245.121.118:/home/ubuntu/crypto-agent/<path>
ssh -i D:/oracle-keys/crypto_agent_vm.key ubuntu@140.245.121.118 "sudo systemctl restart crypto-agent"
```

Always md5-verify local↔VM after the copy, and check the log for a clean scan.
Everything is PAPER and alerts-only. **Ask before deploying** — the user has
several times wanted fixes held local while a review is in progress.

## Where things live

| | |
|---|---|
| `research/crt-manual/_LABELS.md` | every review session, every verdict, every finding |
| `research/crt-manual/_RULEBOOK.md` | the 29 rules with source citations |
| `research/journal/` | the labels/rules/outcomes database and its tools |
| `research/harness/review.py` | chart review: any coin, any date range, any pairing |
| `research/harness/batch_table.py` | renders a review batch as the two rating tables |
| `research/harness/pit_backtest.py` | the honest point-in-time backtest |

`research/` is gitignored — it is local-only by design.
