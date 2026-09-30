# 05 · 其他展示面（状态栏图标 / 锁屏 / AOD / Promoted / Compose）

> 通知除通知栏列表外的所有展示面。shade 列表本体见 [04](./04-shade-list.md)。
> 基准：AOSP `android-17.0.0_r1`；引用格式 `路径` + `类#方法`；源码摘录均已验证。

## 5.1 状态栏图标

### 5.1.1 IconManager：图标资产的唯一管理者

`statusbar/notification/icon/IconManager.kt`（`@SysUISingleton`，类注释明确职责）——「Notifications are represented by icons in a few different places -- in the status bar, in the notification shelf, in AOD, etc. This class is in charge of inflating the views that hold these icons and keeping the icon assets themselves up to date」：

```kotlin
// IconManager.kt（核心结构）
class IconManager @Inject constructor(
    private val notifCollection: CommonNotifCollection,
    private val launcherApps: LauncherApps,
    private val iconBuilder: IconBuilder,
    ...
) : ConversationIconManager {
    fun attach() { notifCollection.addCollectionListener(entryListener) }
    // entryListener: onEntryInit → 挂 sensitivityListener；
    //   onEntryUpdated → updateIcons；onEntryRemoved → icon 释放
    private var launcherPeopleAvatarIconJobs: ConcurrentHashMap<String, Job>  // People 头像异步取
}
```

- `createIcons(entry)` / `updateIcons(entry, usingCache)`：由 `NotificationRowBinderImpl` 在建 row/更新时触发（[02 篇](./02-card-and-inflate.md) §2.2.3）
- 图标视图（`StatusBarIconView`）按**用途多处复用**：状态栏槽位、shelf（列表底部托盘）、AOD——同一份 icon 资产被不同容器各自 inflate
- TODO（类注释）：长期应移入 content inflation pipeline

### 5.1.2 icon/domain 与 icon/ui 分层

- `icon/domain/interactor/NotificationIconsInteractor.kt`：图标数量/可见性逻辑（如 `settingsRepository.showSilentStatusIcons` 消费，`:240`）
- `icon/ui/{viewmodel,viewbinder}`：状态栏/AOD 图标槽位的 ViewModel+Binder（如 `NotificationIconContainerStatusBarViewModel`、`NotificationIconContainerAlwaysOnDisplayViewModel`）

### 5.1.3 StatusBarIconController

`statusbar/phone/ui/StatusBarIconControllerImpl.java`——状态栏图标槽位总控（系统图标 + 通知图标 + 静默图标隐藏）。

### 5.1.4 静默图标设置链

```
NotificationManager.shouldHideSilentStatusBarIcons()（NMS 侧设置）
  → NotificationListener#onNotificationsInitialized / onSilentStatusBarIconsVisibilityChanged
  → SilentNotificationStatusIconsVisibilityInteractor#setHideSilentStatusIcons(hideIcons)
  → NotificationListenerSettingsRepository.showSilentStatusIcons（MutableStateFlow，:33）
  → NotificationIconsInteractor 消费（§5.1.2）
```

### 5.1.5 图标数量截断

`SystemUI-res/res/values/integers.xml`（实测摘录）：

```xml
<integer name="max_notif_icons_on_aod">3</integer>
<integer name="max_notif_icons_on_lockscreen">3</integer>
<integer name="max_notif_static_icons">4</integer>
```

由 `NotificationIconContainerStatusBarViewModel.kt`、`NotificationIconContainerAlwaysOnDisplayViewModel.kt`、`NotificationShelf.java` 消费；`NotificationIconContainer` 按 `mMaxIcons` 截断溢出图标。

## 5.2 锁屏

锁屏通知 = **同一份 shade 列表的受限渲染**，不是独立系统。过滤在管道侧（[01 篇](./01-ingress-pipeline.md) §1.5）：

| Coordinator | hook | 作用 |
|---|---|---|
| `KeyguardCoordinator` | addPreGroupFilter 等 | 锁屏可见性过滤（hide sensitive） |
| `OriginalUnseenKeyguardCoordinator` | collection listener | 「未读点」（since 查看过的记录） |
| `LockScreenMinimalismCoordinator` | flag 门控 | `NotificationMinimalism` 锁屏极简（只显图标/计数） |
| `HideNotifsForOtherUsersCoordinator` | addPreGroupFilter | 锁屏隐藏其他用户通知 |

- 红action 判定：`statusbar/NotificationLockscreenUserManager#getRedactionType`（NONE / VISIBILITY / OTP / REMOVE），是 `NotifInflater.Params.redactionType` 的来源（[02 篇](./02-card-and-inflate.md) §2.4.2 的 PUBLIC 槽）
- 锁屏上展开通知 = 解锁后切完整 shade（keyguard 过渡属 keyguard 领域，本库不含）

## 5.3 AOD 通知

- 息屏时不弹 HUN，走 doze 脉冲（[03 篇](./03-heads-up.md) §3.6 与 [AOD 知识库](../2026-09-30-aod-knowledge-base.md) §1.5）
- AOD 通知呈现（图标行/置顶区）在 keyguard Compose 层：`keyguard/ui/view/layout/sections/AodNotificationIconsSection`、`AodPromotedNotificationSection`（消费方与细节见 AOD 知识库 §2.6）
- `promoted/ShowPromotedNotificationOnAOD.kt`、`AODLowFrequencyModeDelayMs.kt`：置顶通知上 AOD 的节流策略

## 5.4 Promoted Notifications（置顶通知）

「置顶通知」= 把选定通知提升为常驻展示（状态栏 chip + AOD 区）。`statusbar/notification/promoted/` 全包（实测）：

```
PromotedNotificationContentExtractor.kt   # 内容抽取（正文/时间）
PromotedNotificationLogger.kt / PromotedNotificationLog.kt
AODPromotedNotification.kt                # AOD 置顶通知模型
AODLowFrequencyModeDelayMs.kt             # AOD 低频节流延迟
ShowPromotedNotificationOnAOD.kt          # 上 AOD 门控
shared/model/PromotedNotificationContentModel.kt
domain/interactor/PromotedNotificationsInteractor.kt        # 置顶决策
domain/interactor/AODPromotedNotificationInteractor.kt      # AOD 展示决策
domain/interactor/PackageDemotionInteractor.kt              # 包级降级
ui/viewmodel/AODPromotedNotificationViewModel.kt
```

与 HUN 的联动：`statusbar/chips/notification/domain/interactor/StatusBarNotificationChipsInteractor.kt`——

```kotlin
class StatusBarNotificationChipsInteractor ... {
    val promotedNotificationChipTapEvent: SharedFlow<String> = ...   // :74 chip 点击事件流
    suspend fun onPromotedNotificationChipTapped(key: String)        // :77
}
```

`HeadsUpCoordinator` 收集 `promotedNotificationChipTapEvent` 做 HUN 开关 toggle（[03 篇](./03-heads-up.md) §3.2.3）。

## 5.5 Compose 化进程

`compose/features/src/com/android/systemui/notifications/ui/`（实测全览）：

| 文件 | 作用 |
|---|---|
| `composable/Notifications.kt` | 通知栈 Compose 入口 |
| `composable/NotificationsShadeOverlay.kt` | shade 覆盖层（NotificationsShadeSessionModule 模块接线） |
| `composable/HeadsUpNotificationPlaceholders.kt` | HUN 占位（过渡期与 NSSL 交接） |
| `composable/NotificationStackContentHeight.kt` | 栈内容高度 |
| `composable/NotificationScrimFlingBehavior.kt` / `NotificationScrimNestedScrollConnection.kt` | scrim fling/嵌套滚动 |
| `composable/NotificationContentPicker.kt` / `NotificationHeadsUpHeight.kt` / `HeadsUpSnoozeDraggableModifier.kt` | 内容选取/HUN 高度/snooze 手势 |
| `NotificationPlaceholderStateStorage.kt` | 栈占位属性存储（stackScrollTop/stackBounds/hunBounds/stackAlpha），绑定 NSSL |
| `YSpace.kt` | Y 坐标区间数据类 |

**17 现状**：通知栏列表向 Compose 迁移**正在进行**——传统 NSSL 与 Compose 并存，`NotificationPlaceholderStateStorage` 等是两者的交接面。判断某 UI 属哪边：看 `compose/features/notifications/ui/composable/` 有没有对应 composable。

## 5.6 本篇小结（本项目 Gradle 落点）

| 面 | 代码位置（均 `:SystemUI-core`） |
|---|---|
| 状态栏图标 | `statusbar/notification/icon/`（IconManager + domain/ + ui/）+ `statusbar/phone/ui/StatusBarIconControllerImpl` |
| 锁屏 | 管道 coordinator（01 篇）+ `statusbar/NotificationLockscreenUserManager` |
| AOD | `NotificationWakeUpCoordinator` + keyguard Compose sections（跨 keyguard 目录） |
| Promoted | `statusbar/notification/promoted/` + `statusbar/chips/…/StatusBarNotificationChipsInteractor` |
| Compose 迁移 | `compose/features/notifications/ui/` |

排查提示：状态栏图标不对先 dump `StatusBarIconController` 与 `NotificationIconsInteractor`；锁屏不显示看 `KeyguardCoordinator` 过滤理由（`dumpsys … NotifPipeline` STAGE 0 段）；AOD 不亮看 doze 链路（AOD 知识库 §4.3）。
