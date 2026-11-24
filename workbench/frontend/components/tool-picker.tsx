"use client";

import { useEffect, useRef, useState } from "react";
import { useAppData } from "@/lib/app-data";

/** 工具（Agent/Skill）选择器：通用对话 + 团队/个人工具，参照 WorkBuddy 输入框模式。 */
export function ToolPicker() {
  const { tools, activeTool, setActiveTool } = useAppData();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const team = tools.filter((t) => t.scope === "team");
  const personal = tools.filter((t) => t.scope === "personal");

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg border border-border-subtle px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        title="选择模型人格 / 工具"
      >
        {activeTool ? (
          <>
            <span>{activeTool.icon}</span>
            <span className="font-medium text-accent">{activeTool.name}</span>
          </>
        ) : (
          "💬 通用对话"
        )}
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 max-h-80 w-72 overflow-y-auto rounded-xl border border-border-subtle bg-surface p-1.5 shadow-[0_8px_30px_rgba(0,0,0,0.12)]">
          <button
            onClick={() => {
              setActiveTool(null);
              setOpen(false);
            }}
            className={`flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] transition-colors ${
              !activeTool ? "bg-accent-soft font-medium text-accent" : "hover:bg-muted"
            }`}
          >
            💬 通用对话
            <span className="ml-auto text-[10.5px] text-muted-foreground/60">默认</span>
          </button>

          {team.length > 0 && <Group label="团队工具" />}
          {team.map((t) => (
            <ToolOption key={t.id} tool={t} active={activeTool?.id === t.id} onPick={() => { setActiveTool(t); setOpen(false); }} />
          ))}
          {personal.length > 0 && <Group label="我的工具" />}
          {personal.map((t) => (
            <ToolOption key={t.id} tool={t} active={activeTool?.id === t.id} onPick={() => { setActiveTool(t); setOpen(false); }} />
          ))}
          {tools.length === 0 && (
            <div className="px-3 py-3 text-[12.5px] text-muted-foreground/70">
              暂无工具，可在「工具库」创建
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Group({ label }: { label: string }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {label}
    </div>
  );
}

function ToolOption({
  tool,
  active,
  onPick,
}: {
  tool: { icon: string; name: string; kind: string; description: string };
  active: boolean;
  onPick: () => void;
}) {
  return (
    <button
      onClick={onPick}
      className={`flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left transition-colors ${
        active ? "bg-accent-soft" : "hover:bg-muted"
      }`}
    >
      <span className="mt-0.5">{tool.icon}</span>
      <span className="min-w-0 flex-1">
        <span className={`block truncate text-[13px] ${active ? "font-medium text-accent" : ""}`}>
          {tool.name}
          <span className="ml-1.5 rounded bg-muted px-1 py-px text-[9.5px] uppercase text-muted-foreground">
            {tool.kind}
          </span>
        </span>
        <span className="block truncate text-[11px] text-muted-foreground/70">
          {tool.description}
        </span>
      </span>
    </button>
  );
}
