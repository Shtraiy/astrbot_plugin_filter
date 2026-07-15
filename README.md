# astrbot_plugin_filter — 语言逻辑优化大师

🚿 元数据清洗 · 📝 工具名脱敏 · ✂️ LLM 文风优化 · 💬 多消息逐段发送 · 🛡️ 防 OOC · 🖼️ 图片渲染

## 📋 这是什么？

当 AstrBot 接入 OneBot 协议向 QQ/微信输出消息时，LLM 回复常见问题：

1. **元数据泄漏**：`[{text=`、`, type=text}` 等 MCP/OneBot 结构符号暴露给用户
2. **工具痕迹**：bot 叙述内部操作过程、暴露工具函数名
3. **AI味重**：充满 "我来给你整理一下" "以下是…" 等公式化表达
4. **排版混乱**：长文本不分段、多个无关话题混在一条消息里

本插件在消息发送前最后一步（`on_decorating_result` 钩子）执行**八道管线**，一次性解决。

## 🔧 八道管线

```
LLM 生成回复
    │
    ▼
① 垃圾符号清洗    移除 OneBot/MCP 泄漏的元数据符号
    │
    ▼
② 用户→昵称替换  从 @提及 提取群昵称，替换正文中的"用户"
    │
    ▼
③ 敏感信息过滤    屏蔽系统路径、Shell 命令、内网 IP、API key
    │
    ▼
④ 叙述句删除      删除"工具名 + 过程叙述词"同时出现的句子
    │
    ▼
⑤ 工具名脱敏      残存工具函数名 → 自然语言（40+ 条映射）
    │
    ▼
⑥ 去AI味（正则）  清除"我来整理一下""（括号备注）""值得注意的是"等
    │
    ▼
⑦ 智能分段/文风   LLM 文风优化 > LLM 语义分段 > 规则分段
    │
    ▼
⑧ 图片渲染（可选）检测到结构化列表时自动渲染为图片
    │
    ▼
用户收到干净、自然的回复
```

### ① 垃圾符号清洗

移除 OneBot/MCP 泄漏的元数据符号：`[{text=` `, type=text}` `}]` `[{` `}]`

### ② 用户 → 群昵称替换

从消息开头的 `@群昵称` 中提取实际昵称，将正文中的「用户」替换为真实称呼。

### ③ 敏感信息过滤

自动屏蔽系统路径（`/etc/` `/var/` `/AstrBot` 等）、Shell 命令片段、内网 IP、数据库连接/API key 等。

### ④ 过程叙述删除

识别并删除 bot 的内部操作叙述句。判定标准：句子中**同时出现**工具函数名 + 过程叙述关键词，双重校验避免误伤。

### ⑤ 工具名脱敏

将残存的工具函数名替换为自然语言，覆盖 50+ 条映射：

| 工具名 | 替换为 |
|--------|--------|
| `es_search` `rg_search` | 检索 / 文件检索 |
| `mikan_search` `bangumi_search` | 番剧源 / 番剧信息 |
| `ani-rss` `add_subscription` | 订阅系统 / 添加订阅 |
| `read_file` `write_file` | 读取文件 / 写入文件 |
| `saucenao` `trace_moe` | 搜图 / 番剧识别 |
| …… | |

### ⑥ 去AI味（正则）

三层策略清除 AI 公式化表达：
- **第一层**：逐句删除纯填充句、剥离前缀保留正文
- **第二层**：去括号备注、去论文衔接词、去"第X步是"前缀、去"首先/其次/最后"
- **第三层**：清理多余空白和连续标点

### ⑦ 智能分段 & 文风优化（LLM 优先 + 规则降级）

| 优先级 | 模式 | 说明 |
|--------|------|------|
| **1** | LLM 文风优化 | 调用 LLM 进行结构化重组 + 文风润色，保持人设不变 |
| **2** | LLM 语义分段 | 按语义拆分段落，检测无关话题混入 |
| **3** | 规则分段 | 按空行 + 列表检测 + 句子均分进行纯规则分段 |

#### 多消息逐段发送

分段结果可逐条发送，段间 3~10 秒随机延迟，模拟真人节奏。

#### 防 OOC 机制

- **提示词硬约束**：只允许调整分段换行 / 润色措辞，严禁改动人设、语气词、emoji
- **中文字数校验**：输出与原文中文字数偏差超过阈值即判定篡改，自动降级
- bot 人设、语气、系统提示词完全不受影响

### ⑧ 图片渲染（可选）

检测到结构化列表（如追番列表、食谱步骤等编号内容）时自动渲染为图片发送，避免 QQ 气泡排版错乱。需要 `pip install Pillow`。

## 📦 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot 管理面板 → 插件市场中搜索 **语言逻辑优化大师**，点击安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
cd astrbot_plugin_filter
pip install -r requirements.txt
```

安装后重启 AstrBot 即可生效。

## ⚙️ 配置

插件提供 Web UI 配置面板（AstrBot 管理面板 → 插件 → 语言逻辑优化大师 → 配置）：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `llm_provider_id` | 下拉选择 | 空 | 分段/文风优化用 LLM 模型 |
| `enable_llm_style` | 开关 | 关闭 | 启用 LLM 文风优化（含分段） |
| `enable_llm_segment` | 开关 | 关闭 | 启用 LLM 智能分段（不含文风） |
| `enable_de_ai_flavor` | 开关 | 开启 | 启用正则去AI味 |
| `enable_image_render` | 开关 | 关闭 | 启用图片渲染 |
| `image_min_list_items` | 数字 | 3 | 触发图片渲染的最小编号行数 |
| `image_font_size` | 数字 | 22 | 图片字号（px） |
| `image_max_width` | 数字 | 600 | 图片最大宽度（px） |
| `multi_message` | 开关 | 开启 | 多消息逐段发送 |
| `delay_min` | 浮点 | 3.0s | 消息间隔下限（秒） |
| `delay_max` | 浮点 | 10.0s | 消息间隔上限（秒） |

## ⚙️ 兼容性

| 组件 | 版本要求 |
|------|----------|
| AstrBot | `>= 4.16, < 5` |
| 适配协议 | OneBot v11 / v12 |
| Python | `>= 3.10` |

## 📁 项目结构

```
astrbot_plugin_filter/
├── main.py              # 插件入口 — 编排八道管线
├── pipelines.py         # 管线 ①-⑥：正则清洗、脱敏、去AI味
├── segmentation.py      # 管线 ⑦：LLM 分段/文风优化 + 规则降级
├── image_renderer.py    # 管线 ⑧：结构化列表 → 图片渲染
├── _conf_schema.json    # Web UI 配置表单
├── metadata.yaml        # 插件元数据
├── requirements.txt     # Python 依赖声明
├── tests/
│   └── test_pipelines.py # 管线 ①-⑥ 单元测试
├── README.md            # 本文件
├── LICENSE              # AGPL v3
└── .gitignore
```

## 🐛 常见问题

### Q: 安装后没有生效？

- 确认 AstrBot 版本 >= 4.16
- 重启 AstrBot
- 在 AstrBot 日志中搜索 `[语言逻辑优化大师]` 确认插件已加载

### Q: 会不会误删正常消息？

不会。垃圾符号清洗只匹配特定元数据模式。过程叙述删除需**工具名 + 过程关键词同时出现**才触发。工具名脱敏有快速预检，不含关键词直接跳过。防 OOC 校验确保 LLM 不会篡改原文内容。

### Q: LLM 分段/文风优化不工作？

1. 确保已在 AstrBot「系统配置 → LLM 供应商」中配置了至少一个 chat completion 类型的 provider
2. 在插件配置中选择该 provider（`llm_provider_id`）
3. 打开 `enable_llm_segment` 或 `enable_llm_style` 开关
4. 检查日志中是否有 `[LLM分段]` / `[LLM文风]` 相关输出

### Q: 图片渲染不工作？

1. 确保 `enable_image_render` 已开启
2. 执行 `pip install Pillow`
3. 确保系统安装了中文字体（Windows 通常自带微软雅黑，Linux 需安装如 `fonts-noto-cjk`）
4. 检查日志中是否有 `[图片渲染]` 相关输出

### Q: 多消息模式会不会刷屏？

每条消息间隔 3~10 秒随机延迟，模拟真人打字节奏。可以关闭 `multi_message`，所有段落合并为一条消息。

## 📄 开源协议

GNU AGPL v3 — 详见 [LICENSE](./LICENSE)

## 👤 作者

- **Shtraiy** — [GitHub](https://github.com/Shtraiy)
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)
