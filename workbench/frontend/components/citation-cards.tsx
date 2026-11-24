"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { Citation } from "@/lib/types";

export function CitationCards({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const router = useRouter();

  return (
    <div className="mt-3 space-y-1.5">
      <div className="text-[11.5px] font-medium text-muted-foreground">
        引用来源（{citations.length}）
      </div>
      {citations.map((c) => (
        <div key={c.n} className="rounded-lg border border-border-subtle bg-muted/50">
          <button
            onClick={() => setOpen(open === c.n ? null : c.n)}
            className="flex w-full items-center gap-2 px-3 py-2 text-left"
          >
            <span className="flex h-4.5 w-4.5 shrink-0 items-center justify-center rounded bg-accent-soft px-1.5 text-[10.5px] font-semibold text-accent">
              {c.n}
            </span>
            <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium hover:text-accent">
              {c.title}
            </span>
            <span className="text-[10.5px] text-muted-foreground/60">
              {open === c.n ? "收起" : "展开"}
            </span>
          </button>
          {open === c.n && (
            <div className="border-t border-border-subtle px-3 py-2.5">
              <div className="text-[12.5px] leading-relaxed text-muted-foreground">
                {c.snippet}
              </div>
              {c.doc_id > 0 && (
                <button
                  onClick={() => router.push(`/kb?open=${c.doc_id}`)}
                  className="mt-2 text-[11.5px] text-accent hover:underline"
                >
                  查看知识库原文 →
                </button>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
