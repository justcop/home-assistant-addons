# Govee Temporary Display

Temporary RGB frame streaming for a Govee H6631 running pixeld custom firmware. No animation files are created.

The Govee's normal mode should be the Idle Black animation (already configured on this display). pixeld automatically restores it approximately 3 seconds after UDP streaming stops.

## API (Home Assistant authenticated Ingress, no external port)

- GET /api/status
- POST /api/show: JSON {"rgb_base64":"...", "timeout":30}. The decoded base64 should be 4992 RGB bytes, 52x32, row-major.
- POST /api/heartbeat: JSON {"timeout":30} extends the existing frame; repeat to keep it indefinitely.
- POST /api/clear: JSON {} stops streaming, returns to black after ~3s.

Frames are sent to UDP port 7777 twice a second only while active. Expiry is maintained by the service and a restart cancels the image.

Do not expose port 8098 publicly. Only use authenticated Home Assistant Ingress.

## Installation
Install Govee Temporary Display from the justcop/home-assistant-addons repository and start it.
