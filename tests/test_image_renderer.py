from image_renderer import should_render_image


def test_oversized_text_never_triggers_image_render():
    text = "\n".join(f"{i}. item" for i in range(100_000))

    assert not should_render_image(text, lambda key, default: default)


def test_zero_item_threshold_is_clamped():
    text = "1. item\n2. item"

    assert should_render_image(text, lambda key, default: 0)
