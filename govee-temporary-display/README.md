# Govee Temporary Display

Reusable, volatile image/animation transport for Govee H6631 pixeld (52×32, RGB, UDP 7777). **No specific visual effects are built into the add-on.**

The controller has an authenticated Home Assistant Ingress API:
- `POST /api/show`: JSON `{"rgb_base64":"...", "timeout":30}` for a static image (4992 bytes)
- `POST /api/play`: JSON `{"frames_zlib_base64":"...", "fps":12, "loop":false}` for a packed, zlib-compressed sequence of consecutive 4992-byte RGB frames. Defaults to stopping at end of sequence. Up to 2400 frames, 1–12 fps, request under 4 MB. Optional timeout in seconds and loop=true.
- `POST /api/heartbeat`: JSON `{"timeout":30}` extends an existing display; naturally ending nonlooping sequences are not repeated.
- `POST /api/clear`: JSON `{}` stops streaming immediately.
- `GET /api/status`: transport state.

The add-on streams frames at 12 Hz while active and sends **nothing** when idle, leaving pixeld to return to its selected Idle Black animation after about three seconds.

Animations are generated outside the add-on and kept in RAM only for the duration of playback. No named animation endpoints, effect presets, file uploads or persistent image storage. The built-in browser visualiser is optional and independent of the transport API. Don't expose the service's port to the internet.
