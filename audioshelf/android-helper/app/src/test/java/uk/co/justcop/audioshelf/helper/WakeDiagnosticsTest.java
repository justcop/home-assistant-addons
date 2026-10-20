package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import java.time.ZoneId;
import java.time.Instant;
import static org.junit.Assert.*;

public class WakeDiagnosticsTest {
    @Test public void errorLogIncludesCauseTypesWithoutPrivateSdkPayloads() {
        Throwable error = new IllegalStateException("access_token=private https://private.example/",
            new SecurityException("client_id=private-secret"));
        assertEquals("IllegalStateException caused by SecurityException", WakeDiagnostics.failure(error));
        assertEquals("Unknown error", WakeDiagnostics.failure(null));
    }

    @Test public void cyclicCauseChainsRemainBounded() {
        Throwable first = new IllegalStateException();
        Throwable second = new SecurityException();
        first.initCause(second);
        second.initCause(first);
        assertEquals(4, WakeDiagnostics.failure(first).split(" caused by ").length);
    }

    private static final class UserNotAuthorizedException extends Exception {
        UserNotAuthorizedException(String message) { super(message); }
    }

    @Test public void wrappedSdkAuthorisationIsNotConfusedWithWebApiQuota() {
        Throwable sdk = new IllegalStateException("private OAuth details",
            new UserNotAuthorizedException("private Spotify auth payload"));
        assertTrue(WakeDiagnostics.isAuthorizationFailure(sdk));
        assertFalse(WakeDiagnostics.isAuthorizationFailure(new IOExceptionEquivalent()));
        assertFalse(WakeDiagnostics.isAuthorizationFailure(new IllegalStateException("429 Too Many Requests")));
        assertFalse(WakeDiagnostics.isAuthorizationFailure(null));
        // Only exception classes, never token-bearing messages, enter the log.
        assertEquals("IllegalStateException caused by UserNotAuthorizedException", WakeDiagnostics.failure(sdk));
    }

    private static final class IOExceptionEquivalent extends Exception {}

    @Test public void timestampsUseElapsedSecondsAndDoNotDependOnLocale() {
        long epoch = Instant.parse("2026-10-20T15:26:42.123Z").toEpochMilli();
        assertEquals("[2026-10-20 16:26:42.123 +01:00 | +1.234s] Connected",
            WakeDiagnostics.line(epoch,1234,ZoneId.of("Europe/London"),"Connected"));
        assertEquals("[2026-10-20 15:26:42.123 Z | +0.000s] Starting",
            WakeDiagnostics.line(epoch,-1,ZoneId.of("UTC"),"Starting"));
        assertTrue(WakeDiagnostics.sessionId().matches("[0-9a-f]{8}"));
        assertNotEquals(WakeDiagnostics.sessionId(),WakeDiagnostics.sessionId());
    }
}
