from segmentation import combine_console_text


def test_combine_console_text_keeps_all_processed_parts_together():
    assert combine_console_text([" 第一段 ", "", "第二段\n"]) == "第一段\n\n第二段"


def test_combine_console_text_ignores_empty_parts():
    assert combine_console_text(["", "  ", "\n"]) == ""
