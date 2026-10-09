package uk.co.justcop.audioshelf.helper;

/**
 * Authorisation must be an explicit setup action, never a side effect of a
 * routine background wake. Spotify can be prevented by Android from launching
 * its internal authorisation activity while Spotify itself is backgrounded.
 */
final class SpotifyRemoteAuthPolicy {
    static final String REDIRECT_URI = "audioshelf-helper://spotify-callback";

    static boolean validClientId(String clientId) {
        return clientId != null && clientId.matches("[a-fA-F0-9]{32}");
    }

    static boolean showAuthView(boolean deliberateForegroundPairing) {
        return deliberateForegroundPairing;
    }
}
