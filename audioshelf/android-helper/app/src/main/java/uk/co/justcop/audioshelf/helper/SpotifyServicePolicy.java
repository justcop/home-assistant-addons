package uk.co.justcop.audioshelf.helper;

import java.util.Locale;

/** Conservative classifier for optional, user-initiated binding diagnostics. */
final class SpotifyServicePolicy {
    static boolean candidate(String name) {
        if (name == null) return false;
        String s = name.toLowerCase(Locale.ROOT);
        return s.contains("service") && (s.contains("appremote") || s.contains("app_remote")
            || s.contains("remote"));
    }
}
