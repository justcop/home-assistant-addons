package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeMethodTest {
    @Test public void existingInstallsAndUnknownPreferencesKeepCurrentTiming() {
        assertEquals(WakeMethod.CURRENT, WakeMethod.fromPreference(null));
        assertEquals(WakeMethod.CURRENT, WakeMethod.fromPreference(""));
        assertEquals(WakeMethod.CURRENT, WakeMethod.fromPreference("unknown"));
        for (WakeMethod method : WakeMethod.values()) {
            assertEquals(method, WakeMethod.fromPreference(method.preference));
        }
    }

    @Test public void coldStartDiffersOnlyInWhetherResumeIsRequired() {
        for (WakeMethod method : WakeMethod.values()) {
            boolean started = false;
            // Validated, previously trusted request during onCreate.
            boolean duringCreate = method.shouldStart(false, true, started, true);
            if (duringCreate) started = true;
            assertEquals(method == WakeMethod.PREVIOUS, duringCreate);
            boolean duringResume = method.shouldStart(true, true, started, true);
            assertEquals(method == WakeMethod.CURRENT, duringResume);
            if (duringResume) started = true;
            assertTrue(started);
            assertFalse(method.shouldStart(true, true, started, true));
        }
    }

    @Test public void bothMethodsWaitForTrustAndNeverWakeFromTheLauncher() {
        for (WakeMethod method : WakeMethod.values()) {
            assertFalse(method.shouldStart(false, true, false, false));
            assertFalse(method.shouldStart(true, true, false, false));
            assertFalse(method.shouldStart(true, false, false, true));
            // First-use approval can arrive after onResume for either method.
            assertTrue(method.shouldStart(true, true, false, true));
        }
    }

    @Test public void finishedAttemptsCannotRestartOnAnotherResume() {
        for (WakeMethod method : WakeMethod.values()) {
            assertFalse(method.shouldStart(true, true, false, false));
            assertFalse(method.shouldStart(true, true, true, false));
            assertFalse(method.shouldStart(false, true, true, true));
        }
    }
}
