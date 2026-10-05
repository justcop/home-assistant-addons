# Vinyl Guardian

## Vinyl listening statistics

Listening Analytics stores the source of Guardian's accepted Last.fm scrobbles.
Set `listening_analytics_url` to its direct local base URL and
`listening_analytics_token` to its `source_api_token`, then restart. Both add-ons
must use the same Last.fm account. Guardian queues unacknowledged notifications
in `/data`, retries in the background during outages, and deletes them on delivery.
The listening database and Vinyl only filter live in Listening Analytics.
See [connection setup](../lastfm_analytics/README.md#vinyl-attribution).
