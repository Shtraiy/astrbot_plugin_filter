# 实现计划

1. 编写纯函数测试：白名单、分类规范化、模型 JSON 解析、文件名与重复判定。
2. 实现 `storage.py`：解析 AstrBot 数据路径、下载临时文件、SHA-256 去重、保存到 meme_manager 分类目录。
3. 实现 `collector.py`：图片组件提取、视觉/情景模型调用、分类结果降级。
4. 实现 `main.py`：AstrBot 事件监听、并发限制、配置读取和生命周期清理。
5. 补充 `_conf_schema.json`、`metadata.yaml`、`requirements.txt` 和 README，并运行测试与语法检查。
6. 增加 meme_manager 依赖健康检查、状态命令和定时复查。
7. 增加 `/偷取` 即时入口，补充发送前统一出口、情景决策、概率与冷却控制。
8. 将 SHA-256 重复检查前移到模型调用前，并完成 14 项测试、语法和配置校验。
9. 增加视觉 Provider、偷取分类 Provider 与智能回复 Provider 的独立配置及回退测试。
10. 增加已有库的多模态重分类、跨目录移动、编号、索引文档和发送候选匹配。
