package uk.co.justcop.audioshelf.helper;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.Set;

/** The only update channel accepted by this helper. No arbitrary download URLs. */
final class UpdateInfo {
    static final String PACKAGE = "uk.co.justcop.audioshelf.helper";
    static final String BASE = "https://github.com/justcop/home-assistant-addons/releases/download/audioshelf-helper/";
    static final String APK_URL = BASE + "AudioShelf-Helper.apk";
    static final String MANIFEST_URL = BASE + "audioshelf-helper-update.json";
    final int versionCode;
    final String versionName, sha256;
    final long size;

    private UpdateInfo(int code, String name, String hash, long bytes) {
        versionCode = code; versionName = name; sha256 = hash; size = bytes;
    }

    static UpdateInfo parse(String json, long installedCode) {
        JsonObject obj = JsonParser.parseString(json).getAsJsonObject();
        if (obj.get("schema").getAsInt() != 1 || !PACKAGE.equals(obj.get("packageName").getAsString())
                || !APK_URL.equals(obj.get("apkUrl").getAsString())) {
            throw new IllegalArgumentException("Unexpected update channel");
        }
        long code = number(obj, "versionCode"), size = number(obj, "size");
        String name = obj.get("versionName").getAsString(), hash = obj.get("sha256").getAsString();
        if (code < 1 || code > Integer.MAX_VALUE || size < 1 || size > 40L * 1024 * 1024
                || !name.matches("[0-9]+\\.[0-9]+\\.[0-9]+") || !hash.matches("[a-f0-9]{64}")) {
            throw new IllegalArgumentException("Invalid update metadata");
        }
        return code > installedCode ? new UpdateInfo((int) code, name, hash, size) : null;
    }

    private static long number(JsonObject obj, String name) {
        if (!obj.get(name).isJsonPrimitive() || !obj.get(name).getAsJsonPrimitive().isNumber()) {
            throw new IllegalArgumentException("Expected numeric " + name);
        }
        return obj.get(name).getAsBigDecimal().longValueExact();
    }

    boolean allowsCandidate(String packageName, long candidateCode, String candidateName,
            long installedCode, Set<String> candidateSigners, Set<String> installedSigners) {
        return PACKAGE.equals(packageName) && candidateCode == versionCode
            && versionName.equals(candidateName) && candidateCode > installedCode
            && !installedSigners.isEmpty() && candidateSigners.equals(installedSigners);
    }
}
