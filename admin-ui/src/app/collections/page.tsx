"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FolderPlus, Pencil, X } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import type { Collection } from "@/lib/types";
import { Button, Card, ErrorNote, PageTitle, fmtDate, inputCls } from "@/components/common/ui";
import { cn } from "@/lib/utils";

export default function CollectionsPage() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ["collections"], queryFn: apiClient.listCollections });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const create = useMutation({
    mutationFn: () => apiClient.createCollection({ name: name.trim(), description: description.trim() || undefined }),
    onSuccess: () => {
      setName("");
      setDescription("");
      qc.invalidateQueries({ queryKey: ["collections"] });
    },
  });

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageTitle
        title="Collections"
        subtitle="A collection groups documents. Each API key can read only the collections it is given."
      />

      <Card className="p-5">
        <form
          className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_2fr_auto]"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <input className={inputCls} placeholder="Name, e.g. Finance reports" value={name} onChange={(e) => setName(e.target.value)} />
          <input
            className={inputCls}
            placeholder="Description (optional)"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <Button type="submit" disabled={!name.trim() || create.isPending}>
            <FolderPlus className="h-4 w-4" /> Create
          </Button>
        </form>
        <div className="mt-3">
          <ErrorNote error={create.error} />
        </div>
      </Card>

      <ErrorNote error={error} />
      <Card>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 text-left text-xs uppercase text-gray-500 dark:border-gray-800 dark:text-gray-400">
              <th className="px-4 py-2.5">Id</th>
              <th className="px-4 py-2.5">Name</th>
              <th className="px-4 py-2.5">Description</th>
              <th className="px-4 py-2.5 text-right">Documents</th>
              <th className="px-4 py-2.5">Created</th>
              <th className="px-4 py-2.5">Active</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-gray-400">
                  Loading…
                </td>
              </tr>
            )}
            {data?.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-gray-400">
                  No collections yet.
                </td>
              </tr>
            )}
            {data?.map((c) => <Row key={c.id} c={c} />)}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Row({ c }: { c: Collection }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(c.name);
  const [description, setDescription] = useState(c.description ?? "");
  const update = useMutation({
    mutationFn: (body: { name?: string; description?: string; is_active?: boolean }) => apiClient.updateCollection(c.id, body),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["collections"] });
    },
  });

  return (
    <tr className={cn("border-b border-gray-50 last:border-0 dark:border-gray-800", !c.is_active && "opacity-60")}>
      <td className="px-4 py-2.5 tabular-nums text-gray-500">{c.id}</td>
      <td className="px-4 py-2.5 font-medium text-gray-900 dark:text-gray-100">
        {editing ? <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} /> : c.name}
      </td>
      <td className="px-4 py-2.5 text-gray-600 dark:text-gray-400">
        {editing ? (
          <input className={inputCls} value={description} onChange={(e) => setDescription(e.target.value)} />
        ) : (
          c.description || "—"
        )}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums">{c.document_count}</td>
      <td className="px-4 py-2.5 text-gray-500">{fmtDate(c.created_at)}</td>
      <td className="px-4 py-2.5">
        <button
          type="button"
          onClick={() => update.mutate({ is_active: !c.is_active })}
          className={cn(
            "rounded-full px-2 py-0.5 text-xs font-medium",
            c.is_active
              ? "bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300"
              : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"
          )}
          title={c.is_active ? "Deactivate (no new uploads)" : "Activate"}
        >
          {c.is_active ? "Active" : "Inactive"}
        </button>
      </td>
      <td className="px-4 py-2.5 text-right">
        {editing ? (
          <div className="flex justify-end gap-1">
            <Button variant="secondary" onClick={() => update.mutate({ name: name.trim(), description })} disabled={!name.trim()}>
              <Check className="h-4 w-4" />
            </Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              <X className="h-4 w-4" />
            </Button>
          </div>
        ) : (
          <Button variant="secondary" onClick={() => setEditing(true)} title="Edit">
            <Pencil className="h-4 w-4" />
          </Button>
        )}
        {update.error && <ErrorNote error={update.error} />}
      </td>
    </tr>
  );
}
