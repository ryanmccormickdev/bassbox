#!/usr/bin/env python3
"""bassbox: randomized engine+UI stress soak (run ON THE PI as the install user).

    python3 /opt/bassbox/scripts/fuzz_soak.py MINUTES [LOG]

Loads presets, bursts set_param at 30 Hz, adds/removes/toggles pedals and drives the UI (screen
changes, knob drags) while logging xruns, DSP load, RSS and service restarts every 30 s. Ends on
"Night Drive" at -6 dB with the UI on the board.
"""
import json, random, socket, subprocess, sys, time

MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 40
LOG = open(sys.argv[2] if len(sys.argv) > 2 else "/tmp/fuzz_soak.log", "w", buffering=1)
INJ = ["python3", "/opt/bassbox/ui/tools/inject.py"]

s = socket.socket(socket.AF_UNIX); s.connect("/run/bassbox/engine.sock"); f = s.makefile("rw")
_id = 0
def eng(cmd, **kw):
    global _id; _id += 1
    f.write(json.dumps({"id": _id, "cmd": cmd, **kw}) + "\n"); f.flush()
    while True:
        r = json.loads(f.readline())
        if r.get("id") == _id: return r

def ui(*a):
    return subprocess.run(INJ + [str(x) for x in a], capture_output=True, text=True, timeout=20).returncode

def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True).stdout.strip()

def log(msg): LOG.write(time.strftime("%H:%M:%S ") + msg + "\n")

cat = {e["id"]: e for e in eng("catalog")["effects"]}
presets = [p["name"] for p in eng("presets")["presets"] if p["factory"]]
restarts0 = sh("systemctl show -p NRestarts --value bassbox-engine bassbox-ui").split()
t_end = time.time() + MIN * 60; t_log = 0; n = 0; errs = 0
log(f"start: {len(cat)} effects, {len(presets)} presets, restarts {restarts0}")
while time.time() < t_end:
    try:
        st = eng("state")["state"]; chain = st["chain"]
        a = random.random()
        if a < 0.15:
            eng("load_preset", name=random.choice(presets))
        elif a < 0.40 and chain:                    # 30 Hz param burst on a float param, like a knob drag
            c = random.choice(chain); ps = [p for p in cat[c["type"]]["params"] if p["type"] == "float"]
            if ps:
                p = random.choice(ps); v0 = random.uniform(p["min"], p["max"])
                for k in range(60):
                    eng("set_param", uid=c["uid"], param=p["id"], value=min(p["max"], max(p["min"], v0 + (p["max"] - p["min"]) * 0.01 * k * random.choice((-1, 1)))))
                    time.sleep(1 / 30)
        elif a < 0.55:
            if len(chain) < 8 and (random.random() < 0.6 or len(chain) < 2):
                eng("add", type=random.choice(list(t for t in cat if t != "nam")), index=random.randint(0, len(chain)))
            elif chain:
                eng("remove", uid=random.choice(chain)["uid"])
        elif a < 0.65 and chain:
            c = random.choice(chain); eng("set_enabled", uid=c["uid"], enabled=not c["enabled"])
        elif a < 0.72 and len(chain) > 1:
            c = random.choice(chain); eng("move", uid=c["uid"], index=random.randint(0, len(chain) - 1))
        elif a < 0.85 and chain:                    # UI: open an edit face and drag the first hero knob
            ui("goto", "edit", random.choice(chain)["uid"]); ui("drag", 88, 215, 88, random.randint(120, 300), 400)
        else:
            ui("goto", random.choice(["board", "library", "presets", "tuner", "board", "board"]))
        n += 1
    except Exception as e:  # engine restart etc.: note and reconnect
        errs += 1; log(f"ERR {type(e).__name__}: {e}"); time.sleep(2)
        try:
            s = socket.socket(socket.AF_UNIX); s.connect("/run/bassbox/engine.sock"); f = s.makefile("rw")
        except OSError: pass
    time.sleep(random.uniform(0.2, 1.0))
    if time.time() - t_log > 30:
        t_log = time.time()
        m = eng("meters")
        rss = sh("ps -o rss= -C bassbox-engine | head -1") + "k engine, " + sh("pgrep -f 'ui/main.py' | head -1 | xargs -r ps -o rss= -p") + "k ui"
        log(f"ops={n} errs={errs} xruns={m['xruns']} dsp={m['dsp_load']:.3f} max={m['dsp_load_max']:.3f} rss: {rss} chain={len(eng('state')['state']['chain'])} temp={sh('vcgencmd measure_temp')} thr={sh('vcgencmd get_throttled')}")
eng("load_preset", name="Night Drive"); eng("set_master", out_gain_db=-6.0); ui("goto", "board")
log(f"done: ops={n} errs={errs} restarts {sh('systemctl show -p NRestarts --value bassbox-engine bassbox-ui').split()} (start {restarts0})")
