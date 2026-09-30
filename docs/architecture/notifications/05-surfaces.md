# 05 · 其他展示面（状态栏图标 / 锁屏 / AOD / Promoted）

> 通知除通知栏列表外的所有展示面。 shade 列表本体见 [04](./04-shade-list.md)。

## 5.1 状态栏图标

- `statusbar/notification/icon/IconManager.kt`：**为一条通知生成并维护它的全部图标视图**（`StatusBarIconView`）——状态栏、底托（shelf）、AOD 多处复用。在 `NotificationRowBinderImpl` 建 row 时 `createIcons` / 更新时 `updateIcons` 触发
- `StatusBarIconController`（`statusbar/phone/ui/StatusBarIconControllerImpl.java`）：状态栏图标槽位的总控制器（系统图标 + 通知图标 + 静默图标隐藏逻辑）
- `SilentNotificationStatusIconsVisibilityInteractor`：「在状态栏显示静默通知图标」设置项（NotificationListener 启动时同步 `shouldHideSilentStatusBarIcons`）
- `statusbar/notification/icon/` 下还有 `domain/`（图标数量/可见性逻辑，如 `NotificationIconsInteractor`）与 `ui/`（状态栏/AOD 图标槽位的 ViewModel+Binder）两个子目录

数据流：管道 entry 变化 → IconManager 更新图标资源 → StatusBarIconController 重排状态栏槽位。图标数量超限（`max_notif_static_icons` / `max_notif_icons_on_aod` / `max_notif_icons_on_lockscreen` 等 integer 资源）时截断。

## 5.2 锁屏

锁屏通知 = **同一份 shade 列表的受限渲染**，不是独立系统：

- 过滤在管道侧完成：`KeyguardCoordinator` / `OriginalUnseenKeyguardCoordinator` / `LockScreenMinimalismCoordinator`（01 篇 §1.5）——锁屏上隐藏其他用户/敏感通知，控制「未读点」
- 隐藏粒度：`NotificationLockscreenUserManager`（红action 判定，02 篇 inflate 参数的来源之一）
- 新形态：`NotificationMinimalism` flag 门控的锁屏极简模式（通知只显示图标/计数）
- 展开锁屏通知 = 解锁后切到完整 shade 列表（Keyguard 过渡，见 keyguard 知识领域，本库暂不含）

## 5.3 AOD 通知

- 息屏时通知不弹 HUN，走 **doze 脉冲**（AOD 知识库 §1.5/§3.6）；`NotificationWakeUpCoordinator.kt` 是通知侧「要不要唤醒屏幕」的决策器
- AOD 上的通知呈现（图标行 / 置顶区）由 keyguard Compose 层承载：`AodNotificationIconsSection` / `AodPromotedNotificationSection`（AOD 知识库 §2.6），本篇不重复展开
- `promoted/ShowPromotedNotificationOnAOD.kt`、`AODLowFrequencyModeDelayMs.kt`：置顶通知上 AOD 的节流策略

## 5.4 Promoted Notifications（17 新增）

「置顶通知」是 17 的大特性——把选定通知提升为常驻展示（状态栏 chip + AOD 区）：

- `statusbar/notification/promoted/`：`PromotedNotificationContentExtractor`（内容抽取）、`domain/`（Interactor）、`ui/`、`shared/`
- 状态栏侧：`StatusBarNotificationChipsInteractor`（chip 的显示/点击事件流，03 篇 §3.2 的 toggle 就是它）
- 与 HUN 的关系：chip 点击 = 手动 toggle 横幅；promoted 也影响 AOD 展示

## 5.5 Compose 化进程

`compose/features/src/com/android/systemui/notifications/ui/`（17 状态）：

- `NotificationPlaceholderStateStorage.kt` / `YSpace.kt`：通知栈占位属性存储（栈滚动顶、栈/HUN 可视 Y 区间、alpha，绑定到 NSSL）与 Y 坐标区间数据类——通知栏列表向 Compose 迁移的**过渡期设施**（传统 NSSL 与 Compose 并存）
- 判断某 UI 是传统 View 还是 Compose 的最快方式：看 `compose/features` 下有没有对应 composable

## 5.6 本篇小结（本项目落点）

| 面 | 代码位置（均 `:SystemUI-core`） |
|---|---|
| 状态栏图标 | `statusbar/notification/icon/` + `statusbar/phone/ui/StatusBarIconControllerImpl` |
| 锁屏 | 管道 coordinator（01 篇）+ `NotificationLockscreenUserManager` |
| AOD | `NotificationWakeUpCoordinator` + keyguard Compose sections（跨 keyguard 目录） |
| Promoted | `statusbar/notification/promoted/` + `StatusBarNotificationChipsInteractor` |
| Compose 迁移 | `compose/features/notifications/ui/` |

排查提示：状态栏图标不对先 dump `StatusBarIconController`；锁屏不显示通知先看 `KeyguardCoordinator` 过滤理由；AOD 不亮看 doze 链路（AOD 知识库 §4.3）。
