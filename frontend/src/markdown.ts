import { Marked } from "marked";
import DOMPurify from "dompurify";

const markdown = new Marked({ gfm: true, breaks: false });

// The backend stores the raw markdown source, so raw HTML can reach the
// viewer. DOMPurify strips anything executable and we harden a couple of
// attributes on top of its defaults.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "IMG") {
    const src = node.getAttribute("src") ?? "";
    const protocolRelative = /^(?:https?:)?\/\//i.test(src);
    // Any other scheme (javascript:, data: that is not an image, ...) is
    // dropped as well. Only relative paths, root-relative refs and inline
    // data:image URIs survive, so rendering never fires external requests.
    const otherScheme =
      /^[a-z][a-z0-9+.-]*:/i.test(src) && !/^data:image\//i.test(src);
    if (protocolRelative || otherScheme) node.removeAttribute("src");
  } else if (node.tagName === "A") {
    node.setAttribute("rel", "noopener noreferrer");
    node.setAttribute("target", "_blank");
  }
});

/** Render markdown source to sanitized HTML. */
export function renderMarkdown(source: string): string {
  const html = markdown.parse(source, { async: false });
  return DOMPurify.sanitize(html, { FORBID_ATTR: ["style"] });
}
