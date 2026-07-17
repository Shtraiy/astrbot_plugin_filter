# AstrBot 语言逻辑优化大师

一个用于 AstrBot 的输出后处理插件。在消息发送前清理模型输出中的内部痕迹，优化表达和排版，并支持智能分段、多消息发送与列表图片渲染。

## 功能

- 清理 OneBot、MCP 等结构化元数据泄漏
- 过滤系统路径、Shell 命令、内网 IP、API Key 等敏感信息
- 删除工具调用过程中的内部叙述
- 将工具函数名转换为更自然的中文描述
- 使用规则或 LLM 优化 AI 味表达
- 支持 LLM 智能分段，失败时自动降级到规则分段
- 多消息发送前合并高度相似的重复段落
- 同一群聊内按顺序发送不同用户的完整回复，避免消息交错
- 可选：将编号列表渲染为图片发送

## 处理流程

```text
AstrBot 生成回复
        |
        v
垃圾符号清理 -> 用户称呼替换 -> 敏感信息过滤
        |
        v
工具叙述清理 -> 工具名脱敏 -> AI 味优化
        |
        v
LLM 分段/文风优化 -> 规则分段降级 -> 重复段落合并
        |
        v
按会话串行发送
```

## 安装

### 通过 AstrBot 插件市场

在 AstrBot 管理面板的插件市场中搜索“语言逻辑优化大师”，安装后重启 AstrBot。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
cd astrbot_plugin_filter
pip install -r requirements.txt
```

安装后重启 AstrBot，并在管理面板中打开插件配置。

## 配置

配置入口：`AstrBot 管理面板 -> 插件 -> 语言逻辑优化大师 -> 配置`

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | ---: | --- |
| `llm_provider_id` | provider | 空 | LLM 分段和文风优化使用的模型 |
| `enable_llm_style` | bool | `false` | 启用 LLM 文风优化 |
| `enable_llm_segment` | bool | `false` | 启用 LLM 语义分段 |
| `enable_de_ai_flavor` | bool | `true` | 启用规则去 AI 味 |
| `enable_image_render` | bool | `false` | 启用列表图片渲染 |
| `image_min_list_items` | int | `3` | 触发图片渲染的最少列表项数 |
| `image_font_size` | int | `22` | 图片字体大小 |
| `image_max_width` | int | `600` | 图片最大宽度 |
| `multi_message` | bool | `true` | 是否将分段结果逐条发送 |
| `delay_min` | float | `2.0` | 分段消息间隔下限，运行时限制在 2~5 秒 |
| `delay_max` | float | `5.0` | 分段消息间隔上限，运行时限制在 2~5 秒 |

当启用 LLM 功能时，需要先在 AstrBot 中配置可用的 LLM provider，并填写 `llm_provider_id`。LLM 不可用或输出不符合校验要求时，插件会自动使用规则处理。

图片渲染需要 Pillow：

```bash
pip install Pillow
```

## 兼容性

- AstrBot：`>= 4.16, < 5`
- Python：`>= 3.10`
- 消息协议：OneBot v11 / v12

## 开发与测试

```bash
python -m py_compile main.py pipelines.py segmentation.py image_renderer.py
python -m pytest -q
```

## 项目结构

```text
astrbot_plugin_filter/
├── main.py              # 插件入口与输出流程编排
├── pipelines.py         # 文本清理、脱敏和去 AI 味
├── segmentation.py      # LLM/规则分段、重复检测和多消息发送
├── image_renderer.py    # 列表图片渲染
├── _conf_schema.json     # AstrBot 配置项定义
├── metadata.yaml         # 插件元数据
├── requirements.txt      # Python 依赖
├── tests/                # 测试代码
├── LICENSE               # AGPL-3.0 许可证
└── README.md
```

## 常见问题

### 安装后没有生效

确认 AstrBot 版本满足要求，重启 AstrBot，并在日志中检查插件是否成功加载。

### LLM 分段没有生效

确认已配置 `llm_provider_id`，并打开 `enable_llm_segment` 或 `enable_llm_style`。插件会在 LLM 调用失败时自动降级，不影响普通规则分段。

### 多消息发送顺序异常

同一 `unified_msg_origin` 下的回复会串行处理。不同群聊或不同会话之间仍然可以同时发送。分段消息间隔固定限制在 2~5 秒范围内。

## 许可证

本项目采用 [GNU AGPL v3](./LICENSE) 许可证。

## 作者

- Shtraiy
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)
