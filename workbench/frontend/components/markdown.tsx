"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 把 Obsidian 双链 [[目标]] / [[目标|显示名]] 转为可拦截的内部链接（显示名在管道后）。 */
function preprocessWikilinks(content: string): string {
  return content.replace(
    /\[\[([^\]\|#]+)(?:\|([^\]]*))?(?:#[^\]]*)?\]\]/g,
    (_m, title: string, display?: string) => {
      const target = title.trim();
      const label = (display ?? title).trim();
      return `[${label}](#wb-link:${encodeURIComponent(target)})`;
    }
  );
}

export function Markdown({
  content,
  onWikiLink,
  onPreviewHtml,
}: {
  content: string;
  onWikiLink?: (title: string) => void;
  onPreviewHtml?: (html: string) => void;
}) {
  return (
    <div className="md-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => {
            if (href?.startsWith("#wb-link:")) {
              const title = decodeURIComponent(href.slice("#wb-link:".length));
              return (
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    onWikiLink?.(title);
                  }}
                  className="wikilink"
                  title={onWikiLink ? "点击打开" : "双链目标"}
                >
                  {children}
                </a>
              );
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
          },
          code: ({ className, children, ...props }) => {
            const text = String(children ?? "");
            if (/language-html/i.test(className || "") && onPreviewHtml) {
              return (
                <div className="not-prose my-3 overflow-hidden rounded-xl border border-border-subtle">
                  <div className="flex items-center justify-between border-b border-border-subtle bg-muted px-3 py-1.5">
                    <span className="text-[11.5px] text-muted-foreground">HTML · {text.length} 字符</span>
                    <button
                      onClick={() => onPreviewHtml(text)}
                      className="rounded-md bg-accent px-2.5 py-1 text-[11.5px] font-medium text-accent-foreground hover:opacity-90"
                    >
                      预览网页
                    </button>
                  </div>
                  <pre className="hide-scrollbar !m-0 !rounded-none max-h-52 overflow-auto whitespace-pre-wrap break-words bg-neutral-900 px-3.5 py-3 font-mono text-[12px] leading-relaxed text-neutral-200">
                    <code>{text}</code>
                  </pre>
                </div>
              );
            }
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          },
        }}
      >
        {onWikiLink ? preprocessWikilinks(content) : content}
      </ReactMarkdown>
    </div>
  );
}
