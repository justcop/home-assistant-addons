package uk.co.justcop.audioshelf.helper;

/**
 * Fallback for Android/Spotify App Remote authorization failures when the
 * selected phone never appears on Spotify Connect.
 *
 * Background SDK service startup is still the preferred path. Opening the
 * normal Spotify activity is recovery only, never a playback command.
 */
final class SpotifyWakeRecovery {
    static final long FOREGROUND_AFTER_MS = 10000;

    static boolean shouldOpenAutomatically(boolean sdkFailed, boolean serverContacted,
                                           String serverState, boolean deviceReady,
                                           boolean playAccepted, boolean completed,
                                           boolean visible, boolean diagnosticsHold,
                                           boolean alreadyOpened, long elapsedMs) {
        return sdkFailed && serverContacted && "waiting".equals(serverState)
            && !deviceReady && !playAccepted && !completed
            && visible && !diagnosticsHold && !alreadyOpened
            && elapsedMs >= FOREGROUND_AFTER_MS;
    }
}
