def market_quality(volume, avg_volume, atr, price):

    volatility = (atr / price) * 100

    volume_ratio = volume / avg_volume


    print(
        f"Volume Ratio={volume_ratio:.2f} | "
        f"ATR%={volatility:.2f}"
    )


    if volatility > 0.7 and volume_ratio > 0.10:
        return "STRONG"


    elif volatility < 0.3:
        return "LOW"


    else:
        return "NORMAL"