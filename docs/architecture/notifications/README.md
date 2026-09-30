# 通知（Notification）知识库

> 编写时间：2026-09-30（增量编写，讲解 → 纠偏 → 沉淀）
> 基准：AOSP `android-17.0.0_r1`（ADR 0007 冻结），对应本仓库 `SystemUI-core` 源码
> 引用约定：文中路径均为本仓库内路径；锚定到「路径 + 类名 + 方法」，不标行号
> 实时构建状态唯一见 `docs/CURRENT_STATE.md`
> 姊妹篇：[AOD 知识库](../2026-09-30-aod-knowledge-base.md)（§2.6 AOD 通知区）、[插件化知识库](../2026-09-30-systemui-plugin-knowledge-base.md)（NotificationListenerWithPlugins 插件拦截）

本知识库覆盖 SystemUI 通知子系统：从 framework `NotificationManagerService` 下发事件，经 NotifPipeline 七个 Stage 变成 shade 里的列表与视图，再到横幅/锁屏/状态栏等展示面。入口与管道细节见 [01 篇](./01-ingress-pipeline.md)，本篇是全局索引与架构导论。

## 全链路一图

```mermaid
flowchart TD
    NMS["NotificationManagerService (framework)"] -->|"StatusBarNotification + RankingMap<br/>onNotificationPosted/Removed/RankingUpdate"| L
    subgraph SystemUI 通知管道（NotifPipeline, 7 Stage）
        L["STAGE 1 LISTEN<br/>NotificationListener<br/>(NLS 实现 + 插件桥)"] --> C
        C["STAGE 2 BATCH<br/>GroupCoalescer<br/>(分组事件聚合, linger 200/500ms)"] --> COL
        COL["STAGE 3 COLLECT<br/>NotifCollection<br/>(key→NotificationEntry 集合<br/>lifetime extender / dismiss interceptor)"] --> B
        B["STAGE 4 BUILD LIST<br/>ShadeListBuilder<br/>(buildList: filter→group→bundle→promote→<br/>stabilize→section→sort→finalize)"] --> R
        R["STAGE 5 DISPATCH RENDER<br/>RenderStageManager"] --> S
        S["STAGE 6 UPDATE SHADE<br/>ShadeViewManager<br/>(NodeSpecBuilder/ShadeViewDiffer/ViewBarn)"]
    end
    subgraph STAGE 0 横切
        CO["NotifCoordinators（29 常驻 + 3 flag 门控 = 32 个<br/>Coordinator 注册 filter/promoter/section/listener）"]
    end
    CO -.->|attach 时注册 hook| B
    CO -.->|attach 时注册 hook| COL
    CO -.->|attach 时注册 hook| R
    S --> SURFACES["展示面：状态栏图标 / 通知栏列表 /<br/>HUN 横幅 / 锁屏 / AOD"]
```

装配顺序（`collection/init/NotifPipelineInitializer.java#initialize`）自顶向下：

```java
mNotifPluggableCoordinators.attach(mPipelineWrapper);   // STAGE 0 先挂 hook
mShadeViewManager = mShadeViewManagerFactory.create(listContainer);
mShadeViewManager.attach(mRenderStageManager);          // STAGE 6 → 5
mRenderStageManager.attach(mListBuilder);               // STAGE 5 → 4
mListBuilder.attach(mNotifCollection);                  // STAGE 4 → 3
mNotifCollection.attach(mGroupCoalescer);               // STAGE 3 → 2
mGroupCoalescer.attach(mNotificationService);           // STAGE 2 → 1
```

## 关键设计决策（AOSP 14+ 重写后的架构）

1. **数据与视图彻底分离**：管道产出的始终是 `ListEntry` 树（`NotificationEntry`/`GroupEntry`/`BundleEntry`），视图层（row）只是 entry 的渲染产物，由 `NotifViewBarn` 按 entry.key 缓存。
   - 源码证据：`collection/render/NotifViewBarn.kt` 是 `mutableMapOf<String, NotifViewController>()`；`NodeSpecBuilder.kt#buildNotifNode` 首次把 entry 翻译成 NodeSpec（"NotificationEntries finally become associated with the views"）。
2. **一切扩展点都是「可失效的注册器」**：filter/promoter/comparator/sectioner 等 Pluggable 状态变化时回调 `invalidateList(reason)` → `NotifPipelineChoreographer.schedule()` 合并调度 → 重跑 `buildList`（Choreographer 下一帧对齐 + `TIMEOUT_MS = 100` 超时兜底）。
   - 源码证据：`collection/NotifPipelineChoreographer.kt#NotifPipelineChoreographerImpl`；`collection/listbuilder/pluggable/Pluggable.java#setInvalidationListener`。
3. **Coordinator 模式**：32 个 coordinator 按子系统拆分（29 个常驻 + `LockScreenMinimalismCoordinator`/`HighlightsCoordinator`/`HsuCoordinator` 3 个 flag 门控），每个在 `attach(pipeline)` 时往管道挂自己的 hook，互不直接依赖；装配顺序集中写在 `coordinator/NotifCoordinators.kt#NotifCoordinatorsImpl` 的 `init` 块里。
4. **分组原子性**：`GroupCoalescer` 把同一 group 的连续 post 聚合成 `EventBatch`，linger 超时/组内 update/retract 任一满足即一次性 `onNotificationBatchPosted` 交给 Collection（避免「summary 先到、child 后到」的列表跳变）。
   - 源码证据：`collection/coalescer/GroupCoalescer.java#emitBatch`、`MIN_GROUP_LINGER_DURATION = 200`、`MAX_GROUP_LINGER_DURATION = 500`。
5. **事件驱动 + 帧合并调度**：NotifCollection 每个入口动作走 `dispatchEventsAndRebuildList(reason)`——先把 `mEventQueue` 里的 `NotifEvent` 派发给所有 `NotifCollectionListener`，再经 `CollectionReadyForBuildListener.onBuildList` 触发 Choreographer；一帧内 N 个变化只重建一次列表。
   - 源码证据：`collection/NotifCollection.java#dispatchEventsAndRebuildList`；`collection/ShadeListBuilder.java#mReadyForBuildListener`。
6. **视觉稳定性横切**：`VisualStabilityCoordinator` 实现唯一的 `NotifStabilityManager`（`setNotifStabilityManager` 只允许设一次，二次设置抛 `IllegalStateException`），在 buildList 内抑制 group/section/排序变化直到用户不再注视列表。
   - 源码证据：`coordinator/VisualStabilityCoordinator.java#mNotifStabilityManager`；`ShadeListBuilder.java#setNotifStabilityManager`。
7. **17 新增 AI 摘要束**：`BundleEntry`（`collection/BundleEntry.kt`）进入管道树，`ShadeListBuilder#buildList` 在 Step 3.5 `bundleNotifs` 按 `NotifBundler` 分类归束，`SummarizationCoordinator`/`BundleCoordinator` 装配；相关 flag：`NmSummarizationAllFlag`、`NmContextualDisplay`、`NotificationMinimalism`。

## 术语表

| 术语 | 含义 | 源码锚点 |
|---|---|---|
| `StatusBarNotification` (sbn) | framework 传来的原始通知对象，key 唯一 | `NotificationListener.java#onNotificationPosted` 参数 |
| `RankingMap` | NMS 给的排序/通道/重要性信息；与 sbn 有竞态，listener 侧补 stub | `NotificationListener.java#getRankingOrTemporaryStandIn` |
| `Ranking` | 单条通知的排序记录（importance/channel/visibility override 等） | `NotifCollection.java#requireRanking` |
| `NotificationEntry` | SystemUI 侧通知模型（包装 sbn + ranking + row 引用 + 生命周期状态） | `collection/NotificationEntry.java` |
| `GroupEntry` | 逻辑分组（summary + children），`ROOT_ENTRY` 为虚拟根 | `collection/GroupEntry.java#ROOT_ENTRY` |
| `BundleEntry` | 17 新增：AI 摘要/分类生成的通知束 | `collection/BundleEntry.kt` |
| `PipelineEntry` | buildList 列表元素类型（NotificationEntry/GroupEntry/BundleEntry 的公共 sealed 视角） | `collection/PipelineEntry.kt`（`ShadeListBuilder` 全程使用） |
| Pluggable | 管道扩展点基类：`NotifFilter`/`NotifPromoter`/`NotifComparator`/`NotifSectioner`/`NotifStabilityManager`/`NotifBundler`/`Invalidator` | `collection/listbuilder/pluggable/Pluggable.java` |
| Coordinator | 子系统装配器：`attach(pipeline)` 时把该子系统的 pluggable/listener 挂到管道 | `collection/coordinator/Coordinator.java` |
| `NotifCoordinators` | 全部 coordinator 的装配器，集中定义 attach 顺序与 section 顺序 | `collection/coordinator/NotifCoordinators.kt#attach` |
| LifetimeExtender | 通知被 system server retract 后延迟真正移除（如 HUN 还在显示） | `collection/notifcollection/NotifLifetimeExtender.java`；`NotifCollection.java#updateLifetimeExtension` |
| DismissInterceptor | 拦截用户 dismiss（拦截则不发 cancel 给 system server） | `collection/notifcollection/NotifDismissInterceptor.java`；`NotifCollection.java#updateDismissInterceptors` |
| `NotifStabilityManager` | 视觉稳定性闸门：isPipelineRunAllowed / isParentChangeAllowed / isSectionChangeAllowed / isEntryReorderingAllowed / isGroupPruneAllowed | `collection/listbuilder/pluggable/NotifStabilityManager.java` |
| HUN | Heads-Up Notification，横幅通知（管道视角是 promote 的来源之一） | `coordinator/HeadsUpCoordinator.kt#mHeadsUpPromoter` |
| `GroupCoalescer` | STAGE 2：组事件聚合器，batch 原子释放 | `collection/coalescer/GroupCoalescer.java#emitBatch` |
| `EventBatch` | 一个 groupKey 攒下的待释放事件集合，带 `mCancelShortTimeout` 可取消定时器 | `GroupCoalescer.java#EventBatch`（内部类） |
| `NotifCollection` | STAGE 3：key→entry 权威集合，`getAllNotifs()` = 手机上全部通知（未排序未过滤） | `collection/NotifCollection.java` |
| `ShadeListBuilder` | STAGE 4：buildList 心脏，把 collection 变成 shade list | `collection/ShadeListBuilder.java#buildList` |
| `PipelineState` | buildList 内部状态机（IDLE→BUILD_STARTED→…→IDLE），用于失效请求的阶段裁决 | `collection/listbuilder/PipelineState.java` |
| `SemiStableSort` | 稳定性开启时的半稳定排序：同等比较结果保持上一轮 stableIndex | `collection/listbuilder/SemiStableSort.kt`；`ShadeListBuilder.java#sortWithSemiStableSort` |
| `NotifSection` / bucket | 分段与其优先级 bucket（BUCKET_ALERTING/BUCKET_SILENT/BUCKET_UNKNOWN…） | `collection/listbuilder/NotifSection.java`；`stack/NotificationPriorityBucketKt` |
| `RenderStageManager` | STAGE 5：接最终 list → 调 NotifViewRenderer → 派发 OnAfterRender* | `collection/render/RenderStageManager.kt#onRenderList` |
| `ShadeViewManager` | STAGE 6：NotifViewRenderer 装配点（NodeSpecBuilder + ShadeViewDiffer） | `collection/render/ShadeViewManager.kt` |
| `NotifViewBarn` | entry.key→`NotifViewController` 视图缓存（register/remove） | `collection/render/NotifViewBarn.kt#registerViewForEntry` |
| `ShadeViewDiffer` | 把 NodeSpec 与真实视图树 diff，下发 add/remove/move | `collection/render/ShadeViewDiffer.kt#applySpec` |
| `GroupExpansionManager` | 组展开状态跟踪（mExpandedCollections），OnBeforeRenderList 时清理失效 entry | `collection/render/GroupExpansionManagerImpl.java#mNotifTracker` |
| `NotifPipelineChoreographer` | 帧合并调度器：schedule() 在下一帧或 100ms 超时后统一 buildList | `collection/NotifPipelineChoreographer.kt#TIMEOUT_MS` |
| Guts | 通知长按后的设置面板 | `coordinator/GutsCoordinator.kt`（→ 06 篇） |
| `InternalNotifUpdater` | 管道内部回流通道：用户操作导致的本地状态变化经它更新 collection | `NotifCollection.java#getInternalNotifUpdater` |
| `FutureDismissal` | 异步操作（如启动 Activity）期间注册的「未来 dismiss」，entry 移除时回调 | `NotifCollection.java#registerFutureDismissal` |

## 源码地图（`:SystemUI-core`）

```
src/com/android/systemui/statusbar/
├── NotificationListener.java          # STAGE 1：唯一 NLS 实现（插件桥 + ranking 队列）
└── notification/
    ├── collection/                    # 管道主体
    │   ├── NotifPipeline.kt           # 对外门面（hook 0–13 顺序的权威文档，全文见 01 篇）
    │   ├── NotifPipelineChoreographer.kt  # 帧合并调度（TIMEOUT_MS=100）
    │   ├── NotifCollection.java       # STAGE 3（event queue / lifetime extender / dismiss）
    │   ├── ShadeListBuilder.java      # STAGE 4（buildList 心脏 + PipelineState 状态机）
    │   ├── NotificationEntry.java / GroupEntry.java / BundleEntry.kt / ListEntry.kt / PipelineEntry.kt
    │   ├── ListAttachState.kt         # attach 状态（parent/section/promoter/suppressedChanges）
    │   ├── coalescer/GroupCoalescer.java + CoalescedEvent.kt + GroupCoalescerLogger.kt
    │   ├── coordinator/               # STAGE 0：NotifCoordinators.kt + 32 个 Coordinator
    │   ├── listbuilder/               # PipelineState / NotifSection / SemiStableSort / ShadeListBuilderHelper
    │   ├── listbuilder/pluggable/     # 全部可插拔类型（Pluggable/NotifFilter/…）
    │   ├── notifcollection/           # NotifCollectionListener/NotifEvent/LifetimeExtender/DismissInterceptor
    │   ├── inflation/                 # NotifInflater + NotificationRowBinder（→ 02 篇）
    │   ├── init/NotifPipelineInitializer.java  # 七 Stage 装配 + dumpsys 入口
    │   └── render/                    # STAGE 5/6：RenderStageManager/ShadeViewManager/ShadeViewDiffer/
    │                                  #   NodeSpecBuilder/RootNodeController/NotifViewBarn/GroupExpansionManagerImpl
    ├── row/                           # ExpandableNotificationRow 与 inflate（→ 02 篇）
    ├── headsup/                       # HeadsUpManager 等（→ 03 篇）
    ├── stack/ shelf/ promoted/ icon/  # 列表视图层（→ 04/05 篇）
    └── data/ domain/ shared/          # Kotlin 化的新层（05/06 篇涉及）
compose/features/src/com/android/systemui/notifications/   # Compose 通知 UI（05 篇涉及）
```

## 各篇导航

| 篇 | 内容 | 状态 |
|---|---|---|
| [01-ingress-pipeline.md](./01-ingress-pipeline.md) | 入口契约 + 7 Stage 管道全解（buildList 逐段 + coordinator 深读 + 渲染 diff） | ✅ |
| [02-card-and-inflate.md](./02-card-and-inflate.md) | 通知卡片：Entry/Row/Inflate/BindStage | ✅ |
| [03-heads-up.md](./03-heads-up.md) | 横幅：alerting/HeadsUpManager/Avalanche | ✅ |
| [04-shade-list.md](./04-shade-list.md) | 通知栏：stack/sections/分组/footer | ✅ |
| [05-surfaces.md](./05-surfaces.md) | 状态栏图标/锁屏/AOD 通知面 | ✅ |
| [06-interaction.md](./06-interaction.md) | 滑动/dismiss/launch 动画/remote input | ✅ |
| [07-project-landing.md](./07-project-landing.md) | Gradle 落点全表 + dumpsys 调试速查 | ✅ |

**排查入口约定**：`adb shell dumpsys activity service com.android.systemui/.SystemUIService NotifPipeline`，输出由 `NotifPipelineInitializer#dumpPipeline` 按 STAGE 0–6 逐级展开全部内部状态（含每个 coordinator 的私有字段）。
