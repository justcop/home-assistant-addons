package uk.co.justcop.audioshelf.helper;

/** A missing App Remote callback is not a successful background Spotify wake. */
final class SpotifyWakeRecovery {
    static final long NO_CALLBACK_GRACE_MS = 3000;
    // Grant a deliberate foreground Spotify visit enough time to register on
    // Connect, even when the user taps recovery near the normal 20s deadline.
    // The server independently stops its playback job after 60 seconds.
    static final long USER_RECOVERY_GRACE_MS = 15000;

    static boolean shouldStartInitialSdk(boolean resumed, boolean wakeRequest,
                                         boolean sdkWakeStarted, boolean active) {
        return resumed && wakeRequest && !sdkWakeStarted && active;
    }

    static boolean shouldOfferManualRecovery(boolean callbackReceived,
                                              boolean phoneReady,
                                              boolean requestActive,
                                              long elapsedMs) {
        // Only offer an escape hatch, never launch Spotify automatically.
        // AudioShelf alone remains responsible for device choice and playback.
        return !callbackReceived && !phoneReady && requestActive
            && elapsedMs >= NO_CALLBACK_GRACE_MS;
    }
}
