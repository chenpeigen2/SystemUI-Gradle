# SysUISdk android-17.0.0_r1-r2

Prebuilt `android-SysUISdk` compile platform for SystemUI-Gradle. This r2 asset supersedes
`r1` and has been acceptance-tested by the project owner.

## What's new in r2 (vs r1)

- **MockableJarTransform compatibility fix**: every merged class's method body is now
  rewritten to the standard SDK stub shape (same form as stock `android-35` `android.jar`:
  `<init>` keeps its `super()` call and throws `RuntimeException("Stub!")`, other methods
  throw directly). r1 shipped real AOSP method bodies with try-catch blocks, which tripped
  a latent AGP bug in `MockableJarGenerator` (it does not clear `tryCatchBlocks` when
  stubbing bodies; `COMPUTE_FRAMES` then NPEs on dangling handler labels) and broke
  Android Studio Gradle sync with `Cannot read field "outgoingEdges" because
  "handlerRangeBlock" is null`.
- Zero compile-time information loss: javac/kotlinc only consume signatures and constants;
  real framework bytecode remains available to the project through `libs/framework.jar`.
- The body-stubbing pass is part of `tools/build_sysuisdk.py` (`stub_class_method_bodies`)
  and is idempotent, so all future generated platforms are safe by construction.

## Assets

Download both files into the same directory:

- `SysUISdk-android-17.0.0_r1-r2.zip`
- `SysUISdk-android-17.0.0_r1-r2.zip.sha256`

Verify the archive before extracting it:

```bash
sha256sum --check SysUISdk-android-17.0.0_r1-r2.zip.sha256
```

Expected result:

```text
SysUISdk-android-17.0.0_r1-r2.zip: OK
```

SHA-256: `6afc52cc461ea820c99172002ab4e185225e418ccc0a7223f079ce441681d766`

（此 release 故意不在 zip 内嵌自身摘要以避免自指；以本页与 `.zip.sha256` sidecar 为准。）

## Install

Set `ANDROID_SDK_ROOT` to the SDK used by Gradle. Remove or rename an existing
`android-SysUISdk` first; do not merge two platform versions.

```bash
(
  set -eu
  target="$ANDROID_SDK_ROOT/platforms/android-SysUISdk"
  test ! -e "$target" || {
    echo "ERROR: $target already exists; remove or rename it first." >&2
    exit 1
  }
  mkdir -p "$ANDROID_SDK_ROOT/platforms"
  unzip -q SysUISdk-android-17.0.0_r1-r2.zip 'android-SysUISdk/*' \
    -d "$ANDROID_SDK_ROOT/platforms"
  test -f "$target/android.jar"
)
```

Then clone and build the project as documented in
[README.md](https://github.com/convivae/SystemUI-Gradle/blob/main/README.md):

```bash
./gradlew :app:assembleDebug
./gradlew :app:assembleRelease
```

## Provenance and licensing

The platform combines AOSP `android-17.0.0_r1` build outputs with a stock
`android-37.0` SDK platform base. See `NOTICE` inside the archive and
[`release/sysuisdk/NOTICE`](https://github.com/convivae/SystemUI-Gradle/blob/main/release/sysuisdk/NOTICE)
for the component and license breakdown.

The r1 tag is retained at commit `e5ca8dda`; the packaging tool and finalized release
documentation were committed immediately afterward in `928353a0`. The r2 body-stubbing
fix is recorded in `docs/issues/2026-09-30-sysui-common-sdk-path-fallback.md`.
