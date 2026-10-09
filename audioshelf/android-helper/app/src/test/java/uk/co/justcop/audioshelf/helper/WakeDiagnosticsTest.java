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

    @Test public void timestampsUseElapsedSecondsAndDoNotDependOnLocale() {
        assertEquals("[1.234s] Connected", WakeDiagnostics.line(1234, "Connected"));
        assertEquals("[0.000s] Starting", WakeDiagnostics.line(-1, "Starting"));
    }
}
