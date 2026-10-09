package uk.co.justcop.audioshelf.helper;

/** Keep phase transitions and five-second snapshots, without logging every poll. */
final class WakeProgressLog {
    private String lastKey;
    private long lastRecordedAt;

    boolean shouldRecord(String state, String phase, boolean accepted, int deviceSeenMs, long now) {
        // These phases alternate during every device probe. Treat them as one
        // discovery stage; retain current counters/timings in periodic snapshots.
        String stage = "checking_devices".equals(phase) || "waiting_for_device".equals(phase)
            ? "discovery" : phase;
        String key = state + ":" + stage + ":" + accepted + ":" + (deviceSeenMs >= 0);
        if (!key.equals(lastKey) || now - lastRecordedAt >= 5000) {
            lastKey = key;
            lastRecordedAt = now;
            return true;
        }
        return false;
    }
}
