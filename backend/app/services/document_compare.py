"""Compare two user documents — any pair, or versions of the same document.

``documents.source_file_id`` already records the immutable original an edited
file was derived from, so the "versions" of a document are exactly the chain
reached by following ``source_file_id`` to its root. This module provides:

* ``document_versions`` — walk that chain (root -> current).
* ``compare_documents`` — line-level diff between any two owned documents,
  plus a bounded summary and the changed lines themselves, so both the API
  (side-by-side UI) and the agent tool (compact JSON) can consume it.

Diffing is done on the extracted text (``Document.content``), never on the
original binary files. Ownership is enforced for every lookup, so another
user's documents can never be compared or enumerated.
"""

import difflib
import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.document import Document

logger = logging.getLogger("app.document_compare")

# The model can only be given a bounded view of a change.
MAX_COMPARE_LINES = 4000
MAX_MODEL_CHANGED_LINES = 12


def split_lines(text: str | None) -> list[str]:
    """Split extracted text into lines, normalising CRLF/CR to LF."""
    if not text:
        return []
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _opcodes(left_lines: list[str], right_lines: list[str]) -> list[dict]:
    """Line-level diff as structured opcode ranges (like difflib.opcodes)."""
    matcher = difflib.SequenceMatcher(
        a=left_lines, b=right_lines, autojunk=False
    )
    ops: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        kind = {
            "equal": "equal",
            "delete": "delete",
            "insert": "insert",
            "replace": "replace",
        }.get(tag, tag)
        ops.append(
            {
                "kind": kind,
                "left_start": i1,
                "left_end": i2,
                "right_start": j1,
                "right_end": j2,
            }
        )
    return ops


def _summary(ops: list[dict]) -> dict:
    added = removed = changed = unchanged = 0
    for op in ops:
        if op["kind"] == "equal":
            unchanged += op["left_end"] - op["left_start"]
        elif op["kind"] == "insert":
            added += op["right_end"] - op["right_start"]
        elif op["kind"] == "delete":
            removed += op["left_end"] - op["left_start"]
        elif op["kind"] == "replace":
            # A replaced block is both removed (its left lines) and added
            # (its right lines); the block itself is counted as "changed".
            removed += op["left_end"] - op["left_start"]
            added += op["right_end"] - op["right_start"]
            changed += 1
    return {
        "added_lines": added,
        "removed_lines": removed,
        "changed_lines": changed,
        "unchanged_lines": unchanged,
    }


def compute_diff(left_text: str | None, right_text: str | None) -> dict:
    """Return a bounded, side-by-side-ready diff between two extracted texts.

    Line arrays are capped (``MAX_COMPARE_LINES`` per side); when a side was
    capped, ``truncated`` is true so the UI can tell the user the diff covers
    only the first N lines.
    """
    left_lines = split_lines(left_text)
    right_lines = split_lines(right_text)

    truncated = len(left_lines) > MAX_COMPARE_LINES or len(right_lines) > MAX_COMPARE_LINES
    left_lines = left_lines[:MAX_COMPARE_LINES]
    right_lines = right_lines[:MAX_COMPARE_LINES]

    ops = _opcodes(left_lines, right_lines)
    return {
        "left_lines": left_lines,
        "right_lines": right_lines,
        "operations": ops,
        "summary": _summary(ops),
        "equal": ops == []
        or all(op["kind"] == "equal" for op in ops),
        "truncated": truncated,
        "limit": MAX_COMPARE_LINES,
    }


def _ref(document: Document) -> dict:
    return {
        "id": document.id,
        "original_filename": document.original_filename,
        "file_type": document.file_type,
        "content_length": document.content_length,
        "created_at": document.created_at.isoformat()
        if document.created_at
        else None,
        "source_file_id": document.source_file_id,
    }


def _get_owned(document_id: int, user_id: int, db: Session) -> Document:
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == user_id)
        .first()
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    return document


def document_versions(document_id: int, user_id: int, db: Session) -> list[Document]:
    """Return the version chain of a document, root (oldest) first.

    Follows ``source_file_id`` to its root, then walks back to the requested
    document. A plain upload (no ``source_file_id``) yields a single version.
    """
    current = _get_owned(document_id, user_id, db)

    chain = [current]
    seen = {current.id}
    node = current
    while node.source_file_id is not None and node.source_file_id not in seen:
        node = _get_owned(node.source_file_id, user_id, db)
        chain.append(node)
        seen.add(node.id)

    chain.reverse()
    return chain


def compare_documents(
    left_id: int, right_id: int, user_id: int, db: Session
) -> dict:
    """Diff two owned documents, returning refs + a bounded diff payload."""
    left = _get_owned(left_id, user_id, db)
    right = _get_owned(right_id, user_id, db)

    diff = compute_diff(left.content, right.content)
    return {
        "left": _ref(left),
        "right": _ref(right),
        **diff,
    }


def model_summary(result: dict, *, max_changed_lines: int = MAX_MODEL_CHANGED_LINES) -> dict:
    """Compact, model-safe view of a compare result (used by the agent tool).

    Never hands the model the full text: only the counts and a few changed
    lines so the agent can describe *what* changed without reproducing files.
    """
    ops = result.get("operations") or []
    changed: list[dict] = []
    for op in ops:
        if op["kind"] == "equal":
            continue
        left_lines = result["left_lines"][op["left_start"] : op["left_end"]]
        right_lines = result["right_lines"][op["right_start"] : op["right_end"]]
        changed.append(
            {
                "kind": op["kind"],
                "left": left_lines[:max_changed_lines],
                "right": right_lines[:max_changed_lines],
            }
        )
        if len(changed) >= 10:
            break

    return {
        "left": result.get("left"),
        "right": result.get("right"),
        "equal": result.get("equal"),
        "truncated": result.get("truncated"),
        "summary": result.get("summary"),
        "changed_blocks": changed,
    }
