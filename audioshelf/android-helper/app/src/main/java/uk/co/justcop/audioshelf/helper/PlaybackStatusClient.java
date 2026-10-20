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
        final java.util.List<String> sdkAlbumUris;
        final int sdkQueued, sdkExpected;
        final boolean firstTrackConfirmed;
        Result(String state, String message, String phase, int httpStatus, int devices, int confirmations,
               boolean accepted, int seenMs, int acceptedMs, int checkMs, int probeMs,
               String observed, String requestedTag, String cancelledBecause, String retry, String audit, String sdkUri,
               java.util.List<String> albumUris, int queued, int expected, boolean confirmedFirst) {
            this.state = state; this.message = message; this.phase = phase;
            this.httpStatus = httpStatus; deviceChecks = devices; confirmationChecks = confirmations;
            playAccepted = accepted; deviceSeenMs = seenMs; playAcceptedMs = acceptedMs;
            lastDeviceCheckMs = checkMs; lastDeviceProbeMs = probeMs; observation = observed;
            this.requestedTag = requestedTag; cancellationReason = cancelledBecause; preparationRetry = retry;
            trackAudit = audit; sdkTestUri = sdkUri;
            sdkAlbumUris = albumUris; sdkQueued = queued; sdkExpected = expected;
            firstTrackConfirmed = confirmedFirst;
        }
        String progress() {
            String detail;
            switch (phase) {
                case "checking_devices": detail = "asking Spotify for available devices"; break;
                case "waiting_for_device": detail = "preferred phone not available yet"; break;
                case "sdk_queue_ready": detail = "canonical SDK album queued for Android handoff"; break;
                case "sdk_queueing": detail = "SDK album queue progressing"; break;
                case "sdk_queue_confirming": detail = "SDK queue acknowledged; checking Spotify order"; break;
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
            String queued = sdkExpected>0 ? " [SDK tracks acknowledged "+sdkQueued+"/"+sdkExpected+"]" : "";
            return detail + counts + times + retry + queued
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
                false, -1, -1, -1, -1, "", "", "", "", "", "",
                java.util.Collections.emptyList(), 0, 0, false);
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
                observation(object), requestedTag(object), cancellationReason(object), preparationRetry(object), trackAudit(object), sdkTestUri(object),
                sdkAlbumUris(object), boundedCount(object,"sdk_queue_count"), boundedCount(object,"sdk_queue_expected"),
                firstTrackConfirmed(object));
        } catch (RuntimeException exception) {
            throw new IOException("Invalid playback response", exception);
        }
    }

    private static boolean firstTrackConfirmed(JsonObject object) {
        if (!object.has("verification") || !object.get("verification").isJsonObject()) return false;
        JsonObject v=object.getAsJsonObject("verification");
        return flag(v,"device_matches") && flag(v,"track_matches") && flag(v,"is_playing");
    }

    private static boolean flag(JsonObject object,String name) {
        return object.has(name) && object.get(name).isJsonPrimitive()
            && object.get(name).getAsJsonPrimitive().isBoolean()
            && object.get(name).getAsBoolean();
    }

    private static int boundedCount(JsonObject object,String name) {
        try {
            int value=object.get(name).getAsInt();
            return value>=0 && value<=100 ? value : 0;
        } catch (RuntimeException ignored) { return 0; }
    }

    /** Only the short-lived bearer-authenticated helper receives this list;
     * no URLs, device IDs, credentials or unreconciled release tracks accepted. */
    private static java.util.List<String> sdkAlbumUris(JsonObject object) throws IOException {
        if (!object.has("sdk_album") || !object.get("sdk_album").isJsonPrimitive()
                || !object.get("sdk_album").getAsBoolean()) return java.util.Collections.emptyList();
        if (!object.has("sdk_album_uris") || object.get("sdk_album_uris").isJsonNull())
            return java.util.Collections.emptyList();
        if (!object.get("sdk_album_uris").isJsonArray()) throw new IOException("Invalid SDK album queue");
        com.google.gson.JsonArray values=object.getAsJsonArray("sdk_album_uris");
        if (values.size()<1 || values.size()>100) throw new IOException("Invalid SDK album length");
        java.util.ArrayList<String> uris=new java.util.ArrayList<>();
        for (com.google.gson.JsonElement element:values) {
            if (!element.isJsonPrimitive() || !element.getAsString().matches("spotify:track:[A-Za-z0-9]{22}"))
                throw new IOException("Invalid SDK album track URI");
            uris.add(element.getAsString());
        }
        return java.util.Collections.unmodifiableList(uris);
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

    /** HTTPS receipt for sequential local SDK queue callbacks. Never includes
     * Spotify URIs or tokens in URL, logs or body. A non-2xx response stops
     * further queueing rather than falsely reporting a complete album. */
    static void reportSdkQueue(String origin,String account,String job,String token,
                               int count,String state) throws IOException {
        if (count<0 || count>100 || (count==0 && !state.equals("failed")) || !(state.equals("progress") || state.equals("complete") || state.equals("failed")))
            throw new IOException("Invalid SDK queue receipt");
        URL url=new URL(origin+"/api/helper/playback-handoff/"+account+"/"+job+"/sdk-queue");
        HttpURLConnection conn=(HttpURLConnection)url.openConnection();
        try {
            conn.setInstanceFollowRedirects(false);
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(2000);
            conn.setReadTimeout(4000);
            conn.setDoOutput(true);
            conn.setUseCaches(false);
            conn.setRequestProperty("Authorization","Bearer "+token);
            conn.setRequestProperty("X-AudioShelf-Request","1");
            conn.setRequestProperty("Content-Type","application/json");
            conn.setRequestProperty("Accept","application/json");
            byte[] body=("{\"count\":"+count+",\"state\":\""+state+"\"}")
                .getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(body.length);
            try(java.io.OutputStream out=conn.getOutputStream()){out.write(body);}
            if(conn.getResponseCode()!=200)throw new HttpFailure(conn.getResponseCode());
        } finally {conn.disconnect();}
    }

    private PlaybackStatusClient() {}
}
