package uk.co.justcop.audioshelf.helper;

import java.util.Locale;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.UUID;

/** Safe diagnostics: exception types only, never SDK payloads, tokens or URLs. */
final class WakeDiagnostics {
    private static final DateTimeFormatter TIMESTAMP =
        DateTimeFormatter.ofPattern("uuuu-MM-dd HH:mm:ss.SSS XXX", Locale.US);

    /** Every copied line is independently timestamped in the phone's timezone.
     * Elapsed monotonic time is retained for request/SDK performance analysis. */
    static String line(long elapsedMs, String event) {
        return line(System.currentTimeMillis(), elapsedMs, ZoneId.systemDefault(), event);
    }

    /** Deterministic timestamp formatter for unit tests. */
    static String line(long epochMs, long elapsedMs, ZoneId zone, String event) {
        return "[" + TIMESTAMP.withZone(zone).format(Instant.ofEpochMilli(epochMs))
            + " | +" + String.format(Locale.US, "%.3fs", Math.max(0, elapsedMs) / 1000.0)
            + "] " + event;
    }

    /** Local correlation label only. Never log bearer tokens, full job IDs or OAuth URLs. */
    static String sessionId() {
        return UUID.randomUUID().toString().substring(0, 8);
    }

    static boolean isAuthorizationFailure(Throwable error) {
        // Spotify wraps the checked SDK error in a transport exception on some
        // versions. Inspect exception types only; never log raw OAuth payloads.
        for (int depth = 0; error != null && depth < 8; depth++, error = error.getCause()) {
            if ("UserNotAuthorizedException".equals(error.getClass().getSimpleName())) return true;
        }
        return false;
    }

    static String failure(Throwable error) {
        if (error == null) return "Unknown error";
        StringBuilder result = new StringBuilder();
        Throwable current = error;
        for (int count = 0; current != null && count < 4; count++) {
            if (count > 0) result.append(" caused by ");
            String name = current.getClass().getSimpleName();
            result.append(name.isEmpty() ? "Exception" : name);
            current = current.getCause();
        }
        return result.toString();
    }
}
