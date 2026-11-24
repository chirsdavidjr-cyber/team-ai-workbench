"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useAppData } from "@/lib/app-data";

export function ModelPicker({ compact = false }: { compact?: boolean }) {
  const { providers, providersLoading, selection, setSelection } = useAppData();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const team = providers.filter((p) => p.scope === "team");
  const personal = providers.filter((p) => p.scope === "personal");

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className={`flex items-center gap-1.5 rounded-lg border border-border-subtle px-2.5 text-xs text-muted-foreground transition-colors hover:text-foreground ${
          compact ? "py-1" : "py-1.5"
        }`}
        title="切换模型"
      >
        <span className="h-1.5 w-1.5 rounded-full bg-accent" />
        {providersLoading
          ? "加载中…"
          : selection
            ? selection.modelLabel
            : "未配置模型"}
      </button>

      {open && (
        <div className="absolute bottom-full left-0 z-50 mb-2 w-64 rounded-xl border border-border-subtle bg-surface p-1.5 shadow-[0_8px_30px_rgba(0,0,0,0.12)]">
          {providers.length === 0 ? (
            <Link
              href="/settings"
              onClick={() => setOpen(false)}
              className="block rounded-lg px-3 py-2.5 text-[13px] text-muted-foreground hover:bg-muted"
            >
              尚未配置模型，前往设置添加 →
            </Link>
          ) : (
            <>
              {team.length > 0 && <GroupLabel>团队模型</GroupLabel>}
              {team.map((p) =>
                p.models.map((m) => (
                  <Option
                    key={`${p.id}:${m.id}`}
                    active={
                      selection?.providerId === p.id && selection.modelId === m.id
                    }
                    onClick={() => {
                      setSelection({
                        providerId: p.id,
                        providerName: p.name,
                        modelId: m.id,
                        modelLabel: m.label,
                      });
                      setOpen(false);
                    }}
                  >
                    {m.label}
                    <span className="ml-1.5 text-[11px] text-muted-foreground/70">
                      {p.name}
                    </span>
                  </Option>
                ))
              )}
              {personal.length > 0 && <GroupLabel>我的模型</GroupLabel>}
              {personal.map((p) =>
                p.models.map((m) => (
                  <Option
                    key={`${p.id}:${m.id}`}
                    active={
                      selection?.providerId === p.id && selection.modelId === m.id
                    }
                    onClick={() => {
                      setSelection({
                        providerId: p.id,
                        providerName: p.name,
                        modelId: m.id,
                        modelLabel: m.label,
                      });
                      setOpen(false);
                    }}
                  >
                    {m.label}
                    <span className="ml-1.5 text-[11px] text-muted-foreground/70">
                      {p.name}
                    </span>
                  </Option>
                ))
              )}
              <Link
                href="/settings"
                onClick={() => setOpen(false)}
                className="mt-1 block border-t border-border-subtle px-3 pb-1 pt-2 text-[12px] text-muted-foreground hover:text-foreground"
              >
                管理模型配置 →
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function GroupLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/60">
      {children}
    </div>
  );
}

function Option({
  active,
  onClick,
  children,
}: {
  active?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center rounded-lg px-3 py-1.5 text-left text-[13px] transition-colors ${
        active
          ? "bg-accent-soft font-medium text-accent"
          : "hover:bg-muted"
      }`}
    >
      {children}
    </button>
  );
}
