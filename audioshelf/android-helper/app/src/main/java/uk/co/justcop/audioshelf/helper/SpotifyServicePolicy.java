package uk.co.justcop.audioshelf.helper;

import java.util.Locale;

/**
 * Select only Spotify's protocol-bound remote service for diagnostics.
 * UI widget RemoteViews services are unrelated to Spotify App Remote.
 */
final class SpotifyServicePolicy {
    static boolean candidate(String name) {
        if (name == null) return false;
        String s = name.toLowerCase(Locale.ROOT);
        // This is the service found in Spotify 9.1.88, and we also accept
        // future Spotify-owned AppRemoteService variants without false positives
        // from AndroidX RemoteViews widget services.
        if ("com.spotify.interapp.service.service.appprotocolremoteservice".equals(s)) return true;
        return s.startsWith("com.spotify.") && (s.endsWith(".appremoteservice")
            || s.endsWith(".appprotocolremoteservice"));
    }
}
