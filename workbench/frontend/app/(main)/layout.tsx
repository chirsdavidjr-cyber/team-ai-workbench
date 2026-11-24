"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { AppDataProvider } from "@/lib/app-data";
import { Sidebar } from "@/components/sidebar";
import { DialogHost } from "@/components/dialog";

export default function MainLayout({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <div className="flex items-center gap-3 text-sm text-muted-foreground">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
          正在进入工作台…
        </div>
      </div>
    );
  }

  return (
    <AppDataProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-y-auto overflow-x-hidden">{children}</main>
        {/* 右侧展示区：Step 3 知识引用上线后按需展开 */}
        <DialogHost />
      </div>
    </AppDataProvider>
  );
}
