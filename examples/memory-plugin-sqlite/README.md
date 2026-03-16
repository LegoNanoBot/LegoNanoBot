# SQLite Memory Plugin Example

这个示例展示如何把 nanobot 的 memory 层做成插件，并使用 SQLite 存储长期记忆与历史日志。

## 能力说明

- 通过 `nanobot.memory_factories` entry point 注入 memory backend
- 长期记忆写入 SQLite 的 `long_term_memory` 表
- 历史日志写入 SQLite 的 `history_entries` 表
- 不改 nanobot 核心业务逻辑，仅靠插件切换存储后端

## 目录结构

- `pyproject.toml`：插件包定义与 entry point
- `src/nanobot_plugin_memory_sqlite/memory_factory.py`：SQLite 存储实现与工厂

## 安装（editable）

```bash
cd examples/memory-plugin-sqlite
uv pip install -e .
```

## 配置 nanobot

将以下内容合并到 `~/.nanobot/config.json`：

```json
{
  "memory": {
    "backend": "sqlite_memory",
    "plugins": {
      "sqlite_memory": {
        "dbPath": "memory/memory.sqlite3"
      }
    }
  }
}
```

说明：

- `backend` 可写 `sqlite_memory` 或 `sqlite-memory`，两者都会被规范化
- `dbPath` 支持相对路径（相对 `workspace`）或绝对路径

## 验证

1. 运行：

```bash
nanobot agent -m "记住：我喜欢喝无糖咖啡"
```

2. 再发送几轮消息触发 memory consolidate（通常达到一定历史后会触发）。

3. 查看数据库内容：

```bash
sqlite3 ~/.nanobot/workspace/memory/memory.sqlite3 "select id, substr(content,1,120) from long_term_memory;"
sqlite3 ~/.nanobot/workspace/memory/memory.sqlite3 "select id, created_at, substr(entry,1,120) from history_entries order by id desc limit 5;"
```

## 入口点

本示例使用：

- Group: `nanobot.memory_factories`
- Name: `sqlite-memory`
- Factory: `nanobot_plugin_memory_sqlite.memory_factory:create_memory_store`
