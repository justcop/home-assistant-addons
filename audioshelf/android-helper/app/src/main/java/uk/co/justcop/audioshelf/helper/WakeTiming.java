package uk.co.justcop.audioshelf.helper;

/** Bound the helper's own wait: Spotify Connect readiness is verified by AudioShelf. */
final class WakeTiming {
    static final long MIN_SETTLE_MS = 5_000L;
    static final long MAX_PENDING_MS = 8_000L;

    static long remainingSettleMs(long elapsedSinceWakeMs) {
        return Math.max(0L, MIN_SETTLE_MS - Math.max(0L, elapsedSinceWakeMs));
    }

    private WakeTiming() {}
}
