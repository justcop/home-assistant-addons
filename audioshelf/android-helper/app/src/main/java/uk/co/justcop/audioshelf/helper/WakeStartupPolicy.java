package uk.co.justcop.audioshelf.helper;

/** Start the background Spotify SDK connection only after the helper is visible and trusted. */
final class WakeStartupPolicy {
    static boolean shouldStart(boolean resumed, boolean wakeRequest, boolean started, boolean ready) {
        return resumed && wakeRequest && !started && ready;
    }
}
