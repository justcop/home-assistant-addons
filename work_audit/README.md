# Work Audit

A first observation dashboard for EMIS Helper activity and the phone information already in Home Assistant. It records from installation onwards. It does not import existing Home Assistant history, score productivity, or send nudges.

## Install

This add-on lives in the same repository as Vinyl Guardian:

`https://github.com/justcop/home-assistant-addons`

Refresh the repository in Home Assistant's add-on store, install **Work Audit**, configure it, then start it and open its web UI. Supports amd64 and aarch64 Home Assistant installations with Supervisor.

1. Set the MQTT host, port and credentials for the broker that receives `work/monitor/status` and `work/monitor/idle`. The default is the local Mosquitto add-on, `core-mosquitto:1883`. No MQTT publishing permission is required; grant subscribe/read access to these two topics.
2. Update and restart EMIS Helper so it sends status as well as idle every 30 seconds. The accompanying Helper change is at `justcop/Helper`, in `Lib/regularchecks.ahk`.
3. Open the dashboard's **Phone and location setup** section and search for your phone screen entity and `person` or `device_tracker` entity. Copy their IDs into `phone_screen_entity` and `location_entity` in the add-on Configuration tab, then restart.
4. Set `work_location_states` to the exact state your location entity reports at the surgery, for example `Burnley Medical Practice`. This is a state name, not a `zone.*` entity ID. Multiple workplaces are supported.
5. Set `phone_screen_on_states` and `phone_screen_off_states` to the actual states your phone entity uses. For a binary sensor, the defaults `on` and `off` work. Other values remain unknown. Pick a screen-interactive sensor if available. A locked/unlocked sensor measures lock state, which differs from screen illumination.
6. Add explicit browser title/context fragments to `useful_context_patterns` and `distracting_context_patterns`. Matching is case-insensitive, literal substring matching, not regex. Both matching makes a page ambiguous. Unmatched pages stay unclassified. Review defaults against your actual titles.

The phone collector uses the Supervisor-provided API token automatically. There is no additional Home Assistant token to enter. It subscribes to existing Home Assistant state updates. It never requests a new location fix, changes companion-app sensors, or changes phone polling frequency.

For the existing external endpoint, use `mqtt_host: mqtt.justcop.co.uk`, `mqtt_port: 443`, `mqtt_tls: true`, `mqtt_transport: websockets`, and the WebSocket path your broker uses, normally `/mqtt`. Prefer the local broker endpoint if it is the same broker.

## Dashboard

* Date picker, work-time-only totals, full-day or observed-hours timeline, and daily CSV export.
* Independent computer context, idle, phone screen, location, work session, patient record, and phone/idle overlap tracks. Totals overlap between tracks and must not be added together as a partition of the day.
* Clinical application, notes editor active, useful and distracting browser time. Editor active does not mean actual typing. The current Helper feed cannot distinguish keyboard from mouse input or quantify keystrokes.
* Idle is estimated between the existing 30-second samples, using `idle_threshold_seconds` (default 120). Reading, thinking, speaking to a patient and calls may all be idle. New physical activity is only visible at the next sample.
* Phone screen on while computer idle at work is labelled **possible phone use**. It does not establish distraction or whether a call is clinical.
* Patient episodes show a loaded record, including record review and discussions. Daily `Record 1`, `Record 2` labels hide the supplied hash in the dashboard and CSV. Repeated appearances of the same hash use the same label that day. No attempt is made to infer when a patient is physically present.
* Manual **At work**, **Break**, **Away**, or **Use location** session controls. These affect future time only and reset to location mode on restart. They do not alter Home Assistant's entities.

Select a timeline segment for its browser/app context and times. Location transitions provide approximate arrival and departure boundaries as reported by Home Assistant, not exact physical arrival times. When phone or location reports are delayed, the dashboard inherits that delay. The collector being connected does not guarantee that the phone itself is reporting promptly.

## Data and timing

The SQLite database is `/data/work-audit.sqlite3`, in the add-on's persistent storage. Normal stop/start, rebuilds and add-on updates preserve it. Uninstalling and deleting add-on data removes it. Home Assistant backups include this data; cold backup mode stops the add-on for a consistent backup. `retention_days` defaults to 90, with older events pruned hourly. Export individual days before they expire if needed.

Selected screen/location states are stored without GPS coordinates or other HA entity attributes. Other entities are briefly read to populate the setup search but are not persisted. MQTT app/context and supplied patient hashes are stored locally. Browser titles may contain sensitive text. Hashes are pseudonymous, not an anonymity guarantee. The CSV includes app/context; review before sharing it.

Data is timed from add-on receipt, using UTC timestamps internally and `Europe/London` for display and day boundaries. British daylight-saving changes are handled, including 23- and 25-hour days. The timeline clock runs every five seconds, so duration boundaries are approximate. The existing MQTT messages have no source timestamp. Buffered or duplicate messages can distort timing, particularly after a broker outage. Historical accuracy requires a source-timestamped, lossless producer in a later version; the existing AHK inbox can overwrite an unread message after four seconds.

Fresh status and idle messages expire separately after `heartbeat_timeout_seconds` (default 90). Broker disconnects clear computer state immediately. Retained status messages are ignored because they have no trustworthy observation time. Home Assistant disconnects make phone/location unknown until a fresh state snapshot arrives. Add-on downtime and pauses longer than 15 seconds are represented as missing coverage; old activity is never extended across a restart. Phone states are the currently reported states, not proof of fresh sensor sampling.

No public port is exposed. The web UI accepts only Home Assistant's ingress gateway, whose user authentication protects access. MQTT transport can be configured for verified TLS and WebSockets. Credentials are add-on configuration, not part of the database, exports, or logs.

## Development

```
python -m pip install -r work_audit/requirements.txt
PYTHONPATH=work_audit python -m unittest discover -s work_audit -p 'test_*.py' -v
```

Tests cover overlap, independent expiry, disconnects, restart gaps, DST, work filters, record aliases, ingress, CSV escaping and invalid inputs. Run the add-on in Home Assistant to verify the real broker and phone entities. This initial release has not been installed into Justin's Home Assistant from this development environment.
