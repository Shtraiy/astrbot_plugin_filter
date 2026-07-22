# 代码复核记录

## 结论

未发现阻塞问题。实现与设计一致：群聊白名单为空时全量允许，图片通过两阶段模型分类，保存到 meme_manager 的 `memes/<category>/`，并在模型调用前后均使用 SHA-256 去重。

## 已检查项目

- AstrBot 新版 `context.llm_generate()` 调用使用独立 Provider ID，并传入本地图片路径。
- 模型返回支持 JSON、Markdown JSON fenced block 和 JSON 前后附带文本。
- 分类结果和 fallback 均限制为安全目录名。
- 图片 URL、本地路径、`data:image/...;base64` 和 `base64://` 均有处理路径。
- 下载大小、超时、并发数、单消息图片数均有限制。
- meme_manager 已有 `memes_data.json` 描述不会被覆盖。
- 临时图片在模型调用结束后清理，插件终止时取消后台任务。
- meme_manager 健康检查覆盖插件注册状态、数据目录读写和 `memes_data.json` 结构；依赖不可用时不会写入孤儿文件。
- `/偷取` 会立即处理同一消息的图片，并标记事件避免自动监听重复处理。
- 发送前高优先级钩子会清理 `&&category&&` 标记，并由本插件独立执行情景判断、概率控制、冷却和分类取图。
- 视觉识别、偷取分类和智能回复分别支持 Provider 配置；回复 Provider 未填写时安全回退到情景分类 Provider。
- 自动发送已明确为三阶段：先 `should_send` 情景判定，再分类，最后由多模态模型从分类候选图中选图。
- Gemini 调用优化：后台索引按批次提交图片；自动回复将情景、分类和选图合并为一次请求，并在请求前执行冷却与概率门控。
- 同一条消息的多图偷取使用一次批量视觉调用和一次批量情景分类调用，批量接口失败时才逐张回退。
- 表情库索引由 meme_manager 健康检查自动触发；分类目录作为权威来源，只在原目录内编号，不具备跨目录移动路径。

## 验证

- `python -m unittest discover -s tests -v`：17 项通过。
- `python -m py_compile main.py collector.py health.py storage.py`：通过。
- `_conf_schema.json`：JSON 解析通过。
- `git diff --check`：通过。

## 未覆盖

当前工作区没有真实 AstrBot 运行时、视觉模型 Provider 或 QQ/Telegram 适配器，因此尚未做真实平台端到端测试。部署后应先在一个测试群配置白名单和低并发参数，再观察日志与 meme_manager WebUI。
