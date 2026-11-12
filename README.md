# HiBy W4 PEQ

Control the HiBy W4's parametric EQ over USB without HiBy's app. It works on macOS and
Linux, from a browser page or the command line. Changes are applied live.

- `web/index.html`: browser editor (WebHID). Shows the response curve and imports/exports
  AutoEQ, squig.link and Equalizer APO `ParametricEQ.txt` files.
- `hiby_peq.py`: command-line tool doing the same.
- `PROTOCOL.md`: the reverse-engineered USB HID protocol.
- `presets/`: example presets.

The device has 10 bands (peaking, low shelf, high shelf), a preamp and a global on/off switch.
The tools enforce 20–20000 Hz, Q 0.2–20 and ±9 dB, based on HiBy's app. Override with `--force`
or the page's "Allow values outside HiBy's limits".

## Web page

Requires a Chromium-based browser (Chrome, Chromium, Edge, Brave, Helium…).
Firefox and Safari don't support WebHID.

1. Open `web/index.html` from disk. If your browser refuses, serve it with
   `python3 -m http.server -d web 8000` and open <http://localhost:8000>.
2. Click **Connect** and choose "HiBy W4". It reads the current EQ from the device.
3. Edit bands or load a `ParametricEQ.txt`. With "Send changes live" ticked, every edit
   reaches the device immediately.

Audio has to be playing *through the W4* to hear the EQ (on macOS: Sound → Output → HiBy W4).

## Command line

```sh
python3 -m venv .venv && .venv/bin/pip install hidapi
.venv/bin/python hiby_peq.py list                     # find the device
.venv/bin/python hiby_peq.py status                   # show current EQ
.venv/bin/python hiby_peq.py dump > my-backup.txt     # save it as ParametricEQ.txt
.venv/bin/python hiby_peq.py apply presets/xyz.txt    # load a preset and turn PEQ on
.venv/bin/python hiby_peq.py enable | disable | flat
```

Add `--dry-run` to print the packets without sending them, or `-v` to see the raw traffic.
`list` shows two interfaces. The control one has `up=0xff32`. If your `hidapi` build
doesn't report usage pages, pick that interface with `--path`.

## Linux setup

By default only root can open `/dev/hidraw*`. Install the udev rule once:

```sh
sudo cp linux/70-hiby-w4.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then replug the W4. Use a natively installed browser if you can: Snap and Flatpak
builds may not be allowed to reach `/dev/hidraw*`.

## Status

The protocol is verified on a W4 for reads, live writes, preamp and enable, on macOS.
Not done yet:

- Linux has not been tested.
- It's unknown whether settings survive a power cycle. HiBy's separate "write to device"
  (flash) command is not implemented; see `PROTOCOL.md`.
