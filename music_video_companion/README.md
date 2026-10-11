# Music Video Companion (0.2.2)

A **separate** Home Assistant add-on for looking up approved official music videos and sending them to a Cast-enabled TV. Spotify and AudioShelf/Vinyl Guardian do **not** need to be changed for the first version.

## How it works

- Reads current artist/title and play/pause state from an existing Home Assistant **Spotify media_player** entity, using the internal Home Assistant API. No additional Spotify OAuth login is needed.
- Searches YouTube Data API v3 for candidates when requested or when an unknown song starts in automatic mode (one search per song per session).
- **Never auto-plays an untrusted channel.** Each individual approved video can be reused automatically. You may also explicitly trust an artist's verified official channel to enable future strict-title automatic matches.
- Launches the selected YouTube video through Home Assistant's Google Cast integration. Uses `volume_mute` on the target player (which may mute the TV globally; this is not isolated YouTube-audio removal), and restores the previous mute status if it was changed by this add-on when stopped.
- Stores choices and trusted channels in the add-on's persistent `/data/library.json`, with atomic writes.
- A future integration can supply track changes through `POST /api/track` using a shared token, without tying this add-on to Spotify.

**Limitations:** This is a proof of concept for launching related videos. It is **not frame-synchronised** with Spotify, and some videos have different intros or edits. We do not download, strip or alter YouTube media. Spotify's platform developer policy prohibits synchronising Spotify recordings with visual media, and YouTube API rules restrict separation of video from audio. Review both providers' terms before enabling combined playback; the two-player experience is not represented as an officially supported integration.

## Install and configure

1. In Home Assistant add-on store, add/refresh `https://github.com/justcop/home-assistant-addons`, install **Music Video Companion**, and start it.
2. Configure Spotify and Chromecast (Google Cast) integrations in Home Assistant if not already present. The Chromecast device **must** expose a `media_player` entity capable of playing YouTube through Cast; a TV entity without Google Cast will not work.
3. Optionally create a Google Cloud YouTube Data API v3 key and enter it as `youtube_api_key` under add-on Configuration. Without a key, you can still manually save an official video ID.
4. Open the add-on sidebar, select your **Spotify media player** and **Chromecast media player**, and save.
5. Play music to your KEFs from Spotify. Press **Find official candidates** to review YouTube results. Only approve an upload you have verified came from an official artist/label/VEVO channel.
6. Turn on **Automatically cast approved matches** when ready. The add-on never turns auto mode on without you choosing it.

YouTube `search.list` is expensive (100 quota units per request). Automatic channel searches are cached per song for the running process and unresolved matches are not retried every poll. The automatic lookup deliberately prefers *no video* over a questionable match.

## Interoperability

In the first version the input source is Home Assistant's Spotify entity. A future AudioShelf or Vinyl Guardian adapter can POST to `http://<addon-internal-host>:8099/api/track` with header `Authorization: Bearer <source_api_token>` and JSON:

```json
{"artist":"The Beatles","title":"Something","state":"playing","position":12}
```

Set a nonempty `source_api_token` in the add-on configuration first, then choose the **External adapter** input in the UI. `state` can be `playing`, `paused` or `idle`. External updates expire after 30 seconds (send a heartbeat while playing). This is intentionally independent of AudioShelf/Vinyl Guardian's own logic.

## Privacy / control

No YouTube API key is sent to the browser. The UI is exposed through Home Assistant ingress, not an unauthenticated host port. The source API token is never shown in the UI. The add-on modifies **only** the explicitly selected target media player. Pausing source playback stops its own cast session. If you switch the TV to something else, the add-on will not intentionally replace it until a subsequent matching track event.

## Developer tests

```sh
python3 -m unittest discover -s music_video_companion/tests -v
```

The tests use mock Home Assistant responses and do not need network or hardware.

## Home Assistant API authentication

The add-on uses the Home Assistant Supervisor proxy, enabled by `homeassistant_api: true`. If `SUPERVISOR_TOKEN` is missing, the current installation may have been created with stale permissions. Refresh the add-on repository, upgrade to v0.2.1, and restart the add-on first. If permissions still do not appear, reinstalling the add-on after saving its persistent library may refresh the Supervisor container permissions.

If the add-on still displays a Home Assistant authentication error, create a **Long-Lived Access Token** from Home Assistant user Profile > Security > Long-Lived Access Tokens. Paste the token into the add-on Configuration field `ha_access_token`, save, and restart. This is an optional fallback for Home Assistant API access only. It is not the YouTube Data API key and not the source API token. The fallback is only sent to Home Assistant Core on the internal `http://homeassistant:8123/api` address; it is never sent to Google, YouTube or the Supervisor proxy.

To populate the dropdowns, configure the standard **Spotify** and **Google Cast** integrations under Home Assistant Settings > Devices & Services. Then use the add-on's Refresh devices button. Both integrations must expose `media_player` entities. The Samsung TV entity by itself is not a substitute for a Cast media player.

## Device without a mute action

If casting fails with `does not support action media_player.volume_mute`, update to 0.2.2. The add-on now tries the receiver's volume controls in this order:

1. Mute via `media_player.volume_mute` when supported.
2. If the receiver cannot mute, use `media_player.volume_set` to set volume to zero, but **only when its previous volume is available** so it can be restored.
3. If neither works, prevent autoplay with a clear error. Under **Mute audio using**, choose a separate media player representing your Samsung TV or Q800C soundbar (if that entity supports muting or volume adjustment).

The Chromecast entity is still the target for video playback; the optional audio entity is only for silencing sound. Never choose the KEFs playing Spotify as the mute target. The add-on restores audio after its video stops unless the user adjusted the volume manually or an unrelated app took over the Cast receiver. A physical/HDMI volume control not exposed through Home Assistant cannot be controlled by this integration.

## Manual TV muting (v0.2.3)

In the web interface, **TV sound control** defaults to **Manual — I will mute the TV myself** on both new and upgraded installations. Music Video Companion then sends no volume or mute commands and does not attempt to restore volume; you control your TV/soundbar manually. This bypasses unsupported `volume_mute` and `volume_set` actions, including on `media_player.living_room_tv_4`.

**Mute the TV before casting:** YouTube audio may play at the TV/soundbar's current volume. Playback still uses the approved official video, current-song detection, initial seek, and Spotify pause/resume handling as before.

If you later want app-controlled muting, choose **Automatic — Home Assistant controls volume**. That mode retains the safe mute/volume-zero logic and optional separate `Mute audio using` entity.

## Faster Spotify change detection (0.3.0)

Home Assistant's Spotify integration refreshes track data roughly every 30 seconds. Polling its cached entity every 3 seconds cannot make it fresher. Music Video Companion optionally requests a Home Assistant `homeassistant.update_entity` action at a bounded **10-second interval**. The `spotify_refresh_seconds` add-on setting defaults to `10` (use `0` to disable, or `10-60` seconds to tune).

The UI shows how many forced requests have been made, when the next is due, and any errors. Failed requests back off exponentially to a maximum of five minutes, and normal 3-second cached-state polling continues. A successful `update_entity` action does not guarantee Spotify's upstream coordinator supplied new data; the actual improvement must be checked on Home Assistant.

An automation reacting to the Spotify entity's state change is an option, but by itself it is not faster than the entity's source data. An event push from AudioShelf or an external source that knows new tracks immediately can be added later via the existing authenticated `/api/track` endpoint, without another Spotify poll.

## Artist-named VEVO channel approval

With automatic mode enabled and a YouTube API key configured, exactly **one** search candidate can be auto-approved if the YouTube channel's display name normalizes exactly to `<artist>VEVO` and the video headline is an exact artist/title match after common official-video suffixes are removed. Multiple candidates, unrelated VEVO channels, live shows, lyric videos, fan covers and ambiguous matches remain unapproved.

**Important:** YouTube channel display names are not proof of ownership and could be imitated; automatic VEVO recognition is heuristic, not verification of a badge or label rights. Review any surprising choice.

## YouTube adverts

The add-on now waits up to 105 seconds for the approved video's ID to appear on the YouTube Cast receiver, treating other IDs inside the YouTube app as possible pre-roll advertisements. It does not attempt to skip, block, or manipulate adverts. Initial alignment occurs when the real video becomes seekable, with the current Spotify position queried again. YouTube Premium on a signed-in compatible receiver may be required to avoid advertising; the generic Home Assistant Cast launch may not inherit a Premium account session.
