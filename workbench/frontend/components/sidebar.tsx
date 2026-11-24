"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import { ConversationList } from "./conversation-list";
import {
  IconBook,
  IconChevronLeft,
  IconChevronRight,
  IconDashboard,
  IconDatabase,
  IconGlobe,
  IconHome,
  IconLogout,
  IconMoon,
  IconPlus,
  IconSun,
  IconTasks,
  IconTools,
} from "./icons";

const NAV = [
  { href: "/", label: "首页", icon: IconHome },
  { href: "/workbench", label: "我的工作台", icon: IconDashboard },
  { href: "/tools", label: "工具库", icon: IconTools },
  { href: "/kb", label: "知识库", icon: IconBook },
  { href: "/data", label: "数据库", icon: IconDatabase },
  { href: "/tasks", label: "任务中心", icon: IconTasks },
  { href: "/collect", label: "采集任务", icon: IconGlobe },
];

export function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const { refreshConversations } = useAppData();
  const [dark, setDark] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
    setCollapsed(localStorage.getItem("wb-sidebar-collapsed") === "1");
  }, []);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem("wb-sidebar-collapsed", next ? "1" : "0");
  }

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("wb-theme", next ? "dark" : "light");
  }

  async function newConversation() {
    const res = await api<{ id: number }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({}),
    }).catch(() => null);
    if (!res) return;
    refreshConversations();
    router.push(`/chat/${res.id}`);
  }

  return (
    <aside
      className={`flex h-full shrink-0 flex-col border-r border-border-subtle bg-muted/60 transition-[width] ${
        collapsed ? "w-[60px]" : "w-60"
      }`}
    >
      {/* Logo + 新对话 + 折叠 */}
      <div
        className={`flex items-center gap-2 ${
          collapsed ? "mx-auto h-auto flex-col gap-1.5 px-2 py-3" : "h-14 px-4"
        }`}
      >
        <div
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent text-[13px] font-bold text-accent-foreground"
          title="湖北 AI 工作台"
        >
          湖
        </div>
        {!collapsed && (
          <span className="flex-1 truncate text-[14px] font-semibold tracking-wide">
            湖北 AI 工作台
          </span>
        )}
        {!collapsed && (
          <button
            onClick={newConversation}
            title="新对话"
            className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          >
            <IconPlus />
          </button>
        )}
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
        >
          {collapsed ? <IconChevronRight /> : <IconChevronLeft />}
        </button>
      </div>

      {/* 折叠态：新对话图标行 */}
      {collapsed && (
        <div className="px-2 pb-1 pt-0.5">
          <button
            onClick={newConversation}
            title="新对话"
            className="flex w-full items-center justify-center rounded-lg border border-dashed border-border-subtle py-[9px] text-muted-foreground transition-colors hover:border-accent/60 hover:text-accent"
          >
            <IconPlus />
          </button>
        </div>
      )}

      {/* 主导航 */}
      <nav className={`mt-1 space-y-0.5 ${collapsed ? "px-2" : "px-2.5"}`}>
        {NAV.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              title={label}
              className={`flex items-center rounded-lg text-[13.5px] transition-colors ${
                collapsed ? "justify-center px-0 py-[9px]" : "gap-2.5 px-3 py-[7px]"
              } ${
                active
                  ? "bg-background font-medium text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
                  : "text-muted-foreground hover:bg-background/70 hover:text-foreground"
              }`}
            >
              <Icon className={active ? "text-accent" : ""} />
              {!collapsed && label}
            </Link>
          );
        })}
        {user?.role === "admin" && (
          <Link
            href="/admin"
            title="审核中心"
            className={`flex items-center rounded-lg text-[13.5px] transition-colors ${
              collapsed ? "justify-center px-0 py-[9px]" : "gap-2.5 px-3 py-[7px]"
            } ${
              pathname.startsWith("/admin")
                ? "bg-background font-medium text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
                : "text-muted-foreground hover:bg-background/70 hover:text-foreground"
            }`}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
              <path d="m9 12 2 2 4-4" />
            </svg>
            {!collapsed && "审核中心"}
          </Link>
        )}
      </nav>

      {/* 会话列表（折叠时隐藏） */}
      {!collapsed && (
        <div className="mt-5 flex min-h-0 flex-1 flex-col px-2.5">
          <div className="px-3 pb-1.5 text-[11.5px] font-medium uppercase tracking-wider text-muted-foreground/70">
            会话
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-2">
            <ConversationList />
          </div>
        </div>
      )}
      {collapsed && <div className="flex-1" />}

      {/* 底部用户区 */}
      <div className={`border-t border-border-subtle ${collapsed ? "p-2" : "p-2.5"}`}>
        <Link
          href="/settings"
          title="模型与设置"
          className={`mb-1 flex items-center rounded-lg text-[13.5px] transition-colors ${
            collapsed ? "justify-center px-0 py-[9px]" : "gap-2.5 px-3 py-[7px]"
          } ${
            pathname.startsWith("/settings")
              ? "bg-background font-medium text-foreground"
              : "text-muted-foreground hover:bg-background/70 hover:text-foreground"
          }`}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 4h-7M10 4H3M21 12h-9M8 12H3M21 20h-5M12 20H3M14 2v4M8 10v4M16 18v4" />
          </svg>
          {!collapsed && "模型与设置"}
        </Link>
        <div
          className={`flex items-center rounded-lg px-2 py-1.5 ${
            collapsed ? "flex-col gap-2" : "gap-2.5"
          }`}
        >
          <div
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent"
            title={user?.name}
          >
            {user?.name?.charAt(0) ?? "?"}
          </div>
          {!collapsed && (
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium leading-tight">
                {user?.name}
              </div>
              <div className="truncate text-[11px] leading-tight text-muted-foreground">
                {user?.role === "admin" ? "管理员" : "成员"}
              </div>
            </div>
          )}
          <button
            onClick={toggleTheme}
            title="切换主题"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          >
            {dark ? <IconSun /> : <IconMoon />}
          </button>
          <button
            onClick={() => {
              logout();
              router.replace("/login");
            }}
            title="退出登录"
            className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          >
            <IconLogout />
          </button>
        </div>
      </div>
    </aside>
  );
}
