# Supervisor Gateway — 递进式实现蓝图

> **当前状态**：Phase 0（MVP 验证）已完成；Phase 1 / Task 1.1 已落地并通过单测验证（2026-03-27）。  
> **设计原则**：每个 Phase 可独立交付、独立测试、独立回滚。后续 Phase 依赖前置 Phase 但不破坏已有功能。

---

## 目录

- [架构全景](#架构全景)
- [Phase 0 — MVP 验证（✅ 已完成）](#phase-0--mvp-验证-已完成)
- [Phase 1 — 生产加固](#phase-1--生产加固)
- [Phase 2 — 状态持久化与崩溃恢复](#phase-2--状态持久化与崩溃恢复)
- [Phase 3 — 通道集成（双向桥接）](#phase-3--通道集成双向桥接)
- [Phase 4 — Agent Loop 集成](#phase-4--agent-loop-集成)
- [Phase 5 — 记忆与上下文共享](#phase-5--记忆与上下文共享)
- [Phase 6 — 高级调度](#phase-6--高级调度)
- [Phase 7 — 计划智能](#phase-7--计划智能)
- [Phase 8 — 安全与认证](#phase-8--安全与认证)
- [Phase 9 — 可观测性仪表盘](#phase-9--可观测性仪表盘)
- [Phase 10 — 水平扩展与高可用](#phase-10--水平扩展与高可用)
- [实现优先级矩阵](#实现优先级矩阵)
- [附录：当前代码清单](#附录当前代码清单)

---

## 架构全景

最终目标的系统架构：

```
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

## Phase 0 — MVP 验证（✅ 已完成）

**目标**：最小可行产品，验证核心架构可跑通。

**已交付内容**：

| 模块 | 文件 | 行数 | 状态 |
|------|------|------|------|
| 数据模型 | `nanobot/supervisor/models.py` | ~192 | ✅ |
| 注册表 | `nanobot/supervisor/registry.py` | ~350 | ✅ |
| 计划器 | `nanobot/supervisor/planner.py` | ~130 | ✅ |
| 看门狗 | `nanobot/supervisor/watchdog.py` | ~70 | ✅ |
| FastAPI 应用 | `nanobot/supervisor/app.py` | ~90 | ✅ |
| API: Workers | `nanobot/supervisor/api/workers.py` | ~115 | ✅ |
| API: Tasks | `nanobot/supervisor/api/tasks.py` | ~200 | ✅ |
| API: Plans | `nanobot/supervisor/api/plans.py` | ~130 | ✅ |
| Worker 客户端 | `nanobot/worker/client.py` | ~130 | ✅ |
| Worker 运行器 | `nanobot/worker/runner.py` | ~350 | ✅ |
| CLI 命令 | `nanobot/cli/commands.py` | +135 | ✅ |
| X-Ray 事件类型 | `nanobot/xray/events.py` | +14 常量 | ✅ |

**测试**：58 单元测试 + 13 集成测试 = 71 新测试，全套 441 通过。

**设计决策**：
- 内存态注册表（dict + asyncio.Lock）
- FIFO 任务队列
- Worker 主动 poll 模式
- DAG 依赖跟踪
- 任务失败 → 整个计划失败
- httpx.ASGITransport 进程内集成测试

**已知限制**（后续 Phase 解决）：
- ❌ 任务超时字段定义但未强制执行
- ❌ 注册表纯内存，重启即丢
- ❌ 无通道回传（任务结果不回用户）
- ❌ 无认证机制
- ❌ planner 无单元测试
- ❌ 无配置 schema（全靠 CLI 参数）
- ❌ Worker 无重连/断线恢复

---

## Phase 1 — 生产加固

**目标**：让 MVP 在真实环境中可靠运行，填补监控盲区与安全边界。

**前置依赖**：Phase 0 ✅

### Task 1.1 — X-Ray 事件发射

**问题**：14 个事件类型已定义在 `xray/events.py`，但 registry/watchdog 中无发射代码，监控系统对 supervisor 完全失明。

**实现**：
- [x] `registry.py` 接受可选 `collector: XRayCollector` 参数
- [x] 在以下位置发射事件：
  - `register_worker()` → `WORKER_REGISTERED`
  - `heartbeat()` → `WORKER_HEARTBEAT`
  - `scan_unhealthy_workers()` → `WORKER_UNHEALTHY`
  - `evict_worker()` → `WORKER_EVICTED`
  - `create_task()` → `TASK_CREATED`
  - `claim_task()` → `TASK_ASSIGNED`
  - `report_progress()` → `TASK_PROGRESS`
  - `report_result(success)` → `TASK_COMPLETED`
  - `report_result(failure)` → `TASK_FAILED`
  - `cancel_task()` → `TASK_CANCELLED`
  - `create_plan()` → `PLAN_CREATED`
  - `approve_plan()` → `PLAN_APPROVED`
  - `_advance_plan(completed)` → `PLAN_COMPLETED`
  - `_advance_plan(failed)` → `PLAN_FAILED`
- [x] `watchdog.py` 中发射 `WORKER_EVICTED` 事件
- [x] 测试：验证事件发射数量与 payload 正确性

**验收标准**：启动 supervisor + X-Ray 后，仪表盘可实时显示 worker 注册、任务流转、计划推进。

#### Task 1.1 进度说明（2026-03-27）

**本次已完成**：
- supervisor 状态机关键路径已接入事件发射（worker/task/plan/eviction 全链路）
- 引入 `EventSink` 抽象层，隔离 supervisor 领域逻辑与 X-Ray 具体实现
- `claim_task()` 从“全量排序”优化为“线性最早选择”，降低热路径开销
- 任务结果事件 payload 收敛为 `result_preview + result_len`，避免大文本直接上报
- 测试补齐并通过：`PYTHONPATH=. pytest -q tests/test_supervisor_registry.py tests/test_xray_events.py`（29 passed）

**遗漏工作（待补）**：
- 环境缺少 `fastapi`，当前未完成 supervisor 相关集成测试收敛（需补跑 `tests/test_supervisor_integration.py`）
- 事件发射超时阈值当前为代码内默认值（`0.05s`），尚未配置化
- 任务调度仍是遍历式选择，尚未升级为 Phase 6 规划中的优先级/能力匹配索引结构

---

### Task 1.2 — 任务超时强制执行

**问题**：`Task.timeout_s` 和 `Task.max_iterations` 已定义但未强制。Worker 挂死时任务永不结束。

**实现**：
- [ ] `WorkerRunner._execute_task()` 中添加 `asyncio.wait_for(timeout=task.timeout_s)` 包装
- [ ] 超时触发：报告 FAILED + 错误信息 "task timed out after {n}s"
- [ ] `registry.py` 添加 `scan_stale_tasks()` 方法：扫描 `RUNNING` 超过 `timeout_s` 的任务，强制标记 FAILED
- [ ] Watchdog 扩展：除了检查 worker 心跳，也检查 stale tasks
- [ ] 测试：模拟长时间 LLM 调用，验证超时触发与任务状态变更

**验收标准**：任务执行超过 `timeout_s` 后自动标记失败并释放 worker。

---

### Task 1.3 — Planner 单元测试

**问题**：`planner.py` 完全未测试，LLM 返回格式不可控时可能静默失败。

**实现**：
- [ ] 使用 MockProvider 测试以下场景：
  - 简单请求 → planner 返回 None（走单任务路径）
  - 复杂请求 → planner 返回多步骤 Plan
  - LLM 返回无效 JSON → 优雅降级
  - LLM 返回 markdown 代码栅栏包裹的 JSON → 正确解析
  - 空步骤列表 → 返回 None
- [ ] 测试步骤间依赖关系的正确性

**验收标准**：planner 所有正常/异常路径有测试覆盖。

---

### Task 1.4 — 配置 Schema

**问题**：supervisor 的所有参数（port、heartbeat_timeout 等）通过 CLI 硬编码，无法通过配置文件管理。

**实现**：
- [ ] 在 `config/schema.py` 添加 `SupervisorConfig` 模型：
  ```python
  class SupervisorConfig(BaseModel):
      enabled: bool = False
      host: str = "127.0.0.1"
      port: int = 9200
      heartbeat_timeout_s: float = 120.0
      watchdog_interval_s: float = 30.0
      task_default_timeout_s: float = 600.0
      task_default_max_iterations: int = 30
  ```
- [ ] 在 `Config` 顶层模型中添加 `supervisor: SupervisorConfig`
- [ ] CLI 命令中配置文件值作为默认值，CLI 参数覆盖
- [ ] 测试：配置加载与 CLI 覆盖的优先级

**验收标准**：supervisor 可通过 `config.json` 配置，CLI 参数可覆盖。

---

### Task 1.5 — Worker 断线恢复

**问题**：Worker 网络中断后无重连逻辑，直接崩溃退出。

**实现**：
- [ ] `SupervisorClient` 中所有 HTTP 调用添加重试包装（指数退避，最多 5 次）
- [ ] `WorkerRunner._heartbeat_loop()` 中心跳失败不应崩溃整个 worker
- [ ] `WorkerRunner._poll_loop()` 中 claim 失败不应退出循环
- [ ] 注册失败时等待后重试而非立即退出
- [ ] 测试：模拟网络中断 → 重连 → 恢复正常工作

**验收标准**：supervisor 短暂不可用时，worker 自动重连并恢复任务执行。

---

## Phase 2 — 状态持久化与崩溃恢复

**目标**：supervisor 重启后不丢失状态，支持紧急维护与版本升级。

**前置依赖**：Phase 1

### Task 2.1 — Registry 持久化后端

**问题**：当前注册表纯内存，supervisor 进程重启 = 所有状态丢失。

**实现**：
- [ ] 定义 `RegistryStore` 抽象接口：
  ```python
  class RegistryStore(ABC):
      async def save_worker(self, worker: WorkerInfo) -> None
      async def load_workers(self) -> list[WorkerInfo]
      async def save_task(self, task: Task) -> None
      async def load_tasks(self) -> list[Task]
      async def save_plan(self, plan: Plan) -> None
      async def load_plans(self) -> list[Plan]
      async def delete_worker(self, worker_id: str) -> None
      # ...
  ```
- [ ] 实现 `SQLiteRegistryStore`（复用 X-Ray 的 SQLite 模式）
- [ ] `WorkerRegistry` 接受 `store: RegistryStore` 参数
- [ ] 启动时从 store 加载状态，关键操作后写入 store
- [ ] 保持内存态 dict 作为热缓存，store 作为持久层
- [ ] 测试：重启后状态恢复、并发写入安全

**验收标准**：supervisor 重启后，未完成的任务和计划自动恢复。

---

### Task 2.2 — 任务重试机制

**问题**：任务失败后直接标记 FAILED，无重试机会。

**实现**：
- [ ] `Task` 模型添加 `retry_count: int = 0` 和 `max_retries: int = 0`
- [ ] `report_result(status=FAILED)` 时检查 `retry_count < max_retries`：
  - 是 → 状态回 PENDING + `retry_count += 1`
  - 否 → 最终 FAILED
- [ ] 重试时自动选择不同 worker（避免重复故障）
- [ ] 计划步骤级别的重试策略
- [ ] 测试：重试次数耗尽、重试成功、重试分配到不同 worker

**验收标准**：瞬时故障（LLM 超时、网络抖动）可自动重试。

---

### Task 2.3 — Worker 优雅关闭

**问题**：Worker 收到 SIGTERM 时应完成当前任务再退出，而非直接中断。

**实现**：
- [ ] `WorkerRunner` 注册信号处理：`SIGTERM` / `SIGINT`
- [ ] 收到信号后：
  1. 停止轮询新任务
  2. 等待当前任务完成（或超时后强制中断）
  3. 注销 worker
  4. 退出
- [ ] 添加 `--drain-timeout` CLI 参数，控制优雅关闭等待时间
- [ ] 测试：信号处理 + 任务完成后退出

**验收标准**：`kill <worker_pid>` 时 worker 完成当前任务后干净退出。

---

## Phase 3 — 通道集成（双向桥接）

**目标**：用户通过聊天通道发起的请求可以被路由到 supervisor 处理，结果自动回传给用户。

**前置依赖**：Phase 1

### Task 3.1 — 结果回传通道

**问题**：任务模型已有 `origin_channel`、`origin_chat_id`，但结果无法回传。

**实现**：
- [ ] 新增 `SupervisorResultReporter` 类：
  - 监听任务完成事件
  - 构造 `OutboundMessage(channel=origin_channel, chat_id=origin_chat_id, content=result)`
  - 通过 `MessageBus.publish_outbound()` 发送
- [ ] 在 `supervisor` CLI 命令中初始化 ChannelManager（复用 gateway 的通道初始化逻辑）
- [ ] 结果格式化：支持 markdown、代码块、长文本分段
- [ ] 测试：任务完成 → 消息回传到正确通道

**验收标准**：Worker 完成任务后，结果自动出现在用户的聊天窗口中。

---

### Task 3.2 — 请求路由决策

**问题**：目前所有聊天消息都走 AgentLoop 本地处理，无法路由到 supervisor。

**实现**：
- [ ] 定义路由策略接口：
  ```python
  class RoutingStrategy(ABC):
      async def should_delegate(self, message: InboundMessage) -> bool
      async def create_task(self, message: InboundMessage) -> Task
  ```
- [ ] 实现 `KeywordRoutingStrategy`：通过关键词（如 `/delegate`、`/plan`）触发
- [ ] 实现 `ComplexityRoutingStrategy`：通过 LLM 评估消息复杂度决定是否委派
- [ ] 在 `AgentLoop._dispatch()` 中注入路由决策点
- [ ] 测试：路由策略匹配与降级

**验收标准**：用户发送 `/plan 重构认证模块` → supervisor 自动创建计划 → worker 分步执行 → 结果回传。

---

### Task 3.3 — 进度实时推送

**问题**：长时间运行的计划（多步骤、多 worker）用户看不到进度。

**实现**：
- [ ] Worker 每次迭代 `report_progress()` 时，supervisor 向原始通道推送进度消息
- [ ] 进度消息格式：`"⏳ 步骤 2/5: 正在分析代码结构... (Worker: alpha)"`
- [ ] 支持通道侧的进度合并/覆盖（避免消息洪水）
- [ ] 计划级别进度：`"📋 计划进度: 3/5 步骤完成"`
- [ ] 测试：多步骤计划的进度推送时序

**验收标准**：用户在聊天中能实时看到多步骤任务的执行进度。

---

## Phase 4 — Agent Loop 集成

**目标**：将 supervisor 委派能力无缝集成到现有 Agent Loop，让 agent 可以"自然地"将子任务委派给 worker pool。

**前置依赖**：Phase 3

### Task 4.1 — DelegateToWorkerTool

**问题**：当前 `SpawnTool` 在本地创建 subagent，无法利用 worker pool。

**实现**：
- [ ] 新建 `DelegateToWorkerTool`：
  ```python
  class DelegateToWorkerTool(BaseTool):
      name = "delegate_to_worker"
      description = "Delegate a subtask to a remote worker in the supervisor pool"
      async def execute(self, instruction: str, label: str = "") -> str:
          # 1. 通过 SupervisorClient 创建任务
          # 2. 轮询等待完成（或返回 task_id 供后续查询）
          # 3. 返回结果
  ```
- [ ] AgentLoop 启动时检测 supervisor 是否可用，动态注册 delegate tool
- [ ] 支持同步等待（阻塞直到结果）和异步模式（创建后继续）
- [ ] 测试：tool 调用 → 任务创建 → worker 执行 → 结果返回

**验收标准**：LLM 可在对话中自主决定是否将子任务委派给 worker pool。

---

### Task 4.2 — SubagentManager 远程委派

**问题**：`SubagentManager.spawn()` 只支持本地执行。

**实现**：
- [ ] 添加 `mode` 参数：`"local"` | `"remote"` | `"auto"`
- [ ] `remote` 模式：
  - 序列化 task + context
  - 通过 SupervisorClient 提交任务
  - 返回 task_id
  - 后台轮询结果并回调
- [ ] `auto` 模式：supervisor 可用时远程，否则本地降级
- [ ] 保持 API 向后兼容
- [ ] 测试：远程 spawn + 结果回调

**验收标准**：`/spawn` 命令可以透明地委派到远程 worker。

---

### Task 4.3 — 进程内 Worker 模式

**问题**：开发/测试时需要启动多个进程太重，需要轻量级的进程内 worker。

**实现**：
- [ ] `InProcessWorker` 类：在 supervisor 进程内运行 worker loop
- [ ] 通过 ASGITransport 直连 FastAPI app（无 HTTP 开销）
- [ ] `supervisor` CLI 添加 `--workers N` 参数：自动启动 N 个进程内 worker
- [ ] 测试：进程内 worker 端到端测试

**验收标准**：`nanobot supervisor --workers 2` 启动 supervisor + 2 个内嵌 worker。

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

**问题**：DAG 依赖只支持"全部完成才继续"，不支持条件跳转。

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
| **Phase 1 — 生产加固** | 🔴 P0 | 可靠运行的基础 | 中 | **立即开始** |
| **Phase 2 — 持久化** | 🔴 P0 | 生产部署必需 | 高 | Phase 1 后 |
| **Phase 3 — 通道集成** | 🟡 P1 | 用户可见价值 | 中 | Phase 1 后 |
| **Phase 4 — Loop 集成** | 🟡 P1 | 无缝体验 | 中 | Phase 3 后 |
| **Phase 5 — 记忆共享** | 🟡 P1 | 任务质量提升 | 高 | Phase 2 后 |
| **Phase 6 — 高级调度** | 🟢 P2 | 效率优化 | 中 | Phase 2 后 |
| **Phase 7 — 计划智能** | 🟢 P2 | 能力扩展 | 高 | Phase 3 后 |
| **Phase 8 — 安全认证** | 🟡 P1 | 生产安全 | 中 | Phase 1 后 |
| **Phase 9 — 仪表盘** | 🟢 P2 | 运维友好 | 中 | Phase 1 后 |
| **Phase 10 — 水平扩展** | ⚪ P3 | 规模化 | 很高 | Phase 2+8 后 |

### 推荐实施路线

```
Phase 0 ✅ ─┬─► Phase 1（加固） ─┬─► Phase 2（持久化） ─► Phase 5（记忆）
             │                    │                        ─► Phase 6（调度）
             │                    ├─► Phase 3（通道） ─► Phase 4（Loop） ─► Phase 7（计划）
             │                    ├─► Phase 8（安全）
             │                    └─► Phase 9（仪表盘）
             │
             └─► Phase 10（扩展）需要 Phase 2 + Phase 8
```

---

## 附录：当前代码清单

### Supervisor 模块（~1,280 行）

```
nanobot/supervisor/
├── __init__.py                  # 包入口
├── app.py                       # FastAPI 应用工厂（~90 行）
├── event_sink.py                # 事件抽象层与 X-Ray 适配器（~54 行）
├── models.py                    # 域模型：Worker/Task/Plan/协议消息（~192 行）
├── registry.py                  # 注册表：状态管理 + 业务逻辑（~500 行）
├── planner.py                   # LLM 驱动计划生成（~130 行）
├── watchdog.py                  # 心跳监控 + Worker 驱逐（~80 行）
└── api/
    ├── __init__.py
    ├── workers.py               # Worker CRUD 端点（~115 行，5 endpoints）
    ├── tasks.py                 # 任务生命周期端点（~200 行，7 endpoints）
    └── plans.py                 # 计划管理端点（~130 行，5 endpoints）
```

### Worker 模块（~480 行）

```
nanobot/worker/
├── __init__.py                  # 包入口
├── client.py                    # HTTP 客户端（~130 行）
└── runner.py                    # 主事件循环 + LLM 执行（~350 行）
```

### 测试（~1,010+ 行）

```
tests/
├── test_supervisor_models.py        # 数据模型验证（~80 行）
├── test_supervisor_registry.py      # 注册表业务逻辑（~180 行）
├── test_supervisor_api.py           # API 端点（~200 行）
├── test_supervisor_integration.py   # 端到端集成（~550 行，13 tests）
└── test_worker_client.py            # Worker 客户端（~126 行）
```

### X-Ray 集成

```
nanobot/xray/events.py          # 14 个 supervisor 事件类型常量（已定义并已接入发射）
```

### CLI

```
nanobot/cli/commands.py          # supervisor 命令（~85 行）+ worker 命令（~50 行）
```

---

*文档创建于 2026-03-26 | 基于 commit `e882a80` (feature/xray-monitoring)*
