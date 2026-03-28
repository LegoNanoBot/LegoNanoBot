# Supervisor Gateway Release Note — Phase 2 状态持久化与崩溃恢复

> 发布日期：2026-03-28  
> 状态：已完成，主蓝图已切换至 Phase 4 及后续阶段。

## 目标

让 supervisor 在重启、短时故障和维护场景下保持任务与计划状态连续，避免内存态注册表带来的全量丢失风险。

## 交付摘要

- Task 2.1：注册表持久化后端已落地，worker、task、plan 支持 SQLite 持久化与冷启动恢复
- Task 2.2：任务失败重试机制已接入 registry，支持最大重试次数与不同 worker 重新分配
- Task 2.3：worker 优雅关闭链路已完成，收到退出信号后可停止接新任务并等待当前任务收尾

## Task 2.1 — Registry 持久化后端

**已完成**：

- 定义 `RegistryStore` 抽象接口，统一 worker、task、plan 的读写契约
- 实现 `SQLiteRegistryStore`，复用 SQLite 作为 supervisor 持久层
- `WorkerRegistry` 接入 `store` 参数，关键状态变更后同步落盘
- supervisor 启动时支持从 store 恢复 workers、tasks、plans
- 保持内存 dict 作为热路径缓存，持久层仅负责恢复与耐久化

**验证**：

- 重启恢复路径与 store 读写已纳入 supervisor 相关测试
- 并发写入与恢复行为已通过针对性测试覆盖

## Task 2.2 — 任务重试机制

**已完成**：

- `Task` 模型新增 `retry_count` 与 `max_retries`
- `report_result(status=FAILED)` 支持按重试次数回退到 `PENDING`
- 重试分配优先选择不同 worker，降低重复命中故障实例的概率
- 计划步骤状态与任务重试结果保持一致推进
- 达到上限后任务进入最终 `FAILED`

**验证**：

- 覆盖重试成功、重试耗尽、切换 worker 的测试场景
- 与计划推进、watchdog 扫描路径联动验证通过

## Task 2.3 — Worker 优雅关闭

**已完成**：

- `WorkerRunner` 接入 `SIGTERM` / `SIGINT` 处理
- 收到信号后停止 poll 新任务
- 当前任务可在 `--drain-timeout` 时间窗内完成并回报结果
- 退出前执行 worker 注销，避免 registry 残留脏状态
- CLI 已暴露 `--drain-timeout` 参数

**验证**：

- 信号触发、任务 drain、超时退出路径已纳入 worker runner 测试
- shutdown 行为与 supervisor 集成路径联动通过

## 阶段结果

- supervisor 重启后可恢复未完成任务与计划上下文
- 瞬时失败不再直接终结任务，具备有限自动自愈能力
- worker 在发布、重启和运维动作下具备更可控的退出行为

## 后续衔接

- Phase 3 已在此基础上完成通道回传、路由决策与进度推送
- 后续主蓝图从 Phase 4 开始，聚焦 Agent Loop 集成与更高阶调度能力