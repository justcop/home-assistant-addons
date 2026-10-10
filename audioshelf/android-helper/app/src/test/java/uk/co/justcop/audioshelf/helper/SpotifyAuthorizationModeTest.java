package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyAuthorizationModeTest {
    @Test public void onlyAndroid14AndLaterNeedExplicitServiceActivityGrant() {
        assertFalse(SpotifyAuthorizationMode.needsActivityLaunchGrant(26));
        assertFalse(SpotifyAuthorizationMode.needsActivityLaunchGrant(33));
        assertTrue(SpotifyAuthorizationMode.needsActivityLaunchGrant(34));
        assertTrue(SpotifyAuthorizationMode.needsActivityLaunchGrant(37));
    }
}
