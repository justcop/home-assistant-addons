package uk.co.justcop.audioshelf.helper;

import java.util.Locale;

/** Safe diagnostics: exception types only, never SDK payloads, tokens or URLs. */
final class WakeDiagnostics {
    static String line(long elapsedMs, String event) {
        return String.format(Locale.US, "[%.3fs] %s", Math.max(0, elapsedMs) / 1000.0, event);
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
