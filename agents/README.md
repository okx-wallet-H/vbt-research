# Agent Teams 记录规范

> 每个 Agent 一个专属文件夹，自己记录工作，不用都经蜂王中转。

## 文件夹对应

| 文件夹 | Agent | 岗位 |
|---|---|---|
| `watcher/` | watcher | 实盘盯盘监控 |
| `pattern-finder/` | pattern-finder | 规律研究 |
| `optimizer/` | optimizer | 参数优化 |
| `api-researcher/` | api-researcher | 官方 API 调研 |
| `orderbook-researcher/` | orderbook-researcher | 订单簿/挂单墙 |
| `orderbook-event/` | orderbook-event | 事件研究 |
| `live_scan/` | live_scan.py 脚本 | 实盘执行 |

## 每个 Agent 要记录什么

1. **工作日志**：每一轮干了啥（`<name>/日志.md`）
2. **成果/结论**：研究报告、分析结果（`<name>/` 下）
3. **异常**：发现的异常、踩的坑（`<name>/异常.md`）

## 通信规则

- **重要的事**（异常、重大结论）→ SendMessage 报蜂王
- **日常记录** → 自己写进自己的文件夹，不用报
- 队友之间可直接互发消息，不用都经蜂王中转

## 同步

- 本地 `agents/` 会同步到 GitHub `okx-wallet-H/vbt-research` 仓库
- 蜂王定期 push，或 Agent 记录完提醒蜂王同步
