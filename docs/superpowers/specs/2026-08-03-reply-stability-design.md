# Reply Stability Design

## Goal

修复回复生命周期中的三个实际风险：用户消息无法可靠停止已经排队的 follow-up、follow-up 绕过完整的出站安全流水线、同一回复为多个段落重复创建发送任务；同时删除 `main.py` 中已经被后续兼容包装覆盖的旧状态机实现。

## Scope and constraints

- 保留现有文本清理函数、敏感信息规则、Markdown 处理、分段算法和配置语义。
- 不新增第三方运行时依赖。
- 不改变 `multi_message`、`delay_min`、`delay_max`、`gate_seconds`、`gate_ttl_seconds` 的含义。
- Private Companion 仍是可选集成；取消失败只能导致本地失效标记，不能阻塞普通用户回复。
- 所有行为修复先以失败回归测试锁定，再修改生产代码。

## Design

### 1. Session cancellation is state-derived

`ReplySession` 当前在获取锁时复制 `cancel_requested` 和 `superseded_by_user`，之后不会随 `GateState` 更新。新增协调器查询接口，根据 session 的 owner event 与当前 gate 状态实时判断 session 是否已经被用户消息取代或请求取消。

`MessageDispatcher` 在等待延迟前、等待延迟后、处理段落前都查询该状态。这样用户新消息到达后，未发送的后续段落会停止；取消路径仍由现有 `finally` 负责释放锁和 gate。

### 2. Follow-up reuses the outbound pipeline

入口创建一个可复用的 `OutboundTextPipeline`，首段和后续段落都通过同一个异步处理回调。后续段落若被内容防护拦截，发送安全回复；若经过清理后为空，则跳过该段。统计信息继续汇总到主回复日志，不改变分段顺序。

### 3. One follow-up task owns one reply session

入口先收集本次结果中所有需要拆分的后续段落，再统一创建一个 dispatcher 任务。首段仍留在当前 `MessageChain` 中，后续段落按原顺序发送。dispatcher 只在一个任务的 `finally` 中释放 session，避免多个任务竞争同一把锁。

### 4. Remove dead duplicate lifecycle code

删除 `main.py` 中已被后面的 coordinator compatibility shims 覆盖的旧 gate、取消和 reply-lock 实现，以及不再需要的导入。保留兼容包装本身，避免旧测试或外部集成直接调用这些私有辅助方法时行为改变。

## Error handling

- follow-up 单段发送失败：记录警告并继续尝试后续段落，最终释放 session。
- follow-up 处理失败：记录异常，跳过该段，不让后台任务产生未回收异常。
- Private Companion 不可用或取消失败：继续本地失效流程。
- 入口处理失败：沿用现有安全回复替换和 `stop_event` 兜底。

## Verification

新增/调整测试覆盖：

1. 用户消息取代主动回复后，已经排队但尚未发送的 follow-up 不再发送。
2. 后续段落经过敏感信息/工具泄露/Markdown/内容防护处理。
3. 多个 Plain 组件各自可分段时只创建一个 follow-up 任务，且锁在全部发送结束后才释放。
4. 取消、发送异常、处理异常后 session/gate 都能释放。
5. 完整 `pytest`、`py_compile` 和 `git diff --check` 通过。
