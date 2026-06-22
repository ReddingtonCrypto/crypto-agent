def calculate_confidence(
    ema20,
    ema50,
    price,
    rsi
):

    confidence = 50


    # Trend + price alignment

    if ema20 > ema50 and price > ema20:

        confidence += 25


    elif ema20 < ema50 and price < ema20:

        confidence += 25



    # RSI confirmation

    if rsi < 35:

        confidence += 15


    elif rsi > 65:

        confidence += 15


    elif rsi < 45 or rsi > 55:

        confidence += 5



    if confidence > 100:

        confidence = 100


    return confidence