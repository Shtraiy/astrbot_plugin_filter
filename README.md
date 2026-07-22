# AstrBot 表情包偷取与识别

监听群聊中的图片消息，使用两个模型完成：

1. 视觉模型识别图片内容、情绪、图片文字，并判断是否像表情包。
2. 情景模型结合图片描述、群聊文字和 meme_manager 现有分类选择目录。

## 与 meme_manager 联动

插件通过 AstrBot 数据路径 API 定位：

```text
/AstrBot/data/plugin_data/meme_manager/
├── memes_data.json
└── memes/
    └── <category>/
        └── stolen_<timestamp>_<sha256>.png
```

图片会保存到 `memes/<category>/`，同一图片使用 SHA-256 去重；已有的 `memes_data.json` 分类描述不会被覆盖，新分类只会补充默认描述。安装并启用 `anka-afk/astrbot_plugin_meme_manager` 后，重载 meme_manager 使其刷新目录/提示词。

## 配置

- `group_whitelist`：群号或完整 UMO 白名单。留空表示全部群，建议生产环境填写需要收集的群。
- `vision_provider_id`：自定义视觉模型 Provider ID，必须支持图片输入；留空时使用当前会话 Provider。
- `scene_provider_id`：用于“偷取后分类”的情景模型 Provider ID；留空时使用当前会话 Provider。
- `reply_scene_provider_id`：用于判断机器人回复是否需要表情包、选择分类并比较候选图片的多模态 Provider ID；留空时复用 `scene_provider_id`。
- `only_capture_memes`：开启后跳过视觉模型判定为普通照片的图片。
- `fallback_category`：模型不可用或返回非法分类时的降级目录，默认 `confused`。
- `max_images_per_message`、`max_image_size_mb`、`max_concurrent`：控制资源和模型调用成本。
- `health_check_interval`：依赖插件健康检查间隔，默认 60 秒。
- `auto_send_enabled`：启用后由本插件统一接管自动表情包发送，默认开启。
- `auto_send_probability`：情景模型判定适合发送后，实际发送概率，默认 35%。
- `auto_send_cooldown`：同一会话自动发送的最短间隔，默认 30 秒。
- `auto_send_candidate_limit`：每次发送前交给多模态模型比较的候选图片数，默认 8。
- `library_index_provider_id`：后台自动整理已有表情包库使用的多模态 Provider；为空时复用 `vision_provider_id`。后台任务需要明确的 Provider ID。
- `library_index_enabled`：meme_manager 正常运行后是否自动补齐本地表情包索引。
- `library_index_progress_step`：后台索引每处理多少张图片写入一次进度日志。
- `library_index_batch_size`：后台索引一次提交给多模态模型的图片数量，默认 6；Gemini 建议设置为 4～8。
- `library_index_rename_files`：是否将图片重命名为 `happy_0001.png` 格式。

## meme_manager 依赖检查

插件启动时、每次收到图片前以及定时任务中都会检查 `meme_manager`：

- 是否出现在 AstrBot 已加载插件注册表中；
- `data/plugin_data/meme_manager/memes/` 是否存在且可读写；
- `memes_data.json` 是否能正常解析。

检查失败时不会保存图片，避免产生 meme_manager 无法读取的“孤儿文件”。可发送 `/表情偷取状态` 查看当前状态。meme_manager 被安装、启用或重载后，插件会在下一次检查时自动恢复收集。

## 统一发送逻辑

插件在 AstrBot 的发送前钩子中以高优先级运行：先清理 meme_manager 的 `&&happy&&`、`&&shy&&` 等内联标记阻止其发送；随后由 `reply_scene_provider_id` 指定的情景模型判断 `should_send`。只有需要发送时，才根据模型返回的分类进入对应目录，再把该目录中的候选图片和图片索引交给多模态模型，选出最符合当前回复的一张并发送。

模型入口不是在本插件中填写 API Key 或模型名称，而是在 AstrBot 的 Provider 管理中配置支持的模型，再把对应 Provider ID 填入以上配置。`vision_provider_id`、`reply_scene_provider_id` 和 `library_index_provider_id` 都必须支持图片输入；`scene_provider_id` 在只用于偷取分类时可以是文本模型。

因此 meme_manager 的自动发送设置不会再决定最终是否发图；它仍负责 WebUI、分类管理、云同步和文件维护。本插件只接管自动发送出口。发送模型不可用、没有合适分类或分类目录没有图片时，会保持不发送。

## 手动偷取

将图片和命令放在同一条消息中发送：

```text
/偷取 [图片]
```

命令会立即执行视觉识别、重复检查、情景分类和保存；如果没有附带图片，会提示重新发送。手动命令仍遵守群聊和白名单限制。

同一条消息包含多张新图片时，插件会先一次性调用视觉模型批量识别，再一次性调用情景模型批量分类，随后逐张保存结果。单张图片仍使用单图提示词；批量 Provider 不支持多图时会自动回退到逐张调用。

## 自动整理已有表情包库

meme_manager 健康检查通过后，插件会按照现有 `memes/<category>/` 路径后台扫描图片。目录名是权威分类，模型只负责补充图片描述、情绪、文字和标签，不会把图片移动到其他分类目录。每个目录会生成：

```text
index.json   # 机器读取的图片索引
README.md    # 人类可读的图片管理表
```

图片默认会在原目录内重命名为 `shy_0001.png`、`happy_0002.jpg` 等稳定编号。后台会按 `library_index_batch_size` 批量调用多模态模型，并把进度写入 AstrBot 日志。整理失败的图片不会删除，只会在索引中标记为“待重新识别”；后续健康检查会再次尝试。

自动回复表情现在将“是否发送、选择分类、候选图片匹配”合并为一次多模态模型调用；`auto_send_probability` 和 `auto_send_cooldown` 会在调用前生效，用于避免不必要的请求。

插件支持的默认分类包括 `angry`、`happy`、`sad`、`surprised`、`confused`、`color`、`cpu`、`fool`、`givemoney`、`like`、`see`、`shy`、`work`、`reply`、`meow`、`baka`、`morning`、`sleep`、`sigh`。如果 meme_manager 已有自定义分类，插件会优先读取本地目录和 `memes_data.json`。

## 注意

图片会被上传给配置的视觉/情景模型，请确认群成员已知悉并遵守平台、隐私和内容管理要求。插件不会把图片复制进自身目录，卸载插件不会删除已收集的 meme_manager 数据。
