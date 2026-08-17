"""Adaptive chunking tests for the structured PDF edit path.

Verifies that ``_request_edits_structured`` groups logical items into chunks
bounded by BOTH ``EDIT_CHUNK_SIZE`` (max items) and ``EDIT_CHUNK_MAX_CHARS``
(max input characters), that a single oversized item is sent alone (never split),
that ordering and the 1-input -> 1-output invariant hold, and that tables stay
one item.
"""

import json


from app.services.document_edit import (
    EDIT_CHUNK_SIZE,
    EDIT_CHUNK_MAX_CHARS,
    _chunk_items,
    _request_edits_structured,
)


def _text_item(i, text):
    return {"id": i, "type": "text", "text": text}


def _table_item(i, rows, cols, fill="c"):
    cells = [[fill for _ in range(cols)] for _ in range(rows)]
    return {"id": i, "type": "table", "cells": cells}


def test_chunk_constants_are_8_and_8000():
    assert EDIT_CHUNK_SIZE == 8
    assert EDIT_CHUNK_MAX_CHARS == 8000


def test_20_items_split_by_item_count():
    items = [_text_item(i, "word") for i in range(20)]
    chunks = _chunk_items(items)
    # 20 items / 8 per chunk -> 3 chunks (8, 8, 4).
    assert sum(len(c) for c in chunks) == 20
    assert all(len(c) <= EDIT_CHUNK_SIZE for c in chunks)
    # Order preserved across chunks.
    flat = [it["id"] for c in chunks for it in c]
    assert flat == list(range(20))


def test_chunk_size_never_exceeds_8():
    items = [_text_item(i, "x" * 500) for i in range(50)]
    chunks = _chunk_items(items)
    assert all(len(c) <= EDIT_CHUNK_SIZE for c in chunks)


def test_large_table_forces_character_based_split():
    big = _table_item(0, 5, 5, fill="x" * 2000)  # 25 cells * 2000 = 50000 chars
    small = [_text_item(i, "a" * 100) for i in range(20)]
    items = [big] + small
    chunks = _chunk_items(items)
    # The oversized table must be its own chunk (never merged with others, never split).
    assert chunks[0] == [big]
    assert len(chunks[0]) == 1
    # No other chunk may contain part of the big table.
    assert all(big not in c for c in chunks[1:])
    # Every small item still appears exactly once.
    seen = [it["id"] for c in chunks for it in c]
    assert sorted(seen) == sorted([it["id"] for it in items])


def test_oversized_table_item_stays_one_chunk():
    big = _table_item(0, 3, 3, fill="y" * 4000)  # > EDIT_CHUNK_MAX_CHARS
    chunks = _chunk_items([big])
    assert len(chunks) == 1
    assert chunks[0][0] is big
    assert chunks[0][0]["cells"] == big["cells"]


def test_no_item_is_split_across_chunks():
    items = [_text_item(i, "z" * 300) for i in range(17)] + [_table_item(99, 2, 2)]
    chunks = _chunk_items(items)
    # Flatten back: every input item must appear exactly once, intact.
    flat_ids = [it["id"] for c in chunks for it in c]
    assert sorted(flat_ids) == sorted([it["id"] for it in items])
    assert len(flat_ids) == len(items)


def _echo_generate(prompt, system_instruction=None, **kwargs):
    """Fake edit model: return exactly one output item per input, preserving
    id/type/order and (for tables) the exact cell matrix.

    The real user prompt is prose (a table-dimensions note, when present) plus
    a trailing ``{"items": [...]}`` JSON - locate the JSON like the real model
    must, instead of assuming the whole prompt is pure JSON.
    """
    idx = prompt.index('{"items"')
    data = json.loads(prompt[idx:])
    out = []
    for it in data["items"]:
        if it["type"] == "text":
            out.append({"id": it["id"], "type": "text", "text": f"EDIT:{it['text']}"})
        else:
            out.append(
                {"id": it["id"], "type": "table", "cells": [list(r) for r in it["cells"]]}
            )
    return json.dumps({"items": out})


def test_structured_output_order_one_to_one_no_table_split(monkeypatch):
    items = (
        [_text_item(i, f"text {i}") for i in range(20)]
        + [_table_item(99, 4, 3, fill="cell")]
        + [_text_item(100, "tail")]
    )
    monkeypatch.setattr(
        "app.services.document_edit._generate_edits_array", _echo_generate
    )
    out = _request_edits_structured(items, "translate to russian")
    # One output per input, same order.
    assert len(out) == len(items)
    assert [o["id"] for o in out] == [it["id"] for it in items]
    # Table stayed one item with its exact dimensions.
    table_out = [o for o in out if o["type"] == "table"][0]
    assert table_out["id"] == 99
    assert len(table_out["cells"]) == 4
    assert all(len(r) == 3 for r in table_out["cells"])
    # Text items were edited (one-to-one).
    assert all(o["text"].startswith("EDIT:") for o in out if o["type"] == "text")


def test_small_document_single_chunk(monkeypatch):
    items = [_text_item(i, f"para {i}") for i in range(5)]
    monkeypatch.setattr(
        "app.services.document_edit._generate_edits_array", _echo_generate
    )
    out = _request_edits_structured(items, "translate")
    assert len(out) == 5
    assert [o["id"] for o in out] == [0, 1, 2, 3, 4]
    assert all(o["text"].startswith("EDIT:") for o in out)
