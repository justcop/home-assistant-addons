package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeProgressLogTest {
    @Test public void oneMinuteOfAlternatingDevicePollsProducesPeriodicSnapshots() {
        WakeProgressLog log = new WakeProgressLog();
        int lines = 0;
        for (int poll = 0; poll < 80; poll++) {
            String phase = poll % 2 == 0 ? "checking_devices" : "waiting_for_device";
            if (log.shouldRecord("waiting", phase, false, -1, poll * 750)) lines++;
        }
        assertEquals(12, lines);
        assertTrue(log.shouldRecord("expired", "expired", false, -1, 60000));
    }

    @Test public void discoveryPlayAndFinalResultsAreNeverDelayedBySnapshotInterval() {
        WakeProgressLog log = new WakeProgressLog();
        assertTrue(log.shouldRecord("waiting", "waiting_for_device", false, -1, 0));
        assertTrue(log.shouldRecord("waiting", "preparing_playback", false, 100, 100));
        assertTrue(log.shouldRecord("waiting", "sending_play", false, 100, 110));
        assertTrue(log.shouldRecord("waiting", "confirming_playback", true, 100, 120));
        assertFalse(log.shouldRecord("waiting", "confirming_playback", true, 100, 130));
        assertTrue(log.shouldRecord("waiting", "wrong_track", true, 100, 140));
        assertTrue(log.shouldRecord("started", "confirmed", true, 100, 150));
    }
}
