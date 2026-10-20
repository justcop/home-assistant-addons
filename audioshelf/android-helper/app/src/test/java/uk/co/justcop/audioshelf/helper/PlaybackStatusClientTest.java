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

    @Test public void acceptedPlayHasDistinctFlagAndTimedDeviceDiscovery() throws Exception {
        PlaybackStatusClient.Result pending = PlaybackStatusClient.parse(200,
            "{\"state\":\"waiting\",\"phase\":\"sending_play\",\"play_accepted\":false}");
        assertFalse(pending.playAccepted);
        PlaybackStatusClient.Result accepted = PlaybackStatusClient.parse(200,
            "{\"state\":\"waiting\",\"phase\":\"confirming_playback\",\"play_accepted\":true,"
            + "\"device_seen_ms\":7170,\"play_accepted_ms\":9350,"
            + "\"last_device_check_ms\":7170,\"last_device_probe_ms\":125}");
        assertTrue(accepted.playAccepted);
        assertEquals("waiting", accepted.state);
        assertEquals(7170, accepted.deviceSeenMs);
        assertEquals(9350, accepted.playAcceptedMs);
        assertTrue(accepted.progress().contains("phone found at server +7170ms"));
        assertTrue(accepted.progress().contains("Play accepted at server +9350ms"));
        assertFalse(PlaybackStatusClient.parse(200, "{\"state\":\"waiting\"}").playAccepted);
    }

    @Test public void playerEvidenceIdentifiesStaleTrackWithoutLeakingIdsOrTokens() throws Exception {
        PlaybackStatusClient.Result pending = PlaybackStatusClient.parse(200,
            "{\"state\":\"waiting\",\"phase\":\"wrong_track\",\"play_accepted\":true,"
            + "\"verification\":{\"requested\":\"New song\",\"observed\":\"Old song\","
            + "\"requested_tag\":\"aaaa1234\",\"observed_tag\":\"bbbb5678\","
            + "\"device_matches\":true,\"track_matches\":false,\"is_playing\":true,"
            + "\"item_type\":\"track\",\"player_age_ms\":13000}}");
        assertEquals("waiting",pending.state);
        assertTrue(pending.progress().contains("requested=New song (#aaaa1234)"));
        assertTrue(pending.progress().contains("observed=Old song (#bbbb5678)"));
        assertTrue(pending.progress().contains("track matches=no"));
        assertTrue(pending.progress().contains("player age=13000ms"));
        assertFalse(pending.progress().contains("Bearer"));
        assertEquals("",PlaybackStatusClient.parse(200,"{\"state\":\"waiting\"}").observation);
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
    @Test public void reportsWhoCancelledWithoutLoggingArbitraryServerText() throws Exception {
        PlaybackStatusClient.Result user = PlaybackStatusClient.parse(200,
            "{\"state\":\"cancelled\",\"phase\":\"cancelled\",\"cancel_reason\":\"user_cancelled\"}");
        assertEquals("user_cancelled",user.cancellationReason);
        assertTrue(user.progress().contains("cancelled by user cancelled"));
        PlaybackStatusClient.Result device=PlaybackStatusClient.parse(200,
            "{\"state\":\"cancelled\",\"phase\":\"cancelled\",\"cancel_reason\":\"device_change\"}");
        assertTrue(device.progress().contains("device change"));
        PlaybackStatusClient.Result unknown=PlaybackStatusClient.parse(200,
            "{\"state\":\"cancelled\",\"phase\":\"cancelled\",\"cancel_reason\":\"private-token-xxx\"}");
        assertTrue(unknown.cancellationReason.isEmpty());
        assertFalse(unknown.progress().contains("private-token"));
    }

    @Test public void reportsPreparationStagesAndFiltersUnexpectedRetryText() throws Exception {
        PlaybackStatusClient.Result retry=PlaybackStatusClient.parse(200,
            """
            {"state":"waiting","phase":"preparing_transfer_ack","preparation_retry":"device_lost"}
            """);
        assertTrue(retry.progress().contains("waiting for Spotify to acknowledge device transfer"));
        assertTrue(retry.progress().contains("previous preparation retry: device lost"));
        PlaybackStatusClient.Result malicious=PlaybackStatusClient.parse(200,
            """
            {"state":"waiting","phase":"preparing_device_check","preparation_retry":"private_token"}
            """);
        assertEquals("",malicious.preparationRetry);
        assertFalse(malicious.progress().contains("private_token"));
    }

    @Test public void revocationAndUnexpectedStatusFailClosed() throws Exception {
        assertEquals("expired", PlaybackStatusClient.parse(404, "").state);
        assertEquals("cancelled", PlaybackStatusClient.parse(200, "{\"state\":\"cancelled\"}").state);
        try { PlaybackStatusClient.parse(200, "{\"state\":\"other\"}"); fail(); }
        catch (IOException expected) { /* Not a valid state. */ }
    }
}
