"""The one place parameter values are mapped, snapped and formatted. Every screen uses this.

Param specs are the catalog dicts: {id, name, min, max, def, unit, curve: lin|log, type: float|enum|bool,
options, step}. Units from the catalog: %, dB, Hz, ms, s, x, st, ct, "" (unitless).

    fmt(p, v)      -> ("62", "%") | ("-3.0", "dB") | ("4.5", "kHz") | ("1.20", "s") | ("Square", "")
    text(p, v)     -> "62%" | "-3.0 dB" | "4.5 kHz" | "380 ms" | "Square"
"""
import math

MINUS = "−"


def is_enum(p):
    return p.get("type") == "enum"


def is_bool(p):
    return p.get("type") == "bool"


def is_discrete(p):
    return is_enum(p) or is_bool(p)


def clamp(p, v):
    lo, hi = p["min"], p["max"]
    return lo if v < lo else hi if v > hi else v


def snap(p, v):
    v = clamp(p, v)
    if is_discrete(p):
        return float(int(round(v)))
    st = p.get("step") or 0
    if st > 0:
        v = round((v - p["min"]) / st) * st + p["min"]
    return clamp(p, v)


def norm(p, v):
    """value -> 0..1 control position (log params map logarithmically)."""
    lo, hi = p["min"], p["max"]
    if hi == lo:
        return 0.0
    v = clamp(p, v)
    if p.get("curve") == "log" and lo > 0:
        return (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo))
    return (v - lo) / (hi - lo)


def denorm(p, t):
    """0..1 control position -> snapped value."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    lo, hi = p["min"], p["max"]
    if p.get("curve") == "log" and lo > 0:
        v = math.exp(math.log(lo) + t * (math.log(hi) - math.log(lo)))
    else:
        v = lo + t * (hi - lo)
    return snap(p, v)


def _signed(v, dec):
    s = f"{abs(v):.{dec}f}"
    if float(s) == 0:
        return s
    return ("+" if v > 0 else MINUS) + s


# Per-effect display overrides: (effect id, param id) -> {value: label}. "0 means off" params etc.
OVERRIDES = {
    ("wah", "auto"): {0: "Manual"},
    ("synth_filter", "lfo"): {0: "Off"},
    ("gate", "hold"): {0: "Off"},
    ("reverb", "predly"): {0: "Off"},
    ("delay", "mod"): {0: "Off"},
    ("tape", "wow"): {0: "Off"},
    ("octaver", "up"): {0: "Off"},
    ("octaver", "oct2"): {0: "Off"},
}


def fmt(p, v):
    """(number, unit) strings. Number is short enough for a knob label (<= 6 chars)."""
    ov = OVERRIDES.get((p.get("_eid"), p.get("id")))
    if ov:
        for k, lab in ov.items():
            if abs(v - k) < 1e-6:
                return lab, ""
    if is_enum(p):
        opts = p.get("options") or []
        i = int(round(v))
        return (opts[i] if 0 <= i < len(opts) else str(i)), ""
    if is_bool(p):
        return ("ON" if v >= 0.5 else "OFF"), ""
    u = p.get("unit") or ""
    if u == "%":
        span = p["max"] - p["min"]
        if span <= 1.0001:          # tolerate 0..1 "percent" params
            v *= 100
        return f"{v:.0f}", "%"
    if u == "dB":
        return _signed(v, 1 if abs(v) < 10 else 0), "dB"
    if u == "Hz":
        if v >= 1000:
            return (f"{v / 1000:.1f}" if v < 9995 else f"{v / 1000:.0f}"), "kHz"
        return (f"{v:.1f}" if v < 10 else f"{v:.0f}"), "Hz"
    if u == "ms":
        if v >= 1000:
            return f"{v / 1000:.2f}", "s"
        return (f"{v:.1f}" if v < 10 else f"{v:.0f}"), "ms"
    if u == "s":
        return (f"{v:.2f}" if v < 10 else f"{v:.1f}"), "s"
    if u in ("st", "ct"):
        return _signed(v, 0), u
    if u == "x":
        return (f"{v:.1f}" if v < 10 else f"{v:.0f}"), ":1"
    # unitless
    if p["min"] == 0 and p["max"] == 1:
        return f"{v * 100:.0f}", "%"      # 0..1 positions read as percent
    st = p.get("step") or 0
    if st >= 1 or (p["max"] - p["min"] >= 10 and float(v).is_integer()):
        return f"{v:.0f}", u
    if p["max"] - p["min"] <= 1.0001:
        return f"{v * 10:.1f}", u   # 0..1 positions read as 0.0..10.0 like a pedal knob
    return f"{v:.1f}", u


def text(p, v):
    n, u = fmt(p, v)
    if not u:
        return n
    if u in ("%", ":1"):
        return n + u
    return f"{n} {u}"


def short_unit(u):
    return u


def default(p):
    return snap(p, p.get("def", p["min"]))


# Per-slot mix (host dry/wet, 0..1) shown with the same formatter as an effect param.
SLOT_MIX = {"id": "__mix", "name": "Mix", "min": 0.0, "max": 1.0, "def": 1.0, "unit": "%", "curve": "lin",
            "type": "float", "options": [], "step": 0}
GAIN_IN = {"id": "in_gain_db", "name": "In gain", "min": -12.0, "max": 24.0, "def": 0.0, "unit": "dB",
           "curve": "lin", "type": "float", "options": [], "step": 0.5}
GAIN_OUT = {"id": "out_gain_db", "name": "Out level", "min": -40.0, "max": 6.0, "def": 0.0, "unit": "dB",
            "curve": "lin", "type": "float", "options": [], "step": 0.5}


def clean_option(label):
    """Long enum labels (NAM model names) -> (main, variant). Strips [tags], underscores, '.nam', and
    ' - BLEND #n' noise; other ' - X' suffixes (Clean, Crunch, Hi Gain...) become the variant line."""
    import re
    t = re.sub(r"\[[^\]]*\]", "", str(label)).replace("_", " ")
    t = re.sub(r"\.nam$", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*BLEND\s*#?\d*\s*$", "", t, flags=re.I)
    main, _, variant = t.rpartition(" - ")
    if not main:
        main, variant = variant, ""
    return " ".join(main.split()), " ".join(variant.split())


def needs_picker(p):
    opts = p.get("options") or []
    return is_enum(p) and (len(opts) > 6 or any(len(o) > 14 for o in opts))
