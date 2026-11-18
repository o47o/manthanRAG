"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Database, KeyRound, Loader2 } from "lucide-react";
import { clearApiKey, setApiKey } from "@/lib/auth";
import { apiClient } from "@/lib/api-client";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!key.trim()) return;
    setBusy(true);
    setError(null);
    setApiKey(key);
    try {
      // Listing keys needs an admin key: this checks both that the key is valid and that it is admin.
      await apiClient.listKeys();
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && next !== "/login" ? next : "/collections");
    } catch (err) {
      clearApiKey();
      const msg = err instanceof Error ? err.message : String(err);
      setError(
        msg.includes("Admin API key required")
          ? "This key is valid but is not an admin key. The admin screens need an admin key."
          : msg.includes("Invalid") || msg.includes("Missing")
            ? "Unknown or revoked key."
            : msg
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form
        onSubmit={submit}
        className="w-full max-w-md space-y-5 rounded-2xl border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-800 dark:bg-gray-900"
      >
        <div className="flex items-center gap-2">
          <Database className="h-6 w-6 text-blue-600 dark:text-blue-400" />
          <div>
            <p className="text-sm font-bold text-gray-900 dark:text-gray-100">Manthan RAG</p>
            <p className="text-xs text-gray-500 dark:text-gray-400">Admin console</p>
          </div>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="key" className="block text-sm font-semibold text-gray-700 dark:text-gray-300">
            Admin API key
          </label>
          <input
            id="key"
            type="password"
            autoComplete="off"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="mrag_…"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 outline-none focus:border-blue-400 focus:ring-1 focus:ring-blue-400 dark:border-gray-700 dark:bg-gray-950 dark:text-gray-100"
          />
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Kept only in this browser tab until you close it or sign out. Create the first admin key on the server
            with <code>scripts/create_api_key.py --admin</code>.
          </p>
        </div>
        {error && (
          <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={busy || !key.trim()}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-700 py-2.5 text-sm font-semibold text-white hover:bg-blue-800 disabled:cursor-not-allowed disabled:bg-blue-700/50"
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
          Sign in
        </button>
      </form>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
