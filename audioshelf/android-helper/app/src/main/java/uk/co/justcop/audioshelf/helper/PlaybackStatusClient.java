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
        final String message;
        Result(String state, String message) { this.state = state; this.message = message; }
    }

    static Result parse(int code, String response) throws IOException {
        if (code == 401 || code == 403 || code == 404 || code == 410)
            return new Result("expired", "Playback request unavailable.");
        if (code != 200) throw new IOException("Unexpected HTTP status");
        try {
            JsonObject object = JsonParser.parseString(response).getAsJsonObject();
            String state = object.get("state").getAsString();
            if (!state.equals("waiting") && !state.equals("started") && !state.equals("failed")
                    && !state.equals("expired") && !state.equals("cancelled") && !state.equals("unconfirmed"))
                throw new IOException("Unknown playback state");
            String message = object.has("error") && !object.get("error").isJsonNull()
                ? object.get("error").getAsString() : state;
            return new Result(state, message);
        } catch (RuntimeException exception) {
            throw new IOException("Invalid playback response", exception);
        }
    }

    static Result poll(String origin, String account, String job, String token) throws IOException {
        URL url = new URL(origin + "/api/helper/playback-handoff/" + account + "/" + job);
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(25000); // Network safety, not a success deadline.
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            int code = connection.getResponseCode();
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
