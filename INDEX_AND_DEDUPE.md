# 分类索引与感知去重

## 分类索引版本

每个 `memes/<category>/index.json` 同时保存两类版本信息：

- `version`：索引文件格式版本。
- `index_version`、`index_prompt_version`、`index_provider_id`：分类结果签名。

只有图片摘要的 `sha256`、`index_version`、`index_prompt_version`、`index_provider_id` 都匹配当前配置，且条目为 `indexed: true` 时，后台扫描才会复用旧结果。旧索引、切换 Gemini Provider，或升级分类提示词后，插件会自动重新分类；图片仍留在原来的分类目录中。

## 感知去重

保存前先做 SHA-256 精确去重，再使用 Pillow 计算带亮度信息的 8x7 平均感知哈希，识别常见的缩放、重新压缩等视觉等价图片。默认汉明距离阈值为 6，阈值越小越严格。相同消息中的多张相似图片也会在调用模型前合并过滤。

配置项：

- `perceptual_dedupe_enabled`：关闭后仍保留 SHA-256 精确去重。
- `perceptual_duplicate_threshold`：默认 `6`，建议 Gemini 用户先保持默认值；误判相似图时可调低到 `3` 或 `4`。

Pillow 无法加载图片或尚未安装时，会安全回退到 SHA-256，不会阻塞插件启动。
