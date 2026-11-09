package uk.co.justcop.audioshelf.helper;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;
import android.webkit.CookieManager;
import android.webkit.WebView;
import android.widget.Toast;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.concurrent.Executors;

/** Google authenticates in the system browser; PKCE binds the return to this app. */
final class AccessLogin {
    private final Activity activity;
    private final WebView web;
    private final String origin;
    private boolean pending;
    AccessLogin(Activity activity, WebView web, String origin) {
        this.activity=activity; this.web=web; this.origin=origin;
    }
    private String random() {
        byte[] bytes=new byte[32]; new SecureRandom().nextBytes(bytes);
        return Base64.encodeToString(bytes, Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING);
    }
    void start() {
        if(pending)return;
        try {
            String verifier=random(), state=random();
            String challenge=Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.US_ASCII)), Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING);
            activity.getSharedPreferences("access_login",0).edit().putString("verifier",verifier).putString("state",state).putString("origin",origin).putLong("started",System.currentTimeMillis()).apply();
            pending=true;
            activity.startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(origin+"/auth/android/start").buildUpon().appendQueryParameter("challenge",challenge).appendQueryParameter("state",state).build()));
            Toast.makeText(activity,"Sign in with Google in your browser, then return to AudioShelf.",Toast.LENGTH_LONG).show();
        } catch(Exception error) {pending=false; Toast.makeText(activity,"Unable to open Google sign-in.",Toast.LENGTH_LONG).show();}
    }
    boolean complete(Intent intent) {
        Uri uri=intent.getData();
        if(uri==null||!"audioshelf-login".equals(uri.getScheme())||!"callback".equals(uri.getHost()))return false;
        android.content.SharedPreferences prefs=activity.getSharedPreferences("access_login",0);
        String expected=prefs.getString("state",""), verifier=prefs.getString("verifier",""), code=uri.getQueryParameter("code");
        if(expected.isEmpty()||!expected.equals(uri.getQueryParameter("state"))||!origin.equals(prefs.getString("origin",""))||System.currentTimeMillis()-prefs.getLong("started",0)>600000||code==null||!code.matches("[A-Za-z0-9_-]{43}"))return true;
        prefs.edit().clear().apply();
        java.util.concurrent.ExecutorService executor=Executors.newSingleThreadExecutor();
        executor.execute(()->{
            HttpURLConnection connection=null;
            try {
                connection=(HttpURLConnection)new URL(origin+"/auth/android/exchange").openConnection();
                connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(15000);connection.setReadTimeout(15000);
                connection.setRequestMethod("POST");connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type","application/json");connection.setRequestProperty("X-AudioShelf-Request","1");
                byte[] body=new JSONObject().put("code",code).put("verifier",verifier).toString().getBytes(StandardCharsets.UTF_8);
                try(java.io.OutputStream stream=connection.getOutputStream()){stream.write(body);}
                if(connection.getResponseCode()!=200)throw new java.io.IOException("Sign-in exchange failed");
                String response;
                try(java.io.InputStream stream=connection.getInputStream()){
                    java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream();byte[] buffer=new byte[4096];int n;
                    while((n=stream.read(buffer))!=-1){bytes.write(buffer,0,n);if(bytes.size()>32768)throw new java.io.IOException("Response too large");}
                    response=bytes.toString("UTF-8");
                }
                JSONObject result=new JSONObject(response);String token=result.getString("access_token");
                if(!token.matches("[A-Za-z0-9_.-]+"))throw new java.io.IOException("Invalid token");
                long maxAge=Math.max(0,result.getLong("expires")-System.currentTimeMillis()/1000);
                activity.runOnUiThread(()->CookieManager.getInstance().setCookie(origin,"CF_Authorization="+token+"; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age="+maxAge,ok->{pending=false;CookieManager.getInstance().flush();web.loadUrl(origin+"/#shelf");}));
            } catch(Exception error) {activity.runOnUiThread(()->{pending=false;Toast.makeText(activity,"Sign-in expired. Reopen AudioShelf to try again.",Toast.LENGTH_LONG).show();});}
            finally {if(connection!=null)connection.disconnect();executor.shutdown();}
        });
        return true;
    }
}
