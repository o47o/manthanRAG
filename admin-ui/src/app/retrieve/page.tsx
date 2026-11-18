"use client";

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, Search } from "lucide-react";
import { apiClient } from "@/lib/api-client";
import type { RetrieveHit, RetrieveRequest, RetrieveResponse } from "@/lib/types";
import { Button, Card, ErrorNote, PageTitle, inputCls } from "@/components/common/ui";
import { cn } from "@/lib/utils";

const STORE_COLORS: Record<string, string> = {
  vector: "bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
  table: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
  clause: "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
  research: "bg-purple-50 text-purple-700 dark:bg-purple-950 dark:text-purple-300",
  image: "bg-pink-50 text-pink-700 dark:bg-pink-950 dark:text-pink-300",
  graph: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

export default function RetrievePage() {
  const collections = useQuery({ queryKey: ["collections"], queryFn: apiClient.listCollections });
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(10);
  const [selected, setSelected] = useState<number[]>([]);
  const [autoScope, setAutoScope] = useState(true);
  const [fullDoc, setFullDoc] = useState(true);
  const [picker, setPicker] = useState<"default" | "on" | "off">("default");
  const [related, setRelated] = useState(true);
  const [previousFamily, setPreviousFamily] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);

  const run = useMutation({
    mutationFn: (extra: Partial<RetrieveRequest>) =>
      apiClient.retrieve({
        query: query.trim(),
        top_k: topK,
        collection_ids: selected.length ? selected : undefined,
        auto_scope: autoScope,
        full_document: fullDoc,
        use_doc_picker: picker === "default" ? null : picker === "on",
        add_related_tables: related,
        previous_report_family: previousFamily,
        ...extra,
      }),
    onSuccess: (r) => {
      if (r.scope.report_family) setPreviousFamily(r.scope.report_family);
    },
  });
  const res = run.data;

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <PageTitle
        title="Retrieve tester"
        subtitle="Runs POST /api/v1/retrieve exactly as a customer application would, using your admin key."
      />

      <Card className="space-y-4 p-5">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (query.trim()) run.mutate({});
          }}
        >
          <input
            className={inputCls}
            placeholder="Ask a question about your documents…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Button type="submit" disabled={!query.trim() || run.isPending}>
            {run.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
            Retrieve
          </Button>
        </form>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-gray-700 dark:text-gray-300">
          <label className="flex items-center gap-2">
            top_k
            <input
              type="number"
              min={1}
              max={50}
              value={topK}
              onChange={(e) => setTopK(Math.max(1, Number(e.target.value) || 1))}
              className="w-16 rounded border border-gray-300 px-2 py-1 dark:border-gray-700 dark:bg-gray-950"
            />
          </label>
          <Toggle label="auto_scope" value={autoScope} onChange={setAutoScope} />
          <Toggle label="full_document" value={fullDoc} onChange={setFullDoc} />
          <Toggle label="add_related_tables" value={related} onChange={setRelated} />
          <label className="flex items-center gap-2">
            doc picker
            <select
              value={picker}
              onChange={(e) => setPicker(e.target.value as typeof picker)}
              className="rounded border border-gray-300 px-2 py-1 dark:border-gray-700 dark:bg-gray-950"
            >
              <option value="default">server default</option>
              <option value="on">on</option>
              <option value="off">off (rules only)</option>
            </select>
          </label>
          {previousFamily && (
            <span className="flex items-center gap-1 text-xs text-gray-500">
              previous_report_family: <b>{previousFamily}</b>
              <button className="underline" onClick={() => setPreviousFamily(null)} type="button">
                clear
              </button>
            </span>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <span className="text-sm text-gray-500 dark:text-gray-400">Collections:</span>
          {(collections.data ?? []).map((c) => {
            const on = selected.includes(c.id);
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => setSelected((s) => (on ? s.filter((x) => x !== c.id) : [...s, c.id]))}
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-xs",
                  on
                    ? "border-blue-400 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
                    : "border-gray-200 text-gray-600 dark:border-gray-700 dark:text-gray-300"
                )}
              >
                {c.name}
              </button>
            );
          })}
          {selected.length === 0 && <span className="text-xs text-gray-400">(none selected = all)</span>}
        </div>
      </Card>

      <ErrorNote error={run.error} />
      {res && <Result res={res} onPick={(label) => run.mutate({ report_family: label })} showJson={showJson} setShowJson={setShowJson} />}
    </div>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-1.5">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Result({
  res,
  onPick,
  showJson,
  setShowJson,
}: {
  res: RetrieveResponse;
  onPick: (label: string) => void;
  showJson: boolean;
  setShowJson: (v: boolean) => void;
}) {
  const s = res.scope;
  return (
    <div className="space-y-4">
      <Card className="space-y-2 p-4 text-sm">
        <div className="flex flex-wrap gap-x-6 gap-y-1 text-gray-700 dark:text-gray-300">
          <span>
            mode <b>{res.mode}</b>
          </span>
          <span>
            hits <b>{res.hits.length}</b> (candidates {res.total_candidates})
          </span>
          <span>
            took <b>{(res.took_ms / 1000).toFixed(2)} s</b>
          </span>
          <span>stores {res.stores_searched.join(", ") || "—"}</span>
        </div>
        <div className="text-gray-600 dark:text-gray-400">
          scope: {s.applied.length ? s.applied.join(" → ") : "whole collection(s)"}
          {s.picked_by && <> · picked by <b>{s.picked_by}</b></>}
          {s.reason && <> · “{s.reason}”</>}
        </div>
        {s.note && <div className="text-amber-700 dark:text-amber-300">{s.note}</div>}
        {s.choices.length > 0 && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 dark:border-blue-900 dark:bg-blue-950">
            <p className="mb-2 font-medium text-blue-800 dark:text-blue-200">Which report? (scope.choices)</p>
            <div className="flex flex-wrap gap-2">
              {s.choices.map((c) => (
                <Button key={c.label} variant="secondary" onClick={() => onPick(c.label)}>
                  {c.label} ({c.documents})
                </Button>
              ))}
            </div>
          </div>
        )}
        <button
          type="button"
          onClick={() => setShowJson(!showJson)}
          className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700"
        >
          {showJson ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Raw JSON
        </button>
        {showJson && (
          <pre className="max-h-96 overflow-auto rounded bg-gray-50 p-3 text-xs dark:bg-gray-950">
            {JSON.stringify(res, null, 2)}
          </pre>
        )}
      </Card>
      {res.hits.map((h) => (
        <HitCard key={h.rank} h={h} />
      ))}
      {res.hits.length === 0 && <p className="text-sm text-gray-500">No passages found.</p>}
    </div>
  );
}

function HitCard({ h }: { h: RetrieveHit }) {
  const [open, setOpen] = useState(false);
  const long = h.text.length > 600;
  return (
    <Card className="p-4">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-gray-900 dark:text-gray-100">#{h.rank}</span>
        <span className={cn("rounded px-1.5 py-0.5 font-medium", STORE_COLORS[h.store] ?? STORE_COLORS.graph)}>{h.store}</span>
        <span className="tabular-nums text-gray-500">score {h.score === null ? "—" : h.score.toFixed(3)}</span>
        <span className="text-gray-700 dark:text-gray-300">{h.document.name ?? "—"}</span>
        {h.page !== null && <span className="text-gray-500">p{h.page}{h.page_end && h.page_end !== h.page ? `–${h.page_end}` : ""}</span>}
        {h.document.version_no && h.document.version_no > 1 && <span className="text-gray-500">v{h.document.version_no}</span>}
        {h.section_title && <span className="text-gray-500">· {h.section_title}</span>}
        {h.added && <span className="rounded bg-gray-100 px-1.5 py-0.5 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{h.added}</span>}
        {h.document.periods.length > 0 && <span className="text-gray-400">{h.document.periods.join(", ")}</span>}
      </div>
      <pre className="whitespace-pre-wrap break-words font-sans text-sm text-gray-700 dark:text-gray-300">
        {long && !open ? h.text.slice(0, 600) + "…" : h.text}
      </pre>
      {long && (
        <button type="button" onClick={() => setOpen(!open)} className="mt-1 text-xs text-blue-600 hover:underline">
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </Card>
  );
}
