# Listening Analytics

A Home Assistant add-on that turns your Last.fm history into a clear, local analytics dashboard. It imports your available scrobbles, combines song and album versions, and helps you explore how your listening changes.

## What you can do

- Explore an overview, trends, artists, albums, songs and a searchable daily listening diary.
- Compare date ranges, see new versus familiar artists, inspect your listening by weekday and hour, and find artists returning after a long gap.
- Click chart bars or ranking entries to see the underlying scrobbles.
- Switch between original Last.fm entries and combined song or album totals.
- Merge groups, separate individual versions and undo decisions. The original scrobbles remain intact.
- Use five colour palettes in light or dark mode, with a phone interface and an installable HTTPS web app.
- Scroll and zoom long timelines, select individual calendar years, and explore year/month heatmaps for your library, artists, albums and songs.
- Review proposed merges, skipped candidates and existing merges; optionally learn an exact artist-specific suffix from an approved suggestion.
- Try a clearly labelled fictional demo before connecting an account. Demo data uses its own database and makes no Last.fm requests.

## Install

1. In Home Assistant, open **Settings → Add-ons → Add-on Store → Repositories**. Recent Home Assistant versions may call these Apps.
2. Add `https://github.com/justcop/home-assistant-addons`.
3. Find **Listening Analytics** and install it.
4. Enter your Last.fm username and an [API key](https://www.last.fm/api/account/create) in **Configuration**. No Last.fm password is required.
5. Start the add-on and select **Open Web UI**. Enable **Show in sidebar** for convenient phone access.

The add-on does not install or change other add-ons. Home Assistant Ingress is enabled by default. An optional direct web port is disabled by default and requires a configured web password. The container supports `amd64` and `aarch64`.

## Configuration

```yaml
username: your_lastfm_username
api_key: your_lastfm_api_key
timezone: Europe/London
sync_interval_seconds: 300
reconcile_days: 7
demo_mode: false
web_password: ""
```

| Option                  | Default         | Meaning                                                                                     |
| ----------------------- | --------------- | ------------------------------------------------------------------------------------------- |
| `username`              | empty           | Last.fm account to import.                                                                  |
| `api_key`               | empty           | Last.fm application API key, hidden in the configuration UI.                                |
| `timezone`              | `Europe/London` | IANA display timezone, including daylight-saving changes.                                   |
| `sync_interval_seconds` | `300`           | Regular sync interval, between 60 and 86,400 seconds.                                       |
| `reconcile_days`        | `7`             | Recent days rechecked daily for edits, deletions and delayed submissions, between 2 and 90. |
| `demo_mode`             | `false`         | Run the isolated fictional demo without making Last.fm requests.                            |

`web_password` enables the optional direct login and must contain 12 to 256 characters. Leave it blank to block direct access.

Save configuration and restart after changes. Username changes open a separate account database; they do not mix histories. Changing back restores that account's existing import and grouping decisions. Changing an API key keeps its account history.

## Import and sync

Initial import finds the oldest available play, fixes an upper timestamp, then walks back through 30-day windows. Every window is paginated at up to 200 scrobbles per request and read twice. Matching event counts, order, page metadata and event multiplicities are required before a window is committed. Both data and the resume cursor are saved in the same transaction. If interrupted, already committed windows remain and the interrupted window restarts.

All Last.fm requests are paced to at most one request per second, with bounded retries and backoff for rate limits and temporary failures. Large histories can take a while: reading 100,000 plays twice needs roughly 1,000 requests, plus window overhead. Progress is shown as saved plays and the fraction of the date span checked, not a predicted finish time.

Normal sync overlaps the previous two days. Once a day it revisits the configured recent window. If the add-on was offline longer, it fills the entire gap in bounded chunks. A remote edit or deletion is applied only after two complete matching reads; removed entries are archived locally and excluded from analytics. Last.fm has no transactional snapshot or event identifiers, so two identical reads are a consistency check, not an absolute guarantee against all possible concurrent edits.

Each event is identified by its timestamp and original artist, title and album, plus an occurrence number. This preserves repeated plays and even multiple identical scrobbles at the same second. It also makes reimports idempotent. Now-playing status is kept separate and never adds to play counts.

The dashboard checks the local sync status every 15 seconds while visible. It does not poll Last.fm separately. The **Sync** button requests a sync, coalesces repeated clicks and respects a one-minute minimum. Now playing is sampled during sync and expires if stale, so it is an indication rather than a realtime player.

Edits and delayed submissions older than the configured reconciliation window are not automatically rediscovered. Increase `reconcile_days` temporarily, up to 90, to cover recent bulk edits. A full historic rebuild would require a fresh account database; preserve a backup first if retaining manual grouping decisions matters.

## Grouping rules

Original scrobble names and JSON are retained. Grouping is a local mapping over them.

- Recognised whole remaster suffixes combine by the same artist, for example `Come Together (2009 Remaster)` and `Come Together`.
- Album groups also recognise simple deluxe, expanded and special-edition suffixes.
- Case, Unicode compatibility forms, curly apostrophes and repeated spaces normalise for automatic matching. Punctuation with potential meaning is retained.
- Live, acoustic, mix, remix and other unrecognised qualifiers stay separate. Different artists are never automatically or manually combined.
- Open **Settings & grouping** for **Suggestions**, **Skipped candidates** and **Already merged**, with song/album selection and search. This view does not enumerate every unmerged song.
- Suggestions compare the same artist and base title with suffix or punctuation differences. They are candidates, not proof of identical recordings. Skipped candidates explain retained live/mix qualifiers and rejected suggestions. Entries with no plausible match are omitted.
- **Merge these** combines a suggested or skipped pair. **Keep separate** remembers your rejection. **Reconsider** restores a dismissed suggestion. You can inspect merged groups and separate individual versions.
- **Merge and learn suffix** is offered for a single unprotected suffix with an exact matching base title. It remembers that suffix only for the selected artist and song/album type, for future imported entries with a corresponding base entry. It never learns live, acoustic or mix qualifiers. Other artists remain unaffected.
- **Undo latest change** reverses manual decisions in reverse order. It also removes any rule learned by that decision and restores later versions assigned by that rule.

A song can be grouped across different albums. Album identity is based on the scrobbled track artist and album name. Compilation releases may therefore appear under several artists; this version does not infer a missing album artist.

## Interpreting the analysis

- A play is a dated scrobble. It is not measured listening time. Duration estimates and full-album-session claims are deliberately not produced without adequate evidence.
- Discovery means an artist's first appearance in the complete available imported history, not necessarily the first time you heard them in your life. Discovery and percentage comparisons wait for initial import to complete.
- Comparisons use the immediately preceding range of the same elapsed length. Today's partial day is included only up to the query time. All-time views do not have a previous-period percentage.
- A rediscovery is the first play within the selected period after at least 90 days since the most recent earlier recorded play by that artist. During initial import, the interface notes that this is based on partial history.
- Artist concentration is the fraction of selected plays attributable to the five most played artists.
- Days and heatmap hours use the configured timezone. Both occurrences of a repeated daylight-saving hour share the same heatmap cell.
- Missing album metadata is shown in data quality and excluded from album counts. It is never guessed.

## Storage, backups and security

Data lives in account-specific SQLite files under the add-on's persistent `/data`, with WAL mode and indexed dates, artists and versions. Configuration lives in Home Assistant's `/data/options.json`. Database and grouping changes persist across restarts and upgrades. The add-on requests cold backups so SQLite is stopped cleanly before Home Assistant copies its data. Back up the add-on before uninstalling or removing its data.

The production UI accepts only the Home Assistant Ingress gateway at `172.30.32.2`. No Supervisor API, host networking, mapped host directories or elevated capabilities are requested. Mutating requests require a per-process CSRF token. API keys stay on the server and are not returned by the UI or included in sync error messages. `/health` exposes only application health and version for the watchdog.

## Local development and tests

Python 3.12 or newer is recommended. No account is needed for tests or the demo.

```sh
cd lastfm_analytics
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
.venv/bin/python -m analytics --development --data-dir ./dev-data --port 8105
```

Open `http://127.0.0.1:8105/` and choose **Explore a fictional demo**. Development access is loopback only. To use a real test account, create `dev-data/options.json` using the configuration example; this directory is ignored by Git. Never commit API keys.

Browser tests use Playwright and an empty development instance running on port 8105:

```sh
npm install --no-save playwright
npx playwright install chromium
node tests/ui.cjs
```

Optional environment variables: `APP_URL`, `CHROMIUM_PATH`, `PLAYWRIGHT_MODULE` and `SCREENSHOT_DIR`. The test uses fictional data, checks the rendered user journeys and saves desktop light, desktop dark and mobile screenshots to a temporary directory by default.

The fixture suite covers interrupted multi-page import, duplicate multiplicity, timestamp boundaries, offline catch-up, atomic reconciliation, concurrent source changes, reversible grouping across imports, conservative title rules, daylight-saving dates, analytics, now playing, rate limiting, safe error handling and Ingress/CSRF isolation. Browser checks cover setup, demo labelling, chart drill-down, search, merge/separate/undo, mobile layout, theme switching and safe text rendering.

This first version has been tested with fixtures and a local browser. It has not yet been run inside Home Assistant or against a live Last.fm account; those are the remaining integration checks.

## References

- [Last.fm recent tracks API](https://www.last.fm/api/show/user.getRecentTracks)
- [Home Assistant add-on configuration](https://developers.home-assistant.io/docs/add-ons/configuration/)
- [Home Assistant Ingress](https://developers.home-assistant.io/docs/add-ons/presentation/#ingress)

## Phone and remote access

The phone layout has persistent bottom navigation, large touch controls, safe-area spacing and light/dark themes. Use it in your phone browser or the Home Assistant Companion app.

For a standalone web app address:

1. Set **Web login password** (`web_password`) in the Home Assistant add-on configuration. Use a unique password of at least 12 characters. Save and restart.
2. In the add-on **Network** section, assign a host port to `8099/tcp`, for example `8109`. The port is disabled by default.
3. Put an HTTPS reverse proxy in front of that port, forwarding to `http://YOUR_HA_IP:8109`. Only the HTTPS proxy should be reachable from the internet. Do not publish the plain HTTP port to the internet.
4. Open your HTTPS address on your phone, sign in, then use your browser's **Install app** button when offered, or your browser’s **Install app** or **Add to Home Screen** option.

Production login cookies require HTTPS. Login over plain HTTP will not persist. A login lasts seven days; changing the password and restarting invalidates existing logins. Sign out is available in the page header. Failed attempts are limited to ten per client address in ten minutes, with an overall limit on password checks. Behind a reverse proxy, clients may share the proxy's limit. The app ignores forwarded IP headers for authentication and rate limits.

Home Assistant Ingress continues to use your existing Home Assistant login and does not ask for the separate password. Ingress session URLs can expire, so use Home Assistant or its Companion app sidebar for reliable access through Ingress. A separately proxied HTTPS address provides the stable URL for home-screen use.

There is no offline listening-data cache. Internet exposure always needs authentication: the dashboard contains history and can change grouping decisions or trigger syncs. Keep the container and reverse proxy updated. Password protection is implemented and tested, but this is not an independent security audit or a guarantee against every vulnerability.

## Analysis and appearance

The date selector includes calendar years present in your imported history. The current year stops at today. Timeline panels scroll horizontally within the screen; the zoom slider increases bar spacing. Dates scroll with their bars, and selecting a bar opens the underlying history.

The year/month calendar uses the selected date period and entity, with darker cells for higher monthly counts. On artist, album and song detail pages, it includes only that entity's plays. Months outside the selected period are unavailable. The existing weekday/hour heatmap remains in Trends.

Select Violet, Ocean, Forest, Rose or Amber in the header, then use the colour-theme button for light or dark mode. Both preferences persist on that browser. Phone detail sheets fill the viewport and prevent background scrolling, including while a confirmation dialog is open.

Analysis requests show a loading status. Overview and Trends reuse server-side results for up to 60 seconds, invalidated by imports or grouping changes. The first uncached analysis of a large history can still take longer; caching does not replace the original data calculations.
