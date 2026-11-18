# Govee Music DreamView Sync Center — reverse-engineering notes (H607C)

**Status:** Experimental device-specific findings, not a production-wide compatibility claim.

**Captured:** 17 November 2026, Govee Home Android app, Bluetooth HCI/ATT writes while selecting Music DreamView effects on an H607C Floor Lamp 2 acting as Music DreamView Sync Center.

**Purpose:** Document the **multi-device Music DreamView** command family, which is different from the H607C's own single-device microphone mode. No MAC address, user identifier, account token, network address, phone log, or other private information is in this document.

## Evidence and confidence

| Finding | Evidence | Confidence |
|---|---|---|
| 12 Lighting Show names and 10 Carnival names | Govee Home UI and captured effect IDs | High |
| Dynamic/Soft availability | Govee Home UI; selected frames exhibit differing variant byte | High for UI availability, moderate for effect rendering across all combinations |
| `33 60 02` group/effect structure | Captured HCI command frames | High on this device |
| Eight colours encoded as two `A3` frames | Exact captured 20-byte frame pair and independent byte-for-byte encoder verification | High for the example |
| Sending with `ptReal` | Home Assistant's `govee.send_raw_ptreal` accepted frame submissions | High for submission, **not** optical verification |
| Carnival-specific `A3 55 02` profile frames | Extracted capture mapping, preserved in local HA script | Moderate; not every combination has independent visible confirmation |
| Synchronised group on/off | H607C DreamView switch; visibly observed operation | High |
| Broader SKU support | Not tested | Unknown |

The following are **not** claims: that every one of the 36 combinations has been visually verified; that another Sync Center SKU uses identical bytes; that Govee pads fewer than eight requested colours in the same way as our HA scripts; or that the state displayed in Home Assistant is a readback of the RGB actually emitted by the LEDs.

## 1. Music DreamView effect selection

20-byte command:

```text
33 60 02 GG II 00 VV SS 00 00 00 00 00 00 00 00 00 00 00 CC
```

- `GG`: group: `01` Lighting Show, `02` Carnival
- `II`: effect ID from the table
- `VV`: `00` Dynamic, `01` Soft when the Govee Home app presents both
- `SS`: music sensitivity (`64` = 100 in these captures)
- `CC`: XOR of preceding 19 bytes

| Group | Hex ID | Effect | Variants visible in Govee Home |
|---|---|---|---|
| Lighting Show | 00 | Rhythm | Dynamic / Soft |
| Lighting Show | 01 | Sprouting | Dynamic / Soft |
| Lighting Show | 02 | Shiny | Dynamic / Soft |
| Lighting Show | 03 | Beat | Dynamic / Soft |
| Lighting Show | 04 | Waves | Single |
| Lighting Show | 05 | Spectrum | Single |
| Lighting Show | 06 | Rolling | Single |
| Lighting Show | 07 | Starlight | Single |
| Lighting Show | 08 | Piano Keys | Single |
| Lighting Show | 09 | Jumping | Single |
| Lighting Show | 0A | Luminous | Single |
| Lighting Show | 0B | Hopping | Single |
| Carnival | 20 | Brilliant | Dynamic / Soft |
| Carnival | 21 | Pulsate | Dynamic / Soft |
| Carnival | 22 | Dazzle | Dynamic / Soft |
| Carnival | 23 | Fascination | Dynamic / Soft |
| Carnival | 24 | Aspiring | Dynamic / Soft |
| Carnival | 25 | Cadence | Dynamic / Soft |
| Carnival | 26 | Revel | Dynamic / Soft |
| Carnival | 27 | Fluctuate | Dynamic / Soft |
| Carnival | 28 | Funny | Dynamic / Soft |
| Carnival | 29 | Shimmer | Dynamic / Soft |

**Count: 16 Lighting Show choices + 20 Carnival choices = 36.**

Examples (hex, no identifiers):

```text
Rhythm Dynamic: 3360020100000064000000000000000000000034
Rhythm Soft:    3360020100000164000000000000000000000035
Waves:          3360020104000064000000000000000000000030
Shimmer Soft:   336002022900016400000000000000000000001f
```

The first three example frames are capture-derived; Shimmer Soft is constructed from the inferred effect/variant encoding. The UI deliberately does **not** expose Soft for the remaining eight Lighting Show effects, even if the raw protocol can carry the bit.

## 2. Eight-colour palette

Eight 24-bit RGB values are concatenated to 24 raw bytes, carried over *two* `ptReal` packets:

```text
A3 00 01 02 51 08 [first 13 RGB bytes] [xor checksum]
A3 FF [remaining 11 RGB bytes] 00 00 00 00 00 00 [xor checksum]
```

20 bytes per frame. The checksum makes the XOR of all 20 bytes equal to zero.

Captured colour values:

```text
#FFDF56, #FFB74C, #FFF59D, #87CEFA,
#FFCC99, #90EE90, #FFB6C1, #FFECB3
```

Captured frame pair:

```text
A3 00 01 02 51 08 FF DF 56 FF B7 4C FF F5 9D 87 CE FA FF 50
A3 FF CC 99 90 EE 90 FF B6 C1 FF EC B3 00 00 00 00 00 00 CF
```

Both frames reproduce the capture byte-for-byte using the following reference code. It is transport-agnostic; sending is a separate task.

```python
def append_xor_checksum(data: bytes) -> bytes:
    assert len(data) == 19
    checksum = 0
    for b in data:
        checksum ^= b
    return data + bytes([checksum])


def palette_frames(colors: list[str]) -> tuple[bytes, bytes]:
    """Input: exactly eight '#RRGGBB' colours."""
    assert len(colors) == 8
    rgb = b"".join(bytes.fromhex(color.lstrip("#")) for color in colors)
    assert len(rgb) == 24
    first = append_xor_checksum(bytes.fromhex("a30001025108") + rgb[:13])
    second = append_xor_checksum(bytes.fromhex("a3ff") + rgb[13:] + bytes(6))
    return first, second


def effect_frame(group: int, effect_id: int, variant: int = 0, sensitivity: int = 100) -> bytes:
    assert group in (1, 2) and variant in (0, 1) and 0 <= sensitivity <= 100
    return append_xor_checksum(
        bytes([0x33, 0x60, 0x02, group, effect_id, 0, variant, sensitivity]) + bytes(11)
    )


COLORS = [
    "#FFDF56", "#FFB74C", "#FFF59D", "#87CEFA",
    "#FFCC99", "#90EE90", "#FFB6C1", "#FFECB3",
]
first, second = palette_frames(COLORS)
assert first.hex() == "a30001025108ffdf56ffb74cfff59d87cefaff50"
assert second.hex() == "a3ffcc9990ee90ffb6c1ffecb3000000000000cf"
assert effect_frame(1, 0).hex() == "3360020100000064000000000000000000000034"
```

In our Home Assistant helper, 1–7-colour user input is repeated to populate eight slots. That repeat strategy is *our own implementation*.

## 3. Carnival's extra configuration frames

Some Carnival effects send additional configuration data as `A3 00 01 02 55 02...` and a follow-up `A3 FF...` before the `33 60 02 02...` effect frame. These frame bytes are preserved from the local HCI mapping. They are provided as candidate fixtures; this mapping has **not** been revalidated visually across all variants.

| Carnival effect | 20-byte config frame (continuous hexadecimal) |
|---|---|
| Brilliant | `a3000102550203030500000000000000000000f2` |
| Pulsate | `a3000102550207040102000000000000000000f7` |
| Dazzle | `a3000102550206040500000000000000000000f0` |
| Fascination | `a3000102550209040400000000000000000000fe` |
| Aspiring | `a3000102550205030500000000000000000000f4` |
| Cadence | `a3000102550205040500000000000000000000f3` |
| Revel | `a3000102550201010101000000000000000000f7` |
| Fluctuate | `a3000102550209030400000000000000000000f9` |
| Funny | `a3000102550206030400000000000000000000f6` |
| Shimmer | `a3000102550209040400000000000000000000fe` |

Completion frame:

```text
a3ff00000000000000000000000000000000005c
```

Every listed frame is 20 bytes and has XOR checksum zero. The same configuration frame appears for both Fascination and Shimmer in the captured mapping; do not assume the frame *alone* identifies an effect.

## 4. Multi-device controller state and limitations

With an H607C as Music DreamView Sync Center, `switch.<h607c>_dreamview` in [lasswellt/govee-homeassistant](https://github.com/lasswellt/govee-homeassistant) successfully starts/stops Music DreamView. Separately, an H605C TV camera controller exposes `switch.<h605c>_dreamview` for Movie DreamView. The two switches act on distinct controllers.

A Home Assistant automation can impose a **user-defined** priority (Music > Movie), TV-power gating and sun conditions. These are automation policies, not a packet-level behaviour demonstrated by this research.

**Output-state limitation:** For dynamic DreamView, the integration can report the selected control state, but not the continuously changing colours physically shown across synchronised lights. Even a command that is accepted by `ptReal` need not have been rendered optically.

## Related work

- [Protocol reference in lasswellt/govee-homeassistant](https://github.com/lasswellt/govee-homeassistant/blob/main/docs/govee-protocol-reference.md)
- [govee2mqtt-extended: standalone music mode with 1–7 colours](https://github.com/florianhorner/govee2mqtt-extended/blob/main/docs/MUSIC_MODE.md)
- [Community govee2mqtt issue #705: standalone music-mode palettes](https://github.com/wez/govee2mqtt/issues/705)

This document should be updated if the maintainer or an independent hardware tester confirms further frames, especially the Carnival configuration and all 36 variants.
