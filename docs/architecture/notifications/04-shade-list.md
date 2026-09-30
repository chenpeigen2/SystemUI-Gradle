# 04 · 通知栏列表（Shade List）

> 通知在展开的通知面板里的列表呈现。管道产出的列表如何变成 `NotificationStackScrollLayout` 里的视图树，本篇讲完。

## 4.1 视图容器：NotificationStackScrollLayout（NSSL）

`stack/NotificationStackScrollLayout.java` 是通知列表的**自定义 ViewGroup**（SystemUI 最大的自定义布局之一）：

- 子 View = 各 entry 的 `ExpandableNotificationRow` + section header + footer + shelf（底托）
- `AmbientState.java`（同包）：布局/动画的全局状态（当前展开高度、速度、跟踪手势等），layout 与动画解耦的关键
- `ExpandableViewState.java`：每个 child 的目标布局状态（y/高度/z/圆角…），NSSL 据此做 **spring/插值动画驱动**
- 滚动：`OverScrollerWrapper` 包装系统 `OverScroller`（同包 `NoOpOverScroller` 是 `OverScrollerInterface` 的空实现，用于无滚动场景）；顶部/底部 over-scroll 物理效果

Controller：`stack/` 下的 `NotificationStackScrollLayoutController`（协同手势、展开、锁屏转换）。

## 4.2 从管道到 NSSL：渲染绑定回顾与细化

[01 篇](./01-ingress-pipeline.md) §1.6 讲了 `ShadeViewManager`/`NotifStackView`/`NotifViewBarn` 的骨架，本篇补视图侧细节：

1. `collection/render/ShadeViewManager`（经 `NodeSpecBuilder.buildNodeSpec()` 建节点树 + `ShadeViewDiffer.applySpec()` 做 diff）把 pipeline 的 `List<PipelineEntry>` 翻译成「加哪些 row / 移哪些 row / 顺序怎么变」
2. `NotifViewBarn.requireRowController(entry)`（entry→`NotifViewController` 映射）：有注册则复用，未命中直接报错——说明 PreparationCoordinator 闸门未放行（row 还没 inflate）。Barn 缓存的是 **controller** 而非 row view 本身
3. row 进 NSSL 前经 `NotificationListContainer`（接口，NSSL 侧实现）适配栈语义（`bindRow`/`getViewParentForNotification`）
4. `OnAfterRender*` 回调让 coordinator 做渲染后修补（如 `StackCoordinator` 处理 section 位置）

## 4.3 Sections：分段与段头

通知栏按优先级分段（对话 > 正在运行/提醒 > 静默…），段的定义在管道侧（`NotifSectioner`，01 篇 §1.4），**段头的视图管理在 stack 侧**：

- `stack/NotificationSectionsManager.kt`：持有各 section 的 `SectionHeaderView`，按管道结果增删/排序段头
- `stack/SectionHeaderView.java` + `collection/render/SectionHeaderController.kt`（`SectionHeaderNodeControllerImpl`）+ `collection/provider/SectionHeaderVisibilityProvider.kt`（是否显示「静默通知」段的开关）
- `NotificationSectionsFeatureManager.kt`：段功能开关（如 conversation section 是否可用）
- `stack/NotificationSection.java`：旧 section 模型（与 sectioner 对齐使用）

## 4.4 分组呈现

- 组在列表里 = **summary 的 row 展开为 `NotificationChildrenContainer`**（`stack/NotificationChildrenContainer.java`）：收纳组内 child 的 mini row，计算组高度/展开动画
- `collection/render/GroupExpansionManager(Impl).java`：组的展开/折叠状态（用户点 summary 展开），状态回写管道（影响排序与高度）
- child 在组内用 **单行精简视图**（`HybridNotificationView`，02 篇 §2.6）；组展开后 child 变完整 row
- `GroupEntry` 的 summary 也可能被 promoter 提走/child 被提走（HUN、conversation 提升），ChildrenContainer 随之坍缩

## 4.5 Footer 与空态

- `footer/ui/view/FooterView.java` + `FooterViewModel/FooterMessageViewModel/FooterButtonViewModel` + `FooterViewBinder`：footer 采用 **ViewModel+Binder 模式**（四个 VM/Binder 均为 Kotlin；`FooterView` 视图本体仍为 Java）——「清除全部」按钮与状态栏提示文案（如「已隐藏敏感内容」）的展示位
- 空态（empty shade）：无通知时的引导视图（`emptyshade/` 包 + Compose 化的部分在 `compose/features/` 下，05 篇）
- `stack/NotificationPriorityBucket.kt`：优先级分桶（影响视觉分组与圆角策略）

## 4.6 杂项但重要

| 组件 | 作用 |
|---|---|
| `NotificationRoundnessManager` | 相邻 row 间共享圆角（顶部/底部圆角不重叠） |
| `MagneticNotificationRowManager` | 「磁吸」：展开中的 row 吸住顶部等磁性行为 |
| `stack/data/`、`stack/domain/` | stack 的 Kotlin 化新层（状态流化进行中） |
| `DisplaySwitchNotificationsHiderTracker` | 折叠屏切换显示时短暂隐藏通知（防跨屏残影） |

## 4.7 本篇小结（本项目落点）

- 全部 `:SystemUI-core`：`statusbar/notification/stack/`（NSSL 及全部布局组件）。
- 阅读顺序建议：先 [01 篇](./01-ingress-pipeline.md) §1.4/§1.6 弄清列表**怎么来**，再看本篇弄清**怎么摆**。
- NSSL 是 SystemUI 最重的自定义布局，测量/动画逻辑与业务深度耦合——改通知列表 UI 的复杂度大头在这，改前先用 `NotificationSectionsLogger`/`NotificationStackScrollLayout` 的 dump 摸清当前状态。
- 与 [06 篇](./06-interaction.md) 的接缝：NSSL 上的手势（滑动/展开/下拉）由 `NotificationStackScrollLayoutController` 分发给 `SwipeHelper` 与 row。
