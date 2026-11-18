# Govee Palette Studio (experimental)

A self-contained Home Assistant Lovelace custom card for designing and applying Music DreamView palettes. No build process, runtime dependencies or third-party APIs. The JavaScript source is `govee-palette-studio.js`.

## Installed instance

On the user's Home Assistant instance, an inline module resource containing this code was registered and a new sidebar dashboard was created at:

`/govee-studio/palette`

The existing Music DreamView controller is **Floor Lamp 2 (H607C)**. The card's configuration is:

```yaml
type: custom:govee-palette-studio
device_id: 0f19e21af64af9e364dfde26fb48ae69
dreamview_entity: switch.floor_lamp_2_dreamview
palette_entity: input_text.govee_bilresa_random_palette
effect_entity: input_select.govee_bilresa_effect
movie_entity: switch.tv_backlight_dreamview
```

Do not add a second copy of the inline resource to the same instance. The GitHub copy is the maintainable source; subsequent changes to this file do **not** automatically update the installed inline Home Assistant resource.

## Controls

- Up to **eight distinct colours**, with 2–7-colour selections repeated across eight hardware slots.
- Eleven curated moods; analogous, complementary, triadic, split-complementary, tetradic and monochromatic harmonies.
- OKLCH-to-sRGB generation with gamut mapping (not naïve RGB/HSV), adjustable hue, chroma, lightness, spread and contrast.
- Shuffle individual unlocked colours, edit colours with an HTML colour picker, lock them, and reorder with arrows.
- Name and save up to 30 favourites. Favourites remain **browser-local**; the currently selected palette/effect are shared with IKEA BILRESA via HA helpers.
- All **36** Music DreamView effect combinations (12 Lighting Show effects, 10 Carnival effects, 14 effects with Dynamic/Soft).
- Press **Apply palette + effect** to write shared `input_text.govee_bilresa_random_palette` and `input_select.govee_bilresa_effect` helpers. The HA state-change automation applies them using `script.govee_bilresa_apply_selection` and the complete 22-effect driver, including Carnival extra frames. If the settings are unchanged, pressing Apply resends the last selection when Music DreamView is active.
- Incoming HA state updates from the IKEA remote automatically update the Studio's eight swatches, effect selection and Dynamic/Soft variant.
- Live status shows the reported Music/Movie/Off state and four available lights' power. Music colours represent **last HA-commanded colours**, never optical measurement. Movie DreamView's real-time camera-generated colours are not exposed by the integration.
- The Music on/off button invokes `script.govee_bilresa_toggle`, retaining Movie DreamView priority and lamp-power handoff.

## Captured command format

The user's Android Bluetooth HCI log from 17 Nov 2026 established that eight RGB colours are encoded as 24 bytes split between two Govee ptReal frames, both with XOR checksums:

1. `A3 00 01 02 51 08` + first **13** RGB bytes + XOR checksum.
2. `A3 FF` + remaining **11** RGB bytes + **six zero padding** bytes + XOR checksum.

The exact captured frame pair was:

```text
A3 00 01 02 51 08 FF DF 56 FF B7 4C FF F5 9D 87 CE FA FF 50
A3 FF CC 99 90 EE 90 FF B6 C1 FF EC B3 00 00 00 00 00 00 CF
```

Those decode to `#FFDF56 #FFB74C #FFF59D #87CEFA #FFCC99 #90EE90 #FFB6C1 #FFECB3`. The generated command code has been compared byte-for-byte against this capture and matches exactly.

The effect frame is `33 60 02 <group> <id> 00 <variant> 64` + eleven zero bytes + checksum. Group 1 = Lighting Show, group 2 = Carnival. The variant byte is `00` Dynamic, `01` Soft for effects that support it. Lighting Show effect IDs were captured directly. Carnival effect IDs are `20..29` (Brilliant..Shimmer); `21..29` were seen in the sweep and `20` is inferred from the sequence and first event timing.

The shared Home Assistant `script.govee_music_dreamview_all_effects` sends `govee.send_raw_ptreal` service calls targeting the controller. The Studio does **not** independently send raw commands from its Apply button. The original captured two-colour frames were accepted by the service in a live test without changing the DreamView switch state. **Real visual rendering of newly generated colours still needs user confirmation**; service acknowledgement alone is not visual validation.

`switch.floor_lamp_2_dreamview` on/off was separately verified via Home Assistant and by the user observing the synchronised lights.

## Updating and testing

The inline resource can be updated via the Home Assistant dashboard resource API. Source stays in this GitHub folder. Do not alter existing DreamView scripts or Node-RED capture helpers to install this card.

Smoke tests with mocked Home Assistant browser APIs verified the component instantiates and renders; current palette/effect sync from HA helper states; Music/Movie state labelling; manual Apply writes shared HA helpers rather than raw device frames; repeat Apply calls the common HA apply script; and Music toggling uses the existing priority script. The actual browser rendering could not be captured (dashboard screenshot feature is disabled).

Never claim support for all Govee device models. The live protocol was captured on this H607C controller. Custom-colour support may differ for other devices or scenes.
