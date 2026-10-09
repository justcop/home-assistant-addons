package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeTimingTest {
    @Test public void successAndFailureAlwaysAllowFiveSecondsFromStart() {
        assertEquals(5_000L, WakeTiming.remainingSettleMs(0));
        assertEquals(3_000L, WakeTiming.remainingSettleMs(2_000L));
        assertEquals(1L, WakeTiming.remainingSettleMs(4_999L));
        assertEquals(0L, WakeTiming.remainingSettleMs(5_000L));
        assertEquals(0L, WakeTiming.remainingSettleMs(10_000L));
        assertEquals(5_000L, WakeTiming.remainingSettleMs(-100L));
    }

    @Test public void pendingSdkCallbackHasFiniteDeadlineAfterMinimumWakePeriod() {
        assertEquals(8_000L, WakeTiming.MAX_PENDING_MS);
        assertTrue(WakeTiming.MAX_PENDING_MS >= WakeTiming.MIN_SETTLE_MS);
        assertEquals(0L, WakeTiming.remainingSettleMs(WakeTiming.MAX_PENDING_MS));
    }
}
