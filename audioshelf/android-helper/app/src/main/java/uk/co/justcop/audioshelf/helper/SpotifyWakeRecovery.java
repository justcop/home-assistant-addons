package uk.co.justcop.audioshelf.helper;

/** A missing App Remote callback is not a successful background Spotify wake. */
final class SpotifyWakeRecovery {
    static final long NO_CALLBACK_GRACE_MS = 3000;

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
