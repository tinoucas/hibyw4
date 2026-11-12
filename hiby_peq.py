#!/usr/bin/env python3
"""Realtime PEQ control for the HiBy W4 (Airoha HID backend). See PROTOCOL.md.

Requires: pip install hidapi   (module name `hid`)

  hiby_peq.py list                     # find the device / confirm VID, PID, usage page
  hiby_peq.py status                   # read enable, preamp, 10 bands
  hiby_peq.py apply ParametricEQ.txt   # AutoEQ / squig.link / Equalizer APO format
  hiby_peq.py enable | disable | flat
  add --dry-run to print the reports instead of sending them
"""
import argparse
import re
import struct
import sys
import time

VID, PID, USAGE_PAGE = 0x32BB, 0x0004, 0xFF32
REPORT_ID, REPORT_LEN = 5, 62   # "V2" framing: XOR checksum (u16 BE) of bytes 0..59 at 60..61
STATUS = {0: "ok", 1: "invalid cmd", 2: "invalid param", 3: "not supported", 4: "auth required",
          5: "auth failed", 6: "internal error", 7: "timeout", 8: "checksum error"}
CMD_SET, CMD_GET = 7, 9
SET_PREAMP, SET_BAND, SET_ENABLE = 1, 2, 3
GET_PREAMP, GET_ENABLE = 0x0A, 0x0B
NUM_BANDS = 10
DEFAULT_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
TYPE_LS, TYPE_PK, TYPE_HS = 0, 1, 2
TYPE_NAMES = {TYPE_LS: "LS", TYPE_PK: "PK", TYPE_HS: "HS"}
LIMITS = dict(freq=(20, 20000), q=(0.2, 20.0), gain=(-9.0, 9.0))


def report(cmd, payload):
    assert len(payload) <= 55, len(payload)
    r = bytearray(REPORT_LEN)
    r[0] = REPORT_ID
    r[1:5] = struct.pack("<I", cmd)
    r[5:5 + len(payload)] = bytes(payload)
    x = 0
    for b in r[:60]:
        x ^= b
    r[60:62] = struct.pack(">H", x)
    return bytes(r)


def band_report(idx, enabled, ftype, freq, q, gain):
    return report(CMD_SET, bytes([SET_BAND]) + struct.pack(
        "<IIIIIi", idx, int(enabled), ftype, int(round(freq)),
        int(round(q * 100)), int(round(gain * 100))))


def preamp_report(db):
    return report(CMD_SET, bytes([SET_PREAMP]) + struct.pack("<i", int(db * 100)))


def enable_report(on):
    return report(CMD_SET, bytes([SET_ENABLE, 1 if on else 0]))


class Device:
    def __init__(self, path=None, dry_run=False, verbose=False):
        self.dry, self.verbose, self.h = dry_run, verbose, None
        if dry_run:
            return
        import hid
        if path is None:
            path = find_path(hid)
        self.h = hid.device()
        self.h.open_path(path)

    def xfer(self, pkt):
        if self.verbose or self.dry:
            print(">>", pkt[:32].hex(" "))
        if self.dry:
            return None
        self.h.send_feature_report(pkt)
        time.sleep(0.02)
        resp = bytes(self.h.get_feature_report(REPORT_ID, REPORT_LEN))
        if self.verbose:
            print("<<", resp[:32].hex(" "))
        if resp[1:5] != pkt[1:5] or resp[5] != 0:
            sys.exit(f"device error: {STATUS.get(resp[5], resp[5])} (reply {resp[:12].hex(' ')})")
        return resp[6:]  # [sub-op echo][value...]

    def get(self, selector):
        r = self.xfer(report(CMD_GET, [selector]))
        if r is None:
            sys.exit("cannot read in --dry-run")
        return r


def find_path(hid):
    devs = [d for d in hid.enumerate(VID, PID)]
    if not devs:
        sys.exit(f"no device {VID:04x}:{PID:04x} found (try `list --all`, then --path)")
    pref = [d for d in devs if d.get("usage_page") == USAGE_PAGE]
    return (pref or devs)[0]["path"]


def cmd_list(args):
    import hid
    for d in hid.enumerate():
        if args.all or d["vendor_id"] in (0x32BB, 0x3302):
            print(f"{d['vendor_id']:04x}:{d['product_id']:04x} up=0x{d.get('usage_page', 0):04x} "
                  f"u=0x{d.get('usage', 0):04x} if={d.get('interface_number')} "
                  f"{d.get('manufacturer_string')!r} {d.get('product_string')!r} path={d['path']!r}")


def read_state(dev):
    on = dev.get(GET_ENABLE)[1] == 1
    pre = struct.unpack_from("<i", dev.get(GET_PREAMP), 1)[0] / 100
    bands = []
    for i in range(NUM_BANDS):
        # reply order differs from SET order: freq, enabled, type, index, Q*100, gain*100
        freq, en, ftype, idx, q, gain = struct.unpack_from("<IIIIIi", dev.get(i), 1)
        bands.append(dict(idx=idx, enabled=bool(en), ftype=ftype, freq=freq, q=q / 100, gain=gain / 100))
    return on, pre, bands


def cmd_status(dev, args):
    on, pre, bands = read_state(dev)
    print(f"PEQ {'ON' if on else 'OFF'}   preamp {pre:+.2f} dB")
    for i, b in enumerate(bands):
        print(f"  band {i} {'on ' if b['enabled'] else 'off'} {TYPE_NAMES.get(b['ftype'], b['ftype'])} "
              f"{b['freq']:>5} Hz  Q {b['q']:5.2f}  {b['gain']:+6.2f} dB")


def cmd_dump(dev, args):
    """Print current device PEQ in AutoEQ/Equalizer APO format (re-loadable with `apply`)."""
    on, pre, bands = read_state(dev)
    tmap = {TYPE_LS: "LSC", TYPE_PK: "PK", TYPE_HS: "HSC"}
    print(f"# PEQ was {'ON' if on else 'OFF'} on device")
    print(f"Preamp: {pre:.2f} dB")
    for i, b in enumerate(bands):
        print(f"Filter {i + 1}: {'ON' if b['enabled'] else 'OFF'} {tmap.get(b['ftype'], 'PK')} "
              f"Fc {b['freq']} Hz Gain {b['gain']:.2f} dB Q {b['q']:.2f}")


FILTER_RE = re.compile(
    r"Filter\s*\d*\s*:\s*(ON|OFF)\s+(\w+)\s+Fc\s+([\d.]+)\s*Hz"
    r"(?:\s+Gain\s+([-+\d.]+)\s*dB)?(?:\s+Q\s+([\d.]+))?", re.I)
TYPE_MAP = {"PK": TYPE_PK, "PEQ": TYPE_PK, "LS": TYPE_LS, "LSC": TYPE_LS, "LSQ": TYPE_LS,
            "HS": TYPE_HS, "HSC": TYPE_HS, "HSQ": TYPE_HS}


def parse_peq(text):
    preamp, bands = 0.0, []
    for line in text.splitlines():
        m = re.match(r"\s*Preamp\s*:\s*([-+\d.]+)\s*dB", line, re.I)
        if m:
            preamp = float(m.group(1))
            continue
        m = FILTER_RE.search(line)
        if not m:
            continue
        on, t, fc, gain, q = m.groups()
        if t.upper() not in TYPE_MAP:
            sys.exit(f"unsupported filter type {t!r} (device supports PK/LSC/HSC): {line.strip()}")
        bands.append(dict(enabled=on.upper() == "ON", ftype=TYPE_MAP[t.upper()],
                          freq=float(fc), gain=float(gain or 0), q=float(q or 0.707)))
    return preamp, bands


def check(bands, force):
    if len(bands) > NUM_BANDS:
        sys.exit(f"{len(bands)} filters, device has {NUM_BANDS} bands")
    for i, b in enumerate(bands):
        for k, (lo, hi) in LIMITS.items():
            if not lo <= b[k] <= hi:
                msg = f"band {i}: {k}={b[k]} outside app range [{lo}, {hi}]"
                if not force:
                    sys.exit(msg + " (use --force to send anyway)")
                print("warning:", msg, file=sys.stderr)


def write_all(dev, preamp, bands):
    bands = bands + [dict(enabled=False, ftype=TYPE_PK, freq=DEFAULT_FREQS[i], q=0.707, gain=0.0)
                     for i in range(len(bands), NUM_BANDS)]
    dev.xfer(preamp_report(preamp))
    for i, b in enumerate(bands):
        dev.xfer(band_report(i, b["enabled"], b["ftype"], b["freq"], b["q"], b["gain"]))
    dev.xfer(enable_report(True))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=lambda s: s.encode(), help="hidapi device path (from `list`)")
    ap.add_argument("--dry-run", action="store_true", help="print reports, don't open the device")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("--all", action="store_true")
    sub.add_parser("status")
    sub.add_parser("dump", help="print device PEQ as ParametricEQ.txt (backup)")
    a = sub.add_parser("apply")
    a.add_argument("file")
    a.add_argument("--force", action="store_true", help="ignore the official app's value limits")
    sub.add_parser("enable")
    sub.add_parser("disable")
    sub.add_parser("flat")
    args = ap.parse_args()

    if args.cmd == "list":
        return cmd_list(args)
    dev = Device(args.path, args.dry_run, args.verbose)
    if args.cmd == "status":
        cmd_status(dev, args)
    elif args.cmd == "dump":
        cmd_dump(dev, args)
    elif args.cmd == "apply":
        with open(args.file) as f:
            preamp, bands = parse_peq(f.read())
        check(bands, args.force)
        write_all(dev, preamp, bands)
        print(f"applied preamp {preamp:+.1f} dB, {len(bands)} filters")
    elif args.cmd in ("enable", "disable"):
        dev.xfer(enable_report(args.cmd == "enable"))
    elif args.cmd == "flat":
        write_all(dev, 0.0, [])


if __name__ == "__main__":
    main()
