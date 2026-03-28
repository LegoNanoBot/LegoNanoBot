# Supervisor Gateway 部署说明

本文描述的是当前代码状态下可工作的部署方式，不是假设中的最终架构。

## 结论

如果你希望通过 channel 发起任务，当前推荐的启动方式是：

1. 先启动 supervisor
2. 再启动 worker
3. 最后启动 gateway

其中：

- supervisor 负责任务注册表、调度、watchdog、API 与 worker 协调
- worker 负责真正执行 task
- gateway 才是聊天入口，负责消费 channel 的入站消息，并按路由策略决定是否委派给 supervisor

## 为什么不能只启动 supervisor

当前 supervisor 进程虽然会启动 `ChannelManager`，也具备结果回推能力，但它不会启动 `AgentLoop` 来消费 channel 写入 `MessageBus` 的入站消息。

这意味着：

- channel 发来的消息会进入 bus
- 但没有消费者把这条消息转换成 supervisor task
- 所以“API 创建 task 可以跑通”和“channel 直接发起 task”是两条不同链路

换句话说，supervisor 目前是控制面，不是完整的聊天入口。

## 当前推荐部署

### 模式 A：API / 自动化模式

适用场景：

- 你只需要 REST API 创建 task / plan
- 不需要 chat channel 作为入口

启动方式：

```bash
nanobot supervisor --port 9200 --workers 1
```

或拆分成 supervisor + 独立 worker：

```bash
nanobot supervisor --port 9200
nanobot worker --supervisor http://127.0.0.1:9200 --name worker-1
```

说明：

- 这是目前最稳定、最直接的 supervisor 使用方式
- 你已经验证过这条链路可以通过 API 成功创建并执行天气查询 task

### 模式 B：聊天入口 + supervisor 执行

适用场景：

- 你希望用户从 channel 发消息
- 由 gateway 决定是否把请求委派到 supervisor

启动顺序：

```bash
# Terminal 1: supervisor
nanobot supervisor --config ~/.nanobot-supervisor/config.json --port 9200

# Terminal 2: worker
nanobot worker --config ~/.nanobot-supervisor/config.json --supervisor http://127.0.0.1:9200 --name worker-1

# Terminal 3: gateway
nanobot gateway --config ~/.nanobot-gateway/config.json
```

这套模式下：

- channel 挂在 gateway 上
- gateway 中的 AgentLoop 消费 channel 入站消息
- 当消息命中 `/delegate`、`/plan` 或复杂度路由策略时，由 gateway 调用 supervisor API 创建 task / plan
- worker 执行后，结果由 supervisor 侧完成记录与状态更新

## 配置建议

### 1. gateway 配置

gateway 配置负责：

- 开启 channel
- 开启 supervisor delegation
- 指向 supervisor 的 host / port

示例：

```json
{
  "gateway": {
    "port": 18790
  },
  "supervisor": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 9200
  },
  "channels": {
    "plugins": {
      "web-debug": {
        "enabled": true
      }
    }
  }
}
```

### 2. supervisor / worker 配置

supervisor 配置负责：

- supervisor API 监听地址
- state store
- xray
- worker 执行所需 provider / tools / workspace

示例：

```json
{
  "supervisor": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 9200,
    "dbPath": ".nanobot/supervisor.db"
  },
  "xray": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 9100
  }
}
```

## 关键注意事项

### 1. `worker` 命令参数是 `--supervisor`

当前正确命令是：

```bash
nanobot worker --supervisor http://127.0.0.1:9200 --name worker-1
```

不是：

```bash
nanobot worker --supervisor-url ...
```

### 2. 不要把同一个 inbound channel 同时挂在 supervisor 和 gateway 上

当前代码里，supervisor 和 gateway 都会初始化 `ChannelManager`。如果你在两个进程里都启用同一个 channel，通常会出现以下问题：

- 本地端口冲突
- webhook / websocket 重复连接
- 消息重复消费或行为不确定

对于 `web-debug` 这类本地监听型 channel，尤其不要在 supervisor 和 gateway 两边同时开启。

### 3. `web-debug` 当前推荐只挂在 gateway

如果你的目标是“在 web-debug 聊天窗口里发消息，然后由 supervisor 执行 task”，当前最稳妥方式是：

- `web-debug` 只配置在 gateway
- supervisor 只负责 API、registry、watchdog、worker 编排
- 在聊天窗口里显式使用 `/delegate` 或 `/plan`

### 4. xray 集成在 supervisor 服务里

如果你是通过 `nanobot supervisor` 启动 supervisor，当前 xray 页面通常直接挂在 supervisor 进程里。

可优先访问：

```text
http://127.0.0.1:9200/dashboard
```

而不是假设它一定单独跑在 `9100`。

## 推荐验证步骤

### 验证 1：supervisor 与 worker 正常

```bash
curl -s http://127.0.0.1:9200/api/v1/supervisor/workers
curl -s 'http://127.0.0.1:9200/api/v1/supervisor/tasks?status=completed'
```

### 验证 2：gateway 可作为 channel 入口

用启用了 `web-debug` 的 gateway 配置启动后，打开聊天窗口发送：

```text
/delegate 查询今天天气
```

预期行为：

1. gateway 立即回复“已委派”类提示
2. supervisor 中出现新 task
3. worker 认领并执行该 task

### 验证 3：查看 task 结果

```bash
curl -s "http://127.0.0.1:9200/api/v1/supervisor/tasks/TASK_ID"
```

## 当前限制

当前代码状态下，真正稳定可用的是：

- channel 入口由 gateway 持有
- task / plan 执行由 supervisor + worker 持有

如果你想实现“只启动 supervisor，也能直接从 channel 发起 task”，还需要补一层 supervisor 侧的 inbound message consumer，把 `InboundMessage` 自动转换成 `Task` 或 `Plan`。这部分目前还没有在 standalone supervisor 模式中完成。