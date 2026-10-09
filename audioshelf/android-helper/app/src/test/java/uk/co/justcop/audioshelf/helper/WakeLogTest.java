package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeLogTest {
    @Test public void newPairingClearsPreviousDisplayedLogWithoutCarryingHistory() {
        WakeLog log = new WakeLog();
        log.append("Previous pairing failed");
        for (int i = 0; i < 500; i++) log.append("Long previous progress update " + i);
        log.clear();
        assertEquals("", log.toString());
        log.append("[0.000s] Fresh pairing tapped");
        assertEquals("[0.000s] Fresh pairing tapped\\n".replace("\\\\n", "\\n"), log.toString());
        assertFalse(log.toString().contains("Previous pairing failed"));
        assertFalse(log.toString().contains("middle log entries omitted"));
    }

    @Test public void longPollingRunRetainsStartupSdkOutcomeAndFinalResultInOrder() {
        WakeLog log = new WakeLog();
        log.append("[0.000s] AudioShelf helper 0.1.18");
        log.append("[0.001s] Selected Previous wake method");
        log.append("[3.500s] Spotify SDK failed: UserNotAuthorizedException");
        for (int i = 0; i < 5000; i++) {
            log.append("[" + i + "] Repeated polling response with device counters and timings");
        }
        log.append("[60.000s] AudioShelf job finished: expired");
        log.append("[60.001s] Diagnostic hold enabled; waiting for manual return");
        String text = log.toString();
        assertTrue(text.startsWith("[0.000s] AudioShelf helper"));
        assertTrue(text.contains("Selected Previous wake method"));
        assertTrue(text.contains("Spotify SDK failed: UserNotAuthorizedException"));
        assertTrue(text.contains("middle log entries omitted"));
        assertTrue(text.indexOf("UserNotAuthorizedException") < text.indexOf("middle log entries omitted"));
        assertTrue(text.indexOf("middle log entries omitted") < text.indexOf("AudioShelf job finished: expired"));
        assertTrue(text.endsWith("waiting for manual return\n"));
        assertTrue(text.length() < 16200);
    }

    @Test public void normalWakeLogIsCompleteAndOversizedEventsRemainBounded() {
        WakeLog log = new WakeLog();
        log.append("First");
        log.append("Second");
        assertEquals("First\nSecond\n", log.toString());
        for (int i = 0; i < 100; i++) log.append(new String(new char[20000]).replace('\0', 'x'));
        log.append("Final result");
        assertTrue(log.toString().startsWith("First\nSecond\n"));
        assertTrue(log.toString().endsWith("Final result\n"));
        assertTrue(log.toString().length() < 16200);
    }
}
