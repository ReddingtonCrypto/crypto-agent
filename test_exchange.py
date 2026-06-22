"""One-off check: which exchanges will actually serve candle data from
wherever this runs (e.g. GitHub's servers)? Prints OK/FAIL for each.

Run it locally (python test_exchange.py) or via the 'Test Data Sources'
GitHub Action. It does NOT touch the live bot.
"""

import ccxt


EXCHANGES = ["binance", "binanceus", "okx", "kraken", "mexc", "gateio", "bybit"]


def main():
    for name in EXCHANGES:
        try:
            ex = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 20000})
            bars = ex.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=3)
            print(f"OK    {name:10} -> {len(bars)} candles, last close {bars[-1][4]}")
        except Exception as e:
            first_line = str(e).splitlines()[0][:90]
            print(f"FAIL  {name:10} -> {first_line}")


if __name__ == "__main__":
    main()
