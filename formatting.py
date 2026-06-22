"""Small shared helper for showing prices with a sensible number of decimals.

Internally we store prices at 8 decimals (so micro-priced coins like PEPE
don't round to zero), but for display we trim to something readable.
"""


def fmt_price(p):
    p = float(p)
    a = abs(p)
    if a >= 1000:
        d = 2
    elif a >= 1:
        d = 4
    elif a >= 0.01:
        d = 5
    elif a >= 0.0001:
        d = 6
    else:
        d = 8
    return f"{p:.{d}f}"
