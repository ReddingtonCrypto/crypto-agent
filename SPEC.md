# Crypto Agent — Specification

> Living document. Edit freely. This defines WHAT the system does before we build HOW.
> Status legend: ✅ done · ⚠️ partial · ❌ not started

---

## 0. Guiding rules
- **Free first.** Every component must have a $0 path. Pay only once the system earns.
- **Read-only market access.** The bot NEVER places orders or moves money. Alerts only.
- **Phone-first.** All control and output must work from a phone (Telegram now, PWA later).
- **Secure by default.** Secrets live in `.env` / host secrets, never in code or git.

---

## 1. Data sources
| Source | Use | Status | Notes |
|---|---|---|---|
| **OKX (spot)** | Candles (OHLCV) for all coins | ⚠️ partial | **Binance blocks our location (HTTP 451) — OKX is the real source.** Same `BTC/USDT` symbol format via `ccxt`. |
| Kraken / MEXC / Gate | Backup data sources | ❌ | Fallback if OKX fails. All confirmed reachable from our location. |
| On-chain providers | Whale/exchange/stablecoin flows | ❌ | Stage 5. Free tiers: e.g. public APIs, to be chosen later. |
| News / sentiment | AI narrative input | ❌ | Stage 5/6. |

**Coin universe:** start = 19 coins (TAO dropped, not on OKX). Target = top ~100 by volume (auto-pulled from OKX markets).

**Timeframes:** now = 1h only. Target = multi-timeframe (15m, 1h, 4h, 1d) with higher TF setting the trend bias.

---

## 2. Strategies
Each strategy is a module that takes candles + features and returns: `{direction, confidence, reason}`.

| Strategy | Status | Definition |
|---|---|---|
| Trend | ⚠️ partial | EMA20 vs EMA50 cross + price alignment. Works today in `agent.py`. |
| Range | ❌ | Mean-reversion inside a range (buy support / sell resistance, RSI extremes). |
| **SMC layer** | ❌ | See section 6. |

---

## 3. Regime detection
- Now: ⚠️ categorical — `TREND_BULL / TREND_BEAR / RANGE / WEAK_TREND` (`regime_engine.py`).
- Target: **Market State Score** (0–100 each): Trend strength, Volatility, Liquidity → overall Risk = Low/Med/High.

---

## 4. Features (indicators)
- Now: ⚠️ EMA20, EMA50, RSI(14), ATR(14) — computed inline.
- Target: a dedicated **feature engine** module reused by every strategy and the backtester.

---

## 5. Risk rules
| Rule | Status | Definition |
|---|---|---|
| Stop / targets | ⚠️ partial | Stop = 2×ATR, TP1 = 3×ATR, TP2 = 5×ATR (`risk_engine.py`). |
| Default risk | ❌ | 2% of (paper) account per trade. |
| Position sizing | ❌ | size = (account × 2%) ÷ (entry − stop distance). |
| Invalidation | ❌ | A signal is dead if price closes beyond its stop / structure level. |

---

## 6. SMC layer (Stage 2–4) — objective definitions
These must be *measurable*, not vibes. Working definitions:

**Market structure (Stage 2):**
- **Swing high** = a candle high with N lower highs on each side. **Swing low** = mirror.
- **HH/HL** = uptrend, **LH/LL** = downtrend.
- **BOS (Break of Structure)** = price closes beyond the last swing high (bullish) / low (bearish) → *trend continuation*.
- **CHoCH (Change of Character)** = first break *against* the prevailing structure → *possible reversal*.
- **MSS (Market Structure Shift)** = confirmed CHoCH followed by a new opposing swing.

**Liquidity (Stage 3):**
- **Equal highs/lows** = ≥2 swings within X ticks → resting liquidity.
- **Liquidity sweep / stop hunt** = wick pierces an equal high/low then closes back inside.
- **Session high/low sweep** = sweep of prior session's extreme.

**Institutional (Stage 4):**
- **Order Block** = last opposite candle before a strong impulsive move (BOS).
- **Fair Value Gap (FVG)** = 3-candle gap where candle1.high < candle3.low (bullish) or reverse.
- **Breaker / Mitigation block** = failed OB that price returns to.

---

## 7. Database (SQLite now → Postgres later)
| Table | Status | Stores |
|---|---|---|
| `signals` | ✅ | coin, direction, entry, stop, tp1, tp2, score, time |
| `candles` | ❌ | cached OHLCV per coin/timeframe |
| `features` | ❌ | computed indicators per candle |
| `backtests` | ❌ | run results, win rate, expectancy, drawdown |
| `paper_trades` | ❌ | simulated fills + P&L |
| `ai_notes` | ❌ | AI explanations / narratives |
| `strategy_health` | ❌ | per-strategy last-20/50 win rate, status |

---

## 8. Alerts
- Now: ✅ Telegram one-way alerts.
- Target: **interactive Telegram bot** — `/scan`, `/status`, `/top`, `/pause BTC`, `/health` — so the whole thing is controllable from the phone.
- Later: PWA cards (installable web app, free hosting).
- Every alert carries a **confidence score** + plain-English reason.

---

## 9. AI responsibilities (Stage 6)
**AI MAY:** explain signals, rank opportunities, suggest parameter tweaks (for backtest, not live), summarize market, review performance.
**AI MUST NOT:** place trades, move money, change risk rules on its own, or act without a logged, reversible record.

---

## 10. Health & ranking (the "missing" pieces)
- **Strategy Health Monitor** ❌ — tracks last 20/50 trades; auto-reduces weight / disables a failing strategy (e.g. expected 58% vs current 32% → DISABLED).
- **Opportunity Ranking Engine** ⚠️ — `ranking.py` exists but standalone; target = one ranked list across the whole universe (e.g. `1. SOL 92, 2. ETH 88 …`).

---

## 11. Deployment (free, 24/7, phone) — DECIDED: GitHub Actions (no card)
- **Host:** GitHub Actions scheduled workflow (`.github/workflows/scan.yml`) — 100% free, no credit card. Runs `scan_once.py` every ~15 min on GitHub's servers.
- **Why not a loop:** Actions runs one scan per invocation on a fresh machine; `agent.py`'s forever-loop is guarded under `if __name__ == "__main__"` so importing it does not loop. `scan_once.py` calls `run_agent()` once.
- **State:** signal-history DB persisted between runs via `actions/cache` (so duplicates stay blocked across runs).
- **Secrets:** `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` in GitHub repo Secrets (encrypted), never in code. `.env` stays local (gitignored). Repo is public for unlimited free minutes — safe because no secret is in the code.
- **Control/output:** Telegram from the phone.
- **Backup option (needs card):** Oracle Cloud Always Free VM — see `DEPLOY_ORACLE.md`. Better for a true always-on loop / heavier work later.
- **Setup guide:** `DEPLOY_GITHUB.md`.

---

## 12. Version roadmap
- **V1.0 (MVP, current focus):** OKX data · 19→100 coins · 1h→multi-TF · trend + range · regime · Telegram · backtest · paper trade · AI explanations. *(Today: ~40% of V1.0.)*
- **V1.1:** MSS, BOS, CHoCH
- **V1.2:** liquidity sweeps, equal highs/lows, stop hunts
- **V1.3:** order blocks, FVG, breaker blocks
- **V2.0:** open interest, funding, liquidation maps, on-chain, whale tracking, AI optimization
- **Never (until funded & explicitly chosen):** live trading / order placement.

---

## 13. Tech stack (decided)
- **Language:** Python
- **Data:** ccxt (OKX)
- **DB:** SQLite now → PostgreSQL (free tier) at V2
- **Host:** Oracle Cloud Always Free VM
- **Phone UI:** Telegram bot now → PWA (free static host) later
- **AI:** Claude API (cloud) for explanations; local model fallback considered later
