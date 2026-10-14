package uk.co.justcop.audioshelf.helper;

import android.content.Context;
import android.content.Intent;
import android.content.pm.ShortcutInfo;
import android.content.pm.ShortcutManager;
import android.graphics.drawable.Icon;
import android.os.Build;
import java.util.Arrays;

/** Long-press the AudioShelf Android launcher icon for shelf and Now Playing. */
final class ShelfShortcuts {
    static void install(Context context) {
        if(Build.VERSION.SDK_INT<25)return;
        ShortcutManager manager=context.getSystemService(ShortcutManager.class);
        if(manager==null)return;
        ShortcutInfo shelf=new ShortcutInfo.Builder(context,"shelf")
            .setShortLabel("My shelf").setIcon(Icon.createWithResource(context,R.mipmap.ic_launcher))
            .setIntent(new Intent(context,ShelfActivity.class).setAction(ShelfActivity.ACTION_SHELF)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP))
            .build();
        ShortcutInfo playing=new ShortcutInfo.Builder(context,"playing")
            .setShortLabel("Now Playing").setIcon(Icon.createWithResource(context,R.mipmap.ic_launcher))
            .setIntent(new Intent(context,ShelfActivity.class).setAction(ShelfActivity.ACTION_NOW_PLAYING)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP))
            .build();
        manager.setDynamicShortcuts(Arrays.asList(shelf,playing));
    }
    private ShelfShortcuts(){}
}
