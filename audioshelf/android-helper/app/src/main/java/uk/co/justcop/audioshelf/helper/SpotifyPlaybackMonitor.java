package uk.co.justcop.audioshelf.helper;

import android.app.NotificationManager;
import android.content.ComponentName;
import android.content.Context;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;

/** Local, read-only Spotify observation. No SDK session, network or transport commands. */
final class SpotifyPlaybackMonitor {
    private final Context context;
    String status = "not sampled";
    private final LocalPlaybackSignal signal = new LocalPlaybackSignal();

    SpotifyPlaybackMonitor(Context context) { this.context = context; }

    static boolean enabled(Context context) {
        if (android.os.Build.VERSION.SDK_INT < 27)
            return androidx.core.app.NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.getPackageName());
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        return manager.isNotificationListenerAccessGranted(new ComponentName(context, SpotifyNotificationAccess.class));
    }

    boolean sample() {
        if (!enabled(context)) { status = "notification access not enabled"; return false; }
        MediaSessionManager manager = context.getSystemService(MediaSessionManager.class);
        AudioManager audio = context.getSystemService(AudioManager.class);
        status = "no Spotify media session";
        for (MediaController controller : manager.getActiveSessions(new ComponentName(context, SpotifyNotificationAccess.class))) {
            if (!"com.spotify.music".equals(controller.getPackageName())) continue;
            MediaController.PlaybackInfo info = controller.getPlaybackInfo();
            PlaybackState state = controller.getPlaybackState();
            status = "Spotify " + (info != null && info.getPlaybackType() == MediaController.PlaybackInfo.PLAYBACK_TYPE_LOCAL ? "local" : "remote/unknown")
                + "; " + (state != null && state.getState() == PlaybackState.STATE_PLAYING ? "playing" : "not playing")
                + "; device music " + (audio.isMusicActive() ? "active" : "inactive");
            if (!LocalPlaybackSignal.qualifies(controller.getPackageName(),
                    info != null && info.getPlaybackType() == MediaController.PlaybackInfo.PLAYBACK_TYPE_LOCAL,
                    state != null && state.getState() == PlaybackState.STATE_PLAYING, audio.isMusicActive())) continue;
            MediaMetadata metadata = controller.getMetadata();
            String track = "";
            if (metadata != null) {
                track = metadata.getString(MediaMetadata.METADATA_KEY_MEDIA_ID);
                if (track == null || track.isEmpty()) {
                    String title = metadata.getString(MediaMetadata.METADATA_KEY_TITLE);
                    String artist = metadata.getString(MediaMetadata.METADATA_KEY_ARTIST);
                    track = title == null ? "" : title + "\n" + (artist == null ? "" : artist);
                }
            }
            // Identity stays in memory to reject an old track. Never logged or transmitted.
            return signal.observe(true, track);
        }
        return signal.observe(false, "");
    }
}
