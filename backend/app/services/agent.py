"""Agent layer: the LLM decides when to search, read and create documents.

GigaChat is exposed to three tools via native function calling:

* ``search_documents(query)`` — hybrid retrieval over the user's library;
* ``read_document(document_id, offset)`` — bounded reads of a found document;
* ``create_document(document_spec, output_format)`` — generate a docx/odt
  file from a structured, validated spec.

The agent loop is deliberately small:

    user question -> GigaChat -> (tool calls) -> execute tools with the
    existing services -> GigaChat -> final answer

All tools reuse existing pipelines and are always scoped to the authenticated
user, so a user can never search, read or create documents outside their own
library. ``create_document`` never trusts data from the model: the spec is
validated by Pydantic (with size limits), the output format is whitelisted,
the file is generated with python-docx/odfpy (no LLM-generated markup is
executed) and the owner is always taken from the request context.
"""

import json
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.schemas.agent import (
    AgentRequest,
    AgentResponse,
    AgentToolCall,
    AgentToolResult,
)
from app.services import gemini
from app.services.retrieval import retrieve_context

logger = logging.getLogger("app.agent")

SYSTEM_INSTRUCTION = (
    "You are a document agent for the user's uploaded documents. "
    "Your documents are your only source of facts about the user. "
    "Decide between six cases: (1) plain answer, (2) search for information, "
    "(3) read a document, (4) create a document, (5) create a document from "
    "existing documents, (6) unsupported format. "
    "RULE: whenever the user asks to CREATE, GENERATE, PREPARE or FORM a "
    "document or file — 'создай документ', 'сгенерируй документ', 'сделай "
    "договор', 'подготовь файл', 'сформируй документ', 'создай DOCX', "
    "'создай ODT', 'сделай любой документ' — you MUST call create_document. "
    "Do not answer with plain text when the user asked for a file. "
    "When the request references the user's files ('по шаблону', 'используя "
    "данные из файла с данными трудового договора', 'данные из Doc_алексей'), "
    "you MUST first search: derive a search query from the description and "
    "call search_documents. Never wait for an exact file name, document id "
    "or extension, and never claim that data is missing before you have "
    "actually searched, and never claim a referenced document is unavailable "
    "just because its exact name is not in the message. If a search returns "
    "nothing or the wrong documents, run additional searches with different, "
    "broader queries. When search_documents returns several candidates, "
    "compare their file names and excerpts, pick the most relevant one(s), "
    "call read_document, then call create_document with the read content. "
    "Random data: when the user says 'используя рандомные данные', 'используй "
    "любые данные' or 'сделай пример', they allow demo data — use clearly "
    "fictional data (ООО «Альфа», Иванов Иван Иванович, инженер) and mark "
    "the document as a demo/example; do NOT ask the user for real data and do "
    "NOT use real personal data. "
    "Any document: for 'сгенерируй любой документ' with no format specified, "
    "choose 'docx' by default and create a small demo document (a title plus "
    "a few paragraphs); do not ask clarifying questions; after creating, tell "
    "the user the document is ready. "
    "Unsupported format: create_document supports only 'docx' and 'odt'. If "
    "the user asks for another format such as PDF, do NOT call create_document "
    "with an unsupported format — briefly say that format is not supported "
    "yet and offer docx or odt. "
    "Distinguish answer vs create: 'расскажи, что такое трудовой договор' is "
    "a plain answer; 'сгенерируй трудовой договор' is create_document; "
    "'сгенерируй трудовой договор по данным из документа' is search -> read "
    "-> create; 'найди в договоре зарплату Сергея' is search -> read -> "
    "answer. "
    "Template workflow: when the user asks to create a document from a "
    "template ('по шаблону', 'по образцу', 'готовый'), find and read the "
    "template FIRST, understand its structure — title, headings, numbered "
    "sections, paragraphs, lists, tables, and the places that require "
    "employee/employer information, dates, amounts, requisites, signatures — "
    "then find and read the data documents, map the read values onto the "
    "template's places, and build document_spec that PRESERVES the template "
    "structure with the data substituted in. Do not invent a document "
    "structure from scratch when a template exists. "
    "Data mapping: match fields across different wording — e.g. 'ФИО' in a "
    "data file maps to 'Работник' in a contract template, 'Дата приема' to "
    "'Дата начала работы'. "
    "Placeholders: templates may use markers like {{EMPLOYEE_FULL_NAME}}, "
    "{{EMPLOYEE_POSITION}}, {{SALARY}}, {{EMPLOYER_NAME}}, {{CONTRACT_DATE}}. "
    "Fill each with the value read from the data documents. If a value is "
    "missing and the field is critical, keep an explicit placeholder such as "
    "'[НЕ УКАЗАНО]' or ask the user — never invent the value. "
    "Never invent facts when creating a document from source documents: fill "
    "only values that are actually present in the sources. Exceptions: the "
    "user explicitly asked for random/demo data or an example, or the value "
    "is purely presentational (e.g. today's date, numbering). Only after "
    "searching and reading, if the required information is genuinely absent "
    "and the user did NOT allow random data, ask the user for it. "
    "For questions that clearly do NOT depend on the documents — greetings, "
    "small talk, general knowledge, questions about this assistant — answer "
    "directly and do NOT call any tool; tools are only for questions that may "
    "involve the user's files. When in doubt whether a question involves the "
    "documents, search. "
    "Вы — агент для работы с документами. Вы не должны ограничиваться "
    "текстовым ответом, если пользователь просит создать документ. "
    "Инструмент create_document предназначен именно для выполнения таких "
    "задач. Используйте доступные инструменты последовательно и "
    "самостоятельно. Не просите пользователя вручную предоставлять filename "
    "или document_id, если нужный документ можно найти через "
    "search_documents. Не утверждайте, что данных нет, пока не попытались "
    "найти и прочитать релевантные документы. "
    "Examples: "
    "User: «Создай любой документ» -> create_document (default docx, small demo). "
    "User: «Создай договор по шаблону трудового договора» -> search_documents "
    "-> read_document -> create_document. "
    "User: «Создай договор по шаблону и используй данные из Doc_алексей» -> "
    "search_documents -> read_document -> read_document -> create_document. "
    "User: «Создай пример договора с рандомными данными» -> create_document "
    "with fictional demo data. "
    "User: «Сколько зарплата у Сергея?» -> search_documents -> read_document "
    "-> answer. "
    "User: «Создай PDF» -> do not call create_document with PDF; state PDF is "
    "not supported yet and offer docx/odt. "
    "Proactive retrieval: any question about a specific person, employee, "
    "project, amount, date or fact that could live in the user's files is "
    "presumed to need retrieval — search even if the user never says 'find' or "
    "'document'; never answer such questions from memory. "
    "Metadata: when you answer, use the metadata the tools return (file name, "
    "type, dates, sizes) and never invent metadata; mention it only when it "
    "directly helps the answer. "
    "DOCUMENT GENERATION RULE: when the user asks to create, generate, compose, "
    "prepare or fill a document: (1) retrieve all required information from the "
    "user's documents; (2) read the relevant source documents; (3) call "
    "create_document — this tool call is REQUIRED; (4) never claim that document "
    "generation is unavailable while create_document is present in the available "
    "tools; (5) never substitute writing the document in the chat for calling "
    "create_document; (6) put the ENTIRE document into the 'content' argument of "
    "create_document as Markdown — every heading, paragraph, list and table — "
    "then call create_document, and after it succeeds reply with a ONE-LINE "
    "confirmation only (e.g. 'Документ «<title>» создан.'). "
    "OUTPUT FORMAT: the full document body travels inside the 'content' argument "
    "of create_document as Markdown (use '# Заголовок' for headings, plain "
    "paragraphs, '- item' bullet lists, '1. item' numbered lists, and "
    "'| a | b |' pipe tables with a '|---|---|' separator row), NEVER in your "
    "chat reply — so never wrap the generated document in a code block. If you "
    "send only a 'title' without 'content', the saved file will contain no "
    "content. For ordinary (non-document) answers, use plain text and never "
    "wrap them in a code block. "
)

# Short excerpt handed back to the model per matched document.
SNIPPET_MAX_CHARS = 400

# GigaChat-native function-calling specification (legacy API: ``functions``
# field, not the OpenAI ``tools`` array which this provider ignores).
SEARCH_FUNCTION = {
    "name": "search_documents",
    "description": (
        "Search the user's uploaded documents for information relevant to "
        "the query. Use this whenever a question or a reference to a document "
        "depends on the user's files, including descriptive references ('the "
        "template', 'the employee data', 'the last contract', 'the file with "
        "company details'). Documents are matched by BOTH their file name and "
        "their content, so describe what you need instead of guessing an "
        "exact name. Returns the most relevant documents with their id, file "
        "name, a relevance score and a short excerpt of the match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Natural-language query derived from what the user needs "
                    "to find. For a descriptive reference, turn the "
                    "description into a concrete search query."
                ),
            }
        },
        "required": ["query"],
    },
}

READ_FUNCTION = {
    "name": "read_document",
    "description": (
        "Read the extracted text of a document by its id. Call this after "
        "search_documents to read a found document in full and answer detailed "
        "questions about it. Returns the document's file name, type, size and "
        "the requested portion of its text. When 'truncated' is true the "
        "document is longer than the returned portion: call read_document "
        "again with a larger 'offset' (a character index) to read the next "
        "portion. Documents are always limited to the current user's library."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "integer",
                "description": (
                    "Id of the document to read (from search_documents results)."
                ),
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to start reading from. Omit for the "
                    "first portion; increase it when 'truncated' is true."
                ),
            },
        },
        "required": ["document_id"],
    },
}

CREATE_FUNCTION = {
    "name": "create_document",
    "description": (
        "Create a NEW document as a real, downloadable file saved to the "
        "user's library (.docx or .odt). REQUIRED whenever the user asks to "
        "create, generate, prepare or form a document or file — 'создай "
        "документ', 'сгенерируй', 'сделай договор', 'подготовь файл', "
        "'сформируй документ', 'создай docx/odt', 'сделай любой документ'. "
        "Call it AFTER search_documents/read_document when the user "
        "references their files; when the user allows random/demo data, use "
        "clearly fictional data instead of asking. "
        "Write the ENTIRE document body as Markdown in the 'content' argument "
        "using ordinary Markdown: '# Заголовок' for headings (one '#' per "
        "level), plain paragraphs for body text, '- item' / '* item' for "
        "bullet lists, '1. item' for numbered lists, and GitHub pipe tables "
        "(header row, a '|---|---|' separator row, then data rows) for tables. "
        "Put the real text of every section into 'content' — never leave it "
        "empty. 'title' is a short document title string. 'output_format' "
        "accepts ONLY 'docx' or 'odt'; never pass any other value. The created "
        "file can be downloaded afterwards."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": (
                    "Short document title, e.g. 'Трудовой договор'. May be "
                    "overridden by the first level-1 '#' heading in content."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "The FULL document body written in Markdown. Use '#' "
                    "headings, plain paragraphs, '-'/'*' bullet lists, '1.' "
                    "numbered lists, and '| col | col |' GitHub tables with a "
                    "'|---|---|' separator row. This must contain the entire "
                    "document text — never just the title."
                ),
            },
            "author": {
                "type": "string",
                "description": "Optional author metadata.",
            },
            "subject": {
                "type": "string",
                "description": "Optional subject metadata.",
            },
            "keywords": {
                "type": "string",
                "description": "Optional keywords metadata.",
            },
            "output_format": {
                "type": "string",
                "description": "Target file format: 'docx' or 'odt'.",
            },
        },
        "required": ["title", "content", "output_format"],
    },
}


class AgentService:
    """Owns the tool registry and runs the minimal agent loop."""

    def functions_spec(self) -> list[dict]:
        return [SEARCH_FUNCTION, READ_FUNCTION, CREATE_FUNCTION]

    def run_agent(
        self,
        request: AgentRequest,
        user_id: int,
        db: Session | None = None,
    ) -> AgentResponse:
        """Run one agent turn and return the final answer.

        The user and assistant turns are persisted to the chat referenced by
        ``request.chat_id`` (reused/created via the shared chat service) so the
        conversation shows up in the UI and survives a reload. ``db`` is the
        request-scoped session; when omitted a short-lived one is opened.
        """
        document_ids = _document_filter(request)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": request.question},
        ]
        calls: list[AgentToolCall] = []
        results: list[AgentToolResult] = []
        functions_state_id: str | None = None
        answer = ""

        own_db = False
        if db is None:
            from app.database.session import SessionLocal

            db = SessionLocal()
            own_db = True
        try:
            from app.services.chat import resolve_chat, _save_message

            chat = resolve_chat(db, user_id, request.chat_id)
            chat_id = chat.id
            _save_message(db, user_id, chat_id, "user", request.question)

            try:
                for _ in range(max(1, settings.AGENT_MAX_TOOL_ROUNDS)):
                    message, functions_state_id = gemini.chat_with_functions(
                        messages,
                        functions=self.functions_spec(),
                        functions_state_id=functions_state_id,
                    )
                    call = message.get("function_call")
                    if not call:
                        answer = (message.get("content") or "").strip()
                        break

                    name = call.get("name", "")
                    arguments = _parse_arguments(call.get("arguments"))
                    content = self._execute_tool(
                        name, arguments, user_id, document_ids
                    )

                    calls.append(AgentToolCall(name=name, arguments=arguments))
                    results.append(
                        AgentToolResult(
                            tool_call_id=name, name=name, content=content
                        )
                    )
                    messages.append(message)
                    messages.append(
                        {"role": "function", "name": name, "content": content}
                    )
                else:
                    # Bounded loop safety net: one last plain call so we answer.
                    try:
                        message, _ = gemini.chat_with_functions(messages)
                        answer = (message.get("content") or "").strip()
                    except gemini.GeminiError:
                        logger.exception("GigaChat failed on the final agent turn")
                        answer = "[GigaChat unavailable] Please try again later."
            except gemini.GeminiError:
                # Honest degradation: never surface a crash when the model is down.
                logger.exception("GigaChat failed during the agent loop")
                answer = "[GigaChat unavailable] Please try again later."

            _save_message(db, user_id, chat_id, "assistant", answer)
        finally:
            if own_db:
                db.close()

        sources = _derive_sources(results)
        created_documents = _derive_created_documents(results)
        return AgentResponse(
            answer=answer,
            tool_calls=calls,
            tool_results=results,
            chat_id=chat_id,
            sources=sources,
            created_documents=created_documents,
        )

    def _execute_tool(
        self,
        name: str,
        arguments: dict,
        user_id: int,
        document_ids: list[int] | None,
    ) -> str:
        """Run one tool and return a compact JSON string for the model.

        Tool failures never crash the loop: the model receives an ``error``
        object and can answer honestly based on it.
        """
        if name == "create_document":
            try:
                return json.dumps(
                    self._create_document(arguments, user_id),
                    ensure_ascii=False,
                )
            except Exception:
                logger.exception("Agent tool create_document failed")
                return json.dumps(
                    {"success": False, "error": "failed to create the document"},
                    ensure_ascii=False,
                )

        if name == "read_document":
            try:
                return json.dumps(
                    self._read_document(arguments, user_id),
                    ensure_ascii=False,
                )
            except Exception as exc:
                logger.exception("Agent tool read_document failed")
                return json.dumps(
                    {"error": f"read failed: {exc}"}, ensure_ascii=False
                )

        if name != "search_documents":
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

        query = str(arguments.get("query") or "").strip()
        if not query:
            return json.dumps(
                {"error": "search_documents requires a non-empty query"},
                ensure_ascii=False,
            )
        try:
            hits = self._search_documents(user_id, query, document_ids)
        except Exception as exc:
            logger.exception("Agent tool search_documents failed")
            return json.dumps(
                {"error": f"search failed: {exc}"}, ensure_ascii=False
            )
        return json.dumps(hits, ensure_ascii=False)

    def _read_document(self, arguments: dict, user_id: int) -> dict:
        """Read a bounded window of a document's extracted text.

        Ownership is enforced by the SQL query itself (``Document.user_id ==
        the authenticated user``), so a ``document_id`` pointing at another
        user's document or at a nonexistent document produces the same
        ``error`` result — a document's existence is never leaked to the model.
        """
        try:
            document_id = int(arguments.get("document_id"))
        except (TypeError, ValueError):
            return {"error": "read_document requires a numeric document_id"}

        offset = 0
        try:
            offset = max(0, int(arguments.get("offset") or 0))
        except (TypeError, ValueError):
            offset = 0

        from app.database.session import SessionLocal
        from app.models.document import Document

        db = SessionLocal()
        try:
            document = (
                db.query(Document)
                .filter(Document.id == document_id, Document.user_id == user_id)
                .first()
            )
        finally:
            db.close()

        if document is None:
            return {"error": "document not found", "document_id": document_id}

        text = document.content
        total = len(text)
        limit = settings.AGENT_READ_MAX_CHARS
        window = text[offset : offset + limit]
        return {
            "document_id": document.id,
            "filename": document.original_filename,
            "file_type": document.file_type,
            "file_size": document.file_size,
            "content_length": total,
            "offset": offset,
            "length": len(window),
            "truncated": offset + len(window) < total,
            "text": window,
        }

    def _create_document(self, arguments: dict, user_id: int) -> dict:
        """Create a document file from the model's structured spec.

        The LLM only produces a validated DocumentSpec — never raw DOCX/ODT.
        ``user_id`` always comes from the request context and any user_id in
        the tool arguments is ignored. Every failure path returns a safe,
        structured ``{"success": false, "error": ...}`` object.
        """
        output_format = str(arguments.get("output_format") or "").strip().lower()
        if output_format not in ("docx", "odt"):
            return {
                "success": False,
                "error": f"unsupported output format: {output_format!r} (supported: docx, odt)",
            }

        # Preferred path: the model emits the document body as Markdown in
        # ``content`` (reliable across function-calling models). Legacy callers
        # (and tests) may still pass a structured ``document_spec`` object.
        content = arguments.get("content")
        if content is not None:
            content = str(content)
            if not content.strip():
                return {
                    "success": False,
                    "error": "content is empty: put the whole document body in Markdown",
                }
            from app.services.markdown_spec import markdown_to_spec

            title = str(arguments.get("title") or "").strip() or None
            author = str(arguments.get("author") or "").strip() or None
            subject = str(arguments.get("subject") or "").strip() or None
            keywords = str(arguments.get("keywords") or "").strip() or None
            try:
                spec = markdown_to_spec(
                    content,
                    title=title,
                    author=author,
                    subject=subject,
                    keywords=keywords,
                )
            except Exception as exc:
                logger.exception("Failed to parse Markdown document content")
                return {
                    "success": False,
                    "error": f"invalid Markdown document content: {exc}",
                }
        else:
            raw_spec = arguments.get("document_spec")
            if isinstance(raw_spec, str):
                try:
                    raw_spec = json.loads(raw_spec)
                except ValueError:
                    return {"success": False, "error": "document_spec is not valid JSON"}
            if not isinstance(raw_spec, dict):
                return {
                    "success": False,
                    "error": (
                        "provide 'content' (Markdown) with a 'title' and "
                        "'output_format', or a structured 'document_spec' object"
                    ),
                }

            from pydantic import ValidationError

            from app.schemas.document_spec import DocumentSpec

            try:
                spec = DocumentSpec.model_validate(raw_spec)
            except ValidationError as exc:
                first = exc.errors()[0]
                loc = ".".join(str(part) for part in first["loc"])
                return {
                    "success": False,
                    "error": f"invalid document_spec: {loc}: {first['msg']}",
                }

        from app.services.generation import generate_document

        try:
            document = generate_document(spec, output_format, user_id)
        except Exception:
            logger.exception("Agent tool create_document failed to render/save")
            return {
                "success": False,
                "error": "failed to create the document file",
            }

        return {
            "success": True,
            "document_id": document.id,
            "filename": document.original_filename,
            "file_type": document.file_type,
            "file_size": document.file_size,
        }

    def _search_documents(
        self,
        user_id: int,
        query: str,
        document_ids: list[int] | None,
    ) -> list[dict]:
        """Run the shared retrieval pipeline and compact the top hits.

        The result is one entry per matched document: id, file name, relevance
        score and a short excerpt of the best-matching fragment. Duplicates
        across chunks of the same document are collapsed to the best hit.
        """
        chunks = retrieve_context(
            question=query,
            user_id=user_id,
            document_id=document_ids,
            top_k=settings.AGENT_TOP_K,
        )

        hits: list[dict] = []
        seen: set[int] = set()
        for chunk in chunks:
            doc_id = chunk.source.document_id
            if doc_id in seen:
                continue
            seen.add(doc_id)
            hits.append(
                {
                    "document_id": doc_id,
                    "filename": chunk.source.filename,
                    "score": round(chunk.source.score, 4),
                    "snippet": chunk.source.text[:SNIPPET_MAX_CHARS],
                }
            )
        return hits


def _document_filter(request: AgentRequest) -> list[int] | None:
    if request.document_ids:
        return list(request.document_ids)
    if request.document_id is not None:
        return [request.document_id]
    return None


def _derive_sources(results: list[AgentToolResult]) -> list[dict]:
    """Build a UI ``SourceRef``-shaped list from the search tool results.

    Each ``search_documents`` hit yields one source (deduped by document id);
    read/create results carry no retrievable snippet, so they are skipped.
    """
    sources: list[dict] = []
    seen: set[int] = set()
    for result in results:
        if result.name != "search_documents":
            continue
        try:
            hits = json.loads(result.content)
        except ValueError:
            continue
        if not isinstance(hits, list):
            continue
        for hit in hits:
            doc_id = hit.get("document_id")
            if doc_id in seen:
                continue
            seen.add(doc_id)
            sources.append(
                {
                    "document_id": doc_id,
                    "filename": hit.get("filename", ""),
                    "chunk_index": 0,
                    "score": hit.get("score", 0.0),
                    "text": hit.get("snippet", ""),
                }
            )
    return sources


def _derive_created_documents(results: list[AgentToolResult]) -> list[dict]:
    """Surface successfully generated documents so the UI can link them.

    Only ``create_document`` results that actually succeeded (``success:
    true`` and a ``document_id``) are returned; failed calls are skipped so the
    UI never advertises a file that does not exist.
    """
    created: list[dict] = []
    for result in results:
        if result.name != "create_document":
            continue
        try:
            payload = json.loads(result.content)
        except ValueError:
            continue
        if not payload.get("success"):
            continue
        doc_id = payload.get("document_id")
        if doc_id is None:
            continue
        created.append(
            {
                "document_id": doc_id,
                "filename": payload.get("filename", ""),
                "file_type": payload.get("file_type", ""),
            }
        )
    return created


def _parse_arguments(raw: str | dict | None) -> dict:
    """Normalize function-call arguments to a dict.

    GigaChat's native API returns ``arguments`` already parsed as an object,
    but some providers echo it as a JSON string, so both shapes are accepted.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# Single shared instance used by the route (mirrors the chat service pattern).
agent_service = AgentService()
