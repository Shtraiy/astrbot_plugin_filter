"""Tests for the shared event/context access layer."""

from __future__ import annotations

from types import SimpleNamespace

from astrbot.api.message_components import File, Image, Plain

from event_access import (
    entry_content,
    entry_role,
    entry_text,
    get_message_chain,
    has_media,
    has_reply,
    is_image_or_file,
    is_media_part,
    is_reply_component,
    media_components,
    plain_text_of,
    request_contexts,
)


class Reply:
    pass


class Video:
    pass


def _chain_event(chain):
    return SimpleNamespace(message_obj=SimpleNamespace(message=chain))


def _getter_event(chain):
    return SimpleNamespace(message_obj=None, get_messages=lambda: chain)


def test_get_message_chain_reads_message_obj():
    chain = [Plain("hi")]
    result = get_message_chain(_chain_event(chain))

    assert result is chain


def test_get_message_chain_falls_back_to_getter():
    chain = [Plain("hi")]
    result = get_message_chain(_getter_event(chain))

    assert result == chain


def test_get_message_chain_returns_none_when_unavailable():
    assert get_message_chain(SimpleNamespace(message_obj=None)) is None
    assert get_message_chain(None) is None


def test_is_reply_component_matches_class_name_or_type_attr():
    assert is_reply_component(Reply()) is True
    assert is_reply_component(SimpleNamespace(type="reply")) is True
    assert is_reply_component(Plain("x")) is False


def test_has_reply_scans_chain():
    assert has_reply(_chain_event([Plain("x"), Reply()])) is True
    assert has_reply(_chain_event([Plain("x")])) is False
    assert has_reply(_chain_event([])) is False


def test_is_media_part_covers_parts_and_components():
    assert is_media_part(Image(url="x")) is True
    assert is_media_part(File(name="x")) is True
    assert is_media_part(Video()) is True
    assert is_media_part({"type": "image_url", "image_url": {"url": "x"}}) is True
    assert is_media_part({"type": "text", "text": "x"}) is False
    assert is_media_part(Plain("x")) is False
    assert is_media_part("x") is False


def test_is_media_part_detects_url_attribute_and_text_marker():
    assert is_media_part(SimpleNamespace(url="http://x")) is True
    assert is_media_part(SimpleNamespace(text="[Image] something")) is True


def test_is_image_or_file_is_narrow():
    assert is_image_or_file(Image(url="x")) is True
    assert is_image_or_file(File(name="x")) is True
    assert is_image_or_file(Video()) is False
    assert is_image_or_file(SimpleNamespace(url="http://x")) is False


def test_has_media_uses_broad_media_detection():
    assert has_media(_chain_event([Plain("x"), Image(url="x")])) is True
    assert has_media(_chain_event([Plain("x")])) is False
    assert has_media(_chain_event([])) is False


def test_media_components_returns_only_image_and_file():
    image = Image(url="x")
    file_ = File(name="f")
    video = Video()

    result = media_components(_chain_event([Plain("x"), image, video, file_]))

    assert result == [image, file_]


def test_plain_text_of_joins_plain_components():
    assert plain_text_of([Plain("你"), Plain("好")]) == "你好"
    assert plain_text_of([Plain("你"), Image(url="x")]) == "你"
    assert plain_text_of([]) == ""
    assert plain_text_of(None) == ""


def test_request_contexts_reads_req_attribute_or_mapping():
    req = SimpleNamespace(contexts=[{"role": "user"}])
    assert request_contexts(req) == [{"role": "user"}]
    assert request_contexts({"contexts": [1]}) == [1]
    assert request_contexts(SimpleNamespace(no_contexts=True)) is None
    assert request_contexts(None) is None


def test_entry_role_and_content_read_dict_or_object():
    assert entry_role({"role": "user"}) == "user"
    assert entry_content({"content": "x"}) == "x"
    assert entry_role(SimpleNamespace(role="assistant")) == "assistant"
    assert entry_content(SimpleNamespace(content="y")) == "y"


def test_entry_text_extracts_text_only_content():
    assert entry_text("Output stopped.") == "Output stopped."
    assert entry_text([{"type": "text", "text": "你好"}]) == "你好"
    assert (
        entry_text(
            [
                {"type": "text", "text": "Stop "},
                {"type": "text", "text": "output."},
            ]
        )
        == "Stop output."
    )


def test_entry_text_returns_none_for_multimodal_or_missing():
    assert entry_text(None) is None
    assert (
        entry_text(
            [
                {"type": "text", "text": "x"},
                {"type": "image_url", "image_url": {"url": "x"}},
            ]
        )
        is None
    )
    assert entry_text({"type": "text"}) is None
