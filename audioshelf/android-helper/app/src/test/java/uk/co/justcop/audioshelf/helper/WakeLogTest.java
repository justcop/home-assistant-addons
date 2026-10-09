package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeLogTest {
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

    @Test public void pairingTraceIsIndependentOfPreviousWakeAndResetsForEveryAttempt() {
        WakeLog oldWake = new WakeLog();
        oldWake.append("[0.001s] Previous wake failed");
        WakeLog pairing = new WakeLog();
        pairing.append("[0.000s] Pairing button pressed");
        pairing.append("[0.100s] Spotify SDK pending");
        assertEquals("[0.001s] Previous wake failed\\n", oldWake.toString());
        assertTrue(pairing.toString().contains("Pairing button pressed"));
        assertFalse(pairing.toString().contains("Previous wake failed"));

        pairing = new WakeLog(); // New pairing should clear its own prior attempt.
        pairing.append("[0.000s] Pairing button pressed again");
        pairing.append("[30.000s] Pairing timed out; no authorisation confirmed");
        assertFalse(pairing.toString().contains("Spotify SDK pending"));
        assertTrue(pairing.toString().contains("no authorisation confirmed"));
        assertTrue(oldWake.toString().contains("Previous wake failed"));
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
