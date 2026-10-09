package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class PairingAttemptTest {
    @Test public void pairingStartsAndAcceptsOnlyOwnCallback() {
        PairingAttempt attempt = new PairingAttempt();
        assertFalse(attempt.pending());
        int first = attempt.begin();
        assertTrue(attempt.pending());
        assertTrue(attempt.isCurrent(first));
        assertTrue(attempt.finish(first));
        assertFalse(attempt.pending());
        assertFalse(attempt.finish(first));
    }

    @Test public void staleCallbackCannotOverwriteNextPairingResult() {
        PairingAttempt attempt = new PairingAttempt();
        int first = attempt.begin();
        assertTrue(attempt.finish(first)); // 45-second timeout
        int second = attempt.begin();
        assertFalse(attempt.isCurrent(first));
        assertFalse(attempt.finish(first));
        assertTrue(attempt.pending());
        assertTrue(attempt.finish(second));
    }

    @Test public void repeatedPairingsRequireExplicitCompletion() {
        PairingAttempt attempt = new PairingAttempt();
        attempt.begin();
        try {
            attempt.begin();
            fail("should reject a duplicate in-progress pairing");
        } catch (IllegalStateException expected) {
            assertTrue(attempt.pending());
        }
    }

    @Test public void progressIntervalsAreBounded() {
        assertEquals(3000, PairingAttempt.FIRST_PROGRESS_MS);
        assertEquals(10000, PairingAttempt.SECOND_PROGRESS_MS);
        assertEquals(45000, PairingAttempt.TIMEOUT_MS);
    }
}
