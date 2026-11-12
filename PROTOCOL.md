# HiBy W4 (Airoha backend) — USB HID PEQ protocol

Reverse-engineered from **HiBy USB Master 1.0.3 (macOS, Qt/C++, unstripped)** and
**HiByUSBMaster 1.0.0 (Android, Kotlin)**. Device ID, transport, GET and SET
(preamp, band, enable) are verified on a real W4, and the change is audible in realtime over USB audio. Fields marked ⚠ are guesses.

## Device matching

The Mac app hardcodes two device families (`HibyDeviceClass::getHibyDeviceList`).
It builds a key from `vendor_id`, `product_id` and `usage_page` (`"%04X_%04X_%04X"`):

| backend            | VID    | PID    | usage page | usage ⚠ | "isBt" / use feature reports |
|--------------------|--------|--------|------------|---------|------------------------------|
| `airoha/airoha.exe`| 0x32BB | 0x0004 | 0xFF32     | 0x0003  | **1**                        |
| `TTGK/TTGK.exe`    | 0x3302 | 0x51C0 | 0x000C     | 0x0001  | 0                            |

The W4 is a Bluetooth DAC, so it should be the **Airoha** entry. That entry is the
one flagged as BT, and there's a `saveAb1565NvKeyData` stub (AB1565 is an Airoha BT
SoC). ⚠ Run `hidutil list` (macOS) or `lsusb` to confirm the VID/PID.

## Transport — VERIFIED on a W4 (2026-11-12)

The command set is the Mac app's Airoha one, but the current W4 firmware wants the
Android app's **"V2" framing**. The Mac app's 64-byte, unchecksummed reports are
rejected with status 8 (checksum error).

Each request is a **62-byte HID feature report**, report ID **5**:

```
byte 0      : 0x05                 report id
bytes 1..4  : cmd, uint32 little-endian
bytes 5..59 : payload (≤55 bytes), zero padded
bytes 60,61 : XOR of bytes 0..59, as uint16 big-endian (so byte 60 is always 0)
```

Read the reply with `get_feature_report(id=5, 62)`:

```
[0x05][cmd echo, 4B][status][sub-op echo][value…][…][checksum @60]
status: 0 ok, 1 invalid cmd, 2 invalid param, 3 not supported, 4 auth required,
        5 auth failed, 6 internal, 7 timeout, 8 checksum
```

No authorization handshake was needed for PEQ reads.

`cmd` layout (`PeqTranClass::generateCmd`): `flag<<31 | (module & 0x7fff)<<16 | moduleCmd`.

| cmd          | meaning            |
|--------------|--------------------|
| `0x00000007` | SET (realtime)     |
| `0x00000009` | GET                |
| `0x00000008` | write all PEQ to flash (see below) |
| `0x1000000A` | reboot             |

### SET (cmd 7): the first payload byte is a sub-op

| sub-op | payload after sub-op                                                 |
|--------|----------------------------------------------------------------------|
| `0x01` | preamp: `int32 LE` = trunc(dB × 100)                                 |
| `0x02` | band: 6 × `uint32 LE`: `index, enabled, type, freq_hz, Q×100, gain_dB×100 (signed)` |
| `0x03` | global PEQ enable: `uint8` 0/1                                       |

Example: band 0, enabled, peaking, 1000 Hz, Q 1.41, +3.5 dB

```
05 07 00 00 00 02 | 00000000 01000000 01000000 e8030000 8d000000 5e010000 | 00…
```

### GET (cmd 9): payload is a single selector byte

| selector | reply (after the `0x05` report id byte)                           |
|----------|-------------------------------------------------------------------|
| `0..9`   | `02` + band: 6 × `uint32 LE`: `freq, enabled, type, index, Q×100, gain×100` |
| `0x0A`   | `01` + preamp: `int32 LE` dB × 100                                |
| `0x0B`   | `03` + enable: `uint8` (1 = on)                                   |

(The "reply" column shows what follows the status byte. The first byte is the matching SET sub-op.)

The swap of `index` and `freq` compared with the SET order is real firmware
behaviour. It's confirmed on hardware: the test preset `presets/test-bass-shelf.txt` read back
as band 0 = 100 Hz, on, low shelf, index 0, Q 0.71, +6.00 dB, which is these bytes after the sub-op:
`64 00 00 00 | 01 00 00 00 | 00 00 00 00 | 00 00 00 00 | 47 00 00 00 | 58 02 00 00`.

These indices match the Android app's constants `BAND0=0, PREAMP=10, PEQ_ENABLE=11`.

### Value ranges (from the Qt UI)

- 10 bands. Defaults: 31, 62, 125, 250, 500, 1k, 2k, 4k, 8k, 16k Hz
- type: `0` Low Shelf, `1` Peaking, `2` High Shelf
- freq 30–20000 Hz in the Mac UI, but the device stores lower values: a 28 Hz band was read back from a real W4. The tools allow 20 Hz. Q 0.2–20, gain −9…+9 dB

The app also previews the curve with RBJ-cookbook biquads (`PeqTranClass::peaking`,
`lowShelf`, `highShelf`, using `10^(g/40)` and `w0 = 2π f / fs`). On the Airoha
path, only the parameters are sent. The device computes the coefficients itself.

### Persistence

Verified on a W4: settings sent with cmd 7 (SET) are kept after unplugging and
replugging USB. No separate save step is needed.

### "Write PEQ to device" (cmd 8, not implemented) ⚠

It doesn't seem to be needed (see above). HiBy's app builds one blob (`PeqTranClass::makeAllPeqData`):

```
[enabled u8][preamp int32 ×100][10 × band]
band = [index u8][enabled u8][type u8][freq u32][Q×100 u32][gain×100 i32]   (15 bytes)
```

That makes 155 bytes. `setAirohaCmdData` memcpy's it into a fixed stack buffer and
sends one 64-byte report, so chunking is unclear. **Capture this with USB sniffing
before you try it.**

## Other backends and protocols seen

- **TTGK** (USB dongle DACs). Biquad coefficients are computed on the host and sent
  as 5 × float32, plus freq/Q/gain bytes.
- **Android "V2"** (now-playing, lyrics and cover art for devices with screens). Report
  ID 5, 62-byte packets: `[5][cmd 4B][data ≤55B]…[xor-checksum u16 BE @60]`, where the
  XOR covers bytes 0..59. Reply status is at byte 5, data from byte 6. Device info:
  cmd `00000000`, data `[02, key]`. A JSON reply carries key `"5"` = protocol version
  and key `"6"` = public key.
- **"Authorization"** is only device → host: the host sends 24 random bytes, and the
  device returns an ECDSA secp192r1 signature that the *app* verifies. The host needs
  no secret, so third-party tools are not blocked.
