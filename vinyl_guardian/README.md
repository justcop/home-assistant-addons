# Vinyl Guardian

## Vinyl listening statistics

Listening Analytics stores the source of Guardian's accepted Last.fm scrobbles.
Set `listening_analytics_url` to its direct local base URL and
`listening_analytics_token` to its `source_api_token`, then restart. Both add-ons
must use the same Last.fm account. Guardian queues unacknowledged notifications
in `/data`, retries in the background during outages, and deletes them on delivery.
The listening database and Vinyl only filter live in Listening Analytics.
See [connection setup](../lastfm_analytics/README.md#vinyl-attribution).


### Diagnostic audio storage

New experiment event clips are stored as lossless 16-bit FLAC in
`experiments/event_audio` under the configured recording directory. Sample rate,
channels and PCM samples are preserved. Frame traces and JSON metadata remain
alongside each clip. Existing WAV clips remain supported by the review player,
diagnostic ZIP exports, retention rules and offline replay/regression tools.
Existing recordings are not converted. Calibration and continuous dataset WAV
recordings retain their existing format.


While music is playing, every recognised track receives a periodic Shazam check
at 30-second intervals using the latest ten seconds of audio, regardless of
whether its duration is known or it has already scrobbled. A matching identity
preserves the playback clock and scrobble state. A different identity triggers
confirmation from a separate audio window. Failed matches retain the current
identity and are retried on the next interval. Active recognition and boundary
checks can delay a periodic check; delayed checks use fresh audio.
