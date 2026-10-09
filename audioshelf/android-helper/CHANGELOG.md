# 0.1.2

- Explicitly launch Spotify before attempting the optional App Remote connection. SDK failure no longer skips the Spotify launch or shortens its five-second warm-up.
- Return to the trusted AudioShelf HTTPS page automatically, including its album route, instead of waiting for the helper to become visible again.
- Keep playback and device readiness checks in AudioShelf.

# 0.1.1

- Return automatically to AudioShelf when the Spotify SDK connection fails or times out. The SDK can wake Spotify and allow the existing server handoff to start playback even if its own remote session fails.
- Keep actual playback status with AudioShelf, which still waits for the preferred device and preserves the selected tracklist.
- Preserve foreground-only return, cancellation and late-callback cleanup.
