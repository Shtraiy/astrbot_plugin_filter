"""
单元测试：文本处理管线 ①-⑥
"""

import pytest

from pipelines import (
    clean_garbage,
    replace_user,
    filter_sensitive,
    remove_tool_narration,
    deidentify_tool_names,
    de_ai_flavor,
)


# ============================================================
#  ① clean_garbage — 垃圾符号清洗
# ============================================================

class TestCleanGarbage:
    def test_removes_onbot_metadata(self):
        text = '[{text=你好，世界}, type=text}]'
        result = clean_garbage(text)
        assert '你好，世界' in result
        assert '[{text=' not in result
        assert 'type=text' not in result

    def test_removes_bare_brackets(self):
        text = '[{你好}]'
        result = clean_garbage(text)
        assert '[{' not in result
        assert '}]' not in result

    def test_collapses_whitespace(self):
        text = '你好    世界'
        result = clean_garbage(text)
        assert '你好 世界' in result or '你好世界' in result

    def test_normal_text_unchanged(self):
        text = '你好，这是一条普通消息。'
        assert clean_garbage(text) == text

    def test_empty_string(self):
        assert clean_garbage('') == ''


# ============================================================
#  ② replace_user — 昵称替换
# ============================================================

class TestReplaceUser:
    def test_replaces_user_with_at_name(self):
        text = '@小明\n用户刚刚发送了新指令"帮我查一下天气"'
        result = replace_user(text)
        assert '小明' in result
        assert '用户刚刚发送了新指令' not in result

    def test_replaces_bare_user(self):
        text = '@小红\n用户想要查看番剧列表'
        result = replace_user(text)
        assert '小红' in result
        assert '用户想要' not in result

    def test_no_at_mention_unchanged(self):
        text = '用户想要查看番剧列表'
        result = replace_user(text)
        # 没有 @ 就无法提取昵称，保持"用户"不变
        assert '用户' in result

    def test_replaces_user_with_quote_brackets(self):
        text = '@小刚\n用户说"你好"'
        result = replace_user(text)
        assert '小刚' in result

    def test_special_chars_in_name(self):
        """昵称包含正则元字符时不应崩溃"""
        text = '@test.user+*\n用户你好'
        result = replace_user(text)
        assert 'test.user+*' in result


# ============================================================
#  ③ filter_sensitive — 敏感信息过滤
# ============================================================

class TestFilterSensitive:
    def test_removes_system_path(self):
        text = '文件在 /etc/config/foo 里面'
        result = filter_sensitive(text)
        assert '/etc/' not in result

    def test_removes_windows_path(self):
        text = '从 C:\\Users\\test 读取文件'
        result = filter_sensitive(text)
        assert 'C:\\' not in result

    def test_removes_internal_ip(self):
        text = '连接 192.168.1.1:8080 失败'
        result = filter_sensitive(text)
        assert '192.168.1.1' not in result

    def test_removes_shell_command(self):
        text = '执行 rm -rf /tmp/test 清理文件'
        result = filter_sensitive(text)
        assert 'rm -rf' not in result

    def test_removes_sensitive_keywords(self):
        text = 'API key=sk-abc123 已泄露'
        result = filter_sensitive(text)
        assert 'API key' not in result

    def test_normal_text_unchanged(self):
        text = '你好，今天的天气真好。'
        result = filter_sensitive(text)
        assert '天气真好' in result


# ============================================================
#  ④ remove_tool_narration — 删除工具调用叙述句
# ============================================================

class TestRemoveToolNarration:
    def test_removes_narration_with_tool_name(self):
        text = '我先用 es_search 搜索一下相关数据。找到了以下结果：'
        result = remove_tool_narration(text)
        assert '我先用' not in result or 'es_search' not in result

    def test_keeps_normal_content(self):
        text = '以下是搜索结果：找到了3个番剧。推荐你看看这些。'
        result = remove_tool_narration(text)
        assert '以下是搜索结果' in result

    def test_removes_exec_operation_narration(self):
        text = '让我执行 shell 命令查看一下。结果如下：'
        result = remove_tool_narration(text)
        assert '让我执行' not in result or 'shell' not in result

    def test_normal_conversation_unchanged(self):
        text = '你好！让我想想……嗯，我觉得这部电影很不错。'
        result = remove_tool_narration(text)
        # "让我想想" 是正常对话，不应删除（因为没有工具名同时出现）
        assert '让我想想' in result


# ============================================================
#  ⑤ deidentify_tool_names — 工具名脱敏
# ============================================================

class TestDeidentifyToolNames:
    def test_replaces_search_tool(self):
        text = '通过 es_search 找到了结果'
        result = deidentify_tool_names(text)
        assert '检索' in result
        assert 'es_search' not in result

    def test_replaces_file_tool(self):
        text = 'read_file 返回了文件内容'
        result = deidentify_tool_names(text)
        assert '读取文件' in result
        assert 'read_file' not in result

    def test_replaces_subscription_tool(self):
        text = 'add_subscription 成功添加了订阅'
        result = deidentify_tool_names(text)
        assert '添加订阅' in result
        assert 'add_subscription' not in result

    def test_skips_when_no_tool_keyword(self):
        """快速预检：无工具关键词的文本应直接返回"""
        text = '你好，这是一段完全正常的对话内容。'
        result = deidentify_tool_names(text)
        assert result == text

    def test_replaces_multiple_tools(self):
        text = '先用 web_search 搜索，再用 read_file 读取'
        result = deidentify_tool_names(text)
        assert '搜索' in result
        assert '读取文件' in result
        assert 'web_search' not in result
        assert 'read_file' not in result


# ============================================================
#  ⑥ de_ai_flavor — 去AI味
# ============================================================

class TestDeAiFlavor:
    def test_removes_filler_sentence(self):
        text = '我这就把相关信息整理如下：\n1. 第一条\n2. 第二条\n以上就是全部内容。'
        result = de_ai_flavor(text)
        assert '我这就把' not in result
        assert '以上就是' not in result
        assert '第一条' in result
        assert '第二条' in result

    def test_strips_filler_prefix_keeps_content(self):
        text = '以下是对应的内容：实际正文在这里。'
        result = de_ai_flavor(text)
        assert '以下是对应的内容' not in result
        assert '实际正文在这里' in result

    def test_removes_academic_transitions(self):
        text = '值得注意的是，这个功能非常重要。此外，还需要注意兼容性。'
        result = de_ai_flavor(text)
        assert '值得注意的是' not in result
        assert '此外' not in result

    def test_replaces_step_prefix(self):
        text = '第一步是打开设置，第二步是选择选项。'
        result = de_ai_flavor(text)
        assert '1. 打开设置' in result
        assert '2. 选择选项' in result

    def test_removes_bracket_notes(self):
        text = '这个功能（注：需要管理员权限）非常实用。'
        result = de_ai_flavor(text)
        assert '（注：需要管理员权限）' not in result
        assert '需要管理员权限' in result

    def test_normal_text_unchanged(self):
        text = '这家店的奶茶特别好喝，我每周都去！'
        result = de_ai_flavor(text)
        assert text in result or result in text or result == text

    def test_removes_paragraph_sequencers(self):
        text = '首先，我们需要了解背景。其次，分析一下原因。最后，得出结论。'
        result = de_ai_flavor(text)
        assert '首先，' not in result
        assert '其次，' not in result
        assert '最后，' not in result
