"""Agent layer: the LLM decides when to search, read and create documents.

GigaChat is exposed to four tools via native function calling:

* ``search_documents(query)`` — hybrid retrieval over the user's library;
* ``read_document(document_id, offset)`` — bounded reads of a found document;
* ``list_documents()`` — enumerate ALL of the user's documents (authoritative
  source for "which files do I have" questions);
* ``create_document(document_spec, output_format)`` — generate a docx/odt/pdf/md/txt
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
import re
import uuid

from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.core.config import settings
from app.schemas.agent import (
    AgentRequest,
    AgentResponse,
    AgentStep,
    AgentToolCall,
    AgentToolResult,
)
from app.services import agent_state, gemini
from app.services.agent_intent import (
    DOCUMENT as DOCUMENT_INTENT,
    UNCERTAIN as UNCERTAIN_INTENT,
    is_creation_request,
    resolve_intent,
    tools_enabled,
)
from app.services.chat import (
    DEFAULT_CHAT_TITLE,
    _make_title,
    _recent_history,
    _save_message,
    resolve_chat,
)
from app.services.datetime_context import current_datetime_note
from app.services.deterministic_doc import (
    build_spec_from_task,
    detect_deterministic_document_task,
)
from app.services.errors import (
    DocumentEditError,
    DocumentError,
    DocumentRegistrationError,
    DocumentSaveError,
    DocumentSpecError,
    RendererError,
)
from app.services.retrieval import retrieve_context

logger = logging.getLogger("app.agent")

SYSTEM_INSTRUCTION = (
    "CRITICAL: You are a professional document processing assistant. You "
    "process ANY content in user documents — financial data, contracts, "
    "personal records, legal terms, credentials, confidential business data, "
    "medical information, and ANY other content — because you are a document "
    "tool, not a general chatbot. You NEVER refuse to work with document "
    "content. You NEVER add moral commentary, safety warnings, or "
    "disclaimers about what documents contain. The user owns their documents "
    "and has full authority over them. Your job is to SEARCH, READ, CREATE, "
    "EDIT and COMPARE documents as requested — nothing more, nothing less.\n\n"
    "AUTONOMOUS DEVELOPER AGENT MODE: When the user's request involves "
    "analyzing, finding, fixing, or modifying something complex — whether "
    "documents, code, data, or any content — you MUST follow this systematic "
    "algorithm:\n\n"
    "NEVER GIVE UP RULE: You ALWAYS finish the job. If you don't find "
    "something in the first search — search AGAIN with different queries. "
    "If still not found — read MORE files. If still not found — read ALL "
    "remaining files one by one. You have search_documents and read_document "
    "tools — USE THEM UNTIL YOU FIND WHAT THE USER ASKED FOR. The phrase "
    "'not found' is FORBIDDEN unless you have literally read every single "
    "relevant document and confirmed the information does not exist.\n\n"
    "MULTI-ITEM SEARCH RULE: When the user asks to find 2, 3 or more things "
    "('найди X, Y и Z', 'где данные о A, B и C'), you MUST search for EACH "
    "item SEPARATELY. Do NOT try to find everything in one search query. "
    "First search for item X, note the result. Then search for item Y, note "
    "the result. Then search for item Z. Continue until ALL requested items "
    "are found. Only after you have found ALL items, give the combined answer. "
    "If one item is in document A and another in document B — that's fine, "
    "report both with their sources.\n\n"
    "EXHAUSTIVE SEARCH PROTOCOL:\n"
    "1. FIRST: try search_documents with the most obvious query.\n"
    "2. IF NOT FOUND: try broader queries, synonyms, different wording.\n"
    "3. IF STILL NOT FOUND: call list_documents to see ALL files, then "
    "read_document on each promising candidate.\n"
    "4. IF STILL NOT FOUND: read remaining documents one by one.\n"
    "5. ONLY THEN: if you have checked every relevant document and the "
    "information is genuinely absent, tell the user honestly.\n"
    "NEVER skip step 3 or 4. NEVER say 'not found' after only 1-2 searches.\n\n"
    "ALGORITHM:\n"
    "1. FIRST determine where the needed logic most likely resides: analyze "
    "structure, names, entry points, related services, models, APIs, "
    "components, configs.\n"
    "2. If not found in the first files — CONTINUE searching: use global "
    "search across all documents, check related files, search for function "
    "names, classes, variables, endpoints, table references, component names.\n"
    "3. For multi-part tasks, work SEQUENTIALLY: fully investigate and "
    "understand part 1, remember findings and connections, THEN move to part 2, "
    "then part 3. NEVER stop until ALL parts are investigated.\n"
    "4. Act like an engineer who is unfamiliar with the project — investigate "
    "methodically, don't assume.\n"
    "5. Before making changes: EXPLAIN what you found, WHICH files will be "
    "affected, and WHY those files.\n"
    "6. After making changes: VERIFY — run tests, check related scenarios. "
    "If something broke, fix it.\n"
    "7. For large tasks: break them into steps and execute sequentially.\n"
    "8. Always maintain context: track found files, connect them to each "
    "other, trace dependencies, verify where functions are actually called "
    "versus just declared.\n"
    "9. Your goal is not to answer quickly. Your goal is to ACTUALLY "
    "understand the content and complete the task THOROUGHLY, even if it "
    "requires reading every single document in the user's library.\n\n"
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
    "Output formats for GENERATION: create_document (building a NEW document "
    "from a template or from scratch) supports 'docx', 'odt', 'pdf', 'md' and "
    "'txt'. When "
    "the user asks to GENERATE a brand-new PDF ('создай PDF-договор', 'сформируй "
    "файл в PDF'), call create_document with output_format 'pdf'. When the "
    "user asks for a Markdown file ('создай MD', 'сформируй файл в формате "
    "Markdown') use output_format 'md'; for a plain text file ('создай TXT', "
    "'сделай текстовый файл') use 'txt'. EDITING an "
    "existing document never changes its format: edit_document preserves the "
    "format of the file you edit — including PDF — and returns a NEW file in "
    "that same format (editing a PDF yields a PDF, keeping images and layout "
    "as far as the format allows). So whenever the user wants to translate, "
    "rewrite or otherwise modify an uploaded document and keep or request PDF, "
    "use edit_document — never create_document and never suggest docx/odt "
    "instead. "
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
    "User: «Перечисли мне список всех моих файлов» -> list_documents -> "
    "enumerate every returned file. "
    "User: «Создай с нуля PDF-договор» (GENERATE a brand-new PDF) -> create_document "
    "with output_format 'pdf'. User: «переведи этот PDF на русский» or "
    "«отредактируй PDF и верни в PDF» -> edit_document (keeps PDF, returns a new PDF, "
    "original untouched). "
    "Proactive retrieval: any question about a specific person, employee, "
    "project, amount, date or fact that could live in the user's files is "
    "presumed to need retrieval — search even if the user never says 'find' or "
    "'document'; never answer such questions from memory. "
    "DOCUMENT LISTING RULE: when the user asks to list, enumerate, count or "
    "name their files or documents ('перечисли все мои файлы', 'покажи список "
    "документов', 'сколько у меня файлов', 'какие документы у меня есть', "
    "'имена моих файлов'), this is a filesystem/document-listing request: you "
    "MUST call list_documents FIRST. The tool result is the ONLY source of "
    "truth about the set of the user's documents: enumerate EVERY document the "
    "tool returns (id, name, type, date) and never skip one. Never compose the "
    "list from conversation memory or from documents mentioned earlier in the "
    "chat, and never use search_documents/RAG results for listing (search "
    "returns only the most relevant subset). Never invent documents the tool "
    "did not return and never claim there are other documents besides the "
    "tool's result: if list_documents returns N documents, the user has "
    "exactly N. "
    "Metadata: when you answer, use the metadata the tools return (file name, "
    "type, dates, sizes) and never invent metadata; mention it only when it "
    "directly helps the answer. "
    "COMPARE: when the user asks to compare documents, versions, or to see "
    "what changed ('сравни два документа', 'в чём разница между этими "
    "файлами', 'что изменилось после редактирования', 'покажи отличия'), "
    "you MUST call compare_documents with the document_id of each file. "
    "Resolve the ids with list_documents / search_documents / read_document "
    "if the user only names the files; never invent a document_id. Describe "
    "the differences ONLY from the tool result — never fabricate added, "
    "removed or changed content that the tool did not return."
    "EDITING EXISTING FILES: when the user wants to CHANGE an existing uploaded "
    "file ('сделай этот документ понятнее', 'перепиши коротким русским языком', "
    "'удали упоминания X из этого файла', 'переведи этот документ'), you MUST "
    "call edit_document with that file's document_id and a clear editing "
    "instruction. edit_document copies the file and edits only the copy, so the "
    "original is preserved and a brand-new file is returned — IN THE SAME FORMAT "
    "as the source (editing a PDF returns a PDF; images and layout are preserved "
    "as far as technically possible). This is NOT the same as create_document, "
    "which builds a new document from a template or from other documents' data. "
    "If the user explicitly asks for the edited result as PDF, edit the PDF "
    "document with edit_document and return that PDF — do NOT switch to "
    "create_document and do NOT offer docx/odt. If the user names a file but you "
    "do not know its document_id, first call search_documents (or read_document) "
    "to resolve it; never invent a document_id. If you still cannot identify the "
    "file, ask the user which file to edit before calling edit_document. When a "
    "document is attached via ATTACHED CONTEXT, you already have its document_id "
    "there — use it directly as file_id and NEVER ask the user for the id. "
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
    "CONFIRMATION QUALITY: after create_document succeeds, your one-line "
    "confirmation MUST be based on the tool result, not on your own guess: "
    "cite the REAL file name and format it returned (e.g. 'Документ "
    "«Трудовой договор.docx» создан в формате DOCX и сохранён в вашей "
    "библиотеке.'). Never invent a file name, format or size that the tool "
    "did not return; never claim a download link you cannot provide. After "
    "edit_document succeeds, say that a NEW file was created, name it and its "
    "format from the result, and state that the original document is "
    "unchanged (e.g. 'Создан новый файл «…» — исходный документ не "
    "изменён.'). If the tool result contains errors or unfilled placeholders, "
    "do not claim success: summarize exactly what is missing and ask the "
    "user for it. "
    "ANTI-FABRICATION (critical): never claim that you called a tool, that a "
    "tool returned data, that a document contains something, or that a file "
    "was created, edited or is downloadable, unless the actual tool result in "
    "THIS conversation proves it. If a tool was not called, failed, or "
    "returned nothing useful, say so honestly and ask the user — never invent "
    "tool calls, tool results, document contents, file names, document ids or "
    "download links. A download link must come only from the download_url the "
    "tool actually returned; a created/edited file must come only from a "
    "success: true tool result with a real document_id. "
    "INTENT GATE: before choosing any tool, decide whether this message is "
    "about the user's documents at all. Pure greetings ('привет', "
    "'здравствуйте', 'добрый день'), politeness ('спасибо', 'пожалуйста', "
    "'понятно'), small talk ('как дела?'), questions about the assistant "
    "itself ('что ты умеешь?', 'расскажи, на что ты способен') and "
    "general-knowledge questions are answered directly in plain text — do "
    "NOT search, read, list, create, edit or compare anything for them. "
    "Document tools exist ONLY for requests about the user's files, facts "
    "that live in those files, or file creation/edit/compare/list actions. "
    "Never call a document tool for a message that carries no such signal. "
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


LIST_FUNCTION = {
    "name": "list_documents",
    "description": (
        "List ALL documents the user has uploaded or generated, with their id, "
        "file name, file type and creation date. Call this whenever the user "
        "asks to list, enumerate, count or name their files/documents "
        "('перечисли все мои файлы', 'покажи список документов', 'сколько у "
        "меня файлов', 'какие документы у меня есть', 'имена моих файлов'). "
        "The result is the ONLY source of truth about which documents exist: "
        "enumerate every document it returns. Do NOT use search_documents for "
        "listing — it only returns the most relevant subset and must not be "
        "used to answer listing questions. Always scoped to the current "
        "user's library."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


CREATE_FUNCTION = {
    "name": "create_document",
    "description": (
        "Create a NEW document as a real, downloadable file saved to the "
        "user's library (.docx, .odt, .pdf, .md or .txt). REQUIRED whenever the user asks "
        "to create, generate, prepare or form a document or file — 'создай "
        "документ', 'сгенерируй', 'сделай договор', 'подготовь файл', "
        "'сформируй документ', 'создай docx/odt/pdf/md/txt', 'сделай любой документ'. "
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
        "accepts 'docx', 'odt', 'pdf', 'md' or 'txt'; match it to what the "
        "user asked for (use 'pdf' when they asked for a PDF, 'md' for "
        "Markdown, 'txt' for plain text). The created "
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
                "description": "Target file format: 'docx', 'odt', 'pdf', 'md' or 'txt'.",
            },
        },
        "required": ["title", "content", "output_format"],
    },
}


EDIT_FUNCTION = {
    "name": "edit_document",
    "description": (
        "Edit an ALREADY-UPLOADED document file owned by the user and save the "
        "result as a NEW file. The original file is NEVER modified — a copy is "
        "made and only the copy is changed. Use this when the user wants to "
        "rewrite, simplify, translate, rephrase or clean up an existing file "
        "they uploaded, e.g. 'сделай этот документ понятнее', 'перепиши этот "
        "PDF коротким русским языком', 'удали все упоминания ADA MaX из этого "
        "документа'. This is NOT generation from a template or from other "
        "documents' data — for that use create_document. Pass the exact "
        "document_id of the file to edit (resolve it with search_documents / "
        "read_document if the user only names the file) and a clear editing "
        "instruction in Russian. Supported formats: docx, odt, pdf, txt, md. "
        "Never pass a path; only a document_id the backend has authorised. If the "
        "target file is attached via ATTACHED CONTEXT, its document_id is already "
        "given there — pass it directly as file_id and do not ask the user for it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "integer",
                "description": (
                    "Id of the existing document to edit (from search_documents / "
                    "read_document results). The original file is never modified."
                ),
            },
            "instruction": {
                "type": "string",
                "description": (
                    "What to change in the document, e.g. 'сделай текст понятнее и "
                    "короче' or 'удали все упоминания ADA MaX'."
                ),
            },
        },
        "required": ["file_id", "instruction"],
    },
}


COMPARE_FUNCTION = {
    "name": "compare_documents",
    "description": (
        "Compare two of the user's documents and describe what changed. Use "
        "this whenever the user asks to compare documents or versions, find "
        "differences, or see what was changed in an edited file — e.g. "
        "'сравни два документа', 'в чём разница между этими файлами', "
        "'что изменилось после редактирования', 'покажи отличия версий'. "
        "Pass the exact document_id of each file (resolve them with "
        "list_documents / search_documents / read_document if the user only "
        "names the files). Returns the file names, an equal flag, a summary of "
        "added/removed/changed/unchanged lines, and a few changed blocks. "
        "Answer the user in their language based ONLY on what the tool "
        "returns — never invent differences that are not in the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "left_id": {
                "type": "integer",
                "description": (
                    "document_id of the first document to compare (from "
                    "list_documents / search_documents results)."
                ),
            },
            "right_id": {
                "type": "integer",
                "description": (
                    "document_id of the second document to compare (from "
                    "list_documents / search_documents results)."
                ),
            },
        },
        "required": ["left_id", "right_id"],
    },
}


class AgentService:
    """Owns the tool registry and runs the minimal agent loop."""

    def functions_spec(self) -> list[dict]:
        return [
            SEARCH_FUNCTION,
            READ_FUNCTION,
            LIST_FUNCTION,
            CREATE_FUNCTION,
            EDIT_FUNCTION,
            COMPARE_FUNCTION,
        ]

    def run_agent(
        self,
        request: AgentRequest,
        user_id: int,
        db: Session | None = None,
    ) -> AgentResponse:
        """Non-streaming wrapper around :meth:`run_agent_stream`.

        Implemented on top of the streaming generator so memory, task state and
        event ordering are identical to the ``/api/agent/stream`` endpoint.
        """
        sink: dict = {
            "calls": [],
            "results": [],
            "answer": "",
            "chat_id": 0,
            "sources": [],
            "agent_steps": [],
            "created_documents": [],
        }
        for _ in self.run_agent_stream(request, user_id, db=db, sink=sink):
            pass
        return AgentResponse(
            answer=sink["answer"],
            tool_calls=sink["calls"],
            tool_results=sink["results"],
            agent_steps=sink["agent_steps"],
            chat_id=sink["chat_id"],
            sources=sink["sources"],
            created_documents=sink["created_documents"],
        )

    def run_agent_stream(
        self,
        request: AgentRequest,
        user_id: int,
        db: Session | None = None,
        sink: dict | None = None,
    ):
        """Run one agent turn, yielding realtime, model-safe event dicts.

        Emits events of type ``agent_step`` (status ``running``/``completed``/
        ``error``), ``document_created`` and ``final``. When ``sink`` is given it
        is filled with the structured ``AgentResponse`` fields so the
        non-streaming ``run_agent`` can reuse the exact same logic.

        The agent is context-aware: it restores the prior conversation history
        and the structured task/document state from the database, runs the tool
        loop, and persists the updated state so a later turn (or a backend
        restart) resumes the same task.
        """
        document_ids = _document_filter(request)

        own_db = False
        if db is None:
            from app.database.session import SessionLocal

            db = SessionLocal()
            own_db = True
        try:
            chat = resolve_chat(db, user_id, request.chat_id)
            chat_id = chat.id
            user_msg = _save_message(
                db,
                user_id,
                chat_id,
                "user",
                request.question,
                context_document_ids=request.context_document_ids,
            )

            # Name the chat after the first question so it never stays "Новый чат".
            if chat.title == DEFAULT_CHAT_TITLE:
                chat.title = _make_title(request.question)
                db.commit()

            # --- Explicit (pinned) context: hard override of target resolution
            # When exactly one document is attached and the request is an
            # edit/translate/modify of "this document", the target is already
            # known. Edit it directly — no search_documents, no read of another
            # file, no clarifying question, and never a PDF refusal. This is the
            # strongest guarantee that explicit context > RAG.
            explicit_ids = list(request.context_document_ids or [])
            if len(explicit_ids) == 1 and _is_edit_intent(request.question):
                yield from self._run_explicit_context_edit(
                    explicit_ids[0], request, user_id, db, chat_id, sink
                )
                return

            # --- Deterministic shortcuts: skip the LLM entirely --------------
            # Some requests are fully determined by their wording (repeat N
            # times, empty doc, save-this-text). We build the spec in code and
            # render directly, so they work even when GigaChat is unavailable
            # and always honour exact repetition counts. Architecture is
            # unchanged: still spec -> renderer -> .docx/.odt -> save/register.
            task = detect_deterministic_document_task(request.question)
            if task is not None:
                yield from self._run_deterministic(task, user_id, chat_id, sink)
                assistant_msg = _save_message(
                    db, user_id, chat_id, "assistant", sink.get("answer", "")
                )
                created = sink.get("created_documents") or []
                if created:
                    assistant_msg.document_id = created[-1]["document_id"]
                    db.commit()
                agent_state.save_state(db, user_id, chat_id, {})
                yield {
                    "type": "final",
                    "content": sink.get("answer", ""),
                    "chat_id": chat_id,
                    "sources": [],
                }
                return

            # --- Short-term memory: restore task state + document context -----
            state = agent_state.load_state(db, user_id, chat_id)
            # Build the note from the *already persisted* state (before recording
            # the current request), so a brand-new chat stays clean and only real
            # prior context is injected.
            context_note = agent_state.build_context_note(state)
            # Hard-attach the documents the user explicitly selected in the UI so
            # the agent uses them directly instead of re-searching the library.
            explicit_ctx = _build_explicit_context_note(request, db, user_id)
            if explicit_ctx:
                context_note = (
                    f"{context_note}\n\n{explicit_ctx}".strip()
                    if context_note
                    else explicit_ctx
                )
            # GigaChat's native `functions` API accepts ONLY ONE system message;
            # a second one returns HTTP 422 ("system message must be the first
            # message"). Merge the note into the single system message instead of
            # appending a second system role.
            system_content = SYSTEM_INSTRUCTION
            if context_note:
                system_content = f"{SYSTEM_INSTRUCTION}\n\n{context_note}"
            # The model cannot know "today" on its own: inject the real current
            # date/time so date fields in generated documents are filled with the
            # actual date instead of a guessed/hallucinated one.
            system_content = f"{system_content}\n\n{current_datetime_note()}"
            messages: list[dict] = [{"role": "system", "content": system_content}]
            state.setdefault("task", {})["user_request"] = request.question

            # --- Short-term memory: recent conversation turns ----------------
            history = _recent_history(db, chat_id, before_id=user_msg.id)
            messages.extend(history)

            # Current user turn (the system/history above are context only).
            messages.append({"role": "user", "content": request.question})

            # INTENT GATE: a message with no real document signal (a greeting,
            # thanks, small talk, a question about this assistant, pure general
            # knowledge) must never touch the document tools. We simply omit the
            # ``functions`` payload for such a turn — an LLM cannot call a tool
            # it was not given — so «привет» can never accidentally fire
            # search_documents/create_document/edit_document and end up as
            # "Файл не был создан: данных для подготовки документа не хватает".
            allow_tools = tools_enabled(
                request.question,
                has_document_context=_has_active_document_context(
                    request, state, document_ids
                ),
            )

            calls: list[AgentToolCall] = []
            results: list[AgentToolResult] = []
            agent_steps: list[AgentStep] = []
            functions_state_id: str | None = None
            answer = ""

            # Token accounting: every LLM call in the loop/verdict contributes
            # to one UsageLog row persisted when the turn finishes.
            tokens_acc: list[int] = []

            def _usage_hook(t: int) -> None:
                tokens_acc.append(t)

            try:
                for _ in range(max(1, settings.AGENT_MAX_TOOL_ROUNDS)):
                    message, functions_state_id = gemini.chat_with_functions(
                        messages,
                        functions=self.functions_spec() if allow_tools else None,
                        functions_state_id=functions_state_id,
                        usage_hook=_usage_hook,
                    )
                    call = message.get("function_call")
                    if not call:
                        answer = (message.get("content") or "").strip()
                        break

                    name = call.get("name", "")
                    arguments = _parse_arguments(call.get("arguments"))
                    step_id = uuid.uuid4().hex

                    # Running event: emitted BEFORE the tool runs so the UI can
                    # show the step immediately (realtime streaming).
                    running_msg = self._format_step_message(state, name, arguments)
                    yield {
                        "type": "agent_step",
                        "step_id": step_id,
                        "status": "running",
                        "tool": name,
                        "message": running_msg,
                    }

                    try:
                        # When the user pinned documents as context, use the pinned
                        # target directly instead of asking for an id. This makes a
                        # single attached document the unambiguous file_id for
                        # edit_document, and resolves a named file among several.
                        if name == "edit_document":
                            resolved = _resolve_explicit_file_id(
                                request, arguments, db, user_id
                            )
                            if resolved is not None:
                                arguments = {**arguments, "file_id": resolved}
                        content = self._execute_tool(
                            name, arguments, user_id, document_ids, chat_id=chat_id,
                            request=request, db=db,
                        )
                    except Exception:
                        logger.exception("Agent tool %s failed", name)
                        content = json.dumps(
                            {"error": f"{name} failed"}, ensure_ascii=False
                        )

                    self._update_state_from_tool(state, name, arguments, content)

                    is_error = ('"error"' in content) or ('"success": false' in content)
                    final_status = "error" if is_error else "completed"
                    yield {
                        "type": "agent_step",
                        "step_id": step_id,
                        "status": final_status,
                        "tool": name,
                        "message": self._format_step_message(state, name, arguments),
                    }
                    agent_steps.append(
                        AgentStep(
                            step_id=step_id,
                            tool=name,
                            message=self._format_step_message(state, name, arguments),
                            status=final_status,
                        )
                    )

                    if name in ("create_document", "edit_document"):
                        try:
                            payload = json.loads(content)
                        except ValueError:
                            payload = {}
                        if payload.get("success"):
                            yield {
                                "type": "document_created",
                                "document_id": payload["document_id"],
                                "filename": payload["filename"],
                                "download_url": (
                                    f"{settings.API_PREFIX}/documents/"
                                    f"{payload['document_id']}/file"
                                ),
                            }

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
                        message, _ = gemini.chat_with_functions(
                            messages, usage_hook=_usage_hook
                        )
                        answer = (message.get("content") or "").strip()
                    except gemini.GeminiError:
                        logger.exception("GigaChat failed on the final agent turn")
                        answer = "[GigaChat unavailable] Please try again later."
            except gemini.GeminiError:
                # Honest degradation: never surface a crash when the model is down.
                logger.exception("GigaChat failed during the agent loop")
                answer = "[GigaChat unavailable] Please try again later."

            # CONFIRMATION QUALITY fallback: if the model returned an empty
            # final answer but a document was actually created/edited, surface a
            # factual confirmation from the tool result instead of an empty box.
            if not answer.strip():
                factual = _derive_tool_confirmation(results)
                if factual:
                    answer = factual

            # ANTI-FABRICATION: never let the model's prose claim a created
            # file or a download link that no real tool result backs up.
            answer = _sanitize_final_answer(answer, results, request.question)

            assistant_msg = _save_message(db, user_id, chat_id, "assistant", answer)
            # Persist task/document state so the next turn (or a restart) resumes.
            agent_state.save_state(db, user_id, chat_id, state)

            # Link the assistant message to the file it produced so the file
            # card can be restored from the database after a page reload.
            created_documents = _derive_created_documents(results)
            if created_documents:
                assistant_msg.document_id = created_documents[-1]["document_id"]
                db.commit()

            sources = _derive_sources(results)
            created_documents = _derive_created_documents(results)
            if sink is not None:
                sink["calls"] = calls
                sink["results"] = results
                sink["answer"] = answer
                sink["chat_id"] = chat_id
                sink["sources"] = sources
                sink["agent_steps"] = agent_steps
                sink["created_documents"] = created_documents

            yield {
                "type": "final",
                "content": answer,
                "chat_id": chat_id,
                "sources": sources,
            }
        finally:
            try:
                from app.services.usage_log import record_tokens

                record_tokens(db, user_id, sum(tokens_acc))
            except NameError:
                # tokens_acc is bound only after the tool loop starts (i.e. the
                # pre-loop early returns for explicit-context/deterministic edits
                # never reach the LLM loop); nothing to account for then.
                pass
            if own_db:
                db.close()

    @staticmethod
    def _format_step_message(state: dict, name: str, arguments: dict) -> str:
        """Human-readable, model-safe label for one tool call (action log only)."""
        a = arguments or {}
        if name == "search_documents":
            return f"Поиск по документам: {a.get('query', '')}"
        if name == "read_document":
            doc_id = a.get("document_id")
            name_hint = ""
            for doc in state.get("documents") or []:
                if doc.get("id") == doc_id:
                    name_hint = f" ({doc.get('name')})"
                    break
            return f"Чтение документа #{doc_id}{name_hint}"
        if name == "list_documents":
            return "Получение списка документов"
        if name == "create_document":
            return f"Создание документа ({a.get('output_format', 'docx')})"
        if name == "compare_documents":
            return f"Сравнение документов #{a.get('left_id')} и #{a.get('right_id')}"
        return f"Действие: {name}"

    def _update_state_from_tool(
        self, state: dict, name: str, arguments: dict, content: str
    ) -> None:
        """Fold a tool result into the persisted task/document state."""
        try:
            data = json.loads(content)
        except ValueError:
            return

        if name == "search_documents" and isinstance(data, list):
            state.setdefault("task", {})["retrieval_completed"] = True
            for hit in data:
                if not isinstance(hit, dict):
                    continue
                did = hit.get("document_id")
                if did is None:
                    continue
                meta = {
                    k: hit.get(k)
                    for k in ("type", "file_size", "content_length", "created_at", "owner_id")
                    if hit.get(k) is not None
                }
                agent_state.remember_document(
                    state, did, hit.get("filename", ""),
                    doc_type=hit.get("type"), metadata=meta, read=False,
                )
                agent_state.remember_source(
                    state, did, hit.get("filename", ""), hit.get("score", 0)
                )
        elif name == "read_document" and isinstance(data, dict) and "document_id" in data:
            state.setdefault("task", {})["documents_read"] = True
            meta = {
                k: data.get(k)
                for k in ("file_type", "file_size", "content_length", "created_at", "owner_id")
                if data.get(k) is not None
            }
            agent_state.remember_document(
                state, data["document_id"], data.get("filename", ""),
                doc_type=data.get("file_type"), metadata=meta, read=True,
            )
        elif name == "create_document" and isinstance(data, dict):
            task = state.setdefault("task", {})
            task["generation_requested"] = True
            if data.get("success"):
                task["document_created"] = True
                task["status"] = "completed"
                task["created_document_id"] = data.get("document_id")
                agent_state.remember_document(
                    state, data["document_id"], data.get("filename", ""),
                    doc_type=data.get("file_type"), read=False,
                )
        elif name == "compare_documents" and isinstance(data, dict):
            state.setdefault("task", {})["comparison_done"] = True
            for ref in (data.get("left"), data.get("right")):
                if not isinstance(ref, dict) or not ref.get("id"):
                    continue
                agent_state.remember_document(
                    state, ref["id"], ref.get("original_filename", ""),
                    doc_type=ref.get("file_type"), read=True,
                )

    def _execute_tool(
        self,
        name: str,
        arguments: dict,
        user_id: int,
        document_ids: list[int] | None,
        chat_id: int | None = None,
        *,
        request: AgentRequest | None = None,
        db: Session | None = None,
    ) -> str:
        """Run one tool and return a compact JSON string for the model.

        Tool failures never crash the loop: the model receives an ``error``
        object and can answer honestly based on it.

        When the user pinned documents (``request.context_document_ids``), the
        model is NOT allowed to target a different file: ``read_document`` and
        ``edit_document`` are forced onto the pinned set, so RAG can never be
        used to (re)discover the target document.
        """
        explicit = list(getattr(request, "context_document_ids", None) or [])

        if name == "create_document":
            try:
                from app.services.document_quality import is_template_request

                template_mode = is_template_request(
                    getattr(request, "question", None) or ""
                )
                return json.dumps(
                    self._create_document(
                        arguments,
                        user_id,
                        chat_id=chat_id,
                        template_mode=template_mode,
                    ),
                    ensure_ascii=False,
                )
            except Exception:
                logger.exception("Agent tool create_document failed")
                return json.dumps(
                    {
                        "success": False,
                        "error_type": "DocumentError",
                        "error": "failed to create the document",
                    },
                    ensure_ascii=False,
                )

        if name == "edit_document":
            # Force the target onto the pinned document(s).
            if explicit:
                resolved = _resolve_explicit_file_id(request, arguments, db, user_id)
                if resolved is not None:
                    arguments = {**arguments, "file_id": resolved}
                elif len(explicit) > 1:
                    return json.dumps(
                        {
                            "success": False,
                            "error_type": "DocumentEditError",
                            "error": (
                                "edit_document must target one of the attached "
                                f"documents: {explicit}"
                            ),
                        },
                        ensure_ascii=False,
                    )
            try:
                return json.dumps(
                    self._edit_document(arguments, user_id, chat_id=chat_id, db=db),
                    ensure_ascii=False,
                )
            except Exception:
                logger.exception("Agent tool edit_document failed")
                return json.dumps(
                    {
                        "success": False,
                        "error_type": "DocumentEditError",
                        "error": "failed to edit the document",
                    },
                    ensure_ascii=False,
                )

        if name == "read_document":
            if explicit:
                arg_id = arguments.get("document_id")
                if len(explicit) == 1:
                    # Single pinned document is the only allowed target.
                    arguments = {**arguments, "document_id": explicit[0]}
                elif arg_id not in explicit:
                    # Several pinned: only those ids may be read.
                    return json.dumps(
                        {
                            "error": (
                                "read_document must use an attached document_id; "
                                f"attached documents: {explicit}"
                            )
                        },
                        ensure_ascii=False,
                    )
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

        if name == "list_documents":
            try:
                return json.dumps(
                    self._list_documents(user_id), ensure_ascii=False
                )
            except Exception as exc:
                logger.exception("Agent tool list_documents failed")
                return json.dumps(
                    {"error": f"list failed: {exc}"}, ensure_ascii=False
                )

        if name == "compare_documents":
            try:
                return json.dumps(
                    self._compare_documents(arguments, user_id, db=db),
                    ensure_ascii=False,
                )
            except Exception as exc:
                logger.exception("Agent tool compare_documents failed")
                return json.dumps(
                    {"error": f"compare failed: {exc}"}, ensure_ascii=False
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
            "owner_id": document.user_id,
            "file_type": document.file_type,
            "file_size": document.file_size,
            "content_length": total,
            # ``pages`` is not tracked by the extractor; reported as unknown
            # rather than fabricated. ``indexed``/``content_available`` reflect
            # what the backend actually knows.
            "pages": None,
            "indexed": True,
            "content_available": bool(text),
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "offset": offset,
            "length": len(window),
            "truncated": offset + len(window) < total,
            "text": window,
        }

    def _list_documents(self, user_id: int) -> list[dict]:
        """Return a compact listing of ALL of the user's documents.

        This is the authoritative answer for "which files do I have" questions:
        every row of the user in the documents table is returned (id, name,
        type, size, dates) with the same field shape the search/read tools use,
        so the model maps names to document_ids without re-searching. Unlike
        ``search_documents`` (relevance-ranked, top-k only), this never omits a
        document and never consults RAG or conversation memory.
        """
        from app.database.session import SessionLocal
        from app.services.documents import list_documents

        db = SessionLocal()
        try:
            rows = list_documents(user_id, db)
            return [
                {
                    "document_id": doc.id,
                    "filename": doc.original_filename,
                    "type": doc.file_type,
                    "file_size": doc.file_size,
                    "content_length": doc.content_length,
                    "owner_id": doc.user_id,
                    "content_available": bool(doc.content),
                    "indexed": True,
                    "pages": None,
                    "created_at": (
                        doc.created_at.isoformat() if doc.created_at else None
                    ),
                }
                for doc in rows
            ]
        finally:
            db.close()

    def _compare_documents(
        self,
        arguments: dict,
        user_id: int,
        *,
        db: Session | None = None,
    ) -> dict:
        """Compare two of the user's documents and return a compact summary.

        Ownership is enforced by the service itself (``Document.user_id ==
        user``), so ids pointing at another user's document produce the same
        safe ``error`` result and never leak whether a document exists. The
        model receives only the bounded ``model_summary`` payload — never the
        full document text.
        """
        try:
            left_id = int(arguments.get("left_id"))
            right_id = int(arguments.get("right_id"))
        except (TypeError, ValueError):
            return {
                "error": "compare_documents requires numeric left_id and right_id"
            }

        from app.services import document_compare as compare_service

        own_db = False
        if db is None:
            from app.database.session import SessionLocal

            db = SessionLocal()
            own_db = True
        try:
            try:
                result = compare_service.compare_documents(
                    left_id=left_id, right_id=right_id, user_id=user_id, db=db
                )
            except HTTPException as exc:
                return {"error": exc.detail}
            return compare_service.model_summary(result)
        finally:
            if own_db:
                db.close()

    def _create_document(
        self,
        arguments: dict,
        user_id: int,
        *,
        chat_id: int | None = None,
        template_mode: bool = False,
    ) -> dict:
        """Create a document file from the model's structured spec.

        The LLM only produces a validated DocumentSpec — never raw DOCX/ODT.
        ``user_id`` always comes from the request context and any user_id in
        the tool arguments is ignored. Every failure path returns a safe,
        structured ``{"success": false, "error": ...}`` object.

        ``template_mode`` marks template requests (``по шаблону``/``по образцу``):
        they legitimately keep placeholder slots, so unfilled fields are not
        reported as a quality problem. In normal mode a generated document that
        still contains critical placeholders is returned *with* a ``warning``
        and ``placeholders`` list, so the agent warns the user instead of
        claiming the document is fully ready.
        """
        output_format = str(arguments.get("output_format") or "").strip().lower()
        if output_format not in ("docx", "odt", "pdf", "md", "txt"):
            return {
                "success": False,
                "error": f"unsupported output format: {output_format!r} (supported: docx, odt, pdf, md, txt)",
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

        # --- Validation gate: never render a "finished" document that still
        # carries critical placeholders. When the user asked for a ready
        # document, unfilled fields ({{...}}, [дата], [НЕ УКАЗАНО], TODO) mean
        # the spec is not complete — return a warning instead of silently
        # producing an unfinished file. Template requests are exempt.
        from app.services.document_quality import find_placeholders
        from app.services.document_renderer import spec_to_text

        placeholders = []
        if not template_mode:
            placeholders = find_placeholders(spec_to_text(spec))

        if placeholders:
            return {
                "success": False,
                "error_type": "DocumentIncompleteError",
                "error": (
                    "the generated document still contains unfilled fields: "
                    f"{', '.join(placeholders[:8])}. Fill these fields with real "
                    "values (use the current date, real names/amounts) and retry — "
                    "or tell the user which fields are missing so they can supply "
                    "the data."
                ),
                "placeholders": placeholders,
            }

        try:
            document = generate_document(
                spec, output_format, user_id, chat_id=chat_id
            )
        except (
            DocumentSpecError,
            RendererError,
            DocumentSaveError,
            DocumentRegistrationError,
            DocumentError,
        ) as exc:
            logger.exception("Agent tool create_document failed (typed error)")
            return {
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        except Exception:
            logger.exception("Agent tool create_document failed to render/save")
            return {
                "success": False,
                "error_type": "DocumentError",
                "error": "failed to create the document file",
            }

        return {
            "success": True,
            "document_id": document.id,
            "filename": document.original_filename,
            "file_type": document.file_type,
            "file_size": document.file_size,
            "download_url": f"{settings.API_PREFIX}/documents/{document.id}/file",
            # Factual, ready-to-cite confirmation: the model must echo this
            # data (real file name, format) instead of inventing a summary.
            "confirmation": (
                f"Документ «{document.original_filename}» создан "
                f"в формате {document.file_type.upper()} и сохранён в вашей "
                "библиотеке — его можно скачать."
            ),
        }

    def _edit_document(
        self,
        arguments: dict,
        user_id: int,
        *,
        chat_id: int | None = None,
        db: Session | None = None,
    ) -> dict:
        """Edit an existing document: copy it, apply LLM text edits, save a new file.

        The model only supplies the ``file_id`` (resolved from search/read) and a
        natural-language ``instruction`` — the actual binary change is performed by
        ``app.services.document_edit``. The original file is never written to.
        """
        try:
            file_id = int(arguments.get("file_id"))
        except (TypeError, ValueError):
            return {
                "success": False,
                "error_type": "DocumentEditError",
                "error": "edit_document requires a numeric file_id",
            }

        instruction = str(arguments.get("instruction") or "").strip()
        if not instruction:
            return {
                "success": False,
                "error_type": "DocumentEditError",
                "error": "edit_document requires a non-empty instruction",
            }

        from app.services.document_edit import edit_document

        try:
            result = edit_document(file_id, instruction, user_id, db, chat_id=chat_id)
        except DocumentEditError as exc:
            logger.exception("Agent tool edit_document failed (typed error)")
            return {
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        except Exception as exc:
            logger.exception("Agent tool edit_document failed")
            return {
                "success": False,
                "error_type": "DocumentEditError",
                "error": f"failed to edit the document: {exc}",
            }

        # Factual confirmation the model must cite: a NEW file was created and
        # the original document is untouched.
        return {
            **result,
            "download_url": f"{settings.API_PREFIX}/documents/{result['document_id']}/file",
            "confirmation": (
                f"Создан новый файл «{result['filename']}» "
                f"(формат {result['file_type'].upper()}). Исходный документ не изменён."
            ),
        }

    def _run_deterministic(
        self,
        task: "object",
        user_id: int,
        chat_id: int,
        sink: dict | None,
    ):
        """Render a deterministic task WITHOUT calling GigaChat.

        Builds the spec in code, validates it, renders to a real file via the
        normal pipeline and emits the same ``agent_step`` / ``document_created``
        / ``final`` events the LLM-driven path would. Any typed error from the
        render/save/register steps is reported honestly with an ``error_type``.
        """
        step_id = uuid.uuid4().hex
        step_msg = f"Создание документа ({getattr(task, 'output_format', 'docx')})"

        from app.services.generation import generate_document

        try:
            spec = build_spec_from_task(task)  # re-validates the spec
            document = generate_document(
                spec, task.output_format, user_id, chat_id=chat_id
            )
        except (
            DocumentSpecError,
            RendererError,
            DocumentSaveError,
            DocumentRegistrationError,
            DocumentError,
        ) as exc:
            logger.warning("Deterministic document generation failed: %s", exc)
            content = json.dumps(
                {
                    "success": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
            yield {
                "type": "agent_step",
                "step_id": step_id,
                "status": "error",
                "tool": "create_document",
                "message": step_msg,
            }
            answer = (
                "Не удалось создать документ. Причина: "
                f"{type(exc).__name__}: {exc}"
            )
            if sink is not None:
                sink["calls"] = [
                    AgentToolCall(
                        name="create_document",
                        arguments={"output_format": task.output_format, "deterministic": True},
                    )
                ]
                sink["results"] = [
                    AgentToolResult(
                        tool_call_id="create_document",
                        name="create_document",
                        content=content,
                    )
                ]
                sink["answer"] = answer
                sink["chat_id"] = chat_id
                sink["sources"] = []
                sink["agent_steps"] = [
                    AgentStep(
                        step_id=step_id,
                        tool="create_document",
                        message=step_msg,
                        status="error",
                    )
                ]
                sink["created_documents"] = []
            return

        content = json.dumps(
            {
                "success": True,
                "document_id": document.id,
                "filename": document.original_filename,
                "file_type": document.file_type,
                "file_size": document.file_size,
                "deterministic": True,
            },
            ensure_ascii=False,
        )
        yield {
            "type": "agent_step",
            "step_id": step_id,
            "status": "completed",
            "tool": "create_document",
            "message": step_msg,
        }
        yield {
            "type": "document_created",
            "document_id": document.id,
            "filename": document.original_filename,
            "download_url": (
                f"{settings.API_PREFIX}/documents/{document.id}/file"
            ),
        }
        answer = f"Документ «{document.original_filename}» создан."
        if sink is not None:
            sink["calls"] = [
                AgentToolCall(
                    name="create_document",
                    arguments={"output_format": task.output_format, "deterministic": True},
                )
            ]
            sink["results"] = [
                AgentToolResult(
                    tool_call_id="create_document",
                    name="create_document",
                    content=content,
                )
            ]
            sink["answer"] = answer
            sink["chat_id"] = chat_id
            sink["sources"] = []
            sink["agent_steps"] = [
                AgentStep(
                    step_id=step_id,
                    tool="create_document",
                    message=step_msg,
                    status="completed",
                )
            ]
            sink["created_documents"] = [
                {
                    "document_id": document.id,
                    "filename": document.original_filename,
                    "file_type": document.file_type,
                }
            ]

    def _run_explicit_context_edit(
        self,
        pinned_id: int,
        request: AgentRequest,
        user_id: int,
        db: Session,
        chat_id: int,
        sink: dict | None,
    ):
        """Edit the single pinned document directly — explicit context > RAG.

        No ``search_documents`` and no ``read_document`` of any other file: the
        pinned document is the target by definition. Emits the same
        ``agent_step`` / ``document_created`` / ``final`` events as the LLM path,
        but the only document ever touched is ``pinned_id``.
        """
        from app.models.document import Document

        row = (
            db.query(Document.id, Document.original_filename)
            .filter(Document.id == pinned_id, Document.user_id == user_id)
            .first()
        )
        filename = row[1] if row else f"document {pinned_id}"

        read_step = uuid.uuid4().hex
        edit_step = uuid.uuid4().hex

        yield {
            "type": "agent_step",
            "step_id": read_step,
            "status": "running",
            "tool": "read_document",
            "message": f"Использован прикреплённый документ: {filename}",
        }
        yield {
            "type": "agent_step",
            "step_id": read_step,
            "status": "completed",
            "tool": "read_document",
            "message": f"Чтение документа #{pinned_id}",
        }
        yield {
            "type": "agent_step",
            "step_id": edit_step,
            "status": "running",
            "tool": "edit_document",
            "message": f"Редактирование документа #{pinned_id}",
        }

        instruction = request.question
        content = self._edit_document(
            {"file_id": pinned_id, "instruction": instruction},
            user_id,
            chat_id=chat_id,
            db=db,
        )
        payload = content if isinstance(content, dict) else json.loads(content)

        is_error = payload.get("success") is not True
        yield {
            "type": "agent_step",
            "step_id": edit_step,
            "status": "error" if is_error else "completed",
            "tool": "edit_document",
            "message": (
                "Не удалось отредактировать документ"
                if is_error
                else f"Документ #{pinned_id} отредактирован"
            ),
        }

        if not is_error and payload.get("success"):
            yield {
                "type": "document_created",
                "document_id": payload["document_id"],
                "filename": payload["filename"],
                "download_url": (
                    f"{settings.API_PREFIX}/documents/{payload['document_id']}/file"
                ),
            }
            answer = (
                f"Документ «{payload['filename']}» готов (переведён/отредактирован). "
                "Исходный файл не изменён."
            )
        else:
            answer = "Не удалось отредактировать прикреплённый документ."

        assistant_msg = _save_message(db, user_id, chat_id, "assistant", answer)
        if not is_error and payload.get("success"):
            assistant_msg.document_id = payload["document_id"]
            db.commit()
        agent_state.save_state(db, user_id, chat_id, {})

        yield {
            "type": "final",
            "content": answer,
            "chat_id": chat_id,
            "sources": [],
        }

        if sink is not None:
            sink["answer"] = answer
            sink["chat_id"] = chat_id
            sink["sources"] = []
            sink["calls"] = [
                AgentToolCall(
                    name="edit_document",
                    arguments={"file_id": pinned_id, "instruction": instruction},
                )
            ]
            sink["results"] = [
                AgentToolResult(
                    tool_call_id="edit_document",
                    name="edit_document",
                    content=json.dumps(content, ensure_ascii=False),
                )
            ]
            sink["agent_steps"] = [
                AgentStep(
                    step_id=read_step,
                    tool="read_document",
                    message=f"Чтение документа #{pinned_id}",
                    status="completed",
                ),
                AgentStep(
                    step_id=edit_step,
                    tool="edit_document",
                    message=(
                        f"Документ #{pinned_id} отредактирован"
                        if not is_error
                        else "Не удалось отредактировать документ"
                    ),
                    status="error" if is_error else "completed",
                ),
            ]
            sink["created_documents"] = (
                [
                    {
                        "document_id": payload["document_id"],
                        "filename": payload["filename"],
                        "file_type": payload.get("file_type"),
                    }
                ]
                if (not is_error and payload.get("success"))
                else []
            )

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

        Self-correction: when the query yields no hits, a few deterministic
        reformulations (strip pleading verbs, drop function words, single
        content tokens) are tried automatically instead of reporting "not
        found". A hit produced by a reformulated query is labelled with the
        ``reformulated_query`` it actually matched, so the model and the user
        see why the search still succeeded.
        """
        chunks = retrieve_context(
            question=query,
            user_id=user_id,
            document_id=document_ids,
            top_k=settings.AGENT_TOP_K,
        )

        # --- Self-correction: zero hits -> try rewritten variants ----------
        # Each hit produced by a reformulated query is labelled with the
        # variant it actually matched, so the model/user see the mapping.
        matched_variant: str | None = None
        if not chunks:
            from app.services.query_reformulation import reformulate_query

            for variant in reformulate_query(query):
                candidate = retrieve_context(
                    question=variant,
                    user_id=user_id,
                    document_id=document_ids,
                    top_k=settings.AGENT_TOP_K,
                )
                if not candidate:
                    continue
                # First variant that finds anything wins: it is the closest to
                # the user's original wording.
                chunks = candidate
                matched_variant = variant
                break

        hits: list[dict] = []
        seen: set[int] = set()
        doc_ids: list[int] = []
        for chunk in chunks:
            doc_id = chunk.source.document_id
            if doc_id in seen:
                continue
            seen.add(doc_id)
            doc_ids.append(doc_id)

        # Normalised document metadata so the model sees id/name/type/size/date
        # and can map a user's "use Doc_алексей" to a concrete document_id
        # without re-searching. Nothing is fabricated: unknown fields are null.
        from app.database.session import SessionLocal
        from app.models.document import Document

        docs_by_id: dict[int, Document] = {}
        if doc_ids:
            db = SessionLocal()
            try:
                rows = (
                    db.query(Document)
                    .filter(Document.id.in_(doc_ids), Document.user_id == user_id)
                    .all()
                )
                docs_by_id = {row.id: row for row in rows}
            finally:
                db.close()

        emitted: set[int] = set()
        for chunk in chunks:
            doc_id = chunk.source.document_id
            if doc_id in emitted:
                continue
            emitted.add(doc_id)
            doc = docs_by_id.get(doc_id)
            hit = {
                "document_id": doc_id,
                "filename": chunk.source.filename,
                "score": round(chunk.source.score, 4),
                "snippet": chunk.source.text[:SNIPPET_MAX_CHARS],
            }
            if matched_variant is not None:
                hit["reformulated_query"] = matched_variant
            if doc is not None:
                hit["type"] = doc.file_type
                hit["file_size"] = doc.file_size
                hit["content_length"] = doc.content_length
                hit["created_at"] = (
                    doc.created_at.isoformat() if doc.created_at else None
                )
                hit["owner_id"] = doc.user_id
                hit["pages"] = None
                hit["indexed"] = True
                hit["content_available"] = bool(doc.content)
            hits.append(hit)
        return hits


def _is_edit_intent(question: str) -> bool:
    """True when the user asks to change/translate/rewrite an existing document.

    Used to route a single pinned document straight to ``edit_document`` without
    the LLM first searching the library to (re)discover the target. Plain
    questions ("о чём этот документ?") and generation requests ("создай…") are
    intentionally NOT matched — only edit/translate/modify verbs.
    """
    q = (question or "").lower()
    triggers = (
        "перевед", "translate", "translation",
        "отредактиру", "редактиру", "edit",
        "перепиши", "переписать", "rewrite", "rephrase", "перефраз",
        "улучши", "улучшить", "improve",
        "сократи", "сократить", "shorten",
        "понятнее", "clearer", "clean up", "упрости", "simplify",
        "убери", "удали", "remove",
        "исправ", "исправь", "fix",
        "измени", "change", "modify", "переделай", "переработай",
        "адаптируй", "adapt",
    )
    return any(t in q for t in triggers)


def _document_filter(request: AgentRequest) -> list[int] | None:
    # Explicitly attached context wins over everything else (single-document
    # limit, multi-document limit, and RAG retrieval).
    if request.context_document_ids:
        return list(request.context_document_ids)
    if request.document_ids:
        return list(request.document_ids)
    if request.document_id is not None:
        return [request.document_id]
    return None


def _has_active_document_context(
    request: AgentRequest, state: dict, document_ids: list[int] | None
) -> bool:
    """Whether this chat already has an active document context.

    Used by the intent gate: an UNCERTAIN follow-up (e.g. "продолжай",
    "а второй пункт?") still keeps the document tools when the user pinned
    documents for this turn or a previous tool turn left discovered documents /
    a running task in the persisted agent state.
    """
    if document_ids:
        return True
    if (state.get("documents") or []) or (state.get("sources") or []):
        return True
    task = state.get("task") or {}
    return bool(
        task.get("retrieval_completed")
        or task.get("documents_read")
        or task.get("generation_requested")
        or task.get("document_created")
        or task.get("created_document_id")
    )


def _resolve_explicit_file_id(
    request: AgentRequest, arguments: dict, db: Session, user_id: int
) -> int | None:
    """Determine the target ``file_id`` for ``edit_document`` from explicit context.

    Resolution order:
      1. an explicit ``file_id`` the model already supplied;
      2. the single attached document (unambiguous target);
      3. a name match among several attached documents (the instruction names one).

    Returns ``None`` only when several documents are attached and the request does
    not name any — in that case the model may ask which file (by name).
    """
    raw = arguments.get("file_id")
    if raw not in (None, "", 0):
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass

    from app.models.document import Document

    explicit = list(request.context_document_ids or [])
    if len(explicit) == 1:
        return explicit[0]
    if len(explicit) > 1:
        instruction = " ".join(
            str(arguments.get(k) or "")
            for k in ("instruction", "content", "title", "document_spec")
        ).lower()
        rows = (
            db.query(Document.id, Document.original_filename)
            .filter(Document.id.in_(explicit), Document.user_id == user_id)
            .all()
        )
        for doc_id, filename in rows:
            name = filename.lower()
            base = name.rsplit(".", 1)[0]
            if name in instruction or base in instruction:
                return doc_id
    return None


def _build_explicit_context_note(request: AgentRequest, db: Session, user_id: int) -> str:
    """Inject the user-attached documents (UI chips) as a hard instruction.

    The agent already restricts retrieval to ``request.context_document_ids`` via
    ``_document_filter``, but we additionally surface the concrete document ids
    and filenames in the system prompt so the model uses them directly for
    reads/edits instead of re-searching the whole library. When exactly one
    document is attached it is unambiguously the target, so we tell the model to
    call ``edit_document`` with that ``file_id`` without asking the user.
    """
    ids = request.context_document_ids
    if not ids:
        return ""
    from app.models.document import Document

    rows = (
        db.query(Document.id, Document.original_filename)
        .filter(Document.id.in_(ids), Document.user_id == user_id)
        .all()
    )
    if not rows:
        return ""

    if len(rows) == 1:
        doc_id, filename = rows[0]
        return (
            "ATTACHED CONTEXT: the user explicitly attached exactly ONE document "
            f'for this turn by selecting it in the interface: document_id={doc_id} '
            f'("{filename}"). This document IS the target of the request. Use it '
            "DIRECTLY — call edit_document(file_id=<that document_id>, "
            "instruction=...) WITHOUT searching and WITHOUT asking the user for the "
            "id; the id is already given above. Editing preserves the original "
            "format (a PDF stays a PDF, keeping images and layout as far as "
            "technically possible), so a translation/edit of this PDF yields a new "
            "PDF. If the user only says 'translate/edit this document', that refers "
            "to this attached document."
        )

    lines = [
        "ATTACHED CONTEXT: the user explicitly attached the following documents for "
        "this turn by selecting them in the interface. Treat them as the "
        "authoritative source and prefer them over any vector search:",
    ]
    for doc_id, filename in rows:
        lines.append(f'- document_id={doc_id} ("{filename}")')
    lines.append(
        "When the user references a specific attached file BY NAME, use its "
        "document_id DIRECTLY as the file_id for edit_document (or document_id for "
        "read_document) — do not search and do not ask the user for the id. For "
        "'edit/translate this document' with several attached files, pick the one "
        "the user named; if none is named and the target is genuinely ambiguous, "
        "you may ask which attached file they mean — by file NAME, never by id. If "
        "the user asks to create a document from the attached files (e.g. 'fill this "
        "template with data from the other file'), use the named attached files as "
        "the template and the data source. Do not search for a different document "
        "when an attached one already satisfies the request."
    )
    return "\n".join(lines)


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


def _derive_tool_confirmation(results: list[AgentToolResult]) -> str:
    """Factual confirmation string for the most recent create/edit tool result.

    Reads the ``confirmation`` key the tools return (real file name, format,
    download url / original-unchanged note) so a model that answers with an
    empty or unusable message still leaves the user with a correct summary.
    Returns "" when no successful create/edit result carries a confirmation.
    """
    for result in reversed(results):
        if result.name not in ("create_document", "edit_document"):
            continue
        try:
            payload = json.loads(result.content)
        except ValueError:
            continue
        if not payload.get("success"):
            continue
        confirmation = str(payload.get("confirmation") or "").strip()
        if confirmation:
            return confirmation
    return ""


_DOWNLOAD_URL_RE = re.compile(
    rf"{re.escape(settings.API_PREFIX)}/documents/(\d+)/file"
)


def _successful_file_ids(results: list[AgentToolResult]) -> set[int]:
    """document_ids that were REALLY created/edited successfully this turn."""
    ids: set[int] = set()
    for result in results:
        if result.name not in ("create_document", "edit_document"):
            continue
        try:
            payload = json.loads(result.content)
        except ValueError:
            continue
        if payload.get("success") and payload.get("document_id") is not None:
            try:
                ids.add(int(payload["document_id"]))
            except (TypeError, ValueError):
                continue
    return ids


_SUCCESS_CLAIMS = (
    "создан",
    "сгенерирован",
    "сформирован",
    "готов",
    "доступен",
    "скачайте",
    "скачать",
    "сохранён",
    "файл создан",
    "pdf создан",
    "файл готов",
)
_NEGATION_HINTS = (
    "не создан",
    "не удалось",
    "не смог",
    "невозможно",
    "недоступен",
    "не доступен",
    "не готов",
    "не сгенерирован",
    "не сформирован",
    "не получилось",
)


def _looks_like_success_claim(text: str) -> bool:
    low = text.lower()
    if any(hint in low for hint in _NEGATION_HINTS):
        return False
    return any(word in low for word in _SUCCESS_CLAIMS)


def _honest_creation_failure(
    results: list[AgentToolResult], question: str = ""
) -> str:
    """An honest final answer when no file was really created this turn.

    Prefers the real tool error when create/edit actually ran and failed. When
    no create/edit ran but a search did and found nothing relevant, say so —
    the user's document set simply has no data for the request, and the honest
    answer must reflect that instead of blaming a tool that was never called.
    """
    for result in reversed(results):
        if result.name not in ("create_document", "edit_document"):
            continue
        try:
            payload = json.loads(result.content)
        except ValueError:
            continue
        if not payload.get("success"):
            error = payload.get("error") or "недостаточно данных"
            return (
                "Файл не создан: "
                + str(error)
                + ". Я не выдумываю данные — укажите недостающие сведения, "
                "и я подготовлю документ."
            )

    # A search ran but returned nothing: tell the user the document set lacks
    # the data, never hint that the tools were skipped.
    for result in reversed(results):
        if result.name != "search_documents":
            continue
        try:
            hits = json.loads(result.content)
        except ValueError:
            continue
        if isinstance(hits, list) and not hits:
            topic = ""
            if question:
                topic = (
                    f' по запросу «{question.strip()[:120]}»'
                    if len(question.strip()) <= 120
                    else ""
                )
            return (
                "Я поискал в ваших документах"
                + topic
                + ", но нужных данных не нашёл. Файл не создан: "
                "недостающие сведения не выдумываются. Укажите участников, "
                "задачи, сроки, ответственных, финансовые показатели и "
                "текущий статус — или загрузите документы проекта, и я "
                "подготовлю PDF-отчёт."
            )

    return (
        "Файл не был создан: данных для подготовки документа не хватает. "
        "Недостающие сведения не выдумываются — укажите их, и я подготовлю "
        "документ."
    )


def _sanitize_final_answer(
    answer: str, results: list[AgentToolResult], question: str = ""
) -> str:
    """Anti-fabrication guard for the model's free-text final answer.

    The structured fields (tool_calls / tool_results / sources /
    created_documents / document_created events) are already built ONLY from
    real tool executions. This function closes the last hole: the model's
    *prose* could still claim "PDF created, download here" when nothing was
    actually created. We strip download URLs that do not point to a real file
    created this turn, and if no file was created at all we replace a
    fabricated success claim with an honest statement.

    INTENT-GATED: the replacement is only the file-focused "was not created /
    data is missing" phrasing when the request genuinely asked for document
    creation. Greetings/small-talk (whose prose may contain words like "готов"
    meaning "ready to help") are never rewritten that way, and non-creation
    document requests get a neutral statement instead.
    """
    if not answer or not answer.strip():
        return answer
    real_ids = _successful_file_ids(results)

    def _replace_url(match: re.Match) -> str:
        doc_id = int(match.group(1))
        return match.group(0) if doc_id in real_ids else ""

    sanitized = _DOWNLOAD_URL_RE.sub(_replace_url, answer)
    if real_ids:
        # A real file exists this turn; keep the model's prose otherwise.
        return sanitized
    if _looks_like_success_claim(sanitized):
        intent = resolve_intent(question)
        if intent == DOCUMENT_INTENT and is_creation_request(question):
            # The user asked for a file that was NOT really created: replace the
            # fabricated success claim with the honest outcome.
            return _honest_creation_failure(results, question)
        if intent == UNCERTAIN_INTENT or intent == DOCUMENT_INTENT:
            # A non-creation request ("приведи в пример", "найди вариант",
            # "а что дальше?"): a "не хватает данных для подготовки документа"
            # message would be meaningless here. Say plainly that nothing was
            # created, without blaming missing document data.
            return _nothing_was_created()
        # CONVERSATIONAL intent: the model answered normal chit-chat; a claim
        # word like "готов" in "Привет! Готов помочь" must NOT be rewritten
        # into a "file was not created" message.
        return sanitized
    return sanitized


def _nothing_was_created() -> str:
    """Neutral, honest fallback for a claimed-but-never-created file when the
    request is NOT a document-creation request."""
    return (
        "Не получилось выполнить этот запрос — ничего не было создано. "
        "Уточните, пожалуйста, что нужно сделать."
    )


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
