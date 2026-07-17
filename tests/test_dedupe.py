from segmentation import dedupe_similar_paragraphs


def test_collapses_repeated_tool_process_replies():
    paragraphs = [
        "@二阶堂艾玛 为了帮你订阅《猫与龙》这部番剧，我需要先看一下我这边的追番管理说明书，确认一下怎么操作才行。我这就去翻一下",
        "@二阶堂艾玛 为了帮你订阅《猫与龙》这部番剧，我先在蜜柑上搜一下看能不能找到对应的番剧页面。你稍微等我一下下哦。",
    ]

    result = dedupe_similar_paragraphs(paragraphs)

    assert len(result) == 1
    assert "猫与龙" in result[0]


def test_keeps_distinct_useful_paragraphs():
    paragraphs = [
        "已经找到《猫与龙》的番剧页面。",
        "订阅已添加成功，后续更新会自动推送。",
        "如果你想换字幕组，也可以之后再调整。",
    ]

    assert dedupe_similar_paragraphs(paragraphs) == paragraphs
