# AstrBot 输出 Markdown 过滤插件

一个用于 AstrBot 的输出后处理插件：在消息发送前过滤掉常见的 Markdown 特殊语法，转换为适合聊天窗口展示的纯文本。

## 功能

- 去掉标题（`#`）、引用（`>`）、分隔线（`---`）等行级标记
- 去掉粗体、斜体、删除线标记，保留文字内容
- 链接 `[文字](url)` 和图片 `![alt](url)` 只保留文字 / alt
- 代码块与行内代码去掉反引号，代码内容原样保留（内部的 `**` 等不会被误删）
- 无序列表转换为 `•` 前缀
- 保留有序列表、数学符号、未闭合的星号/下划线等非 Markdown 内容

## 安装

### 通过 AstrBot 插件市场

在 AstrBot 管理面板的插件市场中搜索"Markdown 语法过滤"，安装后重启 AstrBot。

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
cd astrbot_plugin_filter
pip install -r requirements.txt
```

安装后重启 AstrBot。

## 配置

无配置项。

## 兼容性

- AstrBot：`>= 4.16, < 5`
- Python：`>= 3.10`
- 消息协议：OneBot v11 / v12

## 开发与测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## 项目结构

```text
astrbot_plugin_filter/
├── main.py              # 插件入口：对回复中的纯文本应用 Markdown 过滤
├── pipelines.py         # Markdown 特殊语法清洗
├── _conf_schema.json    # AstrBot 配置项定义（无配置项）
├── metadata.yaml        # 插件元数据
├── requirements.txt     # 运行时依赖（无）
├── requirements-dev.txt # 本地测试依赖
├── tests/               # 测试代码
└── LICENSE              # AGPL-3.0 许可协议
```

## 许可协议

本项目采用 [GNU AGPL v3](./LICENSE) 许可协议。

## 作者

- Shtraiy
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)
