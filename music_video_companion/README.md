# Music Video Companion (0.1.0)

A **separate** Home Assistant add-on for looking up approved official music videos and sending them to a Cast-enabled TV. Spotify and AudioShelf/Vinyl Guardian do **not** need to be changed for the first version.

## How it works

- Reads current artist/title and play/pause state from an existing Home Assistant **Spotify media_player** entity, using the internal Home Assistant API. No additional Spotify OAuth login is needed.
- Searches YouTube Data API v3 for candidates **only when requested**, or for new songs from a previously approved artist channel while automatic mode is enabled.
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
