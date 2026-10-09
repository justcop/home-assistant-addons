package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyRemoteAuthPolicyTest {
    @Test public void routineWakeNeverRequestsAnAuthorizationActivity() {
        assertFalse(SpotifyRemoteAuthPolicy.showAuthView(false));
    }

    @Test public void pairingRequiresADeliberateForegroundAction() {
        assertTrue(SpotifyRemoteAuthPolicy.showAuthView(true));
    }

    @Test public void refuseUntrustedOrMalformedClientIds() {
        assertFalse(SpotifyRemoteAuthPolicy.validClientId(null));
        assertFalse(SpotifyRemoteAuthPolicy.validClientId(""));
        assertFalse(SpotifyRemoteAuthPolicy.validClientId("not-a-client-id"));
        assertTrue(SpotifyRemoteAuthPolicy.validClientId("a1234567890123456789012345678901"));
        assertEquals("audioshelf-helper://spotify-callback",
            SpotifyRemoteAuthPolicy.REDIRECT_URI);
    }
}
