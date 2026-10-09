package uk.co.justcop.audioshelf.helper;

import java.io.IOException;
import org.junit.Test;
import static org.junit.Assert.*;

public class PlaybackStatusClientTest {
    @Test public void reportsServerProgressWithoutTreatingPlayDispatchAsSuccess() throws Exception {
        PlaybackStatusClient.Result response = PlaybackStatusClient.parse(200,
            "{\"state\":\"waiting\",\"phase\":\"sending_play\",\"device_checks\":2,\"confirmation_checks\":0}");
        assertEquals("waiting", response.state);
        assertEquals(200, response.httpStatus);
        assertTrue(response.progress().contains("sending Play"));
        assertTrue(response.progress().contains("device checks 2"));
        response = PlaybackStatusClient.parse(200, "{\"state\":\"started\",\"phase\":\"confirmed\"}");
        assertTrue(response.progress().contains("confirmed playing"));
        assertTrue(PlaybackStatusClient.parse(200, "{\"state\":\"waiting\"}").progress().contains("update AudioShelf"));
    }

    @Test public void distinguishesDnsTlsHttpAndTimeoutWithoutLoggingPrivateExceptionText() {
        assertEquals("DNS could not resolve AudioShelf", PlaybackStatusClient.describe(new java.net.UnknownHostException("private.host")));
        assertEquals("HTTPS/TLS connection failed", PlaybackStatusClient.describe(new javax.net.ssl.SSLException("private host and certificate")));
        assertEquals("connection or response timed out", PlaybackStatusClient.describe(new java.net.SocketTimeoutException("private URL")));
        assertEquals("HTTP 502 from AudioShelf or its proxy", PlaybackStatusClient.describe(new PlaybackStatusClient.HttpFailure(502)));
        assertEquals("SocketException", PlaybackStatusClient.describe(new java.net.SocketException("private network details")));
    }

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
