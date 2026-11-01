#!/usr/bin/env python3
"""bassbox: write PiPedal's on-disk config (runs ON THE PI, as root, with pipedald STOPPED).

  pipedal_preset.py install [--mix X]  write the "bassbox" bank with the "Bass Drive" preset,
                                       select it for boot, write channel routing.
                                       --mix overrides the split blend (-1 = drive only,
                                       0 = equal, 1 = clean-low only); used for measurements.
  pipedal_preset.py audio <dev> [buf] [nbuf]
                                       write AudioConfig.json for ALSA card id <dev>
                                       (e.g. wm8960soundcard, Loopback, or null)
  pipedal_preset.py ports <uri>        print a plugin's input control ports (debug)
  pipedal_preset.py show               print the boot preset chain (debug)

Formats are taken from PiPedal 2.0.110 (Storage.cpp, JackServerSettings.cpp, Pedalboard.cpp).
Plugin control defaults are read live from `lv2info`, so every control is present and in range.
"""
import json
import os
import re
import subprocess
import sys

DATA = "/var/pipedal"
PRESETS = f"{DATA}/presets"
BANK_NAME = "bassbox"
PRESET_NAME = "Bass Drive"
BYPASS_NAME = "Bypass (clean)"
OWNER = "pipedal_d"

T = "http://two-play.com/plugins/"
NAM_URI = T + "toob-nam"
GATE_URI = T + "toob-noise-gate"
VOLUME_URI = T + "toob-volume"
PEQ_URI = T + "toob-parametric-eq"
GEQ_URI = T + "toob-graphiceq"
SPLIT_URI = "uri://two-play/pipedal/pedalboard#Split"
EMPTY_URI = "uri://two-play/pipedal/pedalboard#Empty"

# Split blend: -1 = drive only, 0 = equal, +1 = clean-low only.
SPLIT_MIX = 0
# Clean-low branch make-up gain. Measured with the test riff at -6 dBFS peak: the NAM branch
# (Normalized output) is ~6 dB louder RMS than the low-passed clean branch.
CLEAN_GAIN_DB = 4

NAM_MODEL = "NeuralAmpModels/Factory Models/MRSH JM50LD I Crunch2 FAT CAB.nam"

# WM8960 line-in full scale is ~1 Vrms (= +2.2 dBu) at 0 dB analog gain. scripts/calibrate.sh
# rewrites config/input_calibration_dbu when the input gain changes.
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def input_calibration_dbu():
    try:
        with open(os.path.join(BASE, "config", "input_calibration_dbu")) as f:
            return float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return 2.2


# ---------------------------------------------------------------- lv2 metadata
_port_cache = {}


def ports(uri):
    """Input control ports of a plugin: {symbol: (min, max, default)} in port order."""
    if uri in _port_cache:
        return _port_cache[uri]
    out = subprocess.run(["lv2info", uri], capture_output=True, text=True, check=True).stdout
    result = {}
    for block in re.split(r"\n\tPort \d+:\n", out)[1:]:
        if "lv2core#ControlPort" not in block or "lv2core#InputPort" not in block:
            continue
        field = lambda k: (re.search(rf"{k}:\s+(\S+)", block) or [None, None])[1]
        sym = field("Symbol")
        lo, hi, d = (float(field(k) or 0) for k in ("Minimum", "Maximum", "Default"))
        result[sym] = (lo, hi, d)
    if not result:
        raise SystemExit(f"lv2info found no control ports for {uri} (plugin not installed?)")
    _port_cache[uri] = result
    return result


def plugin_name(uri):
    out = subprocess.run(["lv2info", uri], capture_output=True, text=True, check=True).stdout
    m = re.search(r"Name:\s+(.+)", out)
    return m.group(1).strip() if m else ""


# ---------------------------------------------------------------- pedalboard items
class Ids:
    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n


def item(ids, uri, values=None, enabled=True, title="", path_props=None):
    """A plugin item with every input control present (defaults from lv2info, then overrides)."""
    values = values or {}
    p = ports(uri)
    unknown = set(values) - set(p)
    if unknown:
        raise SystemExit(f"{uri}: unknown control(s) {sorted(unknown)}; known: {sorted(p)}")
    cv = []
    for sym, (lo, hi, d) in p.items():
        if sym in values:
            v = float(values[sym])
            if not lo <= v <= hi:
                raise SystemExit(f"{uri}: {sym}={v} out of range [{lo}, {hi}]")
        else:
            v = min(max(d, lo), hi)   # some TTL defaults sit outside their own range
        cv.append({"key": sym, "value": v})
    lv2_state = [False, {}]
    path_properties = {}
    if path_props:
        state = {}
        for prop, rel in path_props.items():
            state[prop] = {"flags": 3, "atomType": "http://lv2plug.in/ns/ext/atom#Path", "value": rel}
            path_properties[prop] = json.dumps({"otype_": "Path", "value": rel})
        lv2_state = [True, state]
    return {
        "instanceId": ids.next(), "uri": uri, "isEnabled": enabled, "controlValues": cv,
        "pluginName": plugin_name(uri), "midiBindings": [], "midiChannelBinding": None,
        "stateUpdateCount": 0, "lv2State": lv2_state, "lilvPresetUri": "",
        "pathProperties": path_properties, "title": title, "useModUi": False,
        "iconColor": "default", "sideChainInputId": -1,
    }


def structural(ids, uri, control_values=None, **extra):
    d = {
        "instanceId": ids.next(), "uri": uri, "isEnabled": True,
        "controlValues": [{"key": k, "value": float(v)} for k, v in (control_values or {}).items()],
        "pluginName": "", "midiBindings": [], "midiChannelBinding": None, "stateUpdateCount": 0,
        "lv2State": [False, {}], "lilvPresetUri": "", "pathProperties": {}, "title": "",
        "useModUi": False, "iconColor": "", "sideChainInputId": -1,
    }
    d.update(extra)
    return d


def clean_low_branch(ids):
    """Branch B: clean bass, low-passed to keep only the fundamentals (~200 Hz and below).

    ToobAmp has no variable low-pass that reaches 200 Hz, so this stacks two EQs:
      * Graphic EQ: 400 Hz..6.4 kHz bands at -15 dB (broad shelf above ~250 Hz)
      * Parametric EQ: hi-cut at its 1 kHz minimum (steep roll-off of what's left), plus wide
        -15 dB cuts at 650 Hz and 1.3 kHz to deepen the 300 Hz..1 kHz region
    The measured response is recorded in NOTES.md (scripts/measure_lowpass.sh).
    """
    geq_cuts = {s: -15 for s in ports(GEQ_URI) if re.fullmatch(r"gain_(400|800|1600|3200|6400)hz", s)}
    return [
        item(ids, GEQ_URI, geq_cuts, title="Low pass (EQ)"),
        item(ids, PEQ_URI, {
            "hiCut": 1,
            "lmfC": 650, "lmfLevel": -15, "lmfQ": 1.2,
            "hmfC": 1.3, "hmfLevel": -15, "hmfQ": 0.8,
            "gain": CLEAN_GAIN_DB,
        }, title="Low pass (hi-cut)"),
    ]


def bass_drive_pedalboard(mix=SPLIT_MIX):
    ids = Ids()
    gate = item(ids, GATE_URI, {
        "threshold": -70, "hysteresis": -6, "reduction": -60,
        "attack": 1, "hold": 50, "release": 200,
    }, title="Gate")
    nam = item(ids, NAM_URI, {
        "inputCalibrationMode": 1,          # Calibrated: model sees realistic dBu levels
        "calibration": input_calibration_dbu(),
        "inputGain": 6,                     # push the crunch a bit harder for bass
        "outputCalibration": 0,             # Normalized (-18 dB loudness)
        "outputGain": 0,
        "gate": -120,                       # off; the dedicated gate is first in the chain
        "toneStack": 3,                     # bypass
        "buffer": 0,                        # unthreaded: no extra buffer of latency
    }, title="Drive (NAM)", path_props={NAM_URI + "#modelFile": NAM_MODEL})
    split = structural(
        ids, SPLIT_URI,
        # splitType 1 = Mix; mix -1 = A (top) only .. 0 = equal .. +1 = B (bottom) only
        {"mix": mix, "panL": 0, "panR": 0, "select": 0, "splitType": 1, "volL": 0, "volR": 0},
        topChain=[nam],
        bottomChain=clean_low_branch(ids),
    )
    # +3 dB: with the split at mix 0 the active preset then roughly matches the bypassed level
    out_vol = item(ids, VOLUME_URI, {"vol": 3}, title="Output volume")
    return {
        "name": PRESET_NAME,
        "input_volume_db": 0,
        "output_volume_db": 0,
        "items": [gate, split, out_vol],
        "nextInstanceId": ids.next(),
        "snapshots": [None] * 6,
        "selectedSnapshot": -1,
        "selectedPlugin": gate["instanceId"],
    }


def bypass_pedalboard():
    """Empty pedalboard: input passes straight to the outputs (the 'bass is clean' check)."""
    ids = Ids()
    empty = structural(ids, EMPTY_URI)
    return {
        "name": BYPASS_NAME, "input_volume_db": 0, "output_volume_db": 0,
        "items": [empty], "nextInstanceId": ids.next(), "snapshots": [None] * 6,
        "selectedSnapshot": -1, "selectedPlugin": -1,
    }


# ---------------------------------------------------------------- files
def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)
    subprocess.run(["chown", f"{OWNER}:{OWNER}", path], check=False)
    os.chmod(path, 0o664)


def bank_file(name):
    return f"{PRESETS}/{name.replace(' ', '+')}.bank"


def pipedald_running():
    return subprocess.run(["systemctl", "is-active", "--quiet", "pipedald"]).returncode == 0


def install(mix=None):
    if pipedald_running():
        raise SystemExit("stop pipedald first (it rewrites presets on shutdown)")
    index_path = f"{PRESETS}/index.banks"
    with open(index_path) as f:
        index = json.load(f)
    entry = next((e for e in index["entries"] if e["name"] == BANK_NAME), None)
    if entry is None:
        index["nextInstanceId"] += 1
        entry = {"instanceId": index["nextInstanceId"], "name": BANK_NAME}
        index["entries"].insert(0, entry)
    index["selectedBank"] = entry["instanceId"]

    bank = {"name": BANK_NAME, "nextInstanceId": 2, "selectedPreset": 1,
            "presets": [
                {"instanceId": 1, "preset": bass_drive_pedalboard(SPLIT_MIX if mix is None else mix)},
                {"instanceId": 2, "preset": bypass_pedalboard()},
            ]}
    write_json(bank_file(BANK_NAME), bank)
    write_json(index_path, index)

    # Boot loads index.selectedBank -> bank.selectedPreset, unless currentPreset.json exists.
    try:
        os.remove(f"{DATA}/currentPreset.json")
    except FileNotFoundError:
        pass

    # Left input only (mono pedalboard), mono output copied to both outputs.
    write_json(f"{DATA}/ChannelRouterSettings.json", {
        "configured": True, "changed": False, "channelRouterPresetId": -1,
        "mainInputChannels": [0, 0], "mainOutputChannels": [0, 1],
        "auxInputChannels": [-1, -1], "auxOutputChannels": [-1, -1],
    })
    write_json(f"{DATA}/JackChannelSelection.json", {
        "inputAudioPorts": ["system::capture_0"],
        "outputAudioPorts": ["system::playback_0", "system::playback_1"],
        "inputMidiDevices": [],
    })
    print(f"installed bank '{BANK_NAME}' (id {entry['instanceId']}): '{PRESET_NAME}' (boot preset), '{BYPASS_NAME}'")


def audio(dev, buf=64, nbuf=3):
    if pipedald_running():
        raise SystemExit("stop pipedald first")
    hw = "null" if dev == "null" else f"hw:{dev}"
    write_json(f"{DATA}/AudioConfig.json", {
        "valid": True, "isOnboarding": False, "rebootRequired": False, "isJackAudio": False,
        "alsaDevice": "", "alsaInputDevice": hw, "alsaOutputDevice": hw,
        "alsaInputDeviceName": "null" if dev == "null" else "",
        "alsaOutputDeviceName": "null" if dev == "null" else "",
        "sampleRate": 48000, "bufferSize": int(buf), "numberOfBuffers": int(nbuf),
    })
    print(f"AudioConfig: {hw} 48000 Hz, {buf}x{nbuf}")


def show():
    with open(f"{PRESETS}/index.banks") as f:
        index = json.load(f)
    entry = next(e for e in index["entries"] if e["instanceId"] == index["selectedBank"])
    with open(bank_file(entry["name"])) as f:
        bank = json.load(f)
    preset = next(p for p in bank["presets"] if p["instanceId"] == bank["selectedPreset"])["preset"]
    print(f"boot preset: bank '{entry['name']}' / '{preset['name']}'")

    def walk(items, depth=1):
        for it in items:
            label = it.get("title") or it.get("pluginName") or it["uri"].split("#")[-1]
            print("  " * depth + f"- {label} [{'on' if it['isEnabled'] else 'off'}]")
            for k in ("topChain", "bottomChain"):
                if k in it:
                    print("  " * (depth + 1) + k)
                    walk(it[k], depth + 2)
    walk(preset["items"])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install":
        install(float(sys.argv[3]) if len(sys.argv) == 4 and sys.argv[2] == "--mix" else None)
    elif cmd == "audio" and len(sys.argv) >= 3:
        audio(*sys.argv[2:5])
    elif cmd == "ports" and len(sys.argv) == 3:
        for s, r in ports(sys.argv[2]).items():
            print(s, r)
    elif cmd == "show":
        show()
    else:
        print(__doc__)
        sys.exit(64)
