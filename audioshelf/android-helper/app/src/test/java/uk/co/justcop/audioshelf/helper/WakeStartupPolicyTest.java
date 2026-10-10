package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeStartupPolicyTest {
    @Test public void coldStartWaitsForResumeAndTrust() {
        assertFalse(WakeStartupPolicy.shouldStart(false, true, false, true));
        assertFalse(WakeStartupPolicy.shouldStart(true, true, false, false));
        assertTrue(WakeStartupPolicy.shouldStart(true, true, false, true));
    }

    @Test public void noDuplicateWakeOrLauncherWake() {
        assertFalse(WakeStartupPolicy.shouldStart(true, false, false, true));
        assertFalse(WakeStartupPolicy.shouldStart(true, true, true, true));
        assertFalse(WakeStartupPolicy.shouldStart(false, true, true, true));
        assertFalse(WakeStartupPolicy.shouldStart(true, true, true, false));
    }
}
