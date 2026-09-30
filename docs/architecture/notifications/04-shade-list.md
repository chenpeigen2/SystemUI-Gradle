# 04 · 通知栏列表（Shade List）

> 通知在展开的通知面板里的列表呈现：管道产出的 `List<PipelineEntry>` 如何变成 `NotificationStackScrollLayout` 里的视图树。渲染绑定骨架见 [01](./01-ingress-pipeline.md) §1.6，手势见 [06](./06-interaction.md)。
> 基准：AOSP `android-17.0.0_r1`；引用格式 `路径` + `类#方法`；源码摘录均已验证。

## 4.1 视图容器：NotificationStackScrollLayout（NSSL）

`stack/NotificationStackScrollLayout.java`——SystemUI 最大的自定义 ViewGroup（5600+ 行），通知列表的全部布局/滚动/动画都收敛在这里。

### 4.1.1 结构

```java
// NotificationStackScrollLayout.java:176（声明）
public class NotificationStackScrollLayout extends ViewGroup
        implements ScrollContainer, StackScrollAlgorithm.TargetHeadsUpStateListener,
        NotificationListContainer, SwipeableView.OnBeforeSwipeActionListener, ...
```

- **子 View 组成**：各 entry 的 `ExpandableNotificationRow` + `SectionHeaderView`（段头）+ `FooterView`（footer）+ `MediaContainerView` + shelf
- **手势入口**：`onInterceptTouchEvent`（`:3914`）分发给 `NotificationSwipeHelper`（[06 篇](./06-interaction.md)）与展开/滚动手势
- **布局**：`onLayout`（`:1208`）→ `updateChildren()`（`:1457`）→ `updateAlgorithmLayoutMinHeight()`；横幅动画事件 `generateHeadsUpAnimationEvents`（`:3649`，[03 篇](./03-heads-up.md) §3.4 的动画驱动点）

### 4.1.2 AmbientState：全局布局状态

`stack/AmbientState.java`（`implements Dumpable`，`:56`）——布局/动画的**全局状态容器**（当前展开高度、滚动位置、跟踪手势速度、锁屏/doze 状态、pocket 检测等），layout 与动画解耦的关键：算法读 AmbientState 算出每个 child 的目标状态，动画系统拿目标状态插值。

### 4.1.3 ExpandableViewState：每 child 的目标布局状态

`stack/ExpandableViewState.java`（`extends ViewState`，`:39`）：

```java
public class ExpandableViewState extends ViewState {
    public int height;        // 目标高度（:94）
    // 还有 yTranslation、zTranslation、alpha、clipTopAmount、圆角等字段
    // applyToView(view) 把目标状态落到实际 View 上
}
```

布局引擎产出各 child 的 `ExpandableViewState`，`StackStateAnimator` 据此做 spring/插值动画——**「通知条目平滑滑动到新位置」全部由这套目标状态 + 动画器完成**。

### 4.1.4 布局管线全走查（updateChildren → 算法 → 动画）

`NSSL#updateChildren`（`:1457`）是每次结构/状态变化后的总入口：

```java
// NotificationStackScrollLayout#updateChildren（源码摘录）
private void updateChildren() {
    updateScrollStateForAddedChildren();
    mAmbientState.setCurrentScrollVelocity(mScroller.isFinished() ? 0 : mScroller.getCurrVelocity());
    mStackScrollAlgorithm.resetViewStates(mAmbientState, getSpeedBumpIndex());  // 算目标状态
    if (!isCurrentlyAnimating() && !mNeedsAnimation) {
        applyCurrentState();       // 无动画：直接落
    } else {
        startAnimationToState();   // 有动画：交给 StackStateAnimator
    }
    avoidNotificationOverlaps();   // 后处理：隦面裁剪防重叠
}
```

**布局算法** `StackScrollAlgorithm.java`（`:54`）：

1. `resetViewStates(ambientState, speedBumpIndex)`（`:140`）→ `initAlgorithmState`（可见 child 列表、当前 y 游标）→ `updatePositionsForState` → `updateZValuesForState`（z 层级，`:1260`）
2. 逐 child `updateChild(i, algorithmState, ambientState)`（`:670`）：**y 游标累加式布局**——

```java
// StackScrollAlgorithm#updateChild（核心摘录）
ExpandableViewState viewState = view.getViewState();
// 段间间距：按 section provider + 当前展开分数 + 锁屏态算 gap
final float gap = getGapHeightForChild(ambientState.getSectionProvider(), i, view,
        getPreviousView(i, algorithmState), ambientState.getFractionToShade(),
        ambientState.isOnKeyguard());
algorithmState.mCurrentYPosition += expansionFraction * gap;
algorithmState.mCurrentExpandedYPosition += gap;
viewState.setYTranslation(algorithmState.mCurrentYPosition, "...updateChild.init");
```

输出是每个 child 的 `ExpandableViewState`（yTranslation/height/z/alpha/圆角/`location` 字段）。

**动画** `StackStateAnimator.java`（`:52`）：

- `mNewEvents: ArrayList<AnimationEvent>`（`:85`）收集本轮动画事件（ADD/REMOVE/HEADSUP 等，[03 篇](./03-heads-up.md) §3.4 的 `generateHeadsUpAnimationEvents` 也注入这里）
- `mAnimationFilter.applyCombination(mNewEvents)`（`:176`）把多个事件合成为一组统一的动画参数（`AnimationFilter`，含 `hasGoToFullShadeEvent`/`customDelay` 等特殊路径）
- 目标位置/高度以 `ExpandableViewState` 为准做 spring/插值

**重叠防护** `avoidNotificationOverlaps()`：后处理遍历按 `notGoneIndex` 排序的 child，设 `topOverlap/bottomOverlap` 做裁剪——通常后来的视图顶部裁剪防重叠，正在消失（dismiss/移除）的视图则裁底部。

### 4.1.5 滚动

`stack/OverScrollerWrapper.kt` 包装系统 `android.widget.OverScroller`（同包 `NoOpOverScroller.kt` 是 `OverScrollerInterface` 的空实现，用于无滚动场景）；顶部/底部 over-scroll 物理效果在 NSSL 内实现。滚动速度经 `mScroller.getCurrVelocity()` 回写 `AmbientState.setCurrentScrollVelocity`（§4.1.4），参与动画插值。

## 4.2 从管道到 NSSL：渲染绑定全链

[01 篇](./01-ingress-pipeline.md) §1.6 讲了骨架，本篇补视图侧完整链条：

1. `ShadeListBuilder#buildList` Step 8 → `RenderStageManager#onRenderList` → `NotifViewRenderer.onRenderList(notifList)`
2. **NodeSpecBuilder#buildNodeSpec**：`List<PipelineEntry>` → `NodeSpec` 节点树（每个节点一个 `NodeController`：`RootNodeController` 为根、group 节点、entry 节点）
3. **ShadeViewDiffer#applySpec**：diff 目标树与当前树，增删 child、调整顺序（源码语义：「Adds and removes views from the root until their structure matches the provided spec」）
4. **NotifViewBarn#requireRowController(entry)**：entry.key→`NotifViewController` 映射；**未命中直接 `error("No view has been registered...")`**——渲染层永远只拿已 inflate 就绪的视图（PreparationCoordinator 闸门保证，[02 篇](./02-card-and-inflate.md) §2.2.1）
5. child 实际进入 NSSL 经 `NotificationListContainer`（NSSL 实现的接口）的 `bindRow`/`getViewParentForNotification`

## 4.3 Sections：分段与段头

段的**定义**在管道侧（`NotifSectioner`，[01 篇](./01-ingress-pipeline.md) §1.4），段的**视图**在 stack 侧：

### 4.3.1 NotificationSectionsManager

`stack/NotificationSectionsManager.kt`（`:48`）持有各段的 `SectionHeaderView`：

```kotlin
val silentHeaderView: SectionHeaderView?     // :81
val alertingHeaderView: SectionHeaderView?   // :85
val incomingHeaderView: SectionHeaderView?   // :89
val peopleHeaderView: SectionHeaderView?     // :93
val highlightsHeaderView: SectionHeaderView? // :101
```

按管道结果（各 sectioner 的 `onEntriesUpdated` 通知成员变化）增删/排序段头；段头本体 `stack/SectionHeaderView.java`（可展开收起的分隔条）。

### 4.3.2 段头的节点化

`collection/render/SectionHeaderController.kt`（`SectionHeaderNodeControllerImpl`）把段头作为节点插进 `NodeSpec` 树——**段头是渲染树的一等公民**，与 row 同一套 diff 机制管理。`collection/provider/SectionHeaderVisibilityProvider.kt` 控制「静默通知」段头是否显示（`SectionStyleProvider` 的样式接口）。

### 4.3.3 NotificationSection：段的边界模型

`stack/NotificationSection.java`——每段是「bucket + 首尾可见 child」的边界对（源码摘录）：

```java
/** Represents the bounds of a section of the notification shade and handles animation when the bounds change. */
public class NotificationSection {
    private final int mBucket;                       // PriorityBucket
    private ExpandableView mFirstVisibleChild;
    private ExpandableView mLastVisibleChild;
    // setFirstVisibleChild/setLastVisibleChild 返回 changed 布尔→触发段头动画
}
```

`NotificationSectionsManager.kt:117`：`sections = PriorityBucket.getAllInOrder().map { NotificationSection(it) }`——**段与 PriorityBucket 一一对应**（§4.6 的 `NotificationPriorityBucket`）；`updateSection(section)`（`:178`）在每次渲染后更新边界并决定段头显隐动画。

### 4.3.4 相关开关

- `NotificationSectionsFeatureManager.kt`（`notification/` 根包）：段功能开关（如 conversation section 可用性）

## 4.4 分组呈现

### 4.4.1 NotificationChildrenContainer

`stack/NotificationChildrenContainer.java`——组 summary 的 row 展开后内部的**子通知容器**：

- 管理组内 child 的 mini row（`HybridNotificationView` 单行精简视图，[02 篇](./02-card-and-inflate.md) §2.7）
- 计算组高度（折叠/展开两态）、展开动画
- child 被 promoter 提走（[03 篇](./03-heads-up.md) HUN 提升）时随 `GroupEntry` 结构变化坍缩

### 4.4.2 GroupExpansionManagerImpl

`collection/render/GroupExpansionManagerImpl.java`（`implements GroupExpansionManager, Dumpable`，`:49`）：

```java
public boolean isGroupExpanded(EntryAdapter entry) { ... }          // :127
// toggle：setGroupExpanded(groupRoot, !isGroupExpanded(groupRoot))  // :163
```

用户点 summary 展开/折叠组；展开态回写管道（影响排序、高度计算与 child 的 SINGLE_LINE 槽需求）。

### 4.4.3 HybridGroupManager

`row/HybridGroupManager.java`——组内 child 单行混合视图（icon + 单行文本）的构建与更新；锁屏/紧凑场景同样使用。

## 4.5 Footer 与空态

- `footer/ui/view/FooterView.java` + `FooterViewModel`/`FooterMessageViewModel`/`FooterButtonViewModel` + `FooterViewBinder`（`footer/ui/viewbinder/`）：footer 采用 **ViewModel+Binder 模式**（四个 VM/Binder 均为 Kotlin；`FooterView` 视图本体仍为 Java）——「清除全部」按钮与状态栏提示文案（如「已隐藏敏感内容」）的展示位
- 空态：`emptyshade/` 包（`EmptyShadeView`/`EmptyShadeViewModel` 等）——无通知时的引导视图

## 4.6 布局周边系统

| 组件 | 机制 |
|---|---|
| `NotificationRoundnessManager` | 相邻 row 共享圆角（顶部/底部圆角不重叠；滚动时动态调整） |
| `MagneticNotificationRowManager(Impl).kt` | 「磁吸」：展开中的 row 吸住顶部等磁性行为 |
| `stack/NotificationPriorityBucket.kt` | 优先级分桶（影响视觉分组与圆角策略） |
| `DisplaySwitchNotificationsHiderTracker.kt` | 折叠屏切屏时短暂隐藏通知（防跨屏残影） |
| `stack/data/`、`stack/domain/` | stack 的 Kotlin 化新层（状态流化进行中） |
| `NotificationChildrenContainerLogger`/`NotificationSectionsLogger` | 日志缓冲（dumpsys/logcat 排查用） |

## 4.7 本篇小结（本项目 Gradle 落点）

- 全部 `:SystemUI-core`：`statusbar/notification/stack/`（NSSL 及全部布局组件）+ `collection/render/`（NodeSpecBuilder/ShadeViewDiffer/ViewBarn/GroupExpansion）。
- 阅读顺序建议：先 [01 篇](./01-ingress-pipeline.md) §1.4/§1.6 弄清列表**怎么来**，再看本篇弄清**怎么摆**。
- NSSL 是复杂度大头：测量/动画与业务深度耦合，改前先 `dumpsys` 看 `NotificationStackScrollLayout`/`AmbientState`（均 `Dumpable`）与 `NotificationSectionsLogger` 缓冲。
- 与 [06 篇](./06-interaction.md) 的接缝：NSSL 的 `onInterceptTouchEvent` 分发手势给 `NotificationSwipeHelper` 与 row。
