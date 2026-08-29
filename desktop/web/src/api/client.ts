// 后端 API 客户端：封装 fetch + JSON 解析 + 错误处理
// 所有 endpoint 与 backend/app/main.py 中的路由一一对应

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(message: string, status = 0, detail = "") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export async function readJson<T = unknown>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  const payload = data as { ok?: boolean; detail?: string; message?: string };
  if (!response.ok || payload.ok === false) {
    const message = payload.detail || payload.message || `请求失败 (${response.status})`;
    throw new ApiError(message, response.status, payload.detail || "");
  }
  return data as T;
}

export async function apiGet<T = unknown>(url: string): Promise<T> {
  const response = await fetch(url, { cache: "no-store", headers: { accept: "application/json" } });
  return readJson<T>(response);
}

export async function apiJson<T = unknown>(url: string, method: "POST" | "PUT" | "DELETE", body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: { "content-type": "application/json", accept: "application/json" },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fetch(url, init);
  return readJson<T>(response);
}

type TelegramWebApp = { initData?: string };

function telegramInitData(): string {
  return ((window as Window & { Telegram?: { WebApp?: TelegramWebApp } }).Telegram?.WebApp?.initData || "").trim();
}

export async function apiTelegramGet<T = unknown>(url: string): Promise<T> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      accept: "application/json",
      "X-Telegram-Init-Data": telegramInitData(),
    },
  });
  return readJson<T>(response);
}

export async function apiTelegramJson<T = unknown>(url: string, method: "PUT" | "DELETE", body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "X-Telegram-Init-Data": telegramInitData(),
    },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  const response = await fetch(url, init);
  return readJson<T>(response);
}

export const api = {
  get: apiGet,
  post: <T = unknown>(url: string, body?: unknown) => apiJson<T>(url, "POST", body),
  put: <T = unknown>(url: string, body?: unknown) => apiJson<T>(url, "PUT", body),
  delete: <T = unknown>(url: string) => apiJson<T>(url, "DELETE"),
};
