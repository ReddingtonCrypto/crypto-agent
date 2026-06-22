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


    return {
        "entry": round(entry, 4),
        "stop": round(stop, 4),
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4)
    }