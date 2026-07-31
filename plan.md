# 冷静期实现计划

1. 在配置 schema 和 README 中增加 `cooldown_seconds`。
2. 在 `LanguageLogicOptimizer` 中维护按会话的冷静期截止时间。
3. 将冷静期等待接入现有回复锁，并在普通回复和分段回复完成路径统一记录截止时间。
4. 增加配置、同会话等待、跨会话隔离和锁释放测试。
5. 执行 JSON 校验、Python 编译、diff 检查和 mock 运行时验证。
