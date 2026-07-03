# astrbot_plugin_filter — Bug 修补大师

🚿 自动清洗 AstrBot 通过 OneBot 输出到 QQ 时产生的垃圾元数据符号。

## 📋 这是什么？

当 AstrBot 接入 OneBot 协议（如 Lagrange、NapCatQQ 等）向 QQ 输出消息时，LLM 或 MCP 工具调用的返回结果有时会被包裹在大量**结构元数据**中，这些元数据对用户来说是不可读的「垃圾字符」。

### 典型问题示例

**原始输出（用户看到的就是这样）：**

```
[{text=
, type=text}], type=text}][{text=[{text=
柠檬鸭可是南宁当地非常经典且超级开胃的名菜！, type=text}], type=text}]
```

**本插件清洗后：**

```
柠檬鸭可是南宁当地非常经典且超级开胃的名菜！
```

### 垃圾符号清单

以下元数据片段会被自动移除：

| 垃圾片段 | 来源 |
|----------|------|
| `[{text=` | OneBot 消息链序列化残留 |
| `, type=text}` / `, type=\text}` | 组件类型标记 |
| `}]` / `[{` / `}]` | 嵌套结构括号 |

---

## 🚀 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot 管理面板 → 插件市场中搜索 **filter**，点击安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/Shtraiy/astrbot_plugin_filter.git
```

安装后重启 AstrBot 即可生效。

---

## ⚙️ 兼容性

| 组件 | 版本要求 |
|------|----------|
| AstrBot | `>= 4.16, < 5` |
| 适配协议 | OneBot v11 / v12 |
| Python | `>= 3.10` |

---

## 🔧 工作原理

```
LLM 生成回复 → MCP 工具返回结果 → OneBot 序列化
                                      ↓
                              产生元数据垃圾文本
                                      ↓
                           ⚡ 本插件拦截 (on_decorating_result)
                                      ↓
                              正则匹配 + 移除垃圾符号
                                      ↓
                              重建干净的消息链
                                      ↓
                              用户收到纯净文本 ✅
```

本插件注册在 AstrBot 的 `on_decorating_result` 事件钩子上，这是消息发送给平台之前的**最后一步**。在这个阶段：

1. 提取消息链中的纯文本
2. 使用正则表达式一次性匹配所有已知的垃圾符号模式
3. 移除垃圾符号，清理首尾残留字符
4. 用清洗后的文本重建消息链，替换原始内容

整个过程对 LLM 和其他插件透明，不影响正常的消息处理流程。

---

## 📁 项目结构

```
astrbot_plugin_filter/
├── main.py          # 插件主逻辑
├── metadata.yaml    # 插件元数据（名称、版本、作者等）
├── README.md        # 本文件
├── LICENSE          # 开源协议
└── .gitignore
```

---

## 🐛 常见问题

### Q: 安装后没有生效？

- 确认 AstrBot 版本 >= 4.16
- 重启 AstrBot
- 在 AstrBot 日志中搜索 `[Bug修补大师]` 确认插件已加载

### Q: 会不会误删正常消息？

不会。插件只匹配非常特定的元数据模式（`[{text=`、`, type=text}`、`}]` 等），这些模式几乎不可能出现在正常对话中。如果一条消息完全没有命中任何垃圾模式，它会被**原样保留**，不做任何修改。

### Q: 如果有新的垃圾模式怎么办？

欢迎提交 Issue 或 PR，附上新的垃圾文本样例。

---

## 📄 开源协议

MIT License — 详见 [LICENSE](./LICENSE)

---

## 👤 作者

- **Shtraiy** — [GitHub](https://github.com/Shtraiy)
- 仓库：[astrbot_plugin_filter](https://github.com/Shtraiy/astrbot_plugin_filter)
