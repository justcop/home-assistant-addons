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
