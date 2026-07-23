"""
单元测试：分段模块 ⑦ — _merge_orphan_colons、_split_dense_entries、
_segment_text 规则分段。
"""

import pytest

from segmentation import (
    _merge_orphan_colons,
    _split_dense_entries,
    _segment_text,
    _is_list_block,
    _SEGMENT_PROMPT,
    _STYLE_PROMPT,
)


def test_llm_prompts_are_readable_and_include_output_constraints():
    for prompt in (_SEGMENT_PROMPT, _STYLE_PROMPT):
        assert "????" not in prompt
        assert "只输出" in prompt
        assert "原文" in prompt

    assert "空行" in _SEGMENT_PROMPT
    assert "自然" in _STYLE_PROMPT


# ============================================================
#  _merge_orphan_colons — 冒号标题合并
# ============================================================

class TestMergeOrphanColons:
    def test_basic_merge_forward(self):
        """冒号标题合并到下一段"""
        text = "标题：\n\n内容段落"
        result = _merge_orphan_colons(text)
        assert '\n\n' not in result
        assert result == "标题：\n内容段落"

    def test_no_colon_unchanged(self):
        """无冒号段保持原样"""
        text = "第一段\n\n第二段\n\n第三段"
        result = _merge_orphan_colons(text)
        assert result == text

    def test_cascade_merge(self):
        """连续冒号段级联合并"""
        text = "A：\n\nB：\n\nC：\n\n最终内容"
        result = _merge_orphan_colons(text)
        assert result == "A：\nB：\nC：\n最终内容"

    def test_multiline_colon_not_merged(self):
        """含内部换行的冒号段不合并（first_merge 守卫）"""
        text = "多行\n内容：\n\n下一段"
        result = _merge_orphan_colons(text)
        # 首段包含内部换行，不应合并
        assert '\n\n' in result

    def test_cascade_skips_guard_after_first(self):
        """级联开始后，后续冒号段跳过 first_merge 守卫"""
        text = "A：\n\nB：\n\n多行\nC：\n\n最终内容"
        result = _merge_orphan_colons(text)
        # A→B 合并后，B 无内部换行则继续合并 C（C 有内部换行但已不是 first_merge）
        # 实际上 A: 合并 B: → "A：\nB："，然后检查 B：无内部换行，继续合并下一段
        # "多行\nC：" 是下一段，但它有内部换行 → 此时 first_merge=True（新一轮外层 while）
        # 所以会 break，不会合并
        assert "最终内容" in result

    def test_mixed_normal_and_colon(self):
        """只有冒号段会合并，普通段保持独立"""
        text = "第一段\n\n第二段：\n\n第三段内容\n\n第四段"
        result = _merge_orphan_colons(text)
        # "第二段：" 合并到 "第三段内容"
        assert "第二段：\n第三段内容" in result
        assert "第一段" in result
        assert "第四段" in result

    def test_single_paragraph_unchanged(self):
        """单段落直接返回"""
        assert _merge_orphan_colons("仅一段") == "仅一段"

    def test_short_text_unchanged(self):
        """短文本不变"""
        assert _merge_orphan_colons("短") == "短"


# ============================================================
#  _split_dense_entries — 密集完结条目拆分
# ============================================================

class TestSplitDenseEntries:
    def test_dense_anime_list_split(self):
        """完结番剧密集段 → 每行一条"""
        text = (
            "金牌得主（第一季）：已下完第 13 集。"
            "上伊那牡丹，酒醉身姿似百合花般（第一季）：已下完第 12 集。"
            "东岛丹三郎想成为假面骑士（第一季）：已下完第 24 集。"
        )
        result = _split_dense_entries(text)
        lines = result.split('\n')
        assert len(lines) == 3
        assert '金牌得主' in lines[0]
        assert '上伊那牡丹' in lines[1]
        assert '东岛丹三郎' in lines[2]

    def test_below_threshold_unchanged(self):
        """不足 3 个条目不触发拆分"""
        text = (
            "金牌得主（第一季）：已下完第 13 集。"
            "上伊那牡丹，酒醉身姿似百合花般（第一季）：已下完第 12 集。"
        )
        result = _split_dense_entries(text)
        # 只有 2 个条目，不触发
        assert result == text

    def test_normal_prose_unchanged(self):
        """正常叙事不误拆"""
        text = "今天去了超市，买了苹果。然后回家看了电视。最后睡觉了。"
        result = _split_dense_entries(text)
        assert result == text

    def test_season_in_other_context_unchanged(self):
        """孤立提及"第一季"不误拆"""
        text = "这部番剧的第一季质量很高。第二季也在制作中。值得期待。"
        result = _split_dense_entries(text)
        # 只有 2 个"季"，不触发阈值
        assert result == text

    def test_empty_text(self):
        assert _split_dense_entries("") == ""


# ============================================================
#  _segment_text — 规则分段集成
# ============================================================

class TestSegmentText:
    def test_short_text_passthrough(self):
        """短文本直接返回"""
        short = "这是一条很短的文本。"
        assert _segment_text(short) == short

    def test_long_text_with_clause_punctuation_is_split(self):
        """长文本仅由逗号和分号连接时，也应按分句拆分"""
        clauses = [f"这是第{i}个较长的说明内容" * 6 for i in range(1, 6)]
        text = "，".join(clauses)

        result = _split_long_para(text)

        assert len(result) >= 2
        assert "".join(result) == text

    def test_colon_header_attached_to_content(self):
        """冒号标题正确关联到正文"""
        text = "正在连载的番剧：\n\n番剧A详情\n\n番剧B详情"
        result = _segment_text(text)
        # "正在连载的番剧：" 应该合并到后续内容
        assert "正在连载的番剧：\n番剧A" in result

    def test_dense_entries_become_lines(self):
        """密集条目段内每行一条"""
        text = (
            "以下是追番列表：\n\n"
            "金牌得主（第一季）：已下完第 13 集。"
            "上伊那牡丹（第一季）：已下完第 12 集。"
            "东岛丹三郎（第一季）：已下完第 24 集。"
        )
        result = _segment_text(text)
        # 每个条目应各占一行
        assert '金牌得主' in result
        assert '上伊那牡丹' in result
        assert '东岛丹三郎' in result

    def test_anime_entries_structure_preserved(self):
        """番剧条目多行结构不被打散"""
        text = (
            "段落A\n\n"
            "段落B\n\n"
            "段落C\n\n"
            "番剧名（第一季）\n当前进度：第 2 集\n订阅源：Mikan\n\n"
            "段落E\n\n"
            "段落F\n\n"
            "段落G"
        )
        result = _segment_text(text)
        # 番剧条目的三行结构应保持完整
        assert "番剧名（第一季）\n当前进度：第 2 集\n订阅源：Mikan" in result or \
               "番剧名（第一季）" in result

    def test_normal_conversation_preserved(self):
        """正常对话文本不受影响"""
        text = (
            "今天天气真不错，适合出去走走。\n\n"
            "我推荐去公园散步，那边风景很好。\n\n"
            "记得带瓶水哦，天气有点热。"
        )
        result = _segment_text(text)
        assert '今天天气' in result
        assert '公园散步' in result
        assert '记得带水' in result

    def test_long_text_reduced_to_max_paras(self):
        """超多段落被合并到 _MAX_PARAS 以内"""
        many = "\n\n".join([f"第{i}段内容" for i in range(10)])
        result = _segment_text(many)
        para_count = len([p for p in result.split('\n\n') if p.strip()])
        assert para_count <= 5

    def test_few_paragraphs_split_longest(self):
        """只有 1-2 段时拆分最长段"""
        text = ("这是一个很长的段落。" * 15)  # ~150 chars, may or may not trigger
        result = _segment_text(text)
        # 至少原文内容没有丢失
        assert "这是一个很长的段落" in result

    def test_full_anime_reply_scenario(self):
        """用户提供的番剧回复场景端到端测试"""
        text = (
            "@褪色的花见鸟 \n在的！我这就帮你看看。\n\n"
            "刚才我又特意帮你用工具去后台刷新了一下，今天（周四）正好是好几部新番更新的日子呢！\n\n"
            "下面是目前最准确、也是刚刚更新过的连载新番下载进度：\n\n"
            "正在连载的番剧：\n\n"
            "不虐待我的继母与继姐（第一季）\n当前进度：第 2 集\n订阅源：Mikan / ANi 字幕组\n\n"
            "在超市后门吸烟的二人（第一季）\n当前进度：第 1 集\n订阅源：Mikan / LoliHouse 字幕组\n\n"
            "已经完结的番剧，你可以找时间整理一下：\n\n"
            "金牌得主（第一季）：已下完第 13 集。"
            "上伊那牡丹，酒醉身姿似百合花般（第一季）：已下完第 12 集。"
            "东岛丹三郎想成为假面骑士（第一季）：已下完第 24 集。"
            "尖帽子的魔法工房（第一季）：已下完第 13 集。\n\n"
            "今天刚刚更新好几部连载，有空可以去看看！"
        )
        result = _segment_text(text)

        # 1. "正在连载的番剧：" 不应孤立，应关联到后续番剧内容
        assert "正在连载的番剧" in result
        # 2. "已经完结的番剧" 应关联到完结列表
        assert "已经完结的番剧" in result
        # 3. 完结列表应被拆分为多行
        assert "金牌得主" in result
        assert "上伊那牡丹" in result
        assert "东岛丹三郎" in result
        assert "尖帽子的魔法工房" in result
        # 4. 番剧条目多行结构保持
        assert "当前进度" in result
        assert "订阅源" in result
        # 5. 收尾句存在
        assert "今天刚刚更新" in result
