# 通知（Notification）知识库

> 编写时间：2026-09-30（增量编写，讲解 → 纠偏 → 沉淀）
> 基准：AOSP `android-17.0.0_r1`（ADR 0007 冻结），对应本仓库 `SystemUI-core` 源码
> 引用约定：文中路径均为本仓库内路径；锚定到「路径 + 类名 + 方法」，不标行号
> 实时构建状态唯一见 `docs/CURRENT_STATE.md`
> 姊妹篇：[AOD 知识库](../2026-09-30-aod-knowledge-base.md)（§2.6 AOD 通知区）、[插件化知识库](../2026-09-30-systemui-plugin-knowledge-base.md)（NotificationListenerWithPlugins 插件拦截）

## 全链路一图

```mermaid
flowchart TD
    NMS["NotificationManagerService (framework)"] -->|"StatusBarNotification + RankingMap<br/>onNotificationPosted/Removed/RankingUpdate"| L
    subgraph SystemUI 通知管道（NotifPipeline, 7 Stage）
        L["STAGE 1 LISTEN<br/>NotificationListener<br/>(NLS 实现 + 插件桥)"] --> C
        C["STAGE 2 BATCH<br/>GroupCoalescer<br/>(分组事件聚合, linger)"] --> COL
        COL["STAGE 3 COLLECT<br/>NotifCollection<br/>(key→NotificationEntry 集合<br/>lifetime extender / dismiss interceptor)"] --> B
        B["STAGE 4 BUILD LIST<br/>ShadeListBuilder<br/>(14 步: filter→group→promote→<br/>section→sort→finalize)"] --> R
        R["STAGE 5 DISPATCH RENDER<br/>RenderStageManager"] --> S
        S["STAGE 6 UPDATE SHADE<br/>ShadeViewManager<br/>(NotifViewRenderer/ViewBarn/Stack)"]
    end
    subgraph STAGE 0 横切
        CO["NotifCoordinators（32 个 Coordinator<br/>注册 filter/promoter/section/listener）"]
    end
    CO -.->|attach 时注册 hook| B
    CO -.->|attach 时注册 hook| COL
    S --> SURFACES["展示面：状态栏图标 / 通知栏列表 /<br/>HUN 横幅 / 锁屏 / AOD"]
```

## 关键设计决策（AOSP 14+ 重写后的架构）

1. **数据与视图彻底分离**：管道产出的始终是 `ListEntry` 树（`NotificationEntry`/`GroupEntry`/`BundleEntry`），视图层（row）只是 entry 的渲染产物，由 `NotifViewBarn` 按 entry 缓存。
2. **一切扩展点都是「可失效的注册器」**：filter/promoter/comparator/sectioner 等 pluggable 变更时回调 invalidation → `NotifPipelineChoreographer` 合并调度 → 重跑 `buildList`（Choreographer 帧对齐 + 100ms 超时兜底）。
3. **Coordinator 模式**：32 个 coordinator 按子系统拆分（29 个常驻 + LockScreenMinimalism/Highlights/Hsu 3 个 flag 门控）（锁屏/排名/HUN/对话/17 新增 summarization…），每个在 attach 时往管道挂自己的 hook，互不直接依赖。
4. **分组原子性**：`GroupCoalescer` 把同一 group 的连续 post 聚合成 batch 一次性交给 Collection（避免「summary 先到、child 后到」导致的列表跳变）。

## 术语表

| 术语 | 含义 |
|---|---|
| `StatusBarNotification` (sbn) | framework 传来的原始通知对象（key 唯一） |
| `RankingMap` | NMS 给的排序/通道/重要性信息（与 sbn 可能有竞态，listener 侧补 stub） |
| `NotificationEntry` | SystemUI 侧通知模型（包装 sbn + ranking + row 引用 + 生命周期状态） |
| `GroupEntry` | 逻辑分组（summary + children） |
| `BundleEntry` | 17 新增：AI 摘要生成的通知束（NmSummarization） |
| Pluggable | 管道扩展点：`NotifFilter`/`NotifPromoter`/`NotifComparator`/`NotifSectioner`/`NotifStabilityManager`/`NotifBundler`/`Invalidator` |
| Coordinator | 子系统装配器：把该子系统的 pluggable/listener 挂到管道 |
| LifetimeExtender | 通知被 retract 后延迟移除（如 HUN 还在显示） |
| DismissInterceptor | 拦截用户 dismiss（如 sensitive content 需先解锁） |
| HUN | Heads-Up Notification，横幅通知 |
| Guts | 通知长按后的设置面板 |

## 源码地图（`:SystemUI-core`）

```
src/com/android/systemui/statusbar/
├── NotificationListener.java          # STAGE 1：唯一 NLS 实现
└── notification/
    ├── collection/                    # 管道主体
    │   ├── NotifPipeline.kt           # 对外门面（14 步 hook 顺序的权威文档）
    │   ├── NotifPipelineChoreographer.kt  # 帧合并调度
    │   ├── NotifCollection.java       # STAGE 3
    │   ├── ShadeListBuilder.java      # STAGE 4（buildList 心脏）
    │   ├── NotificationEntry.java / GroupEntry.java / BundleEntry.kt / ListEntry.kt
    │   ├── coalescer/GroupCoalescer.java  # STAGE 2
    │   ├── coordinator/               # STAGE 0（33 个 Coordinator）
    │   ├── listbuilder/pluggable/     # 全部可插拔类型
    │   ├── inflation/                 # NotifInflater + NotificationRowBinder（→ 02 篇）
    │   └── render/                    # STAGE 5/6（RenderStageManager/ShadeViewManager/ViewBarn/Stack）
    ├── row/                           # ExpandableNotificationRow 与 inflate（→ 02 篇）
    ├── headsup/                       # HeadsUpManager 等（→ 03 篇）
    ├── stack/ shelf/ promoted/ icon/  # 列表视图层（→ 04 篇）
    └── data/ domain/ shared/          # Kotlin 化的新层（05/06 篇涉及）
compose/features/src/com/android/systemui/notifications/   # Compose 通知 UI（05 篇涉及）
```

## 各篇导航

| 篇 | 内容 | 状态 |
|---|---|---|
| [01-ingress-pipeline.md](./01-ingress-pipeline.md) | 入口契约 + 7 Stage 管道全解 | ✅ |
| [02-card-and-inflate.md](./02-card-and-inflate.md) | 通知卡片：Entry/Row/Inflate/BindStage | ✅ |
| [03-heads-up.md](./03-heads-up.md) | 横幅：alerting/HeadsUpManager/Avalanche | ✅ |
| [04-shade-list.md](./04-shade-list.md) | 通知栏：stack/sections/分组/footer | ✅ |
| [05-surfaces.md](./05-surfaces.md) | 状态栏图标/锁屏/AOD 通知面 | ✅ |
| [06-interaction.md](./06-interaction.md) | 滑动/dismiss/launch 动画/remote input | ✅ |
| [07-project-landing.md](./07-project-landing.md) | Gradle 落点全表 + dumpsys 调试速查 | ✅ |
