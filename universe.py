"""Build the list of coins to scan: the top N by market cap (from CoinGecko,
free, no key) that are actually tradeable as USDT pairs on OKX.

CoinGecko gives the market-cap ranking; OKX gives the candles. TradingView is
not used because it has no free data feed for bots.
"""

import json
import os
import time
import urllib.request


CACHE_FILE = "data/universe.json"
CACHE_TTL = 24 * 3600  # refresh the list once a day

# Stablecoins / pegged tokens we don't want to scan as "/USDT".
STABLES = {
    "USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "BUSD",
    "PYUSD", "USDD", "GUSD", "FRAX", "USDS", "USD1",
}

# Blacklist: coins the per-coin backtest (binanceus, Variant C exits) showed
# lost CONSISTENTLY — avg < -0.3%/trade over a real sample (>=8 trades). These
# are excluded from the scan even if their market cap qualifies; the universe
# backfills from the next coins down. This is the robust use of the screen
# (dropping proven losers), not the overfit-prone cherry-picking of winners.
BLACKLIST = {
    "BNB", "UNI", "NEAR", "ATOM", "HBAR", "SHIB", "POL", "ENA", "HYPE", "SEI",
    "ALGO", "INJ", "MANA", "OP", "ETC", "QNT", "WLFI", "ONDO", "JUP", "APT",
    "ASTER",
}

# Used only if CoinGecko is unreachable and there's no cached list.
FALLBACK = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "BNB/USDT", "TRX/USDT", "LINK/USDT", "AVAX/USDT", "DOT/USDT",
    "ATOM/USDT", "LTC/USDT", "UNI/USDT", "AAVE/USDT", "SUI/USDT",
    "APT/USDT", "NEAR/USDT", "ARB/USDT", "OP/USDT",
]


def _read_symbols_cache():
    """Cached CoinGecko top symbols (exchange-independent), e.g. ['BTC','ETH']."""
    try:
        with open(CACHE_FILE) as f:
            data = json.load(f)
        if time.time() - data.get("saved_at", 0) < CACHE_TTL and data.get("symbols"):
            return data["symbols"]
    except Exception:
        pass
    return None


def _save_symbols_cache(symbols):
    try:
        os.makedirs("data", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"saved_at": time.time(), "symbols": symbols}, f)
    except Exception:
        pass


def _coingecko_top(limit):
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&order=market_cap_desc"
        f"&per_page={limit}&page=1"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-agent"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [c["symbol"].upper() for c in data]


def get_universe(exchange, limit=100):
    """Return ['BTC/USDT', ...]: the top `limit` coins by market cap that the
    given exchange supports as spot USDT pairs. The market-cap list is cached
    (exchange-independent); the intersection is recomputed for whichever
    exchange is passed in, so switching exchanges Just Works."""

    # CoinGecko's top-N by mcap is padded with wrapped/staked tokens and
    # stablecoins that aren't Binance spot USDT pairs, so the tradeable
    # intersection is much smaller than N. Over-fetch (up to 250) so we can
    # actually reach `limit` real pairs after filtering.
    raw_needed = min(250, max(limit * 3, 100))
    symbols = _read_symbols_cache()
    if symbols is None or len(symbols) < raw_needed:
        try:
            symbols = _coingecko_top(raw_needed)
            _save_symbols_cache(symbols)
        except Exception as e:
            print(f"CoinGecko unavailable ({type(e).__name__}); using fallback list.")
            if symbols is None:
                symbols = [p.split("/")[0] for p in FALLBACK]

    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"Could not load markets ({type(e).__name__}); using fallback list.")
        return FALLBACK

    pairs = []
    for sym in symbols:
        if sym in STABLES or sym in BLACKLIST:
            continue
        pair = f"{sym}/USDT"
        m = markets.get(pair)
        if m and m.get("spot") and m.get("active", True):
            pairs.append(pair)

    return (pairs or FALLBACK)[:limit]


def get_universe_ranked(exchange, limit=40, pool=150,
                        w_mcap=0.45, w_vol=0.40, w_heat=0.15):
    """LIVE universe: blend market-cap rank + Binance 24h volume + sector-heat
    (narrative) so we scan big, liquid, in-favour coins.

    NOTE: uses live 24h volume + current sector heat, so it CANNOT be backtested
    without look-ahead — the backtester keeps the plain mcap get_universe().
    Fails soft to market-cap order if tickers/heat are unavailable.
    """
    import sector_flow

    # 1) Candidate pool: top `pool` by market cap, tradeable on the exchange.
    raw_needed = min(250, max(pool, 100))
    symbols = _read_symbols_cache()
    if symbols is None or len(symbols) < raw_needed:
        try:
            symbols = _coingecko_top(raw_needed)
            _save_symbols_cache(symbols)
        except Exception as e:
            print(f"CoinGecko unavailable ({type(e).__name__}); mcap fallback.")
            if symbols is None:
                symbols = [p.split("/")[0] for p in FALLBACK]
    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"Could not load markets ({type(e).__name__}); fallback list.")
        return FALLBACK[:limit]

    candidates = []  # (symbol, pair, mcap_rank)
    for i, sym in enumerate(symbols[:pool]):
        if sym in STABLES or sym in BLACKLIST:
            continue
        pair = f"{sym}/USDT"
        m = markets.get(pair)
        if m and m.get("spot") and m.get("active", True):
            candidates.append((sym, pair, i))
    if not candidates:
        return FALLBACK[:limit]

    # 2) Binance 24h quote volume (liquidity) — public, no key needed.
    # Fetch ALL tickers (the vision host rejects a symbol-list filter) then index.
    try:
        tickers = exchange.fetch_tickers()
    except Exception as e:
        print(f"fetch_tickers failed ({type(e).__name__}); volume weight off.")
        tickers = {}
    vols = {p: float((tickers.get(p) or {}).get("quoteVolume") or 0.0)
            for _, p, _ in candidates}
    max_vol = max(vols.values()) or 1.0

    # 3) Sector heat (narrative) per coin.
    try:
        heat = {h["sector"]: h["avg_pct"] for h in sector_flow.sector_heat(exchange)}
    except Exception:
        heat = {}
    hvals = list(heat.values()) or [0.0]
    hmin, hspan = min(hvals), (max(hvals) - min(hvals)) or 1.0

    # 4) Blended score, highest first.
    scored = []
    for sym, pair, mrank in candidates:
        mcap_score = (pool - mrank) / pool                 # 1.0 = biggest cap
        vol_score = vols[pair] / max_vol                   # 0..1 by liquidity
        sec = sector_flow.sector_of(sym)
        heat_score = (heat.get(sec, hmin) - hmin) / hspan if heat else 0.0
        score = w_mcap * mcap_score + w_vol * vol_score + w_heat * heat_score
        scored.append((score, pair))
    scored.sort(reverse=True)
    return [p for _, p in scored[:limit]]
