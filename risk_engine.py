def calculate_trade(price, direction, atr):

    if direction == "LONG":

        entry = price

        stop = price - (atr * 2)

        tp1 = price + (atr * 3)

        tp2 = price + (atr * 5)


    else:

        entry = price

        stop = price + (atr * 2)

        tp1 = price - (atr * 3)

        tp2 = price - (atr * 5)


    # Round to 8 decimals, not 4 — otherwise micro-priced coins (SHIB, PEPE,
    # etc.) round to 0.0 and break downstream maths.
    return {
        "entry": round(entry, 8),
        "stop": round(stop, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8)
    }