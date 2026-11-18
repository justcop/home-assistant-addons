# Android helper toolchain

Reviewed 9 November 2026 for issue #132.

The build uses stable AGP 9.5.1, its documented Gradle 9.6.0 minimum, JDK 17, compile API 37 and Build Tools 36.0.0. The Gradle distribution is SHA-256 verified. AndroidX Core 1.19.1, WebKit 1.18.0 and Gson 2.15.0 are stable repository releases.

The minimum remains API 26 (Android 8) and target remains API 35. A newer compile SDK does not change either device support or target-SDK behavior. No Android permissions are added.

CI runs debug unit tests, debug and unsigned release assembly, and debug/release lint. The normal signed debug APK remains the sideload/update artifact. This PR does not publish an update; merging helper sources to main invokes the existing publishing workflow.

Before publishing a new app version, verify the signed APK on a physical phone: existing install updates, Spotify authentication/App Remote connection, explicit device choice, album/disc/side playback, background handoff and retained preferences. CI and JVM tests cannot prove those live integrations.

Sources: https://developer.android.com/build/releases/agp-9-5-0-release-notes , Google Maven metadata for androidx.core/core and androidx.webkit/webkit, and Maven Central metadata for Gson.
