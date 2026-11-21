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

## Reactive proof of concept

`POST /api/reactive/start` with `{"timeout":30}` starts live microphone capture on the Govee using the proven pixeld command `arecord -q -D hw:0,0 -f S16_LE -r 16000 -c 1 -t raw` over SSH. `GET /api/audio/status` returns audio metrics and errors. `POST /api/clear` stops it. This first renderer is a psychedelic test pattern only; future renderer functions can consume the same features without modifying transport.

**SSH setup:** provide a private key already authorised to access `root@192.168.1.47` at `/share/govee/id_ed25519` on Home Assistant (or adjust the option). Store securely, restrict file permissions, do not put its contents in repository or responses. SSH host fingerprints are recorded in `/data/known_hosts` on first connection. Audio stays in RAM and is discarded after analysis. The microphone must not be concurrently owned by pixeld's built-in Music mode; use Idle Black animation.

### Safer one-time pairing

The add-on now generates its own private SSH key under `/data/ssh/id_ed25519` and never exposes it. Call `GET /api/audio/ssh-public-key` to retrieve its **public** key, then authorise it once in the Govee device's root `authorized_keys` using the previously working Windows SSH connection. No private key needs transferring to Home Assistant.

## Generic music-reactive scene API

`POST /api/reactive/start` accepts `{"timeout":30,"renderer":{...}}` where `renderer` is either `"psychedelic"` (legacy demo) or a declarative particle scene. No executable Python is accepted over HTTP. The microphone/SSH and transport are independent of scene rendering; adding a new particle design requires only new JSON, not an add-on release.

Example animated rocket-and-burst configuration:

```json
{"timeout":10,"renderer":{"type":"particles","emitters":[{"trigger":"both","interval":0.16,"count":4,"origin_x":0.5,"origin_y":1,"spread_x":0.95,"velocity_y":-20,"velocity_variance":7,"gravity":6,"lifetime":0.7,"children":48,"child_speed":18,"child_gravity":10,"child_lifetime":1.1,"beat_boost":3,"energy":"bass","palette":[[255,56,90],[255,197,38],[47,194,255],[178,78,255],[53,255,137]]}]}}
```

The generic renderer provides tunable position, spread, velocity, lifetime, gravity, colour palette, music-triggered/periodic emission, beat intensity and child bursts. Nothing is saved on the Govee. Existing `/api/show`, `/api/play`, `/api/clear` remain available.

## Automatic Home Assistant countdown display

This is a generic state-driven countdown, not a GTA-specific API. A saved binding connects an active-state entity and a deadline entity to a pixel-art layout. The add-on polls the HA Core states API locally using its Supervisor token (requires `homeassistant_api: true`), renders a large hours-and-minutes countdown (for example `1H 22M`, rounded up to whole minutes) and releases the display after the session ends. The watcher still tracks the exact deadline in seconds internally. It does not change any gaming controls or state.

Configure once via `POST /api/countdown/configure`:

```json
{"state_entity":"input_select.gaming_session_state","deadline_entity":"input_datetime.gaming_session_deadline","active_states":["active"],"title_lines":["GRAND","THEFT","AUTO"],"mark":"VI"}
```

`GET /api/countdown/status` reports the binding, source errors and remaining seconds. Countdown uses the existing UDP pipeline; manual temporary animations and reactive playback take priority while they run, then the countdown resumes if the session remains active. Inactive sessions are black. The configured binding persists in the add-on data area.

### Countdown display preview

`POST /api/countdown/preview` with `{"remaining":3600,"timeout":10}` renders the configured title and a mock remaining time for up to 30 seconds, without touching the real gaming helpers. The live countdown watcher takes over on the next active session.
