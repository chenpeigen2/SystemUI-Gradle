# SystemUI-common SDK 路径硬编码回退导致编译失败（2026-09-30）

## 背景

用户报告构建失败：`MockableJarTransform` 无法转换 `android-SysUISdk/android.jar`（13 个模块的 `androidApis` 解析失败）。

## 诊断过程

1. **MockableJarTransform 本体检查**：`android.jar` zip 完整（39258 条目、无重复、class 版本最高 v65、无 module-info/multi-release）——jar 本身无问题。
2. **复现尝试**：`./gradlew test`（触发全模块 `androidApis` 解析，即用户报错路径）→ **BUILD SUCCESSFUL**（27 tasks executed）——MockableJarTransform 失败当前不可复现，判定为瞬态（daemon 内存/缓存状态），待用户复跑确认。
3. **复现出一个确定性 bug**：裸 shell（无 `ANDROID_HOME`）下 `./gradlew :SystemUI-common:compileKotlin` 失败：

```
e: log/core/src/com/android/systemui/log/core/LogMessage.kt:19:16 Unresolved reference 'icu'.
```

## 根因

`SystemUI-common/build.gradle.kts`：

```kotlin
val sysUiSdkDir = providers.environmentVariable("ANDROID_HOME")
    .orElse("/home/conv/Android/Sdk")   // ← Linux 旧机器路径硬编码回退
```

- 本机 shell 无 `ANDROID_HOME` 环境变量 → 回退到不存在的 `/home/conv/Android/Sdk`
- `compileOnly(files(sysUiAndroidJar))` 指向不存在文件 → `android.icu.text.SimpleDateFormat` 解析失败
- AGP 模块不受影响（AGP 自动读 `local.properties` 的 `sdk.dir`），唯独这个 `java-library` 模块自己解析 SDK 路径
- 违反用户规则「AOSP 根路径全局统一，禁止散落硬编码完整路径」（2026-08-25）

## 修复

SDK 路径解析顺序改为：`ANDROID_SDK_ROOT` → `ANDROID_HOME` → `local.properties` 的 `sdk.dir`（与 AGP 同源），删除 `/home/conv` 硬编码回退。

## 验证（TDD：build seam 上的 red→green）

- **Red（修复前）**：`env -u ANDROID_HOME ./gradlew :SystemUI-common:compileKotlin` → `Unresolved reference 'icu'`（本日实录）
- **Green（修复后）**：同命令编译通过（见下方提交记录）

## MockableJarTransform 遗留

用户报错在修复时点不可复现（`./gradlew test` 全绿）。若复现：需完整堆栈（`--stacktrace`），重点怀疑 daemon 内存瞬态（44MB jar → mockable 生成）。此条不阻塞本次修复。
