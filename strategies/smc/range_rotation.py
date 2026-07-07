"""Range rotation strategy — FRVP sweep-and-reclaim.

The complement to ICT (which trades trend continuation). This trades RANGES,
the state crypto spends most of its time in. Uses the Fixed-Range Volume Profile
(value_area) to define the range's edges, then requires a liquidity SWEEP +
RECLAIM of an edge before entering — the "trigger" the range traders insist on
(not just "buy the low"):

  LONG  : price wicks BELOW the Value Area Low (sweeps range liquidity) then
          CLOSES back above it (reclaim). Target POC, then VAH. Stop below the
          sweep low.
  SHORT : mirror at the Value Area High.

Banking half at the POC (the magnet) and running to the far edge mirrors the
value-area rotation VAL -> POC -> VAH the traders describe.

Pure function. Returns {direction, entry, stop, tp1(POC), tp2(edge)} or None.
"""

from strategies.smc.volume_profile import value_area


def detect_range_rotation(df, bins=50, buffer_frac=0.10):
    va = value_area(df, bins)
    if va is None:
        return None
    val, poc, vah = va["val"], va["poc"], va["vah"]
    if not (val < poc < vah):
        return None

    last = df.iloc[-1]
    lo, hi, close = float(last["low"]), float(last["high"]), float(last["close"])
    buf = (vah - val) * buffer_frac

    # LONG: swept below VAL then reclaimed it; POC must be a target above entry.
    if lo < val and close > val and poc > close:
        return {"direction": "LONG", "entry": close, "stop": lo - buf,
                "tp1": poc, "tp2": vah}

    # SHORT: swept above VAH then reclaimed it; POC must be a target below entry.
    if hi > vah and close < vah and poc < close:
        return {"direction": "SHORT", "entry": close, "stop": hi + buf,
                "tp1": poc, "tp2": val}

    return None
