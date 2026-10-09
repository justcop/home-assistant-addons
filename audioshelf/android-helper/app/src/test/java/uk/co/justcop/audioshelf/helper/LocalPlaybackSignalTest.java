package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class LocalPlaybackSignalTest {
    @Test public void detectsBackgroundStartWithoutSdkOrNetworkResponse() {
        LocalPlaybackSignal signal = new LocalPlaybackSignal();
        assertFalse(signal.observe(false, ""));
        assertTrue(signal.observe(true, "new-track"));
        assertFalse(signal.observe(true, "new-track"));
    }

    @Test public void existingPlaybackAndRepeatedSnapshotsDoNotTriggerReturn() {
        LocalPlaybackSignal signal = new LocalPlaybackSignal();
        assertFalse(signal.observe(true, "old-track"));
        assertFalse(signal.observe(true, "old-track"));
        assertTrue(signal.observe(true, "new-track"));
    }

    @Test public void pausedRemoteOrOtherAppPlaybackCannotSignalLocalSpotifyStart() {
        assertFalse(LocalPlaybackSignal.qualifies("com.spotify.music", false, true, true));
        assertFalse(LocalPlaybackSignal.qualifies("other.player", true, true, true));
        assertFalse(LocalPlaybackSignal.qualifies("com.spotify.music", true, false, true));
        assertFalse(LocalPlaybackSignal.qualifies("com.spotify.music", true, true, false));
        assertTrue(LocalPlaybackSignal.qualifies("com.spotify.music", true, true, true));
        LocalPlaybackSignal signal = new LocalPlaybackSignal();
        assertFalse(signal.observe(false, "track"));
        assertFalse(signal.observe(false, "track"));
        assertTrue(signal.observe(true, "track"));
    }

    @Test public void metadataArrivingLateDoesNotInventAPlaybackTransition() {
        LocalPlaybackSignal signal = new LocalPlaybackSignal();
        assertFalse(signal.observe(true, ""));
        assertFalse(signal.observe(true, "track"));
        assertFalse(signal.observe(true, "track"));
    }
}
