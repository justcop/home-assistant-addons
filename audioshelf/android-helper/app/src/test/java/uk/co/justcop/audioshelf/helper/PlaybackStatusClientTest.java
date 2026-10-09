package uk.co.justcop.audioshelf.helper;

import java.io.IOException;
import org.junit.Test;
import static org.junit.Assert.*;

public class PlaybackStatusClientTest {
    @Test public void ignoresPendingStatusAndRecognisesVerifiedPlayback() throws Exception {
        assertEquals("waiting", PlaybackStatusClient.parse(200, "{\"state\":\"waiting\",\"error\":null}").state);
        assertEquals("started", PlaybackStatusClient.parse(200, "{\"state\":\"started\"}").state);
        assertEquals("unconfirmed", PlaybackStatusClient.parse(200, "{\"state\":\"unconfirmed\"}").state);
    }
    @Test public void revocationAndUnexpectedStatusFailClosed() throws Exception {
        assertEquals("expired", PlaybackStatusClient.parse(404, "").state);
        assertEquals("cancelled", PlaybackStatusClient.parse(200, "{\"state\":\"cancelled\"}").state);
        try { PlaybackStatusClient.parse(200, "{\"state\":\"other\"}"); fail(); }
        catch (IOException expected) { /* Not a valid state. */ }
    }
}
