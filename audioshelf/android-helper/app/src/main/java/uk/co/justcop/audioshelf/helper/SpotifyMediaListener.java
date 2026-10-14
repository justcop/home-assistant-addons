package uk.co.justcop.audioshelf.helper;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.ComponentName;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.service.notification.NotificationListenerService;
import java.lang.ref.WeakReference;
import java.util.List;

/** Opt-in Spotify-only media status. Never commands Spotify or changes its device. */
public final class SpotifyMediaListener extends NotificationListenerService {
    private static final String CHANNEL="audioshelf-playback";
    private static WeakReference<ShelfActivity> view=new WeakReference<>(null);
    private static SpotifyMediaListener active;
    private final Handler handler=new Handler(Looper.getMainLooper());
    private MediaSessionManager manager;
    private MediaController selected;
    private final MediaController.Callback callback=new MediaController.Callback() {
        @Override public void onMetadataChanged(MediaMetadata metadata){publish();}
        @Override public void onPlaybackStateChanged(PlaybackState state){publish();}
        @Override public void onSessionDestroyed(){scan();}
    };
    private final MediaSessionManager.OnActiveSessionsChangedListener changed=sessions->scan();
    static void attach(ShelfActivity activity) {view=new WeakReference<>(activity);if(active!=null)active.publish();}
    static void detach(ShelfActivity activity) {if(view.get()==activity)view.clear();}
    static void dismiss(android.content.Context context) {
        ((NotificationManager)context.getSystemService(NOTIFICATION_SERVICE)).cancel(44);
    }
    @Override public void onListenerConnected() {
        super.onListenerConnected();active=this;
        manager=(MediaSessionManager)getSystemService(MEDIA_SESSION_SERVICE);
        try{
            manager.addOnActiveSessionsChangedListener(changed,new ComponentName(this,SpotifyMediaListener.class),handler);
            scan();
        }catch(SecurityException ignored){}
    }
    private void scan() {
        if(manager==null)return;
        List<MediaController> sessions;
        try{sessions=manager.getActiveSessions(new ComponentName(this,SpotifyMediaListener.class));}
        catch(SecurityException ignored){return;}
        MediaController next=null;
        for(MediaController item:sessions)if("com.spotify.music".equals(item.getPackageName())){next=item;break;}
        if(next!=selected){
            if(selected!=null)selected.unregisterCallback(callback);
            selected=next;
            if(selected!=null)selected.registerCallback(callback,handler);
        }
        publish();
    }
    private void publish() {
        MediaController spotify=selected;
        if(spotify==null){dismiss(this);return;}
        PlaybackState state=spotify.getPlaybackState();
        MediaMetadata meta=spotify.getMetadata();
        if(state==null || meta==null){dismiss(this);return;}
        boolean playing=state.getState()==PlaybackState.STATE_PLAYING;
        String title=meta.getString(MediaMetadata.METADATA_KEY_TITLE);
        String artist=meta.getString(MediaMetadata.METADATA_KEY_ARTIST);
        ShelfActivity activity=view.get();
        if(activity!=null)activity.runOnUiThread(()->activity.onSpotifyMediaChanged(title,artist,playing));
        boolean enabled=getSharedPreferences(ShelfActivity.PREFS,MODE_PRIVATE)
            .getBoolean("playback_notifications",false);
        if(!enabled || !playing){dismiss(this);return;}
        if(Build.VERSION.SDK_INT>=33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return;
        NotificationManager notifications=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(Build.VERSION.SDK_INT>=26)notifications.createNotificationChannel(
            new NotificationChannel(CHANNEL,"AudioShelf playback",NotificationManager.IMPORTANCE_LOW));
        Intent intent=new Intent(this,ShelfActivity.class).setAction(ShelfActivity.ACTION_NOW_PLAYING);
        PendingIntent pending=PendingIntent.getActivity(this,44,intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        builder.setSmallIcon(R.mipmap.ic_launcher).setContentTitle(title==null?"Spotify playing":title)
            .setContentText(artist==null?"Open AudioShelf":artist+" · Open AudioShelf")
            .setContentIntent(pending).setAutoCancel(false).setOnlyAlertOnce(true);
        notifications.notify(44,builder.build());
    }
    @Override public void onListenerDisconnected(){cleanup();super.onListenerDisconnected();}
    @Override public void onDestroy(){cleanup();super.onDestroy();}
    private void cleanup(){
        if(selected!=null){selected.unregisterCallback(callback);selected=null;}
        if(manager!=null){manager.removeOnActiveSessionsChangedListener(changed);manager=null;}
        if(active==this)active=null;
        dismiss(this);
    }
}
