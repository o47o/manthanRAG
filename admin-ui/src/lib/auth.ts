// Admin API key for this browser tab. Kept in sessionStorage: it is gone when the tab closes
// and is never written to disk by the app. Every API call sends it as X-API-Key.
const STORAGE_KEY = "manthan_rag_admin_key";

export function getApiKey(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setApiKey(key: string): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, key.trim());
  } catch {
    /* storage blocked: the key then lasts only until the next reload */
  }
}

export function clearApiKey(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function authHeaders(): Record<string, string> {
  const key = getApiKey();
  return key ? { "X-API-Key": key } : {};
}

/** Thrown for 401/403 so the app can send the user back to the key screen. */
export class AuthError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
