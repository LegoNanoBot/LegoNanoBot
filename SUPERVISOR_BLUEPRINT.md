# Supervisor Gateway — 活跃蓝图

> **当前状态**：Phase 0 到 Phase 4 已完成并归档；本文件仅保留未完成阶段的活跃规划。  
> **设计原则**：每个 Phase 可独立交付、独立测试、独立回滚。后续 Phase 依赖前置 Phase 但不破坏已有功能。

---

## 目录

- [文档导航](#文档导航)
- [架构全景](#架构全景)
- [Phase 5 — 记忆与上下文共享](#phase-5--记忆与上下文共享)
- [Phase 6 — 高级调度](#phase-6--高级调度)
- [Phase 7 — 计划智能](#phase-7--计划智能)
- [Phase 8 — 安全与认证](#phase-8--安全与认证)
- [Phase 9 — 可观测性仪表盘](#phase-9--可观测性仪表盘)
- [Phase 10 — 水平扩展与高可用](#phase-10--水平扩展与高可用)
- [实现优先级矩阵](#实现优先级矩阵)

---

## 文档导航

- 活跃规划：当前文件
- 文档索引：[docs/supervisor/README.md](docs/supervisor/README.md)
- 已完成归档：
  - [Phase 0 Release Note](docs/supervisor/release-notes/phase-0-mvp.md)
  - [Phase 1 Release Note](docs/supervisor/release-notes/phase-1-production-hardening.md)
  - [Phase 2 Release Note](docs/supervisor/release-notes/phase-2-state-persistence-and-recovery.md)
  - [Phase 3 Release Note](docs/supervisor/release-notes/phase-3-channel-integration.md)
  - [Phase 4 Release Note](docs/supervisor/release-notes/phase-4-agent-loop-integration.md)

## 已完成阶段摘要

### Phase 0 — MVP 验证

- 已完成，详细交付与历史快照见 [docs/supervisor/release-notes/phase-0-mvp.md](docs/supervisor/release-notes/phase-0-mvp.md)

### Phase 1 — 生产加固

- 已完成，详细交付与测试结果见 [docs/supervisor/release-notes/phase-1-production-hardening.md](docs/supervisor/release-notes/phase-1-production-hardening.md)

### Phase 2 — 状态持久化与崩溃恢复

- 已完成，详细交付与恢复/重试/优雅关闭结果见 [docs/supervisor/release-notes/phase-2-state-persistence-and-recovery.md](docs/supervisor/release-notes/phase-2-state-persistence-and-recovery.md)

### Phase 3 — 通道集成（双向桥接）

- 已完成，详细交付与通道回传/路由/进度推送结果见 [docs/supervisor/release-notes/phase-3-channel-integration.md](docs/supervisor/release-notes/phase-3-channel-integration.md)

### Phase 4 — Agent Loop 集成

- 已完成，详细交付与远程委派 / 原生 subagent 集成 / 进程内 worker 结果见 [docs/supervisor/release-notes/phase-4-agent-loop-integration.md](docs/supervisor/release-notes/phase-4-agent-loop-integration.md)

---

## 架构全景

最终目标的系统架构：

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         Supervisor Gateway                          │
│                                                                      │
│  ┌───────────┐    ┌──────────────────┐    ┌───────────────────────┐ │
│  │ Channels   │◄──►│  MessageBus       │◄──►│ SupervisorAgentLoop  │ │
│  │ (TG/WA/    │    │  (pub/sub)        │    │ (dispatch决策)       │ │
│  │  DingTalk   │    └──────────────────┘    └─────────┬───────────┘ │
│  │  Slack...)  │                                      │             │
│  └───────────┘                           ┌────────────┴──────────┐  │
│                                          │                       │  │
│                                     ┌────▼────┐          ┌──────▼──┐│
│                                     │ 本地执行 │          │ 远程委派 ││
│                                     │AgentLoop│          │ Workers  ││
│                                     └─────────┘          └────┬─────┘│
│                                                               │      │
│  ┌────────────┐  ┌─────────────┐  ┌──────────────┐     ┌────▼─────┐│
│  │ Registry   │  │ Planner     │  │ Watchdog     │     │ Worker 1 ││
│  │ (持久化)   │  │ (LLM 分解)  │  │ (健康检查)   │     │ Worker 2 ││
│  └────────────┘  └─────────────┘  └──────────────┘     │ Worker N ││
│                                                          └──────────┘│
│  ┌────────────┐  ┌─────────────┐  ┌──────────────┐                  │
│  │ X-Ray 监控 │  │ 分布式会话  │  │ 共享记忆     │                  │
│  └────────────┘  └─────────────┘  └──────────────┘                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Phase 5 — 记忆与上下文共享

**目标**：多个 worker 可以共享上下文与记忆，避免重复工作。

**前置依赖**：Phase 2

### Task 5.1 — 任务上下文传递

**问题**：`Task.context` 字段存在但未充分利用，worker 之间无法共享前序步骤的输出。

**实现**：
- [ ] 计划推进 `_schedule_ready_steps()` 时，自动将已完成步骤的 `result_summary` 注入下游步骤的 `context`
- [ ] Worker 构建系统提示时包含 `task.context`
- [ ] 支持上下文大小限制与摘要压缩
- [ ] 测试：多步骤计划中上下文在步骤间正确传递

**验收标准**：步骤 B 可以看到步骤 A 的执行结果作为上下文。

---

### Task 5.2 — 分布式会话管理

**问题**：`SessionManager` 是进程内内存缓存，无法跨 worker 共享。

**实现**：
- [ ] 定义 `DistributedSessionStore` 接口
- [ ] 实现 SQLite 后端（复用 X-Ray 的 SQLite 基础设施）
- [ ] Supervisor 端维护会话，worker 通过 API 读写
- [ ] 添加 API 端点：`GET/POST /api/v1/supervisor/sessions/{key}`
- [ ] 测试：多 worker 并发读写同一会话

**验收标准**：多个 worker 处理同一用户的不同任务时，可以访问共享的对话历史。

---

### Task 5.3 — Worker 记忆访问

**问题**：Worker 运行时无法访问 MemoryStore（长期记忆）。

**实现**：
- [ ] 方案 A：Supervisor 端代理记忆访问（添加 API 端点）
- [ ] 方案 B：Worker 直接访问共享记忆后端（需要配置同步）
- [ ] Worker `_build_system_prompt()` 中注入相关记忆上下文
- [ ] 测试：Worker 可读取/写入记忆

**验收标准**：Worker 可以利用长期记忆提升任务执行质量。

---

## Phase 6 — 高级调度

**目标**：从朴素 FIFO 升级为智能调度，支持优先级、能力匹配与负载均衡。

**前置依赖**：Phase 2

### Task 6.1 — 优先级队列

**问题**：所有任务等权重，紧急任务可能被低优先级任务阻塞。

**实现**：
- [ ] `Task` 模型添加 `priority: int = 0`（越大越优先）
- [ ] `claim_task()` 改为优先级排序 + FIFO 辅助排序
- [ ] API 创建任务时支持指定优先级
- [ ] 计划步骤继承计划优先级
- [ ] 测试：高优先级任务优先被认领

---

### Task 6.2 — Worker 能力匹配

**问题**：任务分配不考虑 worker 能力，可能把需要 GPU 的任务分给 CPU-only worker。

**实现**：
- [ ] `WorkerInfo.capabilities` 已定义为 `list[str]`
- [ ] `Task` 模型添加 `required_capabilities: list[str] = []`
- [ ] `claim_task()` 匹配 worker 能力：`task.required_capabilities ⊆ worker.capabilities`
- [ ] 无匹配 worker 时任务保持 PENDING
- [ ] 测试：能力匹配与不匹配场景

---

### Task 6.3 — 负载均衡

**问题**：当前 FIFO claim 可能导致快 worker 承担过多任务。

**实现**：
- [ ] `WorkerInfo` 添加 `active_task_count: int`
- [ ] `claim_task()` 优先分配给负载最低的 worker
- [ ] 支持 worker 最大并发任务数限制
- [ ] 测试：多 worker 均匀分配

---

## Phase 7 — 计划智能

**目标**：从线性 DAG 升级为支持条件分支、补偿与动态重规划的智能计划系统。

**前置依赖**：Phase 2

### Task 7.1 — 条件分支

**问题**：DAG 依赖只支持“全部完成才继续”，不支持条件跳转。

**实现**：
- [ ] `PlanStep` 添加 `condition: str | None`（基于前序步骤结果的条件表达式）
- [ ] `_schedule_ready_steps()` 中评估条件：条件不满足时跳过步骤
- [ ] 支持简单表达式：`step[0].result contains "error"` → 跳转到错误处理步骤
- [ ] 测试：条件分支的各种路径

---

### Task 7.2 — 补偿与回滚

**问题**：步骤失败后整个计划直接标记 FAILED，无法回滚已完成步骤的副作用。

**实现**：
- [ ] `PlanStep` 添加 `compensation_instruction: str | None`
- [ ] 计划失败时：按逆序执行已完成步骤的补偿指令
- [ ] 补偿任务也走 worker 执行流程
- [ ] 支持 `on_failure` 策略：`"fail_plan"` | `"compensate"` | `"skip_and_continue"`
- [ ] 测试：失败 → 补偿 → 回滚

---

### Task 7.3 — 动态重规划

**问题**：计划一旦审批就不可变，无法根据中间结果调整后续步骤。

**实现**：
- [ ] 添加 `POST /api/v1/supervisor/plans/{plan_id}/replan` 端点
- [ ] workers 可在步骤完成时建议重规划（result 中携带 `replan_hint`）
- [ ] supervisor 调用 planner 根据已完成步骤的结果重新生成后续步骤
- [ ] 新步骤替换原计划中未开始的步骤
- [ ] 测试：中途重规划 + 步骤替换

---

## Phase 8 — 安全与认证

**目标**：防止未授权 worker 注册、防止任务数据泄露。

**前置依赖**：Phase 1

### Task 8.1 — Worker 认证

**问题**：任何知道 supervisor URL 的进程都可以注册为 worker。

**实现**：
- [ ] Worker 注册时携带 `auth_token`
- [ ] Supervisor 校验 token 有效性（预共享密钥或 JWT）
- [ ] 所有 API 调用携带 `Authorization` header
- [ ] 配置 `supervisor.auth_token` 和 `worker.auth_token`
- [ ] 测试：无效 token → 401

---

### Task 8.2 — 任务数据加密

**问题**：任务指令和结果通过 HTTP 明文传输。

**实现**：
- [ ] 支持 HTTPS（uvicorn SSL 配置）
- [ ] 敏感字段（API key、密码）在 task context 中脱敏
- [ ] 结果存储时可选加密
- [ ] 测试：TLS 连接验证

---

### Task 8.3 — 工作空间隔离

**问题**：Worker 可能访问超出授权范围的文件系统。

**实现**：
- [ ] Worker 级别的工作空间限制（已部分实现 `restrict_to_workspace`）
- [ ] 任务级别的工作空间覆盖
- [ ] 文件系统工具的路径校验加固
- [ ] 测试：越权访问被拒绝

---

## Phase 9 — 可观测性仪表盘

**目标**：提供可视化的 supervisor 管理界面。

**前置依赖**：Phase 1

### Task 9.1 — Supervisor 仪表盘页面

**问题**：X-Ray 仪表盘已有基础设施（HTMX + Jinja2），但无 supervisor 视图。

**实现**：
- [ ] 新增页面：`/supervisor/dashboard` — worker 列表、任务队列、计划状态
- [ ] 新增页面：`/supervisor/plans/{id}` — 计划详情（DAG 可视化）
- [ ] 新增页面：`/supervisor/workers` — worker 详情与历史
- [ ] 复用 X-Ray 的 SSE 实时更新机制
- [ ] Mermaid DAG 渲染计划依赖关系
- [ ] 测试：页面渲染 + SSE 推送

---

### Task 9.2 — 指标导出

**问题**：无结构化指标，无法对接 Prometheus 等监控系统。

**实现**：
- [ ] 添加 `/metrics` 端点（Prometheus 格式）
- [ ] 指标：
  - `supervisor_workers_total{status="online|busy|unhealthy"}`
  - `supervisor_tasks_total{status="pending|running|completed|failed"}`
  - `supervisor_task_duration_seconds` (histogram)
  - `supervisor_plans_total{status="..."}`
- [ ] 测试：指标格式与数值正确性

---

## Phase 10 — 水平扩展与高可用

**目标**：支持多 supervisor 实例，消除单点故障。

**前置依赖**：Phase 2 + Phase 8

### Task 10.1 — 分布式注册表

**问题**：单个 supervisor 进程是单点故障。

**实现**：
- [ ] 实现 `RedisRegistryStore`
- [ ] 分布式锁替换 `asyncio.Lock`（Redis/etcd）
- [ ] 多 supervisor 实例共享同一 Redis
- [ ] leader 选举决定 watchdog 运行实例
- [ ] 测试：多实例并发操作安全性

---

### Task 10.2 — Worker 自动扩缩

**问题**：worker 数量需要手动管理。

**实现**：
- [ ] 添加自动扩缩策略：基于待处理任务队列长度
- [ ] 支持 Docker/K8s 部署时的水平扩展
- [ ] 缩容时优雅关闭 worker（复用 Task 2.3）
- [ ] 测试：扩缩触发条件与行为

---

## 实现优先级矩阵

| Phase | 优先级 | 价值 | 复杂度 | 建议顺序 |
|-------|--------|------|--------|----------|
| **Phase 2 — 持久化** | 🔴 P0 | 生产部署必需 | 高 | **立即开始** |
| **Phase 3 — 通道集成** | 🟡 P1 | 用户可见价值 | 中 | Phase 2 并行评估 |
| **Phase 5 — 记忆共享** | 🟡 P1 | 任务质量提升 | 高 | Phase 2 后 |
| **Phase 6 — 高级调度** | 🟢 P2 | 效率优化 | 中 | Phase 2 后 |
| **Phase 7 — 计划智能** | 🟢 P2 | 能力扩展 | 高 | Phase 3 后 |
| **Phase 8 — 安全认证** | 🟡 P1 | 生产安全 | 中 | Phase 2/3 后 |
| **Phase 9 — 仪表盘** | 🟢 P2 | 运维友好 | 中 | Phase 2 后 |
| **Phase 10 — 水平扩展** | ⚪ P3 | 规模化 | 很高 | Phase 2 + Phase 8 后 |

### 推荐实施路线

```text
Phase 0 ✅ ─┬─► Phase 1 ✅
             │
             └─► Phase 2（持久化） ─┬─► Phase 5（记忆）
                                     ├─► Phase 6（调度）
                                     ├─► Phase 3（通道） ─► Phase 7（计划）
                                     ├─► Phase 8（安全）
                                     └─► Phase 9（仪表盘）

Phase 10（扩展）需要 Phase 2 + Phase 8
```

---

*文档创建于 2026-03-26 | 活跃版整理于 2026-03-28 | 基于 commit `e882a80` (feature/xray-monitoring)*