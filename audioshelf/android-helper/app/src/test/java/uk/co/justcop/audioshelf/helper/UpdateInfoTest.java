package uk.co.justcop.audioshelf.helper;

import com.google.gson.JsonObject;
import org.junit.Test;
import java.util.Collections;
import java.util.Set;
import static org.junit.Assert.*;

public class UpdateInfoTest {
    private JsonObject manifest() {
        JsonObject obj = new JsonObject();
        obj.addProperty("schema", 1);
        obj.addProperty("packageName", UpdateInfo.PACKAGE);
        obj.addProperty("apkUrl", UpdateInfo.APK_URL);
        obj.addProperty("versionCode", 7);
        obj.addProperty("versionName", "0.1.6");
        obj.addProperty("size", 200000);
        obj.addProperty("sha256", new String(new char[64]).replace('\0', 'a'));
        return obj;
    }

    @Test public void newerReleaseIsOfferedAndCurrentOrOlderReleaseIsIgnored() {
        UpdateInfo update = UpdateInfo.parse(manifest().toString(), 6);
        assertEquals(7, update.versionCode);
        assertEquals("0.1.6", update.versionName);
        assertNull(UpdateInfo.parse(manifest().toString(), 7));
        assertNull(UpdateInfo.parse(manifest().toString(), 8));
    }

    @Test public void rejectsOtherPackagesChannelsAndUnboundedDownloads() {
        for (String field : new String[]{"packageName", "apkUrl", "sha256", "versionName"}) {
            JsonObject obj = manifest(); obj.addProperty(field, "https://example.com/untrusted.apk");
            assertThrows(RuntimeException.class, () -> UpdateInfo.parse(obj.toString(), 6));
        }
        for (long value : new long[]{0, -1, 40L * 1024 * 1024 + 1}) {
            JsonObject obj = manifest(); obj.addProperty("size", value);
            assertThrows(RuntimeException.class, () -> UpdateInfo.parse(obj.toString(), 6));
        }
        JsonObject obj = manifest(); obj.addProperty("versionCode", (long) Integer.MAX_VALUE + 1);
        assertThrows(RuntimeException.class, () -> UpdateInfo.parse(obj.toString(), 6));
    }

    @Test public void rejectsPartialMetadataAndFractionalOrTextVersions() {
        JsonObject partial = manifest(); partial.remove("sha256");
        assertThrows(RuntimeException.class, () -> UpdateInfo.parse(partial.toString(), 6));
        JsonObject fractional = manifest(); fractional.addProperty("versionCode", 7.5);
        assertThrows(RuntimeException.class, () -> UpdateInfo.parse(fractional.toString(), 6));
        JsonObject text = manifest(); text.addProperty("versionCode", "7");
        assertThrows(RuntimeException.class, () -> UpdateInfo.parse(text.toString(), 6));
        assertThrows(RuntimeException.class, () -> UpdateInfo.parse("<html>not a release</html>", 6));
    }

    @Test public void installerApprovalRequiresMatchingSignerPackageAndExactNewerVersion() {
        UpdateInfo update = UpdateInfo.parse(manifest().toString(), 6);
        Set<String> signer = Collections.singleton("original-key");
        assertTrue(update.allowsCandidate(UpdateInfo.PACKAGE, 7, "0.1.6", 6, signer, signer));
        assertFalse(update.allowsCandidate(UpdateInfo.PACKAGE, 7, "0.1.6", 7, signer, signer));
        assertFalse(update.allowsCandidate("another.package", 7, "0.1.6", 6, signer, signer));
        assertFalse(update.allowsCandidate(UpdateInfo.PACKAGE, 8, "0.1.6", 6, signer, signer));
        assertFalse(update.allowsCandidate(UpdateInfo.PACKAGE, 7, "0.1.7", 6, signer, signer));
        assertFalse(update.allowsCandidate(UpdateInfo.PACKAGE, 7, "0.1.6", 6, Collections.singleton("other-key"), signer));
        assertFalse(update.allowsCandidate(UpdateInfo.PACKAGE, 7, "0.1.6", 6, Collections.emptySet(), Collections.emptySet()));
    }
}
