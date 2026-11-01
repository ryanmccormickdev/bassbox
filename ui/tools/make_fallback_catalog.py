"""Generate ui/catalog_fallback.json: the UI's stand-in catalog until engine/catalog.json exists."""
import json, os

def P(id, name, mn, mx, df, unit="", curve="lin", type="float", options=None, step=0):
    d = {"id": id, "name": name, "min": mn, "max": mx, "def": df, "unit": unit, "curve": curve, "type": type, "step": step}
    if options:
        d["options"] = options
    return d

def E(id, name, cat, desc, params, tails=False):
    return {"id": id, "name": name, "category": cat, "description": desc, "tails": tails, "params": params}

pct = lambda id, name, df: P(id, name, 0, 1, df, "%")
EFFECTS = [
    E("comp", "Compressor", "dynamics", "Evens out your attack and sustain.",
      [P("thresh", "Thresh", -60, 0, -18, "dB"), P("ratio", "Ratio", 1, 20, 4, ":1", "log"), P("attack", "Attack", 0.1, 100, 10, "ms", "log"),
       P("release", "Release", 10, 1000, 120, "ms", "log"), P("makeup", "Makeup", 0, 24, 3, "dB")]),
    E("gate", "Noise Gate", "dynamics", "Silences hum and hiss between notes.",
      [P("thresh", "Thresh", -90, -20, -60, "dB"), P("release", "Release", 5, 500, 80, "ms", "log"), P("range", "Range", -90, 0, -80, "dB")]),
    E("limiter", "Limiter", "dynamics", "Brick-wall ceiling so nothing clips.",
      [P("ceiling", "Ceiling", -24, 0, -1, "dB"), P("release", "Release", 10, 500, 60, "ms", "log")]),
    E("overdrive", "Overdrive", "drive", "Warm, amp-like push that cleans up.",
      [pct("drive", "Drive", 0.5), pct("tone", "Tone", 0.5), P("level", "Level", -24, 12, 0, "dB"), pct("blend", "Blend", 1.0)]),
    E("distortion", "Distortion", "drive", "Hard-clipped grit with a tight low end.",
      [pct("drive", "Drive", 0.62), pct("tone", "Tone", 0.45), P("level", "Level", -24, 12, -3, "dB"),
       pct("blend", "Blend", 0.8), P("lowcut", "Low Cut", 20, 400, 80, "Hz", "log")]),
    E("fuzz", "Fuzz", "drive", "Gated, buzzing, square-wave sustain.",
      [pct("fuzz", "Fuzz", 0.7), pct("tone", "Tone", 0.5), P("level", "Level", -24, 12, -6, "dB"), pct("gate", "Gate", 0.2)]),
    E("bitcrush", "Bit Crusher", "drive", "Lo-fi sample and bit reduction.",
      [P("bits", "Bits", 2, 16, 8, "", step=1), P("rate", "Rate", 1000, 48000, 12000, "Hz", "log"), pct("mix", "Crush", 0.6)]),
    E("bass_amp", "Bass Amp", "amp", "Tube preamp with a 3-band tone stack.",
      [pct("gain", "Gain", 0.4), P("bass", "Bass", -12, 12, 2, "dB"), P("mid", "Mid", -12, 12, 0, "dB"), P("treble", "Treble", -12, 12, 1, "dB"),
       P("master", "Master", -24, 12, 0, "dB")]),
    E("cab", "Cabinet", "amp", "8x10, 4x10 or 1x15 speaker cabinet.",
      [P("cab", "Cab", 0, 2, 0, type="enum", options=["8x10", "4x10", "1x15"]), P("mic", "Mic Pos", 0, 1, 0.3), P("air", "Air", 0, 1, 0.2, "%")]),
    E("nam", "Neural Amp", "amp", "Neural capture of a real amp rig.",
      [P("input", "Input", -24, 24, 0, "dB"), P("output", "Output", -24, 24, 0, "dB")]),
    E("wah", "Wah", "filter", "Vocal sweep from heel to toe.",
      [pct("pos", "Pedal", 0.5), P("q", "Q", 0.5, 10, 4, "", "log"), P("lo", "Heel", 200, 800, 350, "Hz", "log"), P("hi", "Toe", 800, 3000, 1800, "Hz", "log")]),
    E("env_filter", "Env Filter", "filter", "Funky auto-wah that follows your dynamics.",
      [pct("sens", "Sens", 0.6), P("q", "Q", 0.5, 10, 5, "", "log"), P("range", "Range", 200, 4000, 1500, "Hz", "log"),
       P("mode", "Mode", 0, 2, 0, type="enum", options=["LP", "BP", "HP"]), P("dir", "Dir", 0, 1, 0, type="enum", options=["Up", "Down"])]),
    E("lowpass", "Low-Pass", "filter", "Resonant filter to roll off the top.",
      [P("cutoff", "Cutoff", 80, 12000, 2000, "Hz", "log"), P("res", "Reso", 0.5, 12, 0.7, "", "log")]),
    E("synth_filter", "Synth Filter", "filter", "Envelope-swept ladder for synth bass.",
      [P("cutoff", "Cutoff", 80, 8000, 600, "Hz", "log"), P("res", "Reso", 0, 1, 0.6, "%"), pct("env", "Env", 0.7), P("decay", "Decay", 20, 2000, 300, "ms", "log")]),
    E("eq3", "3-Band EQ", "eq", "Bass, mid and treble with a sweepable mid.",
      [P("low", "Low", -15, 15, 0, "dB"), P("mid", "Mid", -15, 15, 0, "dB"), P("midf", "Mid Freq", 200, 3000, 800, "Hz", "log"), P("high", "High", -15, 15, 0, "dB")]),
    E("graphic_eq", "Graphic EQ", "eq", "Seven bands tuned for bass.",
      [P(f"b{i}", n, -12, 12, 0, "dB") for i, n in enumerate(["50", "120", "400", "500", "800", "4.5k", "10k"])]),
    E("tilt", "Tilt EQ", "eq", "One knob: darker or brighter.",
      [P("tilt", "Tilt", -6, 6, 0, "dB"), P("pivot", "Pivot", 200, 2000, 700, "Hz", "log")]),
    E("chorus", "Chorus", "modulation", "Lush doubling with a low-end lock.",
      [P("rate", "Rate", 0.05, 8, 0.8, "Hz", "log"), pct("depth", "Depth", 0.5), P("xover", "X-Over", 50, 500, 150, "Hz", "log")]),
    E("flanger", "Flanger", "modulation", "Jet-plane comb sweep.",
      [P("rate", "Rate", 0.05, 5, 0.3, "Hz", "log"), pct("depth", "Depth", 0.7), P("fb", "Feedback", -0.95, 0.95, 0.5)]),
    E("phaser", "Phaser", "modulation", "Swirling four-stage phase sweep.",
      [P("rate", "Rate", 0.05, 8, 0.5, "Hz", "log"), pct("depth", "Depth", 0.6), pct("fb", "Feedback", 0.3),
       P("stages", "Stages", 0, 2, 1, type="enum", options=["2", "4", "8"])]),
    E("tremolo", "Tremolo", "modulation", "Rhythmic volume pulse.",
      [P("rate", "Rate", 0.5, 15, 5, "Hz", "log"), pct("depth", "Depth", 0.6), P("shape", "Shape", 0, 2, 0, type="enum", options=["Sine", "Tri", "Square"])]),
    E("octaver", "Octaver", "pitch", "Adds a sub octave below (and above).",
      [pct("sub", "Sub", 0.6), pct("up", "Oct Up", 0.0), pct("dry", "Dry", 0.8)]),
    E("pitch", "Pitch Shift", "pitch", "Shift up or down by semitones.",
      [P("semi", "Semi", -12, 12, -12, "st", step=1), P("fine", "Fine", -50, 50, 0, "ct")]),
    E("delay", "Delay", "time", "Clean digital echo with tone control.",
      [P("time", "Time", 20, 1500, 380, "ms", "log"), pct("fb", "Feedback", 0.35), pct("tone", "Tone", 0.6), pct("mod", "Mod", 0.1)], True),
    E("tape", "Tape Echo", "time", "Warm, wobbly, saturating repeats.",
      [P("time", "Time", 40, 1200, 300, "ms", "log"), pct("fb", "Feedback", 0.45), pct("wow", "Wow", 0.3), pct("sat", "Sat", 0.4)], True),
    E("reverb", "Reverb", "time", "Room to hall, with a low cut for clarity.",
      [P("size", "Size", 0, 1, 0.5, "%"), P("decay", "Decay", 0.2, 10, 2, "s", "log"), pct("damp", "Damp", 0.5), P("lowcut", "Low Cut", 20, 500, 120, "Hz", "log")], True),
    E("gain", "Gain", "utility", "Clean boost or cut.",
      [P("gain", "Gain", -24, 24, 0, "dB")]),
    E("di", "DI Blend", "utility", "Blends in the clean signal from the input.",
      [pct("blend", "Blend", 0.5), P("lowpass", "Lo-Pass", 60, 1000, 250, "Hz", "log")]),
]
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "catalog_fallback.json")
json.dump({"effects": EFFECTS}, open(out, "w"), indent=1)
print(len(EFFECTS), "effects ->", os.path.abspath(out))
