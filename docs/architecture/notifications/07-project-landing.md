# 07 · 本项目 Gradle 落点与调试速查

> 通知知识库的收口篇：模块归属、构建依赖、flags、调试命令。

## 7.1 模块归属总表

| 内容 | Gradle 模块 | 仓库路径 |
|---|---|---|
| 管道全部（listener/coalescer/collection/listbuilder/render/coordinator） | `:SystemUI-core` | `SystemUI-core/src/com/android/systemui/statusbar/notification/collection/**` |
| NLS 入口 | `:SystemUI-core` | `SystemUI-core/src/com/android/systemui/statusbar/NotificationListener.java` |
| 卡片与 row（~80 文件） | `:SystemUI-core` | `…/statusbar/notification/row/` |
| heads-up + interruption | `:SystemUI-core` | `…/statusbar/notification/{headsup,interruption}/` |
| 通知栏列表（NSSL 家族） | `:SystemUI-core` | `…/statusbar/notification/stack/` |
| 状态栏图标 | `:SystemUI-core` | `…/statusbar/notification/icon/` + `statusbar/phone/ui/StatusBarIconControllerImpl` |
| promoted / footer / emptyshade / promoted | `:SystemUI-core` | `…/statusbar/notification/{promoted,footer,emptyshade}/` |
| 通用滑动框架 | `:SystemUI-core` | `SystemUI-core/src/com/android/systemui/SwipeHelper.java` |
| Compose 迁移部分 | `:SystemUI-core` | `SystemUI-core/compose/features/src/com/android/systemui/notifications/` |
| AOD 通知区 | `:SystemUI-core` | keyguard Compose sections（见 [AOD 知识库](../2026-09-30-aod-knowledge-base.md)） |
| 插件接口（菜单行等） | `:SystemUI-plugin` | `SystemUI-plugin/src/com/android/systemui/plugins/statusbar/` |
| 通知相关资源 | `:SystemUI-res` | `SystemUI-res/res/{layout,values}/`（`status_bar_notification_row` 等布局） |

体量：`statusbar/notification/` 62 子目录/文件、5.3MB 源码——SystemUI 内最大的子系统之一。

## 7.2 构建依赖要点

- framework 契约（`NotificationListenerService`、`StatusBarNotification`、`RankingMap`、`Notification.Builder` 模板方法如 `makePublicContentView`）由 SysUISdk + framework.jar 提供
- `@NotifInflation` CoroutineDispatcher：dagger concurrency 模块（row/content inflate 后台线程）
- Dagger 子组件：`ExpandableNotificationRowComponent`（每 row 一个）、`@CoordinatorScope`（每 coordinator 一个）——通知模块是 SystemUI 里 Dagger scope 用得最密的区域
- 插件接缝：`NotificationMenuRowPlugin`、`NotificationListenerController`（`:SystemUI-plugin`）；插件化保护状态见[插件化知识库](../2026-09-30-systemui-plugin-knowledge-base.md) §6

## 7.3 相关 aconfig / 特性 flags

读通知代码先查这些开关（两条路径并存时代码极易误读）：

| Flag | 影响 |
|---|---|
| `NmSummarizationAllFlag` | AI 摘要 + BundleEntry（17 新管道分支） |
| `NotificationMinimalism` | 锁屏极简通知 |
| `NotificationThrottleHun` / `AvalancheReplaceHunWhenCritical` | 横幅雪崩抑制 |
| `BIGPICTURE_NOTIFICATION_LAZY_LOADING` | 大图懒加载 |
| `SceneContainerFlag` | shade 容器 scene/legacy 双路径 |
| `Flags.enableMinmode()` | minmode 相关通知表现 |
| conversation/people 系列 | 对话 section 与优先级 |

## 7.4 调试速查

```bash
# 管道全状态（STAGE 0–6，排查「通知没显示/顺序不对」第一步）
adb shell dumpsys activity service com.android.systemui/.SystemUIService NotifPipeline

# 横幅判定理由（为什么不弹 HUN）
adb shell dumpsys activity service com.android.systemui/.SystemUIService VisualInterruptionDecisionProvider

# 发一条测试通知
adb shell cmd notification post -S bigtext -t "title" "tag" "text"

# 通知渠道/排序现场
adb shell dumpsys notification

# 关键 settings
adb shell settings get global heads_up_notifications_enabled
adb shell settings get global notification_cooldown    # 通知冷却
```

排查心法：**「通知没来」**先 `dumpsys notification` 确认 NMS 有没有 → 有则看 NotifPipeline STAGE 1–3（被 coalescer 攒批？被 filter 滤掉？）；**「来了不显示」**看 PreparationCoordinator 的 inflate 闸门与 finalize filter；**「不弹横幅」**看 VisualInterruptionDecisionProvider 的 logReason；**「列表位置不对」**看 ShadeListBuilder dump 的 section/comparator 结果。

## 7.5 系列回顾

| 篇 | 一句话 |
|---|---|
| [README](./README.md) | 全链路图 + 术语 + 源码地图 |
| [01](./01-ingress-pipeline.md) | 7 Stage 管道：NLS → Coalescer → Collection → 14 步 ListBuilder → Render → Shade |
| [02](./02-card-and-inflate.md) | Entry→Row→ContentView 三层模型、两段异步 inflate、flag 按需绑定 |
| [03](./03-heads-up.md) | 横幅：三层判定（Condition/Suppressor/Filter）+ Coordinator 六 hook + 雪崩抑制 |
| [04](./04-shade-list.md) | NSSL 自定义布局、sections、分组容器、footer |
| [05](./05-surfaces.md) | 状态栏图标/锁屏/AOD/promoted chips/Compose 迁移 |
| [06](./06-interaction.md) | 点击/滑动 dismiss/launch 动画/remote input/guts/snooze |
