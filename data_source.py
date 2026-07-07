"""Market-data source.

We TRADE on binance.com (global), so we want to ANALYSE binance.com data — exact
prices, wicks, real global volume, and the full ~680 USDT coin list. The catch:
binance.com's main API (api.binance.com) returns HTTP 451 from US IPs (GitHub's
runners) and some regions.

The fix: Binance's PUBLIC market-data host `data-api.binance.vision` serves the
same global spot data and is reachable from US IPs. We point ccxt's public
endpoints there (read-only market data is all the bot needs — it never trades).

`make_exchange()` returns that binance.com-global exchange, probing it once and
falling back to binanceus if the vision host is unreachable, so the bot always
runs. The scan log prints which source is live.
"""

import ccxt

VISION_PUBLIC = "https://data-api.binance.vision/api/v3"


def make_exchange():
    ex = ccxt.binance({"enableRateLimit": True, "timeout": 30000})
    # Route public market-data through the US-reachable global-data host.
    ex.urls["api"]["public"] = VISION_PUBLIC
    try:
        ex.fetch_ohlcv("BTC/USDT", "1h", limit=1)
        print("Data source: binance.com (global) via data-api.binance.vision")
        return ex
    except Exception as e:
        print(f"binance.com vision unreachable ({type(e).__name__}); "
              f"falling back to binanceus.")
        return ccxt.binanceus({"enableRateLimit": True, "timeout": 30000})
