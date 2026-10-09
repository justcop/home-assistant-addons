package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyWakeRecoveryTest {
    @Test public void manualRecoveryHasAnIndependentBoundedConnectWindow() {
        assertEquals(15000, SpotifyWakeRecovery.USER_RECOVERY_GRACE_MS);
        assertTrue(SpotifyWakeRecovery.USER_RECOVERY_GRACE_MS > 3000);
        // An ordinary wake is still governed by the original 20-second
        // deadline. Only the explicit user gesture reschedules that callback.
        assertEquals(20000, WakeReturnState.WAKE_WINDOW_MS);
    }

    @Test public void sdkConnectionOnlyBeginsWithVisibleActivityOnce() {
        assertFalse(SpotifyWakeRecovery.shouldStartInitialSdk(false,true,false,true));
        assertFalse(SpotifyWakeRecovery.shouldStartInitialSdk(true,false,false,true));
        assertFalse(SpotifyWakeRecovery.shouldStartInitialSdk(true,true,true,true));
        assertFalse(SpotifyWakeRecovery.shouldStartInitialSdk(true,true,false,false));
        assertTrue(SpotifyWakeRecovery.shouldStartInitialSdk(true,true,false,true));
    }

    @Test public void hungSdkConnectionOffersManualFallbackAfterThreeSeconds() {
        assertFalse(SpotifyWakeRecovery.shouldOfferManualRecovery(false,false,true,0));
        assertFalse(SpotifyWakeRecovery.shouldOfferManualRecovery(false,false,true,2999));
        assertTrue(SpotifyWakeRecovery.shouldOfferManualRecovery(false,false,true,3000));
        assertTrue(SpotifyWakeRecovery.shouldOfferManualRecovery(false,false,true,10000));
    }

    @Test public void successfulOrFailedCallbackDoesNotMasqueradeAsSdkHang() {
        assertFalse(SpotifyWakeRecovery.shouldOfferManualRecovery(true,false,true,3000));
        // Explicit onFailure follows its existing one-tap 1.2-second path.
    }

    @Test public void neverInterruptSuccessfulWakeOrCompletedRequest() {
        assertFalse(SpotifyWakeRecovery.shouldOfferManualRecovery(false,true,true,20000));
        assertFalse(SpotifyWakeRecovery.shouldOfferManualRecovery(false,false,false,20000));
    }
}
