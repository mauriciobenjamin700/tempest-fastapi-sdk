"""Extraction survives the shapes a model actually returns.

The old fallback sliced from the first ``{`` to the **last** ``}``. That is
greedy, and one stray closing brace after an otherwise perfect payload
turned a decodable answer into a ``ValueError``. Retrying did not help: the
defect was in the cut, not in the generation, so every attempt produced the
same bad slice.

Counting depth fixes both directions at once. A non-greedy regex would not:
it stops at the first closer, truncating any payload with a nested object or
array inside it.

The cases below are the shapes a downstream service already had to handle
against DeepSeek: a fence the prompt asked the model not to add, a sentence
before the payload, a trailing bracket, a bracket inside a string value.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.genai.structured import (
    extract_json_list,
    parse_structured,
    parse_structured_list,
)


class _Item(BaseModel):
    """One suggested item.

    Attributes:
        title (str): The item's title.
        priority (int): Lower sorts first.
    """

    title: str
    priority: int = 0


class TestObjectExtraction:
    """``parse_structured`` against the object shapes models emit."""

    def test_plain_object(self) -> None:
        """A bare JSON object parses."""
        assert parse_structured('{"title": "a"}', _Item).title == "a"

    def test_object_wrapped_in_a_fence(self) -> None:
        """A fenced object parses."""
        raw = '```json\n{"title": "a"}\n```'
        assert parse_structured(raw, _Item).title == "a"

    def test_object_with_prose_on_both_sides(self) -> None:
        """Prose around the object does not prevent the parse."""
        raw = 'Sure! Here it is:\n{"title": "a"}\nHope that helps.'
        assert parse_structured(raw, _Item).title == "a"

    def test_object_followed_by_a_stray_brace(self) -> None:
        """A trailing ``}`` no longer breaks an otherwise valid payload.

        This is the shipped defect: ``rfind("}")`` extended the slice to the
        stray brace and ``json.loads`` rejected the result.
        """
        assert parse_structured('{"title": "a"}}', _Item).title == "a"

    def test_object_containing_a_nested_object(self) -> None:
        """Nesting survives — a first-closer scan would truncate it."""
        raw = '{"title": "a", "meta": {"x": 1}}'
        assert parse_structured(raw, _Item).title == "a"

    def test_brace_inside_a_string_value(self) -> None:
        """A brace inside a string does not unbalance the count."""
        raw = '{"title": "use {braces} sparingly"}'
        assert parse_structured(raw, _Item).title == "use {braces} sparingly"

    def test_no_object_raises(self) -> None:
        """A completion with no object raises, as before."""
        with pytest.raises(ValueError, match="no JSON object"):
            parse_structured("I could not do that", _Item)


class TestListExtraction:
    """``extract_json_list`` against the array shapes models emit."""

    def test_plain_array(self) -> None:
        """A bare array decodes."""
        assert extract_json_list('[{"title": "a"}]') == [{"title": "a"}]

    def test_array_wrapped_in_a_fence(self) -> None:
        """A fenced array decodes."""
        assert extract_json_list('```json\n[{"title": "a"}]\n```') == [{"title": "a"}]

    def test_array_with_prose_on_both_sides(self) -> None:
        """A fence buried between two sentences still decodes.

        ``_FENCE_RE`` alone is anchored and misses this shape.
        """
        raw = 'Here are the items:\n```json\n[{"title": "a"}]\n```\nLet me know.'
        assert extract_json_list(raw) == [{"title": "a"}]

    def test_array_followed_by_a_stray_bracket(self) -> None:
        """A trailing ``]`` no longer breaks the payload."""
        assert extract_json_list('[{"title": "a"}]]') == [{"title": "a"}]

    def test_array_containing_a_nested_array(self) -> None:
        """A nested array survives — a non-greedy regex would truncate it."""
        raw = '[{"title": "a", "tags": ["x", "y"]}]'
        assert extract_json_list(raw) == [{"title": "a", "tags": ["x", "y"]}]

    def test_bracket_inside_a_string_value(self) -> None:
        """A bracket inside a string does not unbalance the count."""
        assert extract_json_list('[{"title": "urgent [sic]"}]') == [
            {"title": "urgent [sic]"},
        ]

    def test_empty_array_is_a_result_not_a_failure(self) -> None:
        """``[]`` decodes to an empty list, never to ``None``.

        The distinction is the whole point of the ``None`` return: ``[]``
        means "the model answered, and the answer is nothing", which must
        not trigger a retry.
        """
        assert extract_json_list("[]") == []

    def test_truncated_array_returns_none(self) -> None:
        """An array that never closes is unrecoverable, so ``None``.

        This is what a completion cut off at the token ceiling looks like.
        """
        assert extract_json_list('[{"title": "a"}, {"title": "b"') is None

    def test_no_array_returns_none(self) -> None:
        """A completion with no array at all returns ``None``."""
        assert extract_json_list("I could not do that") is None

    def test_object_is_not_a_list(self) -> None:
        """A valid JSON object is still not a list, so ``None``."""
        assert extract_json_list('{"title": "a"}') is None


class TestParseStructuredList:
    """``parse_structured_list`` validation behaviour."""

    def test_validates_every_item(self) -> None:
        """Each item comes back as a schema instance, in order."""
        items = parse_structured_list(
            '[{"title": "a", "priority": 2}, {"title": "b"}]',
            _Item,
        )
        assert [(item.title, item.priority) for item in items] == [("a", 2), ("b", 0)]

    def test_raises_on_a_bad_item_by_default(self) -> None:
        """One malformed item fails the whole call unless asked otherwise."""
        with pytest.raises(ValidationError):
            parse_structured_list('[{"title": "a"}, {"nope": 1}]', _Item)

    def test_skip_invalid_keeps_the_good_items(self) -> None:
        """``skip_invalid`` drops the bad item and keeps the rest."""
        items = parse_structured_list(
            '[{"title": "a"}, {"nope": 1}, {"title": "b"}]',
            _Item,
            skip_invalid=True,
        )
        assert [item.title for item in items] == ["a", "b"]

    def test_raises_when_there_is_no_array(self) -> None:
        """No array at all is an error, not an empty list."""
        with pytest.raises(ValueError, match="no JSON array"):
            parse_structured_list("nothing here", _Item)
