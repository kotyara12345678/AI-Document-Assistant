import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../markdown";

describe("renderMarkdown", () => {
  it("renders headings, paragraphs, lists, links, bold/italic", () => {
    const html = renderMarkdown(
      "# Title\n\nSome **bold** and *italic* text.\n\n- one\n- two\n\n1. first\n2. second"
    );
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<em>italic</em>");
    expect(html).toContain("<ul>");
    expect(html).toContain("<ol>");
  });

  it("renders inline code and fenced code blocks", () => {
    const html = renderMarkdown("Use `const x = 1` here.\n\n```js\nconst y = 2;\n```");
    expect(html).toContain("<code>const x = 1</code>");
    expect(html).toContain("<pre><code class=\"language-js\">");
    expect(html).toContain("const y = 2;");
  });

  it("renders blockquotes, horizontal rules and tables", () => {
    const html = renderMarkdown(
      "> quote here\n\n---\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    );
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<hr");
    expect(html).toContain("<table>");
    expect(html).toContain("<th>");
    expect(html).toContain("<td>");
  });

  it("adds target=_blank and rel to links", () => {
    const html = renderMarkdown("[link](https://example.com)");
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("strips scripts, event handlers and javascript: URLs", () => {
    const html = renderMarkdown(
      '<script>alert(1)</script>\n\n<img src="x" onerror="alert(1)">\n\n[bad](javascript:alert(1))'
    );
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("onload");
    expect(html).not.toContain("javascript:");
    expect(html).toContain('<img src="x">');
    expect(html).not.toContain("[bad]");
  });

  it("strips style attributes but keeps safe markup", () => {
    const html = renderMarkdown('<p style="position:fixed">text</p>');
    expect(html).not.toContain("style=");
    expect(html).toContain("<p>text</p>");
  });

  it("blocks external images but keeps relative and data:image sources", () => {
    const html = renderMarkdown(
      "![remote](https://evil.example/x.png)\n\n![rel](./local.png)\n\n![inline](data:image/png;base64,AAAA)"
    );
    expect(html).not.toContain("https://evil.example");
    expect(html).toContain("./local.png");
    expect(html).toContain("data:image/png;base64,AAAA");
  });
});
