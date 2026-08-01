# Private Companion 联动与用户优先闸门设计

## 目标

解决本插件在同一会话已有请求时直接 `stop_event()` 导致真实用户消息丢失、会话看似卡死的问题；同时识别 `astrbot_plugin_private_companion` 的主动合成请求，让原有 Markdown 清理、内容防护、分段发送和输出安全链路继续生效。

## 约束

- 不修改 `astrbot_plugin_private_companion` 源码，不依赖其私有实现才能工作。
- Private Companion 未安装、未加载或 API 不兼容时，本插件自动降级，不能阻断普通消息。
- 真实平台用户消息永远不能因同源主动请求在途而停止传播。
- 只有明确识别为主动/合成请求的事件才使用本插件的请求闸门。
- 用户消息到达后，旧主动请求不得在其后插队发送。

## 方案

### 事件分类

增加显式主动事件检测：优先识别 Private Companion 设置的 `private_companion_proactive_framework`、`_private_companion_external_proactive_source`、`_private_companion_proactive_chat_token` 等标记，以及 `SyntheticPrivateWake` 类型/元数据。没有这些标记的事件按普通用户输入处理，避免通过文本内容猜测来源。

### 用户优先闸门

闸门状态增加“已被用户输入取代”状态。主动事件占用闸门后，普通用户事件只会标记该状态并继续执行，不会停止事件。主动结果到达装饰阶段时，如果已被用户取代，则清空结果、停止该主动事件并释放闸门。原有 TTL 继续作为异常兜底，避免请求永久占用状态。

### Private Companion 适配

通过可选导入 `get_private_companion_api()` 获取扩展 API。用户输入取代主动请求时，如果能拿到主动会话 token，则异步调用 `cancel_proactive_chat(session_id, token=...)`；调用失败只记录日志，不影响用户消息。没有 token 或 API 时，仍通过本插件的结果失效标记阻止旧输出。

### 保留原功能

`on_llm_request` 的内容防护、`on_decorating_result` 的清理/分段/图片渲染、发送完成后的锁释放全部保留。只把“同源事件一律丢弃”改为“主动事件互斥、用户事件优先”。

## 测试

- 主动事件在途时，真实用户事件不停止传播。
- Private Companion 合成事件仍可占用闸门并阻止重复主动事件。
- 用户事件取代主动事件后，旧主动结果被清空并释放闸门。
- API 缺失、导入失败、取消调用异常时，用户事件仍正常通过。
- 原有冷却、TTL、跨来源隔离和输出处理测试继续通过。
