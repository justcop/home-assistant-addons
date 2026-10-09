package uk.co.justcop.audioshelf.helper;

/** Return policy independent of the Spotify SDK session and network availability. */
final class WakeReturnState {
    static final long WAKE_WINDOW_MS = 8000;
    private boolean deadlineReached;
    private boolean jobFinished;

    void onLocalPlayback() { jobFinished = true; }

    void onDeadline() { deadlineReached = true; }

    void onServerState(String state) {
        // Only validated terminal states can finish the job. A deadline is not success.
        if ("started".equals(state) || "failed".equals(state) || "expired".equals(state)
                || "cancelled".equals(state) || "unconfirmed".equals(state)) jobFinished = true;
    }

    boolean shouldReturn(boolean visible, boolean diagnosticHold) {
        return (jobFinished || deadlineReached) && visible && !diagnosticHold;
    }

    boolean shouldPoll(boolean diagnosticHold) {
        return !jobFinished && (!deadlineReached || diagnosticHold);
    }
}
