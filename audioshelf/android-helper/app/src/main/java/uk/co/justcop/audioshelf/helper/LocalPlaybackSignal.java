package uk.co.justcop.audioshelf.helper;

/** Distinguishes a fresh local Spotify start from stale/already-playing media. */
final class LocalPlaybackSignal {
    private boolean initialized, wasPlaying;
    private String previousTrack = "";

    static boolean qualifies(String packageName, boolean localOutput, boolean playing, boolean audioActive) {
        return "com.spotify.music".equals(packageName) && localOutput && playing && audioActive;
    }

    boolean observe(boolean localSpotifyPlaying, String track) {
        String current = track == null ? "" : track;
        boolean started = initialized && localSpotifyPlaying && (!wasPlaying
            || (!current.isEmpty() && !previousTrack.isEmpty() && !current.equals(previousTrack)));
        initialized = true;
        wasPlaying = localSpotifyPlaying;
        previousTrack = current;
        return started;
    }
}
