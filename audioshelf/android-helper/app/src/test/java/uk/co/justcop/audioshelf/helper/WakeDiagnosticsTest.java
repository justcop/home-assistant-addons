package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
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
        assertEquals("[1.234s] Connected", WakeDiagnostics.line(1234, "Connected"));
        assertEquals("[0.000s] Starting", WakeDiagnostics.line(-1, "Starting"));
    }
}
