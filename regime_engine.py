def get_regime(ema20, ema50, rsi):

    # strong bullish trend
    if ema20 > ema50 and rsi >= 50:
        return "TREND_BULL"


    # strong bearish trend
    elif ema20 < ema50 and rsi <= 50:
        return "TREND_BEAR"


    # weak/no direction
    elif abs(ema20 - ema50) < (ema50 * 0.002):
        return "RANGE"


    else:
        return "WEAK_TREND"