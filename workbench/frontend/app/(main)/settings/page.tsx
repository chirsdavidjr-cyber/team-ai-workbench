"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import type { ProviderInfo } from "@/lib/types";
import { askConfirm } from "@/components/dialog";

export default function SettingsPage() {
  const { user } = useAuth();
  const { providers, refreshProviders } = useAppData();
  const [formOpen, setFormOpen] = useState(false);
  const isAdmin = user?.role === "admin";

  return (
    <div className="mx-auto max-w-2xl px-6 py-10">
      <h1 className="text-[20px] font-semibold tracking-tight">模型与设置</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        配置 OpenAI 兼容的模型供应商（智谱 / DeepSeek / 通义 / 豆包 / Kimi /
        OpenAI 等）。管理员配置的对全团队生效，成员可配置个人 Key。
      </p>

      <Section
        title="团队模型"
        desc={isAdmin ? "全员可用，仅管理员可修改" : "由管理员配置，全员可用"}
      >
        {providers.filter((p) => p.scope === "team").map((p) => (
          <ProviderCard key={p.id} p={p} canDelete={isAdmin} onChanged={refreshProviders} />
        ))}
        {providers.filter((p) => p.scope === "team").length === 0 && (
          <Empty>管理员尚未配置团队模型</Empty>
        )}
      </Section>

      <Section
        title="我的 Key（个人）"
        desc="仅自己可用，调用不计入团队配额"
      >
        {providers.filter((p) => p.scope === "personal").map((p) => (
          <ProviderCard key={p.id} p={p} canDelete onChanged={refreshProviders} />
        ))}
        {providers.filter((p) => p.scope === "personal").length === 0 && (
          <Empty>暂无个人供应商</Empty>
        )}
      </Section>

      {formOpen ? (
        <ProviderForm
          isAdmin={!!isAdmin}
          onDone={async () => {
            setFormOpen(false);
            await refreshProviders();
          }}
        />
      ) : (
        <button
          onClick={() => setFormOpen(true)}
          className="rounded-lg bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-foreground transition-opacity hover:opacity-90"
        >
          + 添加供应商
        </button>
      )}
    </div>
  );
}

function Section({
  title,
  desc,
  children,
}: {
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mt-8">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="text-[15px] font-semibold">{title}</h2>
        <span className="text-xs text-muted-foreground">{desc}</span>
      </div>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-border-subtle px-4 py-5 text-center text-[13px] text-muted-foreground/70">
      {children}
    </div>
  );
}

function ProviderCard({
  p,
  canDelete,
  onChanged,
}: {
  p: ProviderInfo;
  canDelete: boolean;
  onChanged: () => void;
}) {
  async function remove() {
    if (!(await askConfirm({ title: "删除供应商", message: `删除供应商「${p.name}」？`, danger: true, confirmText: "删除" }))) return;
    await api(`/api/providers/${p.id}`, { method: "DELETE" });
    onChanged();
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-border-subtle bg-surface px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-medium">{p.name}</span>
          <span
            className={`rounded-full px-2 py-0.5 text-[10.5px] ${
              p.scope === "team"
                ? "bg-accent-soft text-accent"
                : "bg-muted text-muted-foreground"
            }`}
          >
            {p.scope === "team" ? "团队" : "个人"}
          </span>
        </div>
        <div className="mt-0.5 truncate text-xs text-muted-foreground">
          {p.models.map((m) => m.label).join(" · ")}
        </div>
        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground/70">
          {p.base_url} · Key {p.api_key_masked}
        </div>
      </div>
      {canDelete && (
        <button
          onClick={remove}
          className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-red-400 hover:text-red-500"
        >
          删除
        </button>
      )}
    </div>
  );
}

function ProviderForm({
  isAdmin,
  onDone,
}: {
  isAdmin: boolean;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://open.bigmodel.cn/api/paas/v4");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState("");
  const [scope, setScope] = useState<"team" | "personal">(
    isAdmin ? "team" : "personal"
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pulling, setPulling] = useState(false);

  async function submit() {
    setError("");
    if (!name.trim() || !baseUrl.trim() || !apiKey.trim() || !models.trim()) {
      setError("请填写完整：名称、接口地址、API Key、模型");
      return;
    }
    setBusy(true);
    try {
      await api("/api/providers", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim(),
          base_url: baseUrl.trim(),
          api_key: apiKey.trim(),
          scope,
          models: models
            .split(/[,，\n]/)
            .map((s) => s.trim())
            .filter(Boolean)
            .map((id) => ({ id, label: id })),
        }),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-accent";

  return (
    <div className="mt-8 rounded-xl border border-border-subtle bg-surface p-5">
      <h2 className="text-[15px] font-semibold">添加供应商</h2>
      <div className="mt-4 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">名称</label>
            <input className={input} placeholder="如：智谱 GLM" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">
              接口地址（OpenAI 兼容）
            </label>
            <input className={input} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </div>
        </div>
        <div>
          <label className="mb-1 block text-[12.5px] font-medium">API Key</label>
          <input className={input} type="password" placeholder="粘贴 Key（保存后脱敏显示）" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 flex items-center justify-between text-[12.5px] font-medium">
            <span>模型 ID（逗号或换行分隔）</span>
            <button
              onClick={async () => {
                setError("");
                if (!baseUrl.trim() || !apiKey.trim()) {
                  setError("请先填写接口地址与 API Key 再拉取");
                  return;
                }
                setPulling(true);
                try {
                  const list = await api<{ id: string }[]>("/api/providers/models-preview", {
                    method: "POST",
                    body: JSON.stringify({ base_url: baseUrl.trim(), api_key: apiKey.trim() }),
                  });
                  if (list.length === 0) setError("该接口未返回模型");
                  else setModels(list.slice(0, 15).map((m) => m.id).join(", "));
                } catch (e) {
                  setError(e instanceof Error ? e.message : "拉取失败");
                } finally {
                  setPulling(false);
                }
              }}
              disabled={pulling}
              className="font-normal text-accent hover:underline disabled:opacity-60"
            >
              {pulling ? "拉取中…" : "从接口拉取"}
            </button>
          </label>
          <input className={input} placeholder="如：glm5.3, glm5.3-air" value={models} onChange={(e) => setModels(e.target.value)} />
        </div>
        {isAdmin && (
          <div className="flex items-center gap-4 text-[13px]">
            <span className="text-muted-foreground">作用范围：</span>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={scope === "team"} onChange={() => setScope("team")} />
              团队共享
            </label>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={scope === "personal"} onChange={() => setScope("personal")} />
              仅自己
            </label>
          </div>
        )}
        {error && (
          <div className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">
            {error}
          </div>
        )}
        <div className="flex gap-2.5 pt-1">
          <button
            onClick={submit}
            disabled={busy}
            className="rounded-lg bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
          >
            {busy ? "保存中…" : "保存"}
          </button>
          <button
            onClick={onDone}
            className="rounded-lg border border-border-subtle px-4 py-2 text-[13.5px] text-muted-foreground hover:text-foreground"
          >
            取消
          </button>
        </div>
      </div>
    </div>
  );
}
