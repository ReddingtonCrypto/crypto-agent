import ccxt

exchange = ccxt.okx()

coins = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "ADA/USDT"
]

print("\n===== MARKET SCANNER =====\n")

for coin in coins:

    ticker = exchange.fetch_ticker(coin)

    print(
        coin,
        "Price:",
        round(ticker["last"], 4)
    )