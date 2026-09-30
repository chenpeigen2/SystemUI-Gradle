SysUISdk r2 quick install
=========================

Release:
  https://github.com/convivae/SystemUI-Gradle/releases/tag/sysuisdk-android-17.0.0_r1-r2

r2 supersedes r1: merged class method bodies are now standard SDK stubs,
fixing Android Studio Gradle sync (AGP MockableJarTransform crash). See the
Release page for the full change log.

1. Download these two assets from the Release page into the same directory:

   * SysUISdk-android-17.0.0_r1-r2.zip
   * SysUISdk-android-17.0.0_r1-r2.zip.sha256

2. Verify before extracting. From the download directory, run:

       sha256sum --check SysUISdk-android-17.0.0_r1-r2.zip.sha256

   The result must be:

       SysUISdk-android-17.0.0_r1-r2.zip: OK

   The authoritative SHA-256 is shown on the Release page and in the
   .zip.sha256 sidecar.

3. Install only the platform directory. Set ANDROID_SDK_ROOT to the SDK used
   by Gradle, and remove or rename any existing android-SysUISdk first.

       (
         set -eu
         target="$ANDROID_SDK_ROOT/platforms/android-SysUISdk"
         test ! -e "$target" || {
           echo "ERROR: $target already exists; remove or rename it first." >&2
           exit 1
         }
         mkdir -p "$ANDROID_SDK_ROOT/platforms"
         unzip -q SysUISdk-android-17.0.0_r1-r2.zip 'android-SysUISdk/*' -d "$ANDROID_SDK_ROOT/platforms"
         test -f "$target/android.jar"
       )

4. Point local.properties (or ANDROID_HOME / ANDROID_SDK_ROOT) at this SDK,
   then build:

       printf 'sdk.dir=%s\n' "$ANDROID_SDK_ROOT" > local.properties
       ./gradlew :app:assembleDebug
       ./gradlew :app:assembleRelease

The project references this platform via compileSdkPreview = "SysUISdk". The
r2 release has been acceptance-tested by the project owner and builds the
project successfully without an AOSP checkout.

See NOTICE for provenance and licensing details of the archive contents.
