"use client";

import { useEffect, useRef, useState } from "react";

/**
 * 自制对话框（替代 window.prompt / window.confirm）：
 * - askConfirm(message) → Promise<boolean>
 * - askPrompt({ title, defaultValue, placeholder, allowEmpty }) → Promise<string | null>
 * 在 (main)/layout 挂载 <DialogHost /> 一次即可；未挂载时回退原生对话框兜底。
 * 支持 Esc 取消、Enter 提交、空值校验、danger 红色确认键。
 */

interface ConfirmReq {
  kind: "confirm";
  title: string;
  message: string;
  danger?: boolean;
  confirmText?: string;
  resolve: (v: boolean) => void;
}
interface PromptReq {
  kind: "prompt";
  title: string;
  message?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmText?: string;
  allowEmpty?: boolean;
  resolve: (v: string | null) => void;
}
type DialogReq = ConfirmReq | PromptReq;

let push: ((r: DialogReq) => void) | null = null;

export function askConfirm(opts: {
  title?: string;
  message: string;
  danger?: boolean;
  confirmText?: string;
}): Promise<boolean> {
  const p = push;
  if (!p) return Promise.resolve(window.confirm(opts.message));
  return new Promise((resolve) => {
    p({
      kind: "confirm",
      title: opts.title ?? "请确认",
      message: opts.message,
      danger: opts.danger,
      confirmText: opts.confirmText,
      resolve,
    });
  });
}

export function askPrompt(opts: {
  title?: string;
  message?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmText?: string;
  allowEmpty?: boolean;
}): Promise<string | null> {
  const p = push;
  if (!p) return Promise.resolve(window.prompt(opts.title ?? "", opts.defaultValue ?? ""));
  return new Promise((resolve) => {
    p({
      kind: "prompt",
      title: opts.title ?? "请输入",
      message: opts.message,
      defaultValue: opts.defaultValue,
      placeholder: opts.placeholder,
      confirmText: opts.confirmText,
      allowEmpty: opts.allowEmpty,
      resolve,
    });
  });
}

export function DialogHost() {
  const [req, setReq] = useState<DialogReq | null>(null);
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    push = (r) => {
      setReq(r);
      setValue(r.kind === "prompt" ? r.defaultValue ?? "" : "");
      setErr("");
    };
    return () => {
      push = null;
    };
  }, []);

  useEffect(() => {
    if (!req) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(null);
    };
    window.addEventListener("keydown", onKey);
    if (req.kind === "prompt") {
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [req]);

  function close(result: boolean | string | null) {
    setReq((r) => {
      if (!r) return null;
      if (r.kind === "confirm") r.resolve(result === true);
      else r.resolve(typeof result === "string" ? result : null);
      return null;
    });
  }

  function submitPrompt() {
    if (!req || req.kind !== "prompt") return;
    if (!value.trim() && !req.allowEmpty) {
      setErr("内容不能为空");
      return;
    }
    close(value);
  }

  if (!req) return null;
  const danger = req.kind === "confirm" && req.danger;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close(null);
      }}
    >
      <div className="w-[92vw] max-w-sm rounded-2xl border border-border-subtle bg-surface p-5 shadow-2xl">
        <div className="text-[15px] font-semibold">{req.title}</div>
        {req.message && (
          <div className="mt-2 whitespace-pre-wrap text-[13px] leading-relaxed text-muted-foreground">
            {req.message}
          </div>
        )}
        {req.kind === "prompt" && (
          <>
            <input
              ref={inputRef}
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setErr("");
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitPrompt();
              }}
              placeholder={req.placeholder}
              className={`mt-3 w-full rounded-lg border bg-background px-3 py-2 text-[13.5px] outline-none transition-colors ${
                err ? "border-red-400 focus:border-red-400" : "border-border-subtle focus:border-accent/60"
              }`}
            />
            {err && <div className="mt-1.5 text-[12px] text-red-500">{err}</div>}
          </>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={() => close(null)}
            className="rounded-lg border border-border-subtle px-3.5 py-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground"
          >
            取消
          </button>
          <button
            onClick={() => (req.kind === "prompt" ? submitPrompt() : close(true))}
            className={`rounded-lg px-3.5 py-1.5 text-[13px] font-medium text-white transition-opacity hover:opacity-90 ${
              danger ? "bg-red-500" : "bg-accent"
            }`}
          >
            {req.confirmText ?? "确定"}
          </button>
        </div>
      </div>
    </div>
  );
}
