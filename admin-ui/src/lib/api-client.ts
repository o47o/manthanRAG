import { AuthError, authHeaders } from "./auth";
import type {
  ApiKeyCreated,
  ApiKeyInfo,
  ChunksResponse,
  Collection,
  ChunkStore,
  DocumentDetail,
  DocumentImagesResponse,
  DocumentListResponse,
  DocumentSummary,
  HealthResponse,
  JobStatus,
  PageStats,
  PipelineCreateInput,
  PipelineCreateResponse,
  PipelineDetail,
  PipelineRunListResponse,
  RetrieveRequest,
  RetrieveResponse,
} from "./types";

// Use empty base so requests go through Next.js proxy (/api/* → backend).
// Direct http://localhost:8000 bypasses the proxy and hits CORS on non-3000 ports.
const BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");

async function fail(res: Response, prefix = "API"): Promise<never> {
  const body = await res.text();
  let detail = body;
  try {
    const j = JSON.parse(body);
    if (j && typeof j.detail === "string") detail = j.detail;
  } catch {
    /* not JSON */
  }
  if (res.status === 401 || res.status === 403) throw new AuthError(res.status, detail);
  throw new Error(`${prefix} ${res.status}: ${detail}`);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
    // Our polling hooks (useDocumentImages, usePipeline, etc.) hit the exact
    // same URL every 1-1.2s while a run is live. Without this, the browser's
    // HTTP cache can serve back the FIRST response it saw for that URL (e.g.
    // "0 images" from before any figure was stored) on every later poll,
    // since the backend sends no Cache-Control header telling it not to.
    // React Query's own refetch logic is irrelevant once that happens — the
    // fetch never reaches the network. Always bypass the HTTP cache.
    cache: "no-store",
  });
  if (!res.ok) await fail(res);
  return res.json() as Promise<T>;
}

export const apiClient = {
  createPipeline: async (
    input: PipelineCreateInput
  ): Promise<PipelineCreateResponse> => {
    const form = new FormData();
    form.append("name", input.name);
    if (input.description) form.append("description", input.description);
    form.append("source", input.source ?? "admin-ui");
    form.append("collection_id", String(input.collection_id));
    for (const file of input.files) form.append("files", file);

    const res = await fetch(`${BASE}/api/v1/ingest/pipeline`, {
      method: "POST",
      body: form,
      headers: authHeaders(),
    });
    if (!res.ok) await fail(res, "Upload failed");
    return res.json();
  },

  listPipelines: (params?: {
    page?: number;
    limit?: number;
  }): Promise<PipelineRunListResponse> => {
    const qs = new URLSearchParams();
    if (params?.page) qs.set("page", String(params.page));
    if (params?.limit) qs.set("limit", String(params.limit));
    return request<PipelineRunListResponse>(`/api/v1/ingest/pipelines?${qs}`);
  },

  getJobStatus: (jobId: string): Promise<JobStatus> =>
    request<JobStatus>(`/api/v1/ingest/status/${jobId}`),

  listDocuments: (params?: {
    page?: number;
    limit?: number;
    status?: string;
    document_type?: string;
    collection_id?: number;
  }): Promise<DocumentListResponse> => {
    const qs = new URLSearchParams();
    if (params?.collection_id) qs.set("collection_id", String(params.collection_id));
    if (params?.page) qs.set("page", String(params.page));
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.status) qs.set("status", params.status);
    if (params?.document_type) qs.set("document_type", params.document_type);
    return request<DocumentListResponse>(`/api/v1/documents?${qs}`);
  },

  getDocument: (id: string): Promise<DocumentDetail> =>
    request<DocumentDetail>(`/api/v1/documents/${id}`),

  getPageStats: (id: string): Promise<PageStats> =>
    request<PageStats>(`/api/v1/documents/${id}/page-stats`),

  getDocumentImages: (id: string): Promise<DocumentImagesResponse> =>
    request<DocumentImagesResponse>(`/api/v1/documents/${id}/images`),

  getDocumentChunks: (
    id: string,
    store: ChunkStore = "vector",
    page = 1,
    limit = 50,
  ): Promise<ChunksResponse> =>
    request<ChunksResponse>(
      `/api/v1/documents/${id}/chunks?store=${store}&page=${page}&limit=${limit}`,
    ),

  deleteDocument: (id: string): Promise<{ deleted: boolean }> =>
    request(`/api/v1/documents/${id}`, { method: "DELETE" }),

  reprocessDocument: (id: string): Promise<{ document_id: string; job_id: string; pipeline_run_id: string; status: string }> =>
    request(`/api/v1/ingest/documents/${id}/reprocess`, { method: "POST" }),

  deletePipeline: (runId: string): Promise<{ deleted: boolean; run_id: string; documents_deleted: number }> =>
    request(`/api/v1/ingest/pipelines/${runId}`, { method: "DELETE" }),

  getPipeline: (runId: string): Promise<PipelineDetail> =>
    request<PipelineDetail>(`/api/v1/ingest/pipelines/${runId}`),

  renamePipeline: (runId: string, name: string): Promise<{ run_id: string; name: string }> =>
    request(`/api/v1/ingest/pipelines/${runId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  clearAllPipelines: (): Promise<{ deleted: number }> =>
    request("/api/v1/ingest/pipelines", { method: "DELETE" }),

  getHealth: (): Promise<HealthResponse> =>
    request<HealthResponse>("/api/v1/health"),

  // ── Retrieval (what the customer's application calls) ─────────────────────

  retrieve: (body: RetrieveRequest): Promise<RetrieveResponse> =>
    request<RetrieveResponse>("/api/v1/retrieve", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ── Admin: collections and keys ───────────────────────────────────────────

  listCollections: (): Promise<Collection[]> =>
    request<Collection[]>("/api/v1/admin/collections"),

  createCollection: (body: { name: string; description?: string }): Promise<Collection> =>
    request<Collection>("/api/v1/admin/collections", { method: "POST", body: JSON.stringify(body) }),

  updateCollection: (
    id: number,
    body: { name?: string; description?: string; is_active?: boolean },
  ): Promise<Collection> =>
    request<Collection>(`/api/v1/admin/collections/${id}`, { method: "PATCH", body: JSON.stringify(body) }),

  listKeys: (): Promise<ApiKeyInfo[]> => request<ApiKeyInfo[]>("/api/v1/admin/keys"),

  createKey: (body: {
    name: string;
    is_admin: boolean;
    collection_ids: number[];
    expires_at?: string | null;
  }): Promise<ApiKeyCreated> =>
    request<ApiKeyCreated>("/api/v1/admin/keys", { method: "POST", body: JSON.stringify(body) }),

  revokeKey: (id: number): Promise<ApiKeyInfo> =>
    request<ApiKeyInfo>(`/api/v1/admin/keys/${id}/revoke`, { method: "POST" }),
};
