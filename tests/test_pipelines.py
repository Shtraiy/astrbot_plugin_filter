"""单元测试：Markdown 特殊语法清洗。"""

from pipelines import strip_markdown


class TestStripMarkdown:
    def test_unwraps_screenshot_style_bold_text(self):
        text = "7月底到8月初正在打 **BLAST Bounty Summer 2026**（BLAST 赏金赛夏季赛）。"

        assert strip_markdown(text) == (
            "7月底到8月初正在打 BLAST Bounty Summer 2026（BLAST 赏金赛夏季赛）。"
        )

    def test_unwraps_common_inline_emphasis(self):
        assert strip_markdown("**粗体**、*斜体*、__重点__、~~旧内容~~") == (
            "粗体、斜体、重点、旧内容"
        )

    def test_keeps_link_labels_and_image_alt_text(self):
        assert strip_markdown("看[赛程](https://example.com)和![海报](poster.png)") == (
            "看赛程和海报"
        )

    def test_removes_code_delimiters_but_preserves_code_contents(self):
        text = "运行 `value = **raw**`：\n```python\n# title\nprint('*')\n```"

        assert strip_markdown(text) == "运行 value = **raw**：\n# title\nprint('*')"

    def test_escaped_backticks_become_literal_backticks(self):
        assert strip_markdown(r"\`code\`") == "`code`"

    def test_multiple_escaped_backticks(self):
        assert strip_markdown(r"\`code\` 和 \`bar\`") == "`code` 和 `bar`"

    def test_escaped_backticks_inside_fenced_code_are_literal(self):
        text = "```\n\\`code\\`\n```"

        assert strip_markdown(text) == "\\`code\\`"

    def test_normalizes_common_line_level_markdown(self):
        text = (
            "## 比赛信息\n"
            "> 今晚开赛\n"
            "---\n"
            "- 第一场\n"
            "* 第二场\n"
            "+ [x] 已确认"
        )

        assert strip_markdown(text) == (
            "比赛信息\n今晚开赛\n• 第一场\n• 第二场\n• 已确认"
        )

    def test_blockquote_heading_combinations(self):
        assert strip_markdown("> # 标题") == "标题"
        assert strip_markdown("- # 标题") == "• 标题"
        assert strip_markdown("- > # 标题") == "• 标题"
        assert strip_markdown("> - # 标题") == "• 标题"

    def test_trailing_closing_hashes(self):
        assert strip_markdown("## 标题 ##") == "标题"

    def test_setext_heading(self):
        assert strip_markdown("标题\n===") == "标题"

    def test_whitespace_normalized_without_markdown(self):
        assert strip_markdown("  hello  \n\n\nworld  ") == "hello  \n\nworld"

    def test_preserves_ordered_lists(self):
        assert strip_markdown("1. 第一项\n2) 第二项") == "1. 第一项\n2) 第二项"

    def test_preserves_non_markdown_asterisks_and_underscores(self):
        text = "2 * 3 = 6，变量 snake_case，未闭合的 *星号"

        assert strip_markdown(text) == text

    def test_unescapes_markdown_punctuation_after_processing(self):
        assert strip_markdown(r"\*不是斜体\* 和 \#普通井号") == "*不是斜体* 和 #普通井号"

    def test_empty_text_is_unchanged(self):
        assert strip_markdown("") == ""

    def test_plain_text_unchanged(self):
        assert strip_markdown("你好，这是一条普通消息。") == "你好，这是一条普通消息。"
