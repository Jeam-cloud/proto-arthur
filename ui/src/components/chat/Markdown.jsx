// Markdown renderer with two custom pieces:
//
// CodeBlock — copy button + language label (tracker p3t9 "syntax highlight"
// via rehype-highlight, which is highlight.js under the hood).
//
// SvgPreview — design mode (p3t12) has the model emit ```svg fences. Raw
// model SVG is untrusted markup: an <svg> can carry <script> and event
// handlers. DOMPurify with the svg profile strips those, and ONLY the
// sanitized result is injected. react-markdown itself never renders raw HTML
// (no rehype-raw plugin) — this preview is the single, deliberate exception.
import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import DOMPurify from "dompurify";
import { Copy, Check, Eye, Code } from "lucide-react";

function CodeBlock({ language, value }) {
  const [copied, setCopied] = useState(false);
  const [showPreview, setShowPreview] = useState(language === "svg");
  const isSvg = language === "svg" || value.trimStart().startsWith("<svg");

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="codeblock">
      <div className="codeblock-bar">
        <span>{language || "text"}</span>
        <span style={{ display: "flex", gap: 10 }}>
          {isSvg && (
            <button onClick={() => setShowPreview(!showPreview)}>
              {showPreview ? <><Code size={12} /> code</> : <><Eye size={12} /> preview</>}
            </button>
          )}
          <button onClick={copy}>
            {copied ? <><Check size={12} /> copied</> : <><Copy size={12} /> copy</>}
          </button>
        </span>
      </div>
      {isSvg && showPreview ? (
        <div
          className="svg-preview"
          dangerouslySetInnerHTML={{
            __html: DOMPurify.sanitize(value, { USE_PROFILES: { svg: true, svgFilters: true } }),
          }}
        />
      ) : (
        <pre><code className={language ? `language-${language}` : ""}>{value}</code></pre>
      )}
    </div>
  );
}

export default function Markdown({ children }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        // links open in the system browser via Electron's window-open handler
        a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        pre: ({ children: pre }) => {
          const code = pre?.props?.children;
          const lang = (pre?.props?.className || "").replace("hljs language-", "").replace("language-", "");
          if (typeof code === "string") return <CodeBlock language={lang} value={code} />;
          return <CodeBlock language={lang} value={extractText(code)} />;
        },
      }}
    >
      {children}
    </ReactMarkdown>
  );
}

function extractText(node) {
  if (node == null) return "";
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (node.props && node.props.children) return extractText(node.props.children);
  return "";
}
