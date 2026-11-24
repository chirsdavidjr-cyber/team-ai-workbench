"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { login, register } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [allowRegister, setAllowRegister] = useState(true);

  // 未开放注册时隐藏注册入口，避免填完表单才报错（同源相对路径）
  useEffect(() => {
    fetch("/api/auth/config")
      .then((r) => r.json())
      .then((d: { allow_register?: boolean }) => {
        if (d.allow_register === false) {
          setAllowRegister(false);
          setMode("login");
        }
      })
      .catch(() => {});
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), name.trim(), password);
      }
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "w-full rounded-lg border border-border-subtle bg-background px-3.5 py-2.5 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-accent focus:ring-2 focus:ring-accent/15";

  return (
    <div className="flex min-h-screen bg-background">
      {/* 品牌区 */}
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-neutral-950 p-12 text-neutral-100 lg:flex">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500 font-bold text-neutral-950">
            湖
          </div>
          <span className="text-[15px] font-semibold tracking-wide">
            湖北 AI 工作台
          </span>
        </div>
        <div className="max-w-md space-y-8">
          <h1 className="text-[34px] font-semibold leading-snug tracking-tight">
            把团队的知识、数据
            <br />
            和经验，装进一个工作台
          </h1>
          <div className="space-y-5 text-[15px] text-neutral-400">
            {[
              ["多模型对话", "一个入口切换所有大模型，成员无需各自采购"],
              ["知识库问答", "上传文档即建库，回答附带原文引用"],
              ["自然语言问数", "Excel 变成可对话的数据集，图表与 SQL 全透明"],
            ].map(([title, desc]) => (
              <div key={title} className="flex gap-3.5">
                <div className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
                <div>
                  <div className="font-medium text-neutral-200">{title}</div>
                  <div className="mt-0.5 text-sm text-neutral-500">{desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="text-xs text-neutral-600">
          面向小团队的一体化 AI 工作平台
        </div>
      </div>

      {/* 表单区 */}
      <div className="flex flex-1 items-center justify-center px-6">
        <div className="w-full max-w-[380px]">
          <div className="mb-8 lg:hidden">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-emerald-500 font-bold text-white">
                湖
              </div>
              <span className="text-[15px] font-semibold">湖北 AI 工作台</span>
            </div>
          </div>

          <h2 className="text-[22px] font-semibold tracking-tight">
            {mode === "login" ? "欢迎回来" : "创建账号"}
          </h2>
          <p className="mt-1.5 text-sm text-muted-foreground">
            {mode === "login"
              ? "登录你的团队工作台"
              : "注册后即可开始使用团队共享的 AI 能力"}
          </p>

          <form onSubmit={onSubmit} className="mt-7 space-y-3.5">
            <div>
              <label className="mb-1.5 block text-[13px] font-medium">
                邮箱
              </label>
              <input
                className={inputCls}
                type="email"
                required
                autoComplete="email"
                placeholder="you@team.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            {mode === "register" && (
              <div>
                <label className="mb-1.5 block text-[13px] font-medium">
                  姓名
                </label>
                <input
                  className={inputCls}
                  required
                  maxLength={64}
                  placeholder="你的名字"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
            )}

            <div>
              <label className="mb-1.5 block text-[13px] font-medium">
                密码
              </label>
              <input
                className={inputCls}
                type="password"
                required
                minLength={8}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                placeholder="至少 8 位"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && (
              <div className="rounded-lg border border-red-300/60 bg-red-50 px-3.5 py-2.5 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-accent py-2.5 text-sm font-medium text-accent-foreground transition-opacity hover:opacity-90 disabled:opacity-60"
            >
              {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
            </button>
          </form>

          <div className="mt-5 text-center text-[13px] text-muted-foreground">
            {mode === "login" ? (
              allowRegister ? (
                <>
                  还没有账号？{" "}
                  <button
                    className="font-medium text-accent hover:underline"
                    onClick={() => {
                      setMode("register");
                      setError("");
                    }}
                  >
                    注册
                  </button>
                </>
              ) : (
                <span className="text-muted-foreground/60">账号由管理员统一开通</span>
              )
            ) : (
              <>
                已有账号？{" "}
                <button
                  className="font-medium text-accent hover:underline"
                  onClick={() => {
                    setMode("login");
                    setError("");
                  }}
                >
                  直接登录
                </button>
              </>
            )}
          </div>

          <p className="mt-10 text-center text-xs leading-relaxed text-muted-foreground/70">
            首次部署：管理员账号由服务端 .env 初始化
            <br />
            admin@team.local / admin123456（请尽快修改）
          </p>
        </div>
      </div>
    </div>
  );
}
