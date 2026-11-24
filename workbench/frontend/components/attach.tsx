"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { IconPaperclip } from "./icons";

export interface Attachment {
  name: string;
  text: string;
  size: number;
}

const ACCEPT = [
  ".md", ".markdown", ".txt", ".pdf", ".docx", ".pptx", ".doc", ".xls",
  ".csv", ".tsv", ".xlsx", ".json", ".xml", ".yaml", ".yml", ".log", ".html", ".htm",
  ".py", ".js", ".ts", ".tsx", ".java", ".c", ".cpp", ".go", ".rs", ".sql", ".sh",
  ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
].join(",");

/** 对话附件上传按钮：解析文本供 AI 阅读（不自动写知识库；入库由用户要求后经知识库管家执行）。 */
export function AttachButton({
  onLoaded,
}: {
  onLoaded: (files: Attachment[], skipped: { name: string; reason: string }[]) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  return (
    <>
      <input
        ref={ref}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={async (e) => {
          const files = e.target.files;
          if (!files || files.length === 0 || busy) return;
          setBusy(true);
          try {
            const fd = new FormData();
            Array.from(files)
              .slice(0, 5)
              .forEach((f) => fd.append("files", f));
            const res = await api<{ files: Attachment[]; skipped: { name: string; reason: string }[] }>(
              "/api/chat/upload",
              { method: "POST", body: fd }
            );
            onLoaded(res.files, res.skipped);
          } catch {
            onLoaded([], [{ name: "", reason: "上传失败，请重试" }]);
          } finally {
            setBusy(false);
            if (ref.current) ref.current.value = "";
          }
        }}
      />
      <button
        onClick={() => ref.current?.click()}
        disabled={busy}
        title="上传附件：文档/表格/代码/网页自动解析，图片与扫描 PDF 自动视觉识别（旧格式 .doc/.xls 直接支持）"
        className="flex items-center gap-1.5 rounded-lg border border-border-subtle px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
      >
        <IconPaperclip width={13} height={13} />
        {busy ? "解析中…" : "附件"}
      </button>
    </>
  );
}

/** 输入框上方的附件 chips。 */
export function AttachChips({
  files,
  onRemove,
}: {
  files: Attachment[];
  onRemove: (name: string) => void;
}) {
  if (files.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1.5">
      {files.map((f) => (
        <span
          key={f.name}
          className="flex max-w-full items-center gap-1.5 rounded-lg bg-muted px-2.5 py-1 text-[12px] text-muted-foreground"
        >
          <span className="truncate">{f.name}</span>
          <span className="shrink-0 text-[10.5px] text-muted-foreground/50">
            {(f.size / 1024).toFixed(0)}KB
          </span>
          <button
            onClick={() => onRemove(f.name)}
            className="shrink-0 text-muted-foreground/60 hover:text-foreground"
            title="移除"
          >
            ✕
          </button>
        </span>
      ))}
    </div>
  );
}

/** 把附件内容拼进消息文本（AI 阅读附件的方式）。总量控制在 28000 字符内，超出按顺序截断并标注。 */
export function composeWithAttachments(text: string, files: Attachment[]): string {
  if (files.length === 0) return text;
  const LIMIT = 28000;
  const blocks: string[] = [];
  let used = text.length;
  for (const f of files) {
    const head = `【附件：${f.name}】\n`;
    const budget = Math.max(0, LIMIT - used - head.length - 2);
    if (budget < 200) {
      blocks.push(`【附件：${f.name}】\n（内容过长已省略，请减少附件或缩短正文）`);
      continue;
    }
    if (f.text.length <= budget) {
      blocks.push(head + f.text);
      used += head.length + f.text.length;
    } else {
      blocks.push(head + f.text.slice(0, budget) + `\n（…内容过长，已截断，原文件 ${(f.size / 1024).toFixed(0)}KB）`);
      used += head.length + budget;
    }
  }
  return text ? `${text}\n\n${blocks.join("\n\n")}` : blocks.join("\n\n");
}

/** 用户消息中的附件块标记（与 composeWithAttachments 生成的格式对应）。 */
const ATTACH_BLOCK_RE = /(?:^|\n\n)【附件：(.+?)】\n/g;

/** 用户消息渲染：附件全文折叠为卡片，点击展开。
 * 附件内容已随消息正文发给 AI（模型可读全文），这里只优化气泡展示——避免解析文本刷屏。 */
export function UserMessageContent({ content }: { content: string }) {
  const [open, setOpen] = useState<number | null>(null);
  const re = new RegExp(ATTACH_BLOCK_RE.source, "g");
  const marks: { name: string; start: number; bodyStart: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = re.exec(content)) !== null) {
    marks.push({ name: m[1], start: m.index, bodyStart: m.index + m[0].length });
  }
  if (marks.length === 0) return <>{content}</>;

  const out: React.ReactNode[] = [];
  let last = 0;
  marks.forEach((mk, i) => {
    if (mk.start > last) out.push(<span key={`t${i}`}>{content.slice(last, mk.start)}</span>);
    const end = i + 1 < marks.length ? marks[i + 1].start : content.length;
    const body = content.slice(mk.bodyStart, end).replace(/\n+$/, "");
    const expanded = open === i;
    out.push(
      <div key={`a${i}`} className="my-1.5 w-full">
        <button
          onClick={() => setOpen(expanded ? null : i)}
          className="flex w-full items-center gap-1.5 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-left text-[12px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <span className="shrink-0">📎</span>
          <span className="min-w-0 flex-1 truncate">{mk.name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground/60">
            {expanded ? "收起" : "展开内容"}
          </span>
        </button>
        {expanded && (
          <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-border-subtle bg-background px-3 py-2 text-left font-sans text-[12px] leading-relaxed text-foreground/90">
            {body}
          </pre>
        )}
      </div>
    );
    last = end;
  });
  if (last < content.length) out.push(<span key="tail">{content.slice(last)}</span>);
  return <div className="flex w-full flex-col items-end">{out}</div>;
}
