package uk.co.justcop.audioshelf.helper;

import org.junit.Test;
import static org.junit.Assert.*;

public class SpotifyServicePolicyTest {
    @Test public void acceptsRealSpotifyProtocolService() {
        assertTrue(SpotifyServicePolicy.candidate(
            "com.spotify.interapp.service.service.AppProtocolRemoteService"));
        assertTrue(SpotifyServicePolicy.candidate("com.spotify.music.appremote.AppRemoteService"));
    }

    @Test public void excludesWidgetServicesAndUnrelatedServices() {
        assertFalse(SpotifyServicePolicy.candidate(
            "androidx.glance.appwidget.GlanceRemoteViewsService"));
        assertFalse(SpotifyServicePolicy.candidate(
            "androidx.core.widget.RemoteViewsCompatService"));
        assertFalse(SpotifyServicePolicy.candidate("com.spotify.music.BrowserService"));
        assertFalse(SpotifyServicePolicy.candidate("com.spotify.music.RemoteReceiver"));
        assertFalse(SpotifyServicePolicy.candidate("com.other.remote.AppRemoteService"));
        assertFalse(SpotifyServicePolicy.candidate(null));
    }
}
