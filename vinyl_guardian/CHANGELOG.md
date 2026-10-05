# Changelog

## 5.10.7

- Report accepted vinyl scrobbles to Listening Analytics using an optional URL and shared token.
- Retain only pending delivery notifications, retry in the background during outages, and remove them after acknowledgement.
- Verify Last.fm acceptance and use returned corrected metadata for attribution. Failed or ignored submissions no longer appear as successful scrobbles.
