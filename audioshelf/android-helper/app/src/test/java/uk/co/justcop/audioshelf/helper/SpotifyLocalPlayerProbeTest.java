package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyLocalPlayerProbeTest {
    @Test public void fingerprintMatchesServerSha256TrackIdButNeverLeaksUri() {
        assertEquals("ec7c494df6", SpotifyLocalPlayerProbe.trackTag(
            "spotify:track:" + "a".repeat(22)));
        assertEquals("", SpotifyLocalPlayerProbe.trackTag(
            "spotify:album:" + "a".repeat(22)));
        assertEquals("", SpotifyLocalPlayerProbe.trackTag("spotify:track:private"));
        assertEquals("", SpotifyLocalPlayerProbe.trackTag(null));
    }

    @Test public void mayResumeOnlyAfterServerAcceptsPlayAndExactLocalTrackIsPaused() {
        String tag="ec7c494df6";
        assertTrue(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state",tag,tag,true));
        assertTrue(SpotifyLocalPlayerProbe.shouldResume(true,"paused",tag,tag,true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(false,"no_player_state",tag,tag,true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state",tag,tag,false));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state",tag,"ad956c7dd8",true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state",tag,"",true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"wrong_track",tag,tag,true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"wrong_device",tag,tag,true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state","",tag,true));
        assertFalse(SpotifyLocalPlayerProbe.shouldResume(true,"no_player_state",
            "sensitive data",tag,true));
    }
}
