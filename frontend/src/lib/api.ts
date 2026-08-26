const BASE = import.meta.env.VITE_API_URL ?? "";
const REQUEST_TIMEOUT_MS = 8000;

// localStorage can throw inside sandboxed iframes — never let it kill the app.
function lsGet(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function lsSet(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}
function lsDel(key: string) {
  try { localStorage.removeItem(key); } catch { /* ignore */ }
}

let token = lsGet("revora_token") ?? "";

export function getToken() {
  return token;
}

export function setToken(t: string) {
  token = t;
  lsSet("revora_token", t);
}

export function clearToken() {
  token = "";
  lsDel("revora_token");
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = any>(path: string, opts: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${BASE}/api${path}`, {
      ...opts,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(opts.headers ?? {}),
      },
    });
  } catch (err: any) {
    if (err?.name === "AbortError") {
      throw new ApiError(0, "The server took too long to respond (timeout). Try again.");
    }
    throw new ApiError(
      0,
      "Cannot reach the REVORA server. Reload the page — if it still fails, the preview "
        + "server is sleeping; ask for it to be restarted."
    );
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("revora:unauthorized"));
    throw new ApiError(401, "Not authenticated");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}
