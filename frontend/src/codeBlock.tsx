import { useState } from "react";

export interface CodeBlockResult {
  before: string;
  code: string;
  after: string;
}

/** Extract a single fenced Markdown code block from a message.
 *
 * Returns null when there is no block or more than one, so ordinary answers
 * (and messages that only quote code inline) keep rendering as plain text and
 * the agent's monospace document block is the only thing treated specially.
 *
 * Frontend-only behaviour: see goals 7-9 — the agent wraps copyable documents
 * (contracts, letters, instructions, templates, code) in exactly one fenced
 * block, containing ONLY the document content.
 */
export function extractCodeBlock(text: string): CodeBlockResult | null {
  const trimmed = text.trim();
  const fence = "```";
  const openIdx = trimmed.indexOf(fence);
  if (openIdx === -1) return null;

  const afterOpen = trimmed.slice(openIdx + fence.length);
  const nl = afterOpen.indexOf("\n");
  if (nl === -1) return null; // opening fence line has no terminator

  const body = afterOpen.slice(nl + 1);
  const closeIdx = body.indexOf(fence);
  if (closeIdx === -1) return null; // no closing fence

  let code = body.slice(0, closeIdx);
  // Drop the newline that terminates the closing fence line.
  while (code.endsWith("\n") || code.endsWith("\r")) {
    code = code.slice(0, -1);
  }
  // Reject nested/extra fences: exactly one block is allowed.
  if (code.indexOf(fence) !== -1) return null;
  const tail = body.slice(closeIdx + fence.length);
  if (tail.indexOf(fence) !== -1) return null;

  const before = trimmed.slice(0, openIdx).trim();
  const after = tail.trim();
  return { before, code, after };
}

// Inject the minimal styles once, without touching the existing stylesheet.
if (typeof document !== "undefined" && !document.getElementById("code-block-styles")) {
  const style = document.createElement("style");
  style.id = "code-block-styles";
  style.textContent = `
.code-block {
  border: 1px solid var(--border, #e2e2e6);
  border-radius: var(--radius, 10px);
  background: var(--bg-soft, #f5f5f6);
  margin: 6px 0;
  overflow: hidden;
}
.code-block__toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 4px 6px;
  border-bottom: 1px solid var(--border-soft, #ececf0);
}
.code-block__copy {
  border: 1px solid var(--border, #e2e2e6);
  background: var(--bg, #fff);
  color: var(--text, #1d1d1f);
  border-radius: 6px;
  padding: 2px 10px;
  font-size: 12px;
  cursor: pointer;
}
.code-block__copy:hover { background: var(--bg-soft, #f5f5f6); }
.code-block__pre {
  margin: 0;
  padding: 10px 12px;
  max-height: 60vh;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
}
.code-block__note {
  margin: 4px 0;
}
`;
  document.head.appendChild(style);
}

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy execCommand path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export default function CopyableBlock({ result }: { result: CodeBlockResult }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    if (await copyText(result.code)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }
  };
  return (
    <>
      {result.before && <p className="code-block__note">{result.before}</p>}
      <div className="code-block">
        <div className="code-block__toolbar">
          <button type="button" className="code-block__copy" onClick={onCopy}>
            {copied ? "Скопировано" : "Копировать"}
          </button>
        </div>
        <pre className="code-block__pre"><code>{result.code}</code></pre>
      </div>
      {result.after && <p className="code-block__note">{result.after}</p>}
    </>
  );
}
