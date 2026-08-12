# AI Agent and Document Generation

## Current behavior

ADA's AI agent can decide whether a request requires document retrieval and can perform multiple retrieval steps before generating an answer.

Typical document-dependent flow:

```text
User request
    ↓
Agent decision
    ↓
search_documents
    ↓
read_document
    ↓
additional search/read steps when required
    ↓
GigaChat generation
```

The agent should search proactively when the answer may depend on uploaded documents, even when the user does not explicitly say "find" or "in the document".

If the available documents do not contain enough information, the agent must not invent document facts.

## Document generation

For requests that require a real generated file, the agent uses `create_document`.

```text
User request
    ↓
search_documents
    ↓
read_document
    ↓
GigaChat generates document content
    ↓
create_document
    ↓
DOCX saved
    ↓
API returns document information
    ↓
Frontend provides download
```

`create_document` is a real tool call. The agent must not claim that a file was created merely by writing a "Создание документа" step in its response.

The generated DOCX must contain the complete document content, not only the document title. This includes headings, sections, paragraphs, lists, requisites, signatures, and other content produced from the source documents/template.

## Source grounding

When a document is generated from uploaded documents, use only information retrieved from those documents when the task requires document-grounded data.

Do not invent:

- names;
- dates;
- addresses;
- passport or banking details;
- company requisites;
- legal/document facts;
- missing template fields.

If required information is absent, leave the field empty or explicitly indicate that it was not found instead of filling it from model knowledge.

## Frontend agent steps

The frontend may show service steps as gray text so the user can see the agent's progress, for example:

```text
Поиск по документам: «шаблон трудового договора»
Чтение документа #791
Поиск по документам: «Doc_алексей»
Чтение документа #793
Создание документа (docx)
```

These steps should represent actual tool/runtime events where possible. They must not be inferred solely from the final LLM response.

For copyable generated text, the frontend can render the document content in a dedicated monospace block with a copy button. The code block should contain only the document content, without agent commentary, sources, retrieval metadata, or explanations.

## DOCX validation

A generated `.docx` is a ZIP/XML document format, so opening it in a plain text editor such as Notepad does not provide a meaningful representation of its contents. Validation should instead open the DOCX as a document or inspect its XML parts.

Regression tests for document generation should verify:

1. `create_document` is actually invoked;
2. a real DOCX file is created;
3. the generated file contains the full document body, not just its title;
4. Russian/Unicode text survives the DOCX generation pipeline;
5. the API exposes the generated document for download;
6. the frontend can download the generated file.

## Example scenario

For a request such as:

> Используй `Doc_алексей` и составь трудовой договор по шаблону.

The expected behavior is:

```text
search_documents("шаблон трудового договора")
        ↓
read_document(template)
        ↓
search_documents("Doc_алексей")
        ↓
read_document(source document)
        ↓
GigaChat generates the complete contract
        ↓
create_document(full contract content)
        ↓
DOCX download is available
```

A successful response must correspond to an actually created file. A message saying that the document was created is not sufficient evidence by itself.

## Testing notes

The agent regression suite should cover both:

- prompt/policy behavior (proactive retrieval, honest no-data behavior, metadata handling, and response formatting);
- behavioral execution (real retrieval sequence, real `create_document` call, persisted DOCX, and successful download).

CI uses a GigaChat mock for deterministic E2E testing, while live/manual validation can be used to verify the real provider integration.
