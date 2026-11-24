// 同源相对路径（dev/生产均经 next rewrites 代理到后端）；跨域部署可用 NEXT_PUBLIC_API_BASE 覆盖
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";

const TOKEN_KEY = "wb-token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = new Headers(options.headers);
  if (!(options.body instanceof FormData) && options.body) {
    headers.set("Content-Type", "application/json");
  }
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (res.status === 401 && typeof window !== "undefined") {
    setToken(null);
    window.dispatchEvent(new Event("wb-unauthorized"));
  }
  if (!res.ok) {
    let detail = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") detail = data.detail;
      else if (Array.isArray(data.detail)) {
        // Pydantic 422 校验错误数组：取首条的 msg 与字段位置
        const first = data.detail[0] as { loc?: (string | number)[]; msg?: string } | undefined;
        const loc = first?.loc?.filter((x) => x !== "body").join(".");
        detail = first?.msg
          ? `${loc ? `${loc}：` : ""}${first.msg}`
          : `参数不合法（${res.status}）`;
      }
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}
