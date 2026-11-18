"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, Copy, KeyRound } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import type { ApiKeyCreated } from "@/lib/types";
import { Button, Card, ErrorNote, PageTitle, fmtDate, inputCls } from "@/components/common/ui";
import { cn } from "@/lib/utils";

export default function KeysPage() {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: ["keys"], queryFn: apiClient.listKeys });
  const collections = useQuery({ queryKey: ["collections"], queryFn: apiClient.listCollections });
  const names = new Map((collections.data ?? []).map((c) => [c.id, c.name]));

  const [name, setName] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [selected, setSelected] = useState<number[]>([]);
  const [expires, setExpires] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [showRevoked, setShowRevoked] = useState(false);

  const create = useMutation({
    mutationFn: () =>
      apiClient.createKey({
        name: name.trim(),
        is_admin: isAdmin,
        collection_ids: isAdmin ? [] : selected,
        expires_at: expires ? new Date(expires).toISOString() : null,
      }),
    onSuccess: (k) => {
      setCreated(k);
      setCopied(false);
      setName("");
      setSelected([]);
      setIsAdmin(false);
      setExpires("");
      qc.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: number) => apiClient.revokeKey(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["keys"] }),
  });

  const canCreate = name.trim() && (isAdmin || selected.length > 0) && !create.isPending;
  const rows = (keys.data ?? []).filter((k) => showRevoked || !k.revoked_at);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageTitle
        title="API keys"
        subtitle="Give the customer application a key scoped to its collections. Keep admin keys for this console."
      />

      <Card className="space-y-4 p-5">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr]">
          <input className={inputCls} placeholder="Name, e.g. Customer agent – production" value={name} onChange={(e) => setName(e.target.value)} />
          <input
            className={inputCls}
            type="datetime-local"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            title="Expiry (optional)"
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
          Admin key (manages keys and collections, sees every collection)
        </label>
        {!isAdmin && (
          <div>
            <p className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-300">Collections this key can use</p>
            <div className="flex flex-wrap gap-2">
              {(collections.data ?? []).map((c) => {
                const on = selected.includes(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => setSelected((s) => (on ? s.filter((x) => x !== c.id) : [...s, c.id]))}
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm",
                      on
                        ? "border-blue-400 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                        : "border-gray-200 text-gray-600 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-300 dark:hover:bg-gray-800"
                    )}
                  >
                    {c.name}
                  </button>
                );
              })}
              {collections.data?.length === 0 && <span className="text-sm text-gray-400">Create a collection first.</span>}
            </div>
          </div>
        )}
        <Button onClick={() => create.mutate()} disabled={!canCreate}>
          <KeyRound className="h-4 w-4" /> Create key
        </Button>
        <ErrorNote error={create.error} />
        {created && (
          <div className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950">
            <p className="text-sm font-semibold text-amber-800 dark:text-amber-200">
              Copy this key now — it is shown only once.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all rounded bg-white px-2 py-1.5 text-xs dark:bg-gray-900">{created.api_key}</code>
              <Button
                variant="secondary"
                onClick={async () => {
                  await navigator.clipboard.writeText(created.api_key);
                  setCopied(true);
                }}
              >
                {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                {copied ? "Copied" : "Copy"}
              </Button>
            </div>
            <Button variant="secondary" onClick={() => setCreated(null)}>
              Done
            </Button>
          </div>
        )}
      </Card>

      <ErrorNote error={keys.error || revoke.error} />
      <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-400">
        <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} />
        Show revoked keys
      </label>
      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs uppercase text-gray-500 dark:border-gray-800 dark:text-gray-400">
              <th className="px-4 py-2.5">Name</th>
              <th className="px-4 py-2.5">Key</th>
              <th className="px-4 py-2.5">Access</th>
              <th className="px-4 py-2.5">Last used</th>
              <th className="px-4 py-2.5">Expires</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {rows.map((k) => (
              <tr key={k.id} className={cn("border-b border-gray-50 last:border-0 dark:border-gray-800", k.revoked_at && "opacity-50")}>
                <td className="px-4 py-2.5 font-medium text-gray-900 dark:text-gray-100">{k.name}</td>
                <td className="px-4 py-2.5 font-mono text-xs text-gray-500">{k.key_prefix}…</td>
                <td className="px-4 py-2.5 text-gray-600 dark:text-gray-400">
                  {k.is_admin ? (
                    <span className="rounded-full bg-purple-50 px-2 py-0.5 text-xs font-medium text-purple-700 dark:bg-purple-950 dark:text-purple-300">
                      Admin
                    </span>
                  ) : (
                    k.collection_ids.map((id) => names.get(id) ?? `#${id}`).join(", ")
                  )}
                </td>
                <td className="px-4 py-2.5 text-gray-500">{fmtDate(k.last_used_at)}</td>
                <td className="px-4 py-2.5 text-gray-500">{fmtDate(k.expires_at)}</td>
                <td className="px-4 py-2.5 text-right">
                  {k.revoked_at ? (
                    <span className="text-xs text-gray-400">Revoked {fmtDate(k.revoked_at)}</span>
                  ) : (
                    <Button
                      variant="danger"
                      onClick={() => {
                        if (confirm(`Revoke "${k.name}"? Apps using it stop working within 30 seconds.`)) revoke.mutate(k.id);
                      }}
                    >
                      <Ban className="h-4 w-4" /> Revoke
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
