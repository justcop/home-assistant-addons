package uk.co.justcop.audioshelf.helper;

/**
 * One foreground App Remote pairing attempt at a time. Timed-out callbacks
 * must not overwrite the visible result of a subsequent user attempt.
 */
final class PairingAttempt {
    static final long FIRST_PROGRESS_MS = 3000;
    static final long SECOND_PROGRESS_MS = 10000;
    static final long TIMEOUT_MS = 45000;

    private int generation;
    private boolean pending;

    int begin() {
        if (pending) throw new IllegalStateException("Pairing already pending");
        generation++;
        pending = true;
        return generation;
    }

    boolean pending() { return pending; }

    boolean isCurrent(int attempt) { return pending && generation == attempt; }

    boolean finish(int attempt) {
        if (!isCurrent(attempt)) return false;
        pending = false;
        return true;
    }
}
