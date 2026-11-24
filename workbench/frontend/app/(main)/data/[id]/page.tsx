"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

/** 旧详情路由：重定向到 /data?open=id（详情已并入数据页单页切换，与知识库同构）。 */
export default function DatasetDetailRedirect() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  useEffect(() => {
    router.replace(`/data?open=${id}`);
  }, [id, router]);
  return <div className="py-20 text-center text-sm text-muted-foreground">正在打开数据集…</div>;
}
