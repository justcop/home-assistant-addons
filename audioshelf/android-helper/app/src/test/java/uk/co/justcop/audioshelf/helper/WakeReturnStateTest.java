package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class WakeReturnStateTest {
    @Test public void missingSdkCallbackAndOfflineStatusCannotTrapTheHelper() {
        WakeReturnState state = new WakeReturnState();
        assertFalse(state.shouldReturn(true, false));
        state.onServerState("waiting");
        assertFalse(state.shouldReturn(true, false));
        // No SDK callback or network response is needed to reach the deadline.
        state.onDeadline();
        assertTrue(state.shouldReturn(true, false));
        assertFalse(state.shouldPoll(false));
    }

    @Test public void returnOnPlayAcceptanceDoesNotWaitForSpotifyConfirmation() {
        assertEquals(20000, WakeReturnState.WAKE_WINDOW_MS);
        WakeReturnState state = new WakeReturnState();
        state.onServerState("waiting");
        assertFalse(state.shouldReturn(true, false));
        state.onPlayAccepted();
        assertTrue(state.shouldReturn(true, false));
        assertFalse(state.shouldPoll(false));
        assertFalse(state.shouldReturn(false, false));
        assertFalse(state.shouldReturn(true, true));
        assertTrue(state.shouldPoll(true)); // Hold keeps watching for the actual result.
        state.onServerState("started");
        assertFalse(state.shouldPoll(true));
    }

    @Test public void confirmedPlaybackReturnsBeforeDeadlineEvenWithoutSdkConnection() {
        WakeReturnState state = new WakeReturnState();
        state.onServerState("started");
        assertTrue(state.shouldReturn(true, false));
        assertFalse(state.shouldPoll(false));
    }

    @Test public void localPlaybackReturnsEarlyWithoutServerAndHonoursDiagnosticHold() {
        WakeReturnState state = new WakeReturnState();
        state.onLocalPlayback();
        assertTrue(state.shouldReturn(true, false));
        assertFalse(state.shouldReturn(true, true));
        assertFalse(state.shouldPoll(false));
    }

    @Test public void diagnosticHoldStillCollectsLatePlaybackConfirmation() {
        WakeReturnState state = new WakeReturnState();
        state.onDeadline();
        assertFalse(state.shouldReturn(true, true));
        assertTrue(state.shouldPoll(true));
        state.onServerState("started");
        assertFalse(state.shouldReturn(true, true));
        assertFalse(state.shouldPoll(true));
        assertTrue(state.shouldReturn(true, false));
    }

    @Test public void authorizationScreenIsNotInterruptedButReturnRunsWhenVisible() {
        WakeReturnState state = new WakeReturnState();
        state.onDeadline();
        assertFalse(state.shouldReturn(false, false));
        assertTrue(state.shouldReturn(true, false));
    }

    @Test public void terminalFailureReturnsAndUnknownStatesCannotFinishTheJob() {
        for (String terminal : new String[]{"failed", "expired", "cancelled", "unconfirmed"}) {
            WakeReturnState state = new WakeReturnState();
            state.onServerState("unknown");
            assertFalse(state.shouldReturn(true, false));
            state.onServerState(terminal);
            assertTrue(state.shouldReturn(true, false));
            assertFalse(state.shouldPoll(false));
        }
    }
}
