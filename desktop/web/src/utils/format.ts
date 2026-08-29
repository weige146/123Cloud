// 管理前端通用格式化工具。

export function formatBytes(value?: number | null): string {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let current = size;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024;
    index += 1;
  }
  return `${current.toFixed(index >= 3 ? 2 : 1)} ${units[index]}`;
}

export function looksLikePhone(value: string): boolean {
  const digits = String(value).replace(/\D/g, "");
  return digits.length >= 7 && digits.length / Math.max(String(value).length, 1) > 0.65;
}

export function displayName(profile: { nickname?: string; mail?: string } | null, fallback: string): string {
  const data = profile || {};
  const values = [data.nickname, data.mail, fallback, "123 云盘账号"];
  for (const value of values) {
    const text = String(value || "").trim();
    if (text && !looksLikePhone(text)) return text;
  }
  return "123 云盘账号";
}

export function normalizeAvatarUrl(url: string): string {
  if (url.startsWith("//")) return `https:${url}`;
  if (url.startsWith("/")) return `https://www.123pan.com${url}`;
  return url;
}



export function statusText(value?: string): string {
  const map: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    success: "成功",
    partial: "部分成功",
    failed: "失败",
  };
  return map[String(value || "")] || value || "--";
}



export function parseIds(value: string): number[] {
  return String(value || "")
    .split(/[,\n\r\t ]+/)
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isSafeInteger(item) && item > 0);
}

export function parseCookiePool(text?: string): string[] {
  return String(text || "")
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function formatCookiePool(value: string[] | string): string {
  if (Array.isArray(value)) return value.filter(Boolean).join("\n");
  return String(value || "");
}




export function slugId(value: string): string {
  return (
    String(value || "rule")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 32) || "rule"
  );
}
