# 0.1.5

- Add automatic update checks when opening the helper directly, a Check for updates button, verified APK downloads, and Android install confirmation. Updates never interrupt Spotify handover.
- Publish the signed helper update channel on main helper changes; published builds require the saved signing key.
- Match the AudioShelf web icon with the same wine background, record, grooves, cream label and shelf, plus a small green helper badge. Include an adaptive Android icon.

# 0.1.4

- Restore the original Spotify App Remote service wake without any launcher intent or foreground task switching. Wait in the helper for at least five seconds before returning, including after an SDK failure.
- Keep the trusted AudioShelf return link. A pending connection gets up to 45 seconds; first-use Spotify authorisation is allowed to finish before returning.
- Keep the working foreground route available through AudioShelf's manual Open Spotify link.

# 0.1.2

- Explicitly launch Spotify before attempting the optional App Remote connection. SDK failure no longer skips the Spotify launch or shortens its five-second warm-up.
- Return to the trusted AudioShelf HTTPS page automatically, including its album route, instead of waiting for the helper to become visible again.
- Keep playback and device readiness checks in AudioShelf.

# 0.1.1

- Return automatically to AudioShelf when the Spotify SDK connection fails or times out. The SDK can wake Spotify and allow the existing server handoff to start playback even if its own remote session fails.
- Keep actual playback status with AudioShelf, which still waits for the preferred device and preserves the selected tracklist.
- Preserve foreground-only return, cancellation and late-callback cleanup.
