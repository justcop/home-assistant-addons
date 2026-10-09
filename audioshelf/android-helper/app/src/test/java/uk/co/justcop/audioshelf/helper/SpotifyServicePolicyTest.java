package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyServicePolicyTest {
    @Test public void onlySelectPlausibleRemoteServices() {
        assertTrue(SpotifyServicePolicy.candidate("com.spotify.music.appremote.AppRemoteService"));
        assertTrue(SpotifyServicePolicy.candidate("com.spotify.music.remote.RemoteService"));
        assertFalse(SpotifyServicePolicy.candidate("com.spotify.music.BrowserService"));
        assertFalse(SpotifyServicePolicy.candidate("com.spotify.music.RemoteReceiver"));
        assertFalse(SpotifyServicePolicy.candidate(null));
    }
}
