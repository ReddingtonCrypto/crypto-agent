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

    symbols = _read_symbols_cache()
    if symbols is None:
        try:
            symbols = _coingecko_top(limit)
            _save_symbols_cache(symbols)
        except Exception as e:
            print(f"CoinGecko unavailable ({type(e).__name__}); using fallback list.")
            symbols = [p.split("/")[0] for p in FALLBACK]

    try:
        markets = exchange.load_markets()
    except Exception as e:
        print(f"Could not load markets ({type(e).__name__}); using fallback list.")
        return FALLBACK

    pairs = []
    for sym in symbols:
        if sym in STABLES:
            continue
        pair = f"{sym}/USDT"
        m = markets.get(pair)
        if m and m.get("spot") and m.get("active", True):
            pairs.append(pair)

    return pairs or FALLBACK
