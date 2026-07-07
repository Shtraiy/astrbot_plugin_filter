# astrbot_plugin_filter — 语言逻辑优化大师

🚿 自动清洗元数据符号 + 📝 工具名脱敏 + ✂️ 智能分段 + 🧹 过程叙述过滤

## 📋 这是什么？

当 AstrBot 接入 OneBot 协议向 QQ 输出消息时，LLM 或 MCP 工具调用会产生两个层面的问题：

1. **元数据垃圾**：`[{text=`、`, type=text}` 等结构符号泄漏到用户可见文本
2. **语言质量问题**：bot 叙述内部操作过程、暴露工具函数名、长文本不分段

本插件在消息发送前的最后一步（`on_decorating_result` 钩子）执行四道工序，一次性解决。

## 🔧 四道工序

```
LLM 生成回复 → MCP 工具返回结果 → OneBot 序列化
                                        ↓
                                ① 垃圾符号清洗
                                ② 过程叙述句删除
                                ③ 工具名脱敏替换
                                ④ 智能分段
                                        ↓
                                用户收到干净文本 ✅
```

### ① 垃圾符号清洗

移除 OneBot/MCP 泄漏的元数据符号：`[{text=` `, type=text}` `}]` `[{` `}]`

### ② 过程叙述删除

自动识别并删除 bot 的内部操作叙述句。判定标准：句子中**同时出现**工具函数名 + 过程叙述关键词（如 "我先用" "让我" "执行操作" 等）。

**删除前**：
> 我先用 es_search 查看工作区有什么，以及调用 shell 执行相应的操作，或者检查下后台服务。我最好可以用 rg_search 搜一下项目中是否有相关 API 接口。找到了订阅记录。

**删除后**：
> 找到了订阅记录。

### ③ 工具名脱敏

将残存的工具函数名替换为自然语言：

| 工具名 | 替换为 |
|--------|--------|
| `es_search` `rg_search` | 检索 / 文件检索 |
| `web_search` `WebFetch` | 搜索 / 网页 |
| `mikan_search` `bangumi_search` | 番剧源 / 番剧信息 |
| `ani-rss` `add_subscription` | 订阅系统 / 添加订阅 |
| `shell` `exec` `bash` | 终端 / 执行 |
| `read_file` `write_file` | 读取文件 / 写入文件 |
| ... 等 40+ 条映射 | |

### ④ 智能分段

长文本（>150 字）自动拆分为 2~3 个自然段落，超过 500 字自动截断。

## 📦 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot 管理面板 → 插件市场中搜索 **语言逻辑优化大师**，点击安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
```

安装后重启 AstrBot 即可生效。

## ⚙️ 兼容性

| 组件 | 版本要求 |
|------|----------|
| AstrBot | `>= 4.16, < 5` |
| 适配协议 | OneBot v11 / v12 |
| Python | `>= 3.10` |

## 📁 项目结构

```
astrbot_plugin_filter/
├── main.py          # 插件主逻辑（四道工序）
├── metadata.yaml    # 插件元数据
├── README.md        # 本文件
├── LICENSE          # 开源协议
└── .gitignore
```

## 🐛 常见问题

### Q: 安装后没有生效？

- 确认 AstrBot 版本 >= 4.16
- 重启 AstrBot
- 在 AstrBot 日志中搜索 `[语言逻辑优化大师]` 确认插件已加载

### Q: 会不会误删正常消息？

不会。垃圾符号清洗只匹配非常特定的元数据模式。过程叙述删除需要**工具名 + 过程关键词同时出现**才触发，正常对话不会同时满足这两个条件。

### Q: 如果有新的工具名需要脱敏怎么办？

编辑 `main.py` 中 `_TOOL_ALIASES` 列表，按格式添加即可。也欢迎提交 Issue 或 PR。

## 📄 开源协议

MIT License — 详见 [LICENSE](./LICENSE)

## 👤 作者

- **Shtraiy** — [GitHub](https://github.com/Shtraiy)
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)
