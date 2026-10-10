package uk.co.justcop.audioshelf.helper;

/** Android 14 changed bound-service background Activity launch delegation. */
final class SpotifyAuthorizationMode {
    static boolean needsActivityLaunchGrant(int sdkVersion) {
        return sdkVersion >= 34;
    }
}
