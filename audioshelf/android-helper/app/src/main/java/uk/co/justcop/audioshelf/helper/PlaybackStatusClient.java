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
        final String observation, requestedTag, cancellationReason, preparationRetry, trackAudit, sdkTestUri;
        final java.util.List<String> sdkTrackUris;
        Result(String state, String message, String phase, int httpStatus, int devices, int confirmations,
               boolean accepted, int seenMs, int acceptedMs, int checkMs, int probeMs,
               String observed, String requestedTag, String cancelledBecause, String retry, String audit, String sdkUri,
               java.util.List<String> trackUris) {
            this.state = state; this.message = message; this.phase = phase;
            this.httpStatus = httpStatus; deviceChecks = devices; confirmationChecks = confirmations;
            playAccepted = accepted; deviceSeenMs = seenMs; playAcceptedMs = acceptedMs;
            lastDeviceCheckMs = checkMs; lastDeviceProbeMs = probeMs; observation = observed;
            this.requestedTag = requestedTag; cancellationReason = cancelledBecause; preparationRetry = retry;
            trackAudit = audit; sdkTestUri = sdkUri; sdkTrackUris = trackUris;
        }
        String progress() {
            String detail;
            switch (phase) {
                case "checking_devices": detail = "asking Spotify for available devices"; break;
                case "waiting_for_device": detail = "preferred phone not available yet"; break;
                case "sdk_waiting_for_local_play": detail = "SDK test: waiting for local first-track Play, without Web API Play"; break;
                case "preparing_playback": detail = "phone found; preparing playback"; break;
                case "preparing_player_state": detail = "reading Spotify active-player state"; break;
                case "preparing_device_check": detail = "rechecking preferred Spotify phone"; break;
                case "preparing_device_transfer": detail = "transferring the player to the phone (paused)"; break;
                case "preparing_transfer_ack": detail = "waiting for Spotify to acknowledge device transfer"; break;
                case "preparing_transfer_fallback": detail = "checking safe, explicitly targeted Play fallback"; break;
                case "preparing_playback_modes": detail = "checking shuffle and repeat are disabled"; break;
                case "preparing_modes_ack": detail = "waiting for Spotify shuffle/repeat confirmation"; break;
                case "preparing_explicit_play": detail = "ready to send one phone-pinned album Play"; break;
                case "sending_play": detail = "sending Play to Spotify"; break;
                case "play_accepted": detail = "Spotify accepted Play; verifying in the background"; break;
                case "confirming_playback": detail = "Play sent; checking Spotify playback"; break;
                case "no_player_state": detail = "Spotify has not returned player state yet"; break;
                case "wrong_device": detail = "Spotify reports a different device"; break;
                case "wrong_track": detail = "Spotify reports a different track"; break;
                case "paused": detail = "correct phone and track found, but Spotify is not playing"; break;
                case "confirmed": detail = "requested track confirmed playing on the selected phone"; break;
                case "cancelled": detail = cancellationReason.isEmpty() ? "playback request cancelled (source unknown)"
                    : "playback request cancelled by " + cancellationReason.replace('_',' '); break;
                default: detail = state.equals("waiting") ? "waiting for playback confirmation (update AudioShelf for detailed progress)" : state;
            }
            String counts = deviceChecks < 0 ? "" :
                " [device checks " + deviceChecks + ", playback checks " + confirmationChecks + "]";
            String times = lastDeviceCheckMs < 0 ? "" :
                " [server: last device check +" + lastDeviceCheckMs + "ms, request " + lastDeviceProbeMs + "ms]";
            if (deviceSeenMs >= 0) times += " [phone found at server +" + deviceSeenMs + "ms]";
            if (playAcceptedMs >= 0) times += " [Play accepted at server +" + playAcceptedMs + "ms]";
            String retry = preparationRetry.isEmpty() ? "" : " [previous preparation retry: "+preparationRetry.replace('_',' ')+"]";
            return detail + counts + times + retry
                + (observation.isEmpty() ? "" : " [Spotify player: "+observation+"]");
        }
    }

    /** Readable, bounded evidence from the authenticated server's one-job
     * helper view. Full track/device IDs and the Spotify response remain private. */
    static String observation(JsonObject object) {
        if (!object.has("verification") || !object.get("verification").isJsonObject()) return "";
        JsonObject check=object.getAsJsonObject("verification");
        String wanted=safe(check,"requested");
        String actual=safe(check,"observed");
        String wantedTag=safe(check,"requested_tag");
        String actualTag=safe(check,"observed_tag");
        String linkedTag=safe(check,"linked_tag");
        String itemType=safe(check,"item_type");
        String age=check.has("player_age_ms") && check.get("player_age_ms").isJsonPrimitive()
            ? check.get("player_age_ms").getAsString()+"ms" : "unknown";
        return "requested="+(wanted.isEmpty()?"unknown":wanted)
            + (wantedTag.isEmpty()?"":" (#"+wantedTag+")")
            + "; observed="+(actual.isEmpty()?"none":actual)
            + (actualTag.isEmpty()?"":" (#"+actualTag+")")
            + (linkedTag.isEmpty()?"":"; Spotify linked-from #"+linkedTag)
            + "; item="+(itemType.isEmpty()?"missing":itemType)
            + "; device matches="+optionalFlag(check,"device_matches")
            + "; track matches="+optionalFlag(check,"track_matches")
            + "; is_playing="+optionalFlag(check,"is_playing")
            + "; player age="+age;
    }
    private static String safe(JsonObject object, String name) {
        if (!object.has(name) || object.get(name).isJsonNull() || !object.get(name).isJsonPrimitive()) return "";
        String value=object.get(name).getAsString().replaceAll("[\\p{Cntrl}]", " ");
        return value.length()>72 ? value.substring(0,72) : value;
    }
    private static String optionalFlag(JsonObject object,String name) {
        if (!object.has(name) || object.get(name).isJsonNull() || !object.get(name).isJsonPrimitive()) return "unknown";
        return object.get(name).getAsBoolean()?"yes":"no";
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
                false, -1, -1, -1, -1, "", "", "", "", "", "", java.util.Collections.emptyList());
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
                optionalMillis(object, "last_device_check_ms"), optionalMillis(object, "last_device_probe_ms"),
                observation(object), requestedTag(object), cancellationReason(object), preparationRetry(object), trackAudit(object), sdkTestUri(object), sdkTrackUris(object));
        } catch (RuntimeException exception) {
            throw new IOException("Invalid playback response", exception);
        }
    }

    /** Bounded, typed values only; no raw Spotify IDs or untrusted API text. */
    static String trackAudit(JsonObject object) {
        if (!object.has("track_audit") || !object.get("track_audit").isJsonObject()) return "";
        JsonObject audit=object.getAsJsonObject("track_audit");
        String state=safe(audit,"state");
        if (!state.equals("checking") && !state.equals("complete") && !state.equals("failed")) return "";
        String market=safe(audit,"market");
        if (!market.matches("[A-Z]{2}")) market="unknown";
        int count=0;
        try { count=audit.get("track_count").getAsInt(); } catch (RuntimeException ignored) {}
        if (count<0 || count>100) count=0;
        String queue=safe(audit,"queue_tag");
        if (!queue.matches("[0-9a-f]{10}")) queue="unknown";
        StringBuilder out=new StringBuilder("market="+market+"; queued="+count+" tracks; queue #"+queue+"; audit="+state);
        if (audit.has("samples") && audit.get("samples").isJsonArray()) {
            int n=0;
            for (com.google.gson.JsonElement element:audit.getAsJsonArray("samples")) {
                if (n++>=3 || !element.isJsonObject()) break;
                JsonObject sample=element.getAsJsonObject();
                String title=safe(sample,"title"), status=safe(sample,"status");
                String saved=safe(sample,"saved_tag"), returned=safe(sample,"returned_tag");
                String playable=safe(sample,"playable"), restricted=safe(sample,"restriction");
                if (!saved.matches("[0-9a-f]{10}")) saved="unknown";
                if (!returned.matches("[0-9a-f]{10}")) returned="none";
                if (!java.util.Arrays.asList("found","missing","not_found","rate_limited",
                     "access_denied","request_failed","error").contains(status)) status="unknown";
                if (!java.util.Arrays.asList("yes","no","unknown").contains(playable)) playable="unknown";
                if (!java.util.Arrays.asList("market","product","explicit","other").contains(restricted)) restricted="none";
                int position=0;
                try { position=sample.get("position").getAsInt(); } catch (RuntimeException ignored) {}
                if (position<1 || position>100) position=0;
                String linked=(sample.has("relinked") && sample.get("relinked").isJsonPrimitive()
                    && sample.get("relinked").getAsBoolean()) ? "yes" : "no";
                out.append("; #").append(position).append(" ").append(title)
                    .append(" [saved #").append(saved).append(", Spotify=").append(status)
                    .append(", playable=").append(playable).append(", relinked=").append(linked)
                    .append(", returned #").append(returned).append(", restriction=").append(restricted).append("]");
            }
        }
        String visible=safe(audit,"phone_visible_after_play");
        String active=safe(audit,"phone_active_after_play");
        if (!java.util.Arrays.asList("yes","no","unknown").contains(visible)) visible="unknown";
        if (!java.util.Arrays.asList("yes","no","unknown").contains(active)) active="unknown";
        out.append("; phone visible after Play=").append(visible).append(", active=").append(active);
        return out.toString();
    }

    // One scoped, short-lived helper capability may retrieve this validated
    // first-track URI to invoke local App Remote. Never print it in logs.
    private static java.util.List<String> sdkTrackUris(JsonObject object) {
        if (!object.has("sdk_full_album") || !object.get("sdk_full_album").isJsonPrimitive()
                || !object.get("sdk_full_album").getAsBoolean()
                || !object.has("sdk_track_uris") || !object.get("sdk_track_uris").isJsonArray())
            return java.util.Collections.emptyList();
        com.google.gson.JsonArray values=object.getAsJsonArray("sdk_track_uris");
        if (values.size()<1 || values.size()>100) return java.util.Collections.emptyList();
        java.util.ArrayList<String> uris=new java.util.ArrayList<>();
        for (com.google.gson.JsonElement item : values) {
            if (!item.isJsonPrimitive()) return java.util.Collections.emptyList();
            String uri=item.getAsString();
            if (!uri.matches("spotify:track:[A-Za-z0-9]{22}")) return java.util.Collections.emptyList();
            uris.add(uri);
        }
        return java.util.Collections.unmodifiableList(uris);
    }

    private static String sdkTestUri(JsonObject object) {
        if (!object.has("sdk_single_track") || object.get("sdk_single_track").isJsonNull()
                || !object.get("sdk_single_track").isJsonPrimitive()
                || !object.get("sdk_single_track").getAsBoolean()) return "";
        String uri=safe(object,"sdk_test_uri");
        return uri.matches("spotify:track:[A-Za-z0-9]{22}") ? uri : "";
    }

    private static String requestedTag(JsonObject object) {
        if (!object.has("verification") || !object.get("verification").isJsonObject()) return "";
        String tag=safe(object.getAsJsonObject("verification"),"requested_tag");
        return tag.matches("[0-9a-f]{10}") ? tag : "";
    }

    private static String preparationRetry(JsonObject object) {
        if (!object.has("preparation_retry") || object.get("preparation_retry").isJsonNull()
                || !object.get("preparation_retry").isJsonPrimitive()) return "";
        String reason=object.get("preparation_retry").getAsString();
        switch(reason) {
            case "device_lost": case "transfer_unconfirmed":
            case "player_not_ready": case "preparation_unavailable": return reason;
            default: return "";
        }
    }

    private static String cancellationReason(JsonObject object) {
        if (!object.has("cancel_reason") || object.get("cancel_reason").isJsonNull()
                || !object.get("cancel_reason").isJsonPrimitive()) return "";
        String reason=object.get("cancel_reason").getAsString();
        // Never emit arbitrary server response text in a support log.
        switch (reason) {
            case "cancelled": case "user_cancelled": case "new_playback":
            case "device_change": case "direct_playback": case "logout":
            case "spotify_disconnect": return reason;
            default: return "";
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

    /** Idempotent queue receipt for a single verified handoff, never track URIs. */
    static void reportSdkQueue(String origin, String account, String job, String token,
                               int queuedCount, boolean success) throws IOException {
        URL url = new URL(origin + "/api/helper/playback-handoff/" + account + "/" + job + "/sdk-queue");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        try {
            connection.setRequestMethod("POST");
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(2500);
            connection.setReadTimeout(3500);
            connection.setUseCaches(false);
            connection.setDoOutput(true);
            connection.setRequestProperty("Authorization", "Bearer " + token);
            connection.setRequestProperty("X-AudioShelf-Request", "1");
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            String payload="{\\"queued_count\\":"+queuedCount+",\\"success\\":"+(success?"true":"false")+"}";
            byte[] bytes=payload.getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(bytes.length);
            try (java.io.OutputStream output=connection.getOutputStream()) {
                output.write(bytes);
            }
            int code=connection.getResponseCode();
            if (code!=200) throw new HttpFailure(code);
            try (InputStream input=connection.getInputStream()) {
                while (input.read()!=-1) { /* release connection */ }
            }
        } finally {
            connection.disconnect();
        }
    }

    private PlaybackStatusClient() {}
}
