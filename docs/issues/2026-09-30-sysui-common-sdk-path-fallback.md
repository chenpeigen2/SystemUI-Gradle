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

## MockableJarTransform 根因与修复（同日闭环）

**现象**：Android Studio Gradle 同步失败——13 个模块 `androidApis` 解析失败，
`MockableJarTransform: .../android-SysUISdk/android.jar` 报
`Cannot read field "outgoingEdges" because "handlerRangeBlock" is null`。

**根因**（daemon 日志 + 直接调用真 generator 复现 + javap 解剖 AGP 确认）：

1. AGP 的 `MockableJarGenerator`（builder 9.4.1）用 tree API 把方法体替换成 stub 时
   **不清理 `tryCatchBlocks`**；ClassWriter `COMPUTE_FRAMES` 在悬空 handler label 上 NPE
   （`MethodWriter.computeAllFrames`）。
2. Google 标准 android.jar 方法体是空 stub（无 try-catch），从不触发；
   **SysUISdk 按 ADR 0006 合并的 AOSP 真实字节码（带 try-catch）触发该 AGP 潜伏 bug**。
3. 升级 AGP 非 targeted fix（bug 在最新 builder 9.4.1 中存在，已解剖验证）。

**修复**（方案 C）：`tools/build_sysuisdk.py` 新增 `stub_class_method_bodies()`——把
合并进 android.jar 的方法体打成标准 SDK stub 形态（android-35 形态：`<init>` 保留
super() 前缀 + `throw new RuntimeException("Stub!")`；清空 exception table 与 Code
子属性），`compose_android_jar` 接入；`stub_jar_method_bodies()` 可离线修复现有 jar。
实测：javac/kotlinc 只用签名+常量，零编译信息损失（`framework.jar` 另行注入
JavaCompile 保留真实字节码）。

**TDD 证据**（红→绿）：

- Red：`tools/tests/test_stub_class_method_bodies.py` 10 用例（独立 class 解析器，
  期望值来自 android-35 标准 stub 形态）先失败（函数不存在）
- Green：10/10 通过；`tools/tests/` 全量回到基线（仅 3 个本机环境性既有失败）
- 集成验证：直接调用 AGP 真 `MockableJarGenerator.createMockableJar` 修复前 NPE、
  修复后 SUCCESS（输出 26MB mockable jar）
- 幂等：`stub_jar_method_bodies(stub_jar_method_bodies(x)) == stub_jar_method_bodies(x)`

**修复过程中的实现教训**（都由测试/验证逮住）：

- JVM 常量池 `cp_count` 含 0 号无效槽（夹具一度差 1）
- `invokevirtual/special/static` 总长 3 字节（opcode+u2），曾误改为 4 导致指令游走错位
- `MethodHandle`(tag 15) 非 wide 条目；class Utf8 为 modified UTF-8（0xC0 NUL 编码）需容错解码
- tail 拼接一度把字段区写成方法区（由 ASM 实测 AIOOBE 逮住）

**状态**：本机 `android-SysUISdk/android.jar` 已修复（备份已移至 `platforms/android-SysUISdk-pre-stub-bak.jar`）；
`build_sysuisdk.py` 后续生成均自带打桩，AS 同步已可解析。

**编译验证（worker 2026-09-30 复核，仅验证零改动）**：

- `./gradlew test`（原失败路径：全模块 androidApis/mockable jar 解析）BUILD SUCCESSFUL，
  首跑 37s + 复跑 17s 可重复，零 MockableJarTransform 错误
- `./gradlew :SystemUI-core:compileDebugKotlin` BUILD SUCCESSFUL（全链路 UP-TO-DATE）
- 物证：Gradle transforms 缓存内已生成 mockable 产物，`javap` 反汇编
  `android.icu.text.SimpleDateFormat`（原失败涉及的合并类）为标准 stub 形态——
  COMPUTE_FRAMES 真实跑通而非缓存空转
- `tools/tests/` 基线不变（322 passed / 38 既有 AOSP 树依赖的环境失败）
- 残余：未做删 transforms 缓存的冷启验证（需授权）；全仓无单测源码，
  `gradlew test` 验证的是编译/解析链路本身
