# 07 · 本项目 Gradle 落点与调试速查

> 通知知识库的收口篇：模块归属、构建依赖、flags 实测、dumpable 实测清单、调试命令。
> 基准：AOSP `android-17.0.0_r1`；引用格式 `路径` + `类#方法`；体量数据为 2026-09-30 实测。

## 7.1 模块归属总表

| 内容 | Gradle 模块 | 仓库路径 | 关键类（源码锚点） |
|---|---|---|---|
| 管道全部 | `:SystemUI-core` | `SystemUI-core/src/com/android/systemui/statusbar/notification/collection/**` | `NotifPipeline`、`NotifCollection`、`ShadeListBuilder`、`GroupCoalescer`、`NotifPipelineInitializer` |
| NLS 入口 | `:SystemUI-core` | `…/statusbar/NotificationListener.java` | `NotificationListener`、`NotificationListenerWithPlugins` |
| 卡片与 row | `:SystemUI-core` | `…/notification/row/` | `ExpandableNotificationRow`、`NotificationContentView`、`RowContentBindStage`、`NotificationRowContentBinderImpl`、`NotificationGutsManager` |
| heads-up + interruption | `:SystemUI-core` | `…/notification/{headsup,interruption}/` | `HeadsUpManagerImpl`、`AvalancheController`、`VisualInterruptionDecisionProviderImpl`、`HeadsUpCoordinator`（在 `collection/coordinator/`） |
| 通知栏列表 | `:SystemUI-core` | `…/notification/stack/` | `NotificationStackScrollLayout`、`AmbientState`、`ExpandableViewState`、`NotificationSectionsManager` |
| 状态栏图标 | `:SystemUI-core` | `…/notification/icon/` + `statusbar/phone/ui/` | `IconManager`、`StatusBarIconControllerImpl` |
| promoted / footer / emptyshade | `:SystemUI-core` | `…/notification/{promoted,footer,emptyshade}/` | `PromotedNotificationsInteractor`、`FooterViewModel`、`EmptyShadeView` |
| 通用滑动框架 | `:SystemUI-core` | `SystemUI-core/src/com/android/systemui/SwipeHelper.java` | `SwipeHelper`、`NotificationSwipeHelper`（stack/） |
| Compose 迁移部分 | `:SystemUI-core` | `SystemUI-core/compose/features/src/com/android/systemui/notifications/` | `Notifications.kt`、`NotificationsShadeOverlay.kt`、`NotificationPlaceholderStateStorage` |
| AOD 通知区 | `:SystemUI-core` | keyguard Compose sections | `AodNotificationIconsSection`（见 [AOD 知识库](../2026-09-30-aod-knowledge-base.md) §2.6） |
| 插件接口 | `:SystemUI-plugin` | `SystemUI-plugin/src/com/android/systemui/plugins/{,statusbar/}` | `NotificationListenerController`、`NotificationMenuRowPlugin`、`NotificationSwipeActionHelper` |
| 通知相关资源 | `:SystemUI-res` | `SystemUI-res/res/{layout,values}/` | `status_bar_notification_row`（row 壳布局）、`integers.xml` 图标上限 |

**体量实测（2026-09-30）**：`statusbar/notification/` 62 个顶层条目（`ls | wc -l`）、5.3MB（`du -sh`）；`row/` 96 个顶层条目——SystemUI 内最大的子系统之一。

## 7.2 构建依赖要点

### 7.2.1 framework 契约

`NotificationListenerService`、`StatusBarNotification`、`RankingMap`、`Notification.Builder` 模板方法（`makePublicContentView`/`makeNotificationGroupHeader` 等）由 **SysUISdk + framework.jar** 提供（规则 F：framework 代码严禁源码复制）。

### 7.2.2 @NotifInflation 线程链（完整环）

```
SystemUI-shared/…/dagger/qualifiers/  @NotifInflation qualifier 定义
  → util/concurrency/SysUIConcurrencyModule   提供 @NotifInflation Looper / Executor
  → util/kotlin/SysUICoroutinesModule          包装为 CoroutineDispatcher
  → 消费方：AsyncRowInflater（02 篇）、NotificationRowContentBinderImpl（AsyncInflationTask）
```

### 7.2.3 Dagger scope 清单（grep 实测）

| Scope/组件 | 定义处 | 用途 |
|---|---|---|
| `@CoordinatorScope` | `collection/coordinator/dagger/` | 每 coordinator 一个作用域 |
| `ExpandableNotificationRowComponent` | `row/dagger/ExpandableNotificationRowComponent.java`（`@Subcomponent.Builder`） | 每 row 一个子组件（Controller/BigPictureIconManager） |
| `@RemoteInputViewScope` | `row/dagger/RemoteInputViewModule.kt` | 回复框作用域 |
| `@SysUISingleton` | dagger 通用 | 管道主件（NotifCollection/ShadeListBuilder 等） |

## 7.3 flags 表（定义位置 + 消费方，grep 实测）

| Flag | 定义 | 消费方 |
|---|---|---|
| `NmSummarizationAllFlag` | `notification/NmSummarizationAllFlag.kt:26`（包装 `android.app.Flags.nmSummarizationAll`） | `NotifCoordinators`（SummarizationCoordinator/BundleCoordinator）、`BundleCoordinator` 日志开关 |
| `NotificationMinimalism` | `notification/shared/NotificationMinimalism.kt:26` | `NotifCoordinatorsImpl`（锁屏极简 coordinator 门控） |
| `NotificationThrottleHun` | `notification/shared/NotificationThrottleHun.kt` | `AvalancheController` |
| `AvalancheReplaceHunWhenCritical` | `notification/shared/AvalancheReplaceHunWhenCritical.kt` | `AvalancheController`（critical 替换策略） |
| `NotificationChipFromCompactContent` | `notification/shared/NotificationChipFromCompactContent.kt` | compact 横幅/chip 样式 |
| `NotificationHeadsUpCycling` | `notification/shared/NotificationHeadsUpCycling.kt` | HUN 轮换策略 |
| `BIGPICTURE_NOTIFICATION_LAZY_LOADING` | `systemui/flags/Flags.kt:252` | `NotificationRowBinderImpl`、`NotifRemoteViewsFactoryContainer` |
| `SceneContainerFlag` | `scene/shared/flag/` | shade 容器 scene/legacy 双路径 |
| `Flags.enableMinmode()` | `minmode/MinModeManagerUtils.kt` 消费 | minmode 通知表现 |

## 7.4 调试速查

### 7.4.1 真实 dumpable 清单（grep `registerDumpable` 实测）

| dumpable 名 | 注册处 | 看什么 |
|---|---|---|
| `NotifPipeline` | `NotifPipelineInitializer.java:92` | **STAGE 0–6 全量**（每个 coordinator 私有字段、集合、pending 队列） |
| `NotifCollection` | `NotifCollection.java:217` | entry 集合、lifetime extenders、event queue |
| `ShadeListBuilder` | `ShadeListBuilder.java:177` | 最终列表、section 归属、pipeline state |
| `VisualStabilityCoordinator` | `:153` | 视觉稳定判定状态 |
| `GroupExpansionManagerImpl` | `:117` | 组展开态 |
| `NotificationStackScrollLayoutController` + `NotificationStackScrollLayout` | `:929/:949` | 列表布局/滚动状态 |
| `AmbientState` | `:352` | 全局布局状态 |
| `NotificationSwipeHelper` | `:601` | 滑动状态 |
| `GutsCoordinator` / `OriginalUnseenKeyguardCoordinator` / `LockScreenMinimalismCoordinator` / `RemoteInputCoordinator` / `DebugModeFilterProvider` / `NotificationWakeUpCoordinator` | 各自 init | 各子系统状态 |

### 7.4.2 命令

```bash
# 管道全状态（排查「通知没显示/顺序不对」第一步）
adb shell dumpsys activity service com.android.systemui/.SystemUIService NotifPipeline

# 发一条测试通知（framework 侧命令，以设备上 `cmd notification help` 实际输出为准）
adb shell cmd notification post -S bigtext -t "title" "tag" "text"

# 通知渠道/排序现场（NMS 侧）
adb shell dumpsys notification

# 关键 settings（framework 侧 key，以设备实际为准）
adb shell settings get global heads_up_notifications_enabled
adb shell settings get global notification_cooldown    # 通知冷却（AvalancheController）
```

### 7.4.3 排查心法

| 症状 | 第一步 | 看哪个段 | 典型原因 |
|---|---|---|---|
| 通知没显示 | `dumpsys notification` | NMS 有没有这条 | 优先级/渠道设置问题（NMS 侧） |
| NMS 有但 shade 没有 | `dumpsys … NotifPipeline` | STAGE 1–3（listener/coalescer/collection） | 被 coalescer 攒批滞留、被 filter 滤掉、被 dismiss interceptor 拦 |
| posted 了但慢半拍出现 | `dumpsys … NotifPipeline` | STAGE 0 PreparationCoordinator 段（`mInflationStates`） | inflate 未完成（正常）、inflate 失败（故障卡） |
| 列表位置/分组不对 | `dumpsys … NotifPipeline` | STAGE 4 段（section/comparator 结果） | promoter/sectioner 判定、stability 挂起 |
| 不弹横幅 | logcat `VisualInterruptionDecisionLogger` | logReason 字段 | suppressor/filter 抑制（§[03](./03-heads-up.md) §3.1.1 全表） |
| 横幅不消失/秒消失 | `dumpsys … NotifPipeline` | HeadsUpCoordinator 段 | pin/snooze/雪崩窗口（[03 篇](./03-heads-up.md) §3.3/§3.5） |
| 时钟不走/通知不刷新 | logcat `NotificationRowBinder` | bind 回调 | bind 卡死、`isEntryBinding` 宽限 |
| 点击无响应 | logcat `NotificationClickerLogger` | logOnClick 防重原因 | 菜单/guts/组展开防重（[06 篇](./06-interaction.md) §6.1.1） |
| 滑不掉 | `dumpsys … NotifPipeline` | collection 段 interceptor | `BubbleCoordinator` 拦截（[06 篇](./06-interaction.md) §6.2.2） |

## 7.5 系列回顾

| 篇 | 一句话 |
|---|---|
| [README](./README.md) | 全链路图 + 术语 + 源码地图 |
| [01](./01-ingress-pipeline.md) | 7 Stage 管道：NLS → Coalescer → Collection → 9 步 buildList → NodeSpec/ViewDiffer → Shade |
| [02](./02-card-and-inflate.md) | Entry→Row→ContentView 三层模型、两段异步 inflate、flag 按需绑定、reapply/inflate |
| [03](./03-heads-up.md) | 横幅：三层判定（Condition/Suppressor/Filter 全表）+ Coordinator 两级组边界 + 雪崩抑制 |
| [04](./04-shade-list.md) | NSSL 自定义布局、AmbientState/ExpandableViewState、sections、分组容器 |
| [05](./05-surfaces.md) | 状态栏图标/锁屏/AOD/promoted chips/Compose 迁移现状 |
| [06](./06-interaction.md) | 点击防重/启动决策/滑动 dismiss/launch 动画/remote input/guts/snooze |
