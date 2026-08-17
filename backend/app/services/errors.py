"""Typed errors for the document-generation pipeline.

These make it possible to tell apart the distinct failure modes the agent and
the API must report differently:

* ``DocumentSpecError``  - the structured spec rejected validation (bad shape).
* ``RendererError``      - the renderer produced an invalid / unverifiable file.
* ``DocumentSaveError``  - the bytes could not be written to storage.
* ``DocumentRegistrationError`` - the Document row could not be persisted.

LLM/GigaChat failures stay as ``GeminiError`` (raised by the gemini client) and
generic agent-level failures as plain ``Exception``. Splitting them lets the
agent return a precise ``error_type`` instead of a generic message.
"""


class DocumentError(Exception):
    """Base class for document-pipeline errors."""


class DocumentSpecError(DocumentError):
    """The requested document did not pass DocumentSpec validation."""


class RendererError(DocumentError):
    """Rendering or post-render validation of the generated file failed."""


class DocumentSaveError(DocumentError):
    """The rendered document could not be written to the upload store."""


class DocumentRegistrationError(DocumentError):
    """The generated Document row could not be committed to the database."""


class DocumentEditError(DocumentError):
    """Editing an existing file failed before any copy was written."""
