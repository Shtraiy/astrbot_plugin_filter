# 冷静期代码审查

## 结果

- 配置默认为 `0`，不会改变现有行为。
- `on_llm_request` 占用全局闸门，后续唤醒通过 `stop_event()` 丢弃。
- 单条回复在 `after_message_sent` 后开始冷静期，多段回复在最后一条后续消息发送完成后开始。
- 普通回复、异常路径和后续消息任务都统一释放锁并记录状态。
- 非法、负数、NaN 和无穷配置会关闭冷静期。

## 验证

- `_conf_schema.json` 解析通过。
- `py_compile` 通过。
- `git diff --check` 通过。
- 使用 AstrBot API mock 的全局闸门运行时检查通过。
- 完整 pytest 未运行：当前环境未安装 pytest，且未提供 AstrBot 运行时依赖。
