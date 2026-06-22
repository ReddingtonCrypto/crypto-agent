import ccxt
import pandas as pd


print("Connecting to Binance...")


exchange = ccxt.okx()


bars = exchange.fetch_ohlcv(
    symbol="BTC/USDT",
    timeframe="1h",
    limit=200
)


df = pd.DataFrame(
    bars,
    columns=[
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]
)


# ===== Indicators =====

# EMA
df["EMA20"] = df["close"].ewm(span=20).mean()
df["EMA50"] = df["close"].ewm(span=50).mean()


# RSI
delta = df["close"].diff()

gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = gain.rolling(14).mean()
avg_loss = loss.rolling(14).mean()

rs = avg_gain / avg_loss

df["RSI"] = 100 - (100 / (1 + rs))


# ATR
high_low = df["high"] - df["low"]

high_close = abs(
    df["high"] - df["close"].shift()
)

low_close = abs(
    df["low"] - df["close"].shift()
)

true_range = pd.concat(
    [
        high_low,
        high_close,
        low_close
    ],
    axis=1
).max(axis=1)


df["ATR"] = true_range.rolling(14).mean()



latest = df.iloc[-1]


print("\n===== BTC MARKET STATUS =====")

print("Price:", round(latest["close"], 2))
print("EMA20:", round(latest["EMA20"], 2))
print("EMA50:", round(latest["EMA50"], 2))
print("RSI:", round(latest["RSI"], 2))
print("ATR:", round(latest["ATR"], 2))


# ===== Regime Engine =====

if latest["EMA20"] > latest["EMA50"]:
    regime = "TREND_BULL"

elif latest["EMA20"] < latest["EMA50"]:
    regime = "TREND_BEAR"

else:
    regime = "RANGE"


print("Regime:", regime)

print("\nCrypto Agent running successfully 🚀")