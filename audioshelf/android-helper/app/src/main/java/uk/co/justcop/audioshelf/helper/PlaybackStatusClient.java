package uk.co.justcop.audioshelf.helper;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Read only one short-lived playback job; the bearer token is never included in the URL. */
final class PlaybackStatusClient {
    static final class Result {
        final String state;
        final String message, phase;
        final int httpStatus, deviceChecks, confirmationChecks;
        final boolean playAccepted;
        final int deviceSeenMs, playAcceptedMs, lastDeviceCheckMs, lastDeviceProbeMs;
        Result(String state, String message, String phase, int httpStatus, int devices, int confirmations,
               boolean accepted, int seenMs, int acceptedMs, int checkMs, int probeMs) {
            this.state = state; this.message = message; this.phase = phase;
            this.httpStatus = httpStatus; deviceChecks = devices; confirmationChecks = confirmations;
            playAccepted = accepted; deviceSeenMs = seenMs; playAcceptedMs = acceptedMs;
            lastDeviceCheckMs = checkMs; lastDeviceProbeMs = probeMs;
        }
        String progress() {
            String detail;
            switch (phase) {
                case "checking_devices": detail = "asking Spotify for available devices"; break;
                case "waiting_for_device": detail = "preferred phone not available yet"; break;
                case "preparing_playback": detail = "phone found; preparing playback"; break;
                case "sending_play": detail = "sending Play to Spotify"; break;
                case "play_accepted": detail = "Spotify accepted Play; verifying in the background"; break;
                case "confirming_playback": detail = "Play sent; checking Spotify playback"; break;
                case "no_player_state": detail = "Spotify has not returned player state yet"; break;
                case "wrong_device": detail = "Spotify reports a different device"; break;
                case "wrong_track": detail = "Spotify reports a different track"; break;
                case "paused": detail = "correct phone and track found, but Spotify is not playing"; break;
                case "confirmed": detail = "requested track confirmed playing on the selected phone"; break;
                default: detail = state.equals("waiting") ? "waiting for playback confirmation (update AudioShelf for detailed progress)" : state;
            }
            String counts = deviceChecks < 0 ? "" :
                " [device checks " + deviceChecks + ", playback checks " + confirmationChecks + "]";
            String times = lastDeviceCheckMs < 0 ? "" :
                " [server: last device check +" + lastDeviceCheckMs + "ms, request " + lastDeviceProbeMs + "ms]";
            if (deviceSeenMs >= 0) times += " [phone found at server +" + deviceSeenMs + "ms]";
            if (playAcceptedMs >= 0) times += " [Play accepted at server +" + playAcceptedMs + "ms]";
            return detail + counts + times;
        }
    }

    interface Progress { void update(String stage); }

    static final class HttpFailure extends IOException {
        final int status;
        HttpFailure(int status) { super("HTTP status " + status); this.status = status; }
    }

    static String describe(IOException error) {
        if (error instanceof java.net.UnknownHostException) return "DNS could not resolve AudioShelf";
        if (error instanceof javax.net.ssl.SSLException) return "HTTPS/TLS connection failed";
        if (error instanceof java.net.SocketTimeoutException) return "connection or response timed out";
        if (error instanceof HttpFailure) return "HTTP " + ((HttpFailure) error).status + " from AudioShelf or its proxy";
        return error.getClass().getSimpleName();
    }

    static Result parse(int code, String response) throws IOException {
        if (code == 401 || code == 403 || code == 404 || code == 410)
            return new Result("expired", "Playback request unavailable.", "expired", code, -1, -1,
                false, -1, -1, -1, -1);
        if (code != 200) throw new HttpFailure(code);
        try {
            JsonObject object = JsonParser.parseString(response).getAsJsonObject();
            String state = object.get("state").getAsString();
            if (!state.equals("waiting") && !state.equals("started") && !state.equals("failed")
                    && !state.equals("expired") && !state.equals("cancelled") && !state.equals("unconfirmed"))
                throw new IOException("Unknown playback state");
            String message = object.has("error") && !object.get("error").isJsonNull()
                ? object.get("error").getAsString() : state;
            String phase = object.has("phase") ? object.get("phase").getAsString() : "";
            int devices = object.has("device_checks") ? object.get("device_checks").getAsInt() : -1;
            int confirmations = object.has("confirmation_checks") ? object.get("confirmation_checks").getAsInt() : -1;
            boolean accepted = object.has("play_accepted") && !object.get("play_accepted").isJsonNull()
                && object.get("play_accepted").getAsBoolean();
            return new Result(state, message, phase, code, devices, confirmations, accepted,
                optionalMillis(object, "device_seen_ms"), optionalMillis(object, "play_accepted_ms"),
                optionalMillis(object, "last_device_check_ms"), optionalMillis(object, "last_device_probe_ms"));
        } catch (RuntimeException exception) {
            throw new IOException("Invalid playback response", exception);
        }
    }

    private static int optionalMillis(JsonObject object, String name) {
        return object.has(name) && !object.get(name).isJsonNull() ? object.get(name).getAsInt() : -1;
    }

    static Result poll(String origin, String account, String job, String token, Progress progress) throws IOException {
        URL url = new URL(origin + "/api/helper/playback-handoff/" + account + "/" + job + "?wait=0");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(2000);
            connection.setReadTimeout(3000); // Immediate snapshot, with failure feedback inside the wake window.
            connection.setUseCaches(false);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            progress.update("connecting over HTTPS (DNS, connection and TLS)");
            int code = connection.getResponseCode();
            progress.update("received HTTP " + code);
            if (code != 200) return parse(code, "");
            StringBuilder body = new StringBuilder();
            try (InputStream stream = connection.getInputStream();
                BufferedReader reader = new BufferedReader(new InputStreamReader(stream, StandardCharsets.UTF_8))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    body.append(line);
                    if (body.length() > 8192) throw new IOException("Unexpected response size");
                }
            }
            return parse(code, body.toString());
        } finally {
            connection.disconnect();
        }
    }

    private PlaybackStatusClient() {}
}
