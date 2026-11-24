import { API_BASE, getToken } from "./api";
import type { ChatSSEEvent } from "./types";

/** POST + SSE 流式读取：逐事件回调，返回是否正常结束。 */
export async function streamChat(
  body: {
    conversation_id: number;
    provider_id: number;
    model_id: string;
    content: string;
    use_kb?: boolean;
    tool_id?: number | null;
  },
  onEvent: (ev: ChatSSEEvent) => void
): Promise<void> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok || !res.body) {
    let detail = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* ignore */
    }
    onEvent({ type: "error", detail });
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        onEvent(JSON.parse(line.slice(6)));
      } catch {
        /* 忽略无法解析的片段 */
      }
    }
  }
}
