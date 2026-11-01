# What `scripts/setup.sh` changes on the Pi

`setup.sh` builds the pedal from a freshly flashed Raspberry Pi OS Lite (64-bit, Debian 13) image.
It runs on the Pi as root, is idempotent (each step is skipped when already applied) and never
touches the Pi's networking. The Pi needs no internet afterwards. During setup it can borrow the
developer machine's connection through a temporary reverse SOCKS tunnel (`push.sh --setup` opens
it, `setup.sh --proxy` uses it for apt and curl, and the apt proxy file is removed on exit).

| Step | What changes | To revert |
|---|---|---|
| Packages | `sox alsa-utils python3-numpy python3-pil fonts-dejavu-core lilv-utils i2c-tools cmake g++ pkg-config libasound2-dev libgpiod-dev device-tree-compiler stress-ng` | `apt purge ...` |
| PiPedal (fallback) | PiPedal 2.0.110, installed from its release `.deb` (SHA-256 checked) and then disabled with `systemctl disable --now pipedald pipedaladmind`. PipeWire and WirePlumber user units are masked: PiPedal's package pulls PipeWire in (`alsa-base \| pipewire`, and trixie has no `alsa-base`) and they grab `/dev/snd` otherwise. | `systemctl enable --now pipedald pipedaladmind` after stopping `bassbox-engine`; `systemctl --global unmask pipewire.socket pipewire.service pipewire-pulse.socket pipewire-pulse.service wireplumber.service` |
| `/boot/firmware/config.txt` | `dtoverlay=wm8960-soundcard`; `dtoverlay=bassbox-wm8960` (compiled from `config/overlays/bassbox-wm8960.dts` with `dtc`, installed as `/boot/firmware/overlays/bassbox-wm8960.dtbo`); the display block from `config/display.txt`. Backups: `config.txt.bassbox-orig` and `config.txt.bassbox-<timestamp>`. | copy a backup back, reboot |
| `/lib/firmware/st7796s.bin` | panel init sequence from `display/make_st7796s_bin.py --rotate=180` | remove it (the panel then fails to probe) |
| `/boot/firmware/cmdline.txt` | `-console=tty1 +isolcpus=3 +nohz_full=3 +rcu_nocbs=3 +irqaffinity=0-2 +quiet +loglevel=3 +vt.global_cursor_default=0 +logo.nologo`, done by `scripts/cmdline.py`, which keeps every other token as it was and backs the file up (`cmdline.txt.bak-bassbox-<timestamp>`). | copy the backup back, reboot |
| `getty@tty1` | masked: no login prompt on the panel (serial and ssh logins are unaffected) | `systemctl unmask getty@tty1` |
| systemd units | `bassbox-governor` (CPU governor `performance`), `bassbox-mixer` (applies `config/mixer.conf`, waits up to 20 s for the card), `bassbox-engine` (`Restart=always`, `Conflicts=pipedald`), `bassbox-ui`, and a drop-in that orders `pipedald` after the first two | `systemctl disable --now bassbox-engine bassbox-ui bassbox-mixer bassbox-governor` and remove `/etc/systemd/system/bassbox-*.service` |
| Mixer | `scripts/mixer.sh apply`, then `alsactl store` | `alsactl restore` from a saved state |
| Engine | builds `engine/` on the Pi with cmake, pinned to CPU0-1 so the audio core and the benchmark core stay free; `bb-selftest` must pass before anything is installed; installs `bassbox-engine`, `bb-render`, `bb-catalog` to `/usr/local/bin` and restarts the engine only if the binary, its config or its unit changed | `rm /usr/local/bin/{bassbox-engine,bb-render,bb-catalog}` |
| State | `/var/lib/bassbox/live.json` seeded with an empty chain and the output at -12 dB, so a first boot with a loopback cable or a loud amp attached is safe; `touch_cal.json` seeded from `config/touch_cal.json` | delete the files |

`/opt/bassbox` is a mirror of the repository: `push.sh` rsyncs with `--delete`, so anything that
must survive lives in `/var/lib/bassbox` (live state, user presets, touch calibration, soak
results).

`python3-cairo`, which the UI needs, is not in the package list above. It was installed by hand
on the Pi (`apt install python3-cairo`).
