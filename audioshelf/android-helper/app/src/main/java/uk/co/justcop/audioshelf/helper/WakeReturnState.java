package uk.co.justcop.audioshelf.helper;

/** Return policy independent of the Spotify SDK session and network availability. */
final class WakeReturnState {
    static final long WAKE_WINDOW_MS = 20000;
    private boolean deadlineReached;
    private boolean jobFinished;
    private boolean playAccepted;
    private boolean deviceReady;

    void onLocalPlayback() { jobFinished = true; }

    // Play was accepted, but the server continues independent playback verification.
    void onPlayAccepted() { playAccepted = true; }

    // The server has found the uniquely selected Spotify Connect phone. It owns
    // Play dispatch and verification, so the helper can close before Play is sent.
    void onDeviceReady() { deviceReady = true; }

    void onDeadline() { deadlineReached = true; }

    void onServerState(String state) {
        // Only validated terminal states can finish the job. A deadline is not success.
        if ("started".equals(state) || "failed".equals(state) || "expired".equals(state)
                || "cancelled".equals(state) || "unconfirmed".equals(state)) jobFinished = true;
    }

    boolean shouldReturn(boolean visible, boolean diagnosticHold) {
        return (jobFinished || deviceReady || playAccepted || deadlineReached) && visible && !diagnosticHold;
    }

    boolean shouldPoll(boolean diagnosticHold) {
        return !jobFinished && (!deadlineReached || diagnosticHold)
            && (!(deviceReady || playAccepted) || diagnosticHold);
    }
}
