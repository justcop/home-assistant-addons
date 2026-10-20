package uk.co.justcop.audioshelf.helper;

import android.os.Handler;
import android.os.SystemClock;
import com.spotify.android.appremote.api.SpotifyAppRemote;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.function.Consumer;

/**
 * Read the Spotify app's *local* player state, independently of Web API state.
 * Never issue Play, alter queue, choose a device or expose raw track URIs.
 * One Resume is allowed only after the server accepted the canonical Play,
 * and the SDK itself reports that exact first track as paused.
 */
final class SpotifyLocalPlayerProbe {
    private final Handler handler;
    private final Consumer<String> log;
    private SpotifyAppRemote remote;
    private long acceptedSeenAt = -1, lastProbeAt = -1;
    private boolean pending, resumeAttempted, closed;
    private String lastObservation = "";
    private int generation;

    SpotifyLocalPlayerProbe(Handler handler, Consumer<String> log) {
        this.handler = handler;
        this.log = log;
    }

    static String trackTag(String uri) {
        if (uri == null || !uri.matches("spotify:track:[A-Za-z0-9]{22}")) return "";
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(uri.substring("spotify:track:".length()).getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(10);
            for (int n = 0; n < 5; n++) hex.append(String.format(Locale.ROOT, "%02x", digest[n] & 0xff));
            return hex.toString();
        } catch (Exception ignored) { return ""; }
    }

    static boolean shouldResume(boolean serverAccepted, String phase, String expectedTag,
                                String observedTag, boolean paused) {
        return serverAccepted && ("no_player_state".equals(phase) || "paused".equals(phase))
            && expectedTag != null && expectedTag.matches("[0-9a-f]{10}")
            && expectedTag.equals(observedTag) && paused;
    }

    void connected(SpotifyAppRemote appRemote) {
        if (closed) return;
        remote = appRemote;
        // This pre-Play observation is diagnostic only; never Resume from it.
        observe("", "", false);
    }

    void serverStatus(PlaybackStatusClient.Result result) {
        if (closed || remote == null || !"waiting".equals(result.state)) return;
        if (result.playAccepted && acceptedSeenAt < 0) acceptedSeenAt = SystemClock.elapsedRealtime();
        boolean acceptedAndSettled = acceptedSeenAt >= 0
            && SystemClock.elapsedRealtime() - acceptedSeenAt >= 2000;
        if (!acceptedAndSettled) return;
        if (!("no_player_state".equals(result.phase) || "paused".equals(result.phase)
                || "wrong_track".equals(result.phase) || "wrong_device".equals(result.phase))) return;
        observe(result.requestedTag, result.phase, result.playAccepted);
    }

    private void observe(String expectedTag, String phase, boolean accepted) {
        if (closed || remote == null || pending) return;
        long now = SystemClock.elapsedRealtime();
        if (lastProbeAt >= 0 && now - lastProbeAt < 1700) return;
        lastProbeAt = now;
        pending = true;
        int ticket = ++generation;
        SpotifyAppRemote session = remote;
        handler.postDelayed(() -> {
            if (closed || ticket != generation || remote != session || !pending) return;
            pending = false;
            log.accept("Local Spotify SDK player-state request received no callback within 4s.");
        }, 4000);
        try {
            session.getPlayerApi().getPlayerState()
                .setResultCallback(state -> handler.post(() -> {
                    if (closed || ticket != generation || remote != session) return;
                    pending = false;
                    String found = state == null || state.track == null
                        ? "" : trackTag(state.track.uri);
                    boolean paused = state != null && state.isPaused;
                    String observation = (state == null ? "unavailable"
                        : state.track == null ? "no local track"
                        : "track #" + (found.isEmpty() ? "unrecognised URI" : found)
                          + (paused ? ", paused" : ", playing"));
                    if (!observation.equals(lastObservation)) {
                        lastObservation = observation;
                        log.accept("Spotify App Remote local player: " + observation
                            + " (independent of Web API player state).");
                    }
                    if (!resumeAttempted
                            && shouldResume(accepted, phase, expectedTag, found, paused)) {
                        resumeAttempted = true;
                        log.accept("Exact requested track is paused locally despite accepted server Play."
                            + " Sending one SDK Resume; no new Play or queue modification.");
                        try {
                            session.getPlayerApi().resume()
                                .setResultCallback(empty -> handler.post(() -> {
                                    if (!closed && remote == session)
                                        log.accept("Spotify App Remote Resume acknowledged; server must still verify playback.");
                                }))
                                .setErrorCallback(error -> handler.post(() -> {
                                    if (!closed && remote == session)
                                        log.accept("Spotify App Remote Resume failed: " + WakeDiagnostics.failure(error));
                                }));
                        } catch (RuntimeException error) {
                            log.accept("Spotify App Remote Resume threw: " + WakeDiagnostics.failure(error));
                        }
                    }
                }))
                .setErrorCallback(error -> handler.post(() -> {
                    if (closed || ticket != generation || remote != session) return;
                    pending = false;
                    log.accept("Spotify App Remote local player-state query failed: "
                        + WakeDiagnostics.failure(error));
                }));
        } catch (RuntimeException error) {
            pending = false;
            log.accept("Spotify App Remote local player-state query threw: " + WakeDiagnostics.failure(error));
        }
    }

    void close() {
        closed = true;
        ++generation;
        remote = null;
        pending = false;
    }
}
