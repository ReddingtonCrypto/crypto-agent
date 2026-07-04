"""Volume Profile — POC / Value Area from candles (free, no order-book data).

Buckets traded volume by PRICE level over a window to find where the market
actually did business:

- POC (Point of Control) : the price bin with the most volume — the fair-value
                           magnet / mean.
- Value Area (VAH..VAL)  : the contiguous band around the POC holding `va_pct`
                           (default 70%) of the volume — where price was accepted.

Volume is spread across each candle's high-low range (not dumped on the close),
so wide bars distribute their volume like real auction activity. Pure function,
no network — feed it a DataFrame with high/low/volume columns.
"""

import numpy as np


def value_area(df, bins=50, va_pct=0.70):
    """Return {"poc", "vah", "val"} for the given candle window, or None if the
    window is degenerate (no range / no volume)."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)

    lo, hi = low.min(), high.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return None

    width = (hi - lo) / bins
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    vp = np.zeros(bins)

    for h, l, v in zip(high, low, vol):
        if v <= 0 or not np.isfinite(v):
            continue
        lo_idx = max(0, min(bins - 1, int((l - lo) / width)))
        hi_idx = max(0, min(bins - 1, int((h - lo) / width)))
        n = hi_idx - lo_idx + 1
        vp[lo_idx:hi_idx + 1] += v / n  # spread volume across the candle's range

    if vp.sum() <= 0:
        return None

    poc_idx = int(vp.argmax())
    poc = float(centers[poc_idx])

    # Grow the value area outward from the POC, always taking the heavier
    # neighbour, until it holds va_pct of total volume.
    target = vp.sum() * va_pct
    included = vp[poc_idx]
    lo_i = hi_i = poc_idx
    while included < target and (lo_i > 0 or hi_i < bins - 1):
        left = vp[lo_i - 1] if lo_i > 0 else -1.0
        right = vp[hi_i + 1] if hi_i < bins - 1 else -1.0
        if right >= left:
            hi_i += 1
            included += vp[hi_i]
        else:
            lo_i -= 1
            included += vp[lo_i]

    return {"poc": poc, "vah": float(edges[hi_i + 1]), "val": float(edges[lo_i])}
