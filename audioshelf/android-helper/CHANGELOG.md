# 0.1.1

- Return automatically to AudioShelf when the Spotify SDK connection fails or times out. The SDK can wake Spotify and allow the existing server handoff to start playback even if its own remote session fails.
- Keep actual playback status with AudioShelf, which still waits for the preferred device and preserves the selected tracklist.
- Preserve foreground-only return, cancellation and late-callback cleanup.
