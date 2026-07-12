def calculate_trade(price, direction, atr, strategy="Trend", stop_level=None):
    """Entry, stop and targets.

    If `stop_level` is given (ICT: the swept liquidity level), the stop is
    placed just BEYOND it and targets are a fixed reward:risk off that distance.
    Otherwise the stop/targets are sized off ATR (volatility):
      - Trend : wider target, let winners run   (stop 2x, TP1 3x, TP2 6x ATR)
      - Range : quicker target, bounded move     (stop 1.5x, TP1 1.5x, TP2 3x ATR)
    """
    entry = price

    # ----- Structure-based stop (ICT) -----
    if stop_level is not None:
        buffer = atr * 0.2  # park the stop just beyond the swept level
        if direction == "LONG":
            stop = stop_level - buffer
            risk = entry - stop
        else:
            stop = stop_level + buffer
            risk = stop - entry

        if risk > 0:
            # Partial-exit plan (backtested +0.36%/trade vs +0.16% single-TP):
            # bank half at TP1 = 2R, run the rest to TP2 = 4R.
            if direction == "LONG":
                tp1 = entry + risk * 2.0
                tp2 = entry + risk * 4.0
            else:
                tp1 = entry - risk * 2.0
                tp2 = entry - risk * 4.0
            return {
                "entry": round(entry, 8),
                "stop": round(stop, 8),
                "tp1": round(tp1, 8),
                "tp2": round(tp2, 8),
            }
        # risk came out non-positive -> fall back to ATR sizing below.

    # ----- ATR-based stop (Trend / Range, or ICT fallback) -----
    if strategy == "TrendMA":
        # Trend-following: a WIDE catastrophic stop only — the real exit is the
        # MA trend-flip (handled in paper_trading), so let winners run far.
        s_mult, t1_mult, t2_mult = 4.0, 20.0, 40.0
    elif strategy == "Range":
        s_mult, t1_mult, t2_mult = 1.5, 1.5, 3.0
    elif strategy == "ICT":
        # Keep TP1=2R, TP2=4R off the ATR stop (R = 1.5*ATR) to match the
        # structure-based partial plan above.
        s_mult, t1_mult, t2_mult = 1.5, 3.0, 6.0
    else:  # Trend
        s_mult, t1_mult, t2_mult = 2.0, 3.0, 6.0

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
