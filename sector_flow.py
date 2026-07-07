"""Sector / narrative heat — free, from price data only (no API key).

Groups coins into narrative sectors (L1, L2, AI, Meme, DeFi, ...) and ranks the
sectors by recent momentum, so the agent knows WHICH narrative money is rotating
into. This is context/awareness, not a hard entry gate — the money-flow gate
already trades hot coins; this tells you (and a future AI layer) the story behind
them.

`sector_heat(exchange)` returns a list of {sector, avg_pct, coins} sorted hottest
first, using one fetch_tickers call (24h % change per coin).
"""

# Coin -> narrative sector. Extend freely; unmapped coins fall under "Other".
SECTOR_MAP = {
    # Layer 1
    "BTC": "L1", "ETH": "L1", "SOL": "L1", "ADA": "L1", "AVAX": "L1",
    "DOT": "L1", "ATOM": "L1", "NEAR": "L1", "TRX": "L1", "BCH": "L1",
    "LTC": "L1", "ICP": "L1", "SUI": "L1", "APT": "L1", "SEI": "L1",
    "ALGO": "L1", "HBAR": "L1", "TON": "L1",
    # Layer 2 / scaling
    "ARB": "L2", "OP": "L2", "POL": "L2", "MATIC": "L2", "IMX": "L2",
    "STRK": "L2", "MANTA": "L2", "ZK": "L2",
    # AI
    "RENDER": "AI", "TAO": "AI", "FET": "AI", "WLD": "AI", "GRT": "AI",
    "AKT": "AI",
    # Meme
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme", "WIF": "Meme",
    "BONK": "Meme", "FLOKI": "Meme", "PUMP": "Meme",
    # DeFi
    "UNI": "DeFi", "AAVE": "DeFi", "LINK": "DeFi", "CRV": "DeFi",
    "MKR": "DeFi", "LDO": "DeFi", "INJ": "DeFi", "RUNE": "DeFi", "ENA": "DeFi",
    # Payments
    "XRP": "Payments", "XLM": "Payments", "XDC": "Payments",
    # RWA
    "ONDO": "RWA", "PAXG": "RWA",
    # Privacy
    "ZEC": "Privacy", "XMR": "Privacy",
}


def sector_of(symbol):
    """Sector for a 'BTC/USDT' or 'BTC' symbol."""
    base = symbol.split("/")[0].upper()
    return SECTOR_MAP.get(base, "Other")


def sector_heat(exchange, min_coins=2):
    """Rank sectors by average 24h % change (hottest first). Returns a list of
    {sector, avg_pct, coins}. Fails soft (returns []) if tickers are unavailable."""
    try:
        tickers = exchange.fetch_tickers()
    except Exception as e:
        print(f"Sector heat unavailable ({type(e).__name__}).")
        return []

    buckets = {}
    for sym, t in tickers.items():
        if "/USDT" not in sym:
            continue
        pct = t.get("percentage")
        if pct is None:
            continue
        sec = sector_of(sym)
        if sec == "Other":
            continue
        buckets.setdefault(sec, []).append(float(pct))

    heat = [
        {"sector": sec, "avg_pct": round(sum(v) / len(v), 2), "coins": len(v)}
        for sec, v in buckets.items()
        if len(v) >= min_coins
    ]
    heat.sort(key=lambda x: x["avg_pct"], reverse=True)
    return heat
