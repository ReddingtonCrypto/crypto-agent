def calculate_trade(price, direction, atr, strategy="Trend"):
    """Entry, stop and targets sized off ATR (volatility).

    Strategy-specific reward:risk:
      - Trend : wider target, let winners run   (stop 2x, TP1 3x, TP2 6x ATR)
      - Range : quicker target, bounded move     (stop 1.5x, TP1 1.5x, TP2 3x ATR)
    """
    if strategy == "Range":
        s_mult, t1_mult, t2_mult = 1.5, 1.5, 3.0
    elif strategy == "ICT":
        s_mult, t1_mult, t2_mult = 1.5, 3.0, 5.0
    else:  # Trend
        s_mult, t1_mult, t2_mult = 2.0, 3.0, 6.0

    entry = price
    if direction == "LONG":
        stop = price - (atr * s_mult)
        tp1 = price + (atr * t1_mult)
        tp2 = price + (atr * t2_mult)
    else:
        stop = price + (atr * s_mult)
        tp1 = price - (atr * t1_mult)
        tp2 = price - (atr * t2_mult)

    # Round to 8 decimals, not 4 — otherwise micro-priced coins (SHIB, PEPE,
    # etc.) round to 0.0 and break downstream maths.
    return {
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
    }
