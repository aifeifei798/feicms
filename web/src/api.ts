export interface Source {
  id: number;
  name: string;
  url: string;
  api_format: string;
  enabled: boolean;
  priority: number;
  max_pages: number;
  tags: string;
  uptime: number;
  origin: string;
  subscription_url: string;
  health_status: string;
  latency_ms: number | null;
  last_collect_at: string | null;
  last_health_at: string | null;
  last_error: string | null;
  fail_streak: number;
  item_count: number;
  created_at: string;
  updated_at: string;
}

export interface Episode {
  name: string;
  url: string;
}

export interface Play {
  from_name: string;
  episodes: Episode[];
}

export interface Video {
  id: number;
  name: string;
  type_name: string;
  year: string;
  area: string;
  lang: string;
  pic: string;
  local_pic?: string | null;
  blurb: string;
  actor: string;
  director: string;
  remarks: string;
  source_count: number;
  last_updated: string;
  plays: Play[];
}

export interface VideoSummary {
  id: number;
  name: string;
  type_name: string;
  year: string;
  pic: string;
  local_pic?: string | null;
  remarks: string;
  source_count: number;
  last_updated: string;
}

export interface Stats {
  sources: number;
  enabled_sources: number;
  healthy_sources: number;
  videos: number;
  source_items: number;
  last_collect_at: string | null;
  by_source: { name: string; enabled: boolean; health: string; count: number }[];
  by_type: { type_name: string; count: number }[];
}

export interface Job {
  id: number;
  source_id: number;
  started_at: string;
  finished_at: string | null;
  pages: number;
  inserted: number;
  updated: number;
  skipped: number;
  error: string | null;
}

export interface CollectResult {
  source_id: number;
  source_name: string;
  pages: number;
  pagecount: number;
  total: number;
  inserted: number;
  updated: number;
  skipped: number;
  error: string | null;
  elapsed_ms: number;
}

export interface SourceProgress {
  source_id: number;
  name: string;
  status: "pending" | "running" | "done" | "failed";
  pages: number;
  pagecount: number;
  target: number;
  inserted: number;
  updated: number;
  skipped: number;
  elapsed_ms: number;
  current: boolean;
  enabled: boolean;
}

export interface CollectProgress {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  total_sources: number;
  done_sources: number;
  inserted: number;
  updated: number;
  skipped: number;
  failed_sources: number;
  error: string | null;
  sources: SourceProgress[];
}

export interface MergeResult {
  groups: number;
  removed: number;
  moved_items: number;
  fixed_signatures: number;
  dry_run: boolean;
  examples: { signature: string; name: string; keep: number; merge: number[] }[];
}

export interface HealthReport {
  checked: number;
  ok: number;
  dead: number;
  results: {
    source_id: number;
    name: string;
    ok: boolean;
    latency_ms: number;
    status_code: number;
    items: number;
    message: string;
  }[];
}

export interface Page<T> {
  total: number;
  page: number;
  limit: number;
  items: T[];
}

export interface Subscription {
  id: number;
  url: string;
  kind: string;
  enabled: boolean;
  last_sync_at: string | null;
  last_added: number;
  last_updated: number;
  last_entries: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

export interface SyncResult {
  subscription_id: number;
  url: string;
  entries: number;
  added: number;
  updated: number;
  unchanged: number;
  missing: string[];
  error: string | null;
  elapsed_ms: number;
}

export interface ImageRecord {
  id: number;
  url: string;
  host: string;
  status: string;
  path: string;
  ext: string;
  sha256: string;
  size_bytes: number;
  attempts: number;
  last_error: string | null;
  first_seen: string;
  downloaded_at: string | null;
}

export interface DownloadProgress {
  running: boolean;
  queued: number;
  downloaded: number;
  duplicates: number;
  failed: number;
  skipped: number;
  bytes: number;
  started_at: string | null;
  finished_at: string | null;
  elapsed_ms: number;
  errors: { host: string; error: string }[];
}

export interface ImageStats {
  image_dir: string;
  total: number;
  by_status: Record<string, number>;
  bytes: number;
  files_on_disk: number;
  shards_used: number;
  top_hosts: { host: string; count: number }[];
}

export interface ImageBatchResult {
  attempted: number;
  downloaded: number;
  duplicates: number;
  failed: number;
  skipped: number;
  bytes: number;
  elapsed_ms: number;
}

export interface ImageLayout {
  root: string;
  exists: boolean;
  files: number;
  buckets: number;
  busiest: number;
  top: { path: string; files: number }[];
}

export interface SourceImportResult {
  parsed: number;
  created: number;
  updated: number;
  skipped: string[];
}

export interface SchedulerState {
  auto_collect: boolean;
  auto_images: boolean;
  collect_interval_minutes: number;
  health_interval_minutes: number;
  image_interval_minutes: number;
  last_collect_at: string | null;
  last_health_at: string | null;
  last_image_at: string | null;
  last_collect_result: { sources: number; inserted: number; updated: number; failed: number } | null;
  collect_running: boolean;
  collect_progress: CollectProgress;
}

export interface McpToolResult {
  content: { type: string; text: string }[];
  structuredContent?: unknown;
  isError?: boolean;
}

export interface McpEnvelope {
  jsonrpc: string;
  id?: number | null;
  result?: McpToolResult;
  error?: { code: number; message: string };
}

const TOKEN_KEY = "feicms_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** fetch with the admin token attached (used by the API docs "试" buttons). */
export function authorizedFetch(url: string, init?: RequestInit): Promise<Response> {
  return fetch(url, {
    ...init,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(init?.headers ?? {}) },
  }).catch((e: Error) => {
    // Browser-level failure: the request never reached the API.
    throw new Error(`无法连接服务（${e.message}）。请确认服务正在运行、地址正确，或刷新页面重试。`);
  });
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authorizedFetch(url, init);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

/** Fetch a protected file (backup / source list) and save it with the browser. */
export async function downloadFile(url: string, filename: string): Promise<void> {
  const res = await authorizedFetch(url);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(link.href);
}

export interface AuthStatus {
  auth_required: boolean;
  authenticated: boolean;
  user: string | null;
}

export interface LoginResult {
  token: string | null;
  user: string;
  expires_in: number;
  message?: string;
}

export interface ApiKey {
  id: number;
  name: string;
  prefix: string;
  scopes: string;
  enabled: boolean;
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  usage_count: number;
}

export interface CreatedKey {
  key: string;
  id: number;
  name: string;
  scopes: string;
}

export interface McpTool {
  name: string;
  description: string;
  scopes: string[];
  inputSchema?: Record<string, unknown>;
}

export interface McpInfo {
  name: string;
  version: string;
  transport: string;
  endpoint: string;
  authenticated: boolean;
  tools: string[];
}

export interface Config {
  database_url: string;
  sources_file: string;
  subscriptions: string[];
  request_timeout: number;
  impersonate: string;
  request_delay: number;
  user_agent: string;
  concurrency: number;
  max_retries: number;
  default_pages: number;
  page_limit: number;
  full_page_cap: number;
  image_dir: string;
  image_batch: number;
  image_concurrency: number;
  image_delay: number;
  image_max_size: number;
  image_auto: boolean;
  image_interval_minutes: number;
  auto_collect: boolean;
  collect_interval_minutes: number;
  health_interval_minutes: number;
  subscribe_interval_minutes: number;
  health_fail_threshold: number;
  host: string;
  port: number;
  auth_required: boolean;
  admin_user: string;
}

export const api = {
  authStatus: () => request<AuthStatus>("/api/auth/status"),
  login: (username: string, password: string) =>
    request<LoginResult>("/api/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<{ ok: boolean }>("/api/logout", { method: "POST" }),
  stats: () => request<Stats>("/api/stats"),
  config: () => request<Config>("/api/config"),
  sources: (params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams(
      Object.entries(params).map(([k, v]) => [k, String(v)])
    ).toString();
    return request<Source[]>(`/api/sources${qs ? `?${qs}` : ""}`);
  },
  createSource: (body: Partial<Source>) => request<Source>("/api/sources", { method: "POST", body: JSON.stringify(body) }),
  updateSource: (id: number, body: Partial<Source>) =>
    request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSource: (id: number) => authorizedFetch(`/api/sources/${id}`, { method: "DELETE" }),
  toggleSource: (id: number, enabled: boolean) =>
    request<Source>(`/api/sources/${id}/enable`, { method: "POST", body: JSON.stringify({ enabled }) }),
  collectStart: (body: { pages?: number; names?: string[]; source_ids?: number[]; all?: boolean } = {}) =>
    request<CollectProgress>("/api/collect/start", { method: "POST", body: JSON.stringify(body) }),
  collectProgress: () => request<CollectProgress>("/api/collect/progress"),
  healthCheck: () => request<HealthReport>("/api/health-check", { method: "POST" }),
  videos: (params: Record<string, string | number>) => {
    const qs = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();
    return request<Page<VideoSummary>>(`/api/videos?${qs}`);
  },
  video: (id: number) => request<Video>(`/api/videos/${id}`),
  videoSources: (id: number) => request<{ source_id: number; source_name: string; source_vod_id: number; play_from: string; vod_time: string; episodes: { from: string; episodes: Episode[] }[] }[]>(`/api/videos/${id}/sources`),
  prune: () => request<{ removed: number }>("/api/prune", { method: "POST" }),
  repairPlays: () => request<{ total: number; changed: number; videos: number }>("/api/repair-plays", { method: "POST" }),
  mergeDuplicates: (dryRun = false) =>
    request<MergeResult>(`/api/merge-duplicates?dry_run=${dryRun}`, { method: "POST" }),
  subscriptions: () => request<Subscription[]>("/api/subscriptions"),
  createSubscription: (body: { url: string; kind?: string; enabled?: boolean }) =>
    request<Subscription>("/api/subscriptions", { method: "POST", body: JSON.stringify(body) }),
  deleteSubscription: (id: number) => authorizedFetch(`/api/subscriptions/${id}`, { method: "DELETE" }),
  syncSubscriptions: (id?: number) =>
    request<SyncResult[]>(`/api/subscriptions/sync${id ? `?subscription_id=${id}` : ""}`, { method: "POST" }),
  categories: () => request<{ type_name: string; count: number }[]>("/api/categories"),
  jobs: (limit = 50) => request<Job[]>(`/api/jobs?limit=${limit}`),
  imageStats: () => request<ImageStats>("/api/images/stats"),
  imageScan: () => request<{ queued: number; seen: number }>("/api/images/scan", { method: "POST" }),
  imageDownload: (body: { limit?: number; concurrency?: number; retry_failed?: boolean } = {}) =>
    request<ImageBatchResult>("/api/images/download", { method: "POST", body: JSON.stringify(body) }),
  downloadProgress: () => request<DownloadProgress>("/api/images/download/progress"),
  images: (params: Record<string, string | number> = {}) => {
    const entries = Object.entries(params).map(([k, v]) => [k, String(v)]);
    const qs = new URLSearchParams(entries).toString();
    return request<ImageRecord[]>(`/api/images${qs ? `?${qs}` : ""}`);
  },
  imagePrune: (deleteFiles = true) => request<{ removed: number; files_deleted: number }>(`/api/images/prune?delete_files=${deleteFiles}`, { method: "POST" }),
  imageRepair: () => request<{ restored: number; requeued: number; files_deleted: number }>("/api/images/repair", { method: "POST" }),
  imageLayout: (top = 12) => request<ImageLayout>(`/api/images/layout?top=${top}`),
  importSources: (text: string, maxPages?: number, enabled = true) =>
    request<SourceImportResult>(
      "/api/sources/import",
      { method: "POST", body: JSON.stringify({ text, max_pages: maxPages, enabled }) }
    ),
  scheduler: () => request<SchedulerState>("/api/scheduler"),
  setScheduler: (body: {
    auto_collect?: boolean;
    auto_images?: boolean;
    collect_interval_minutes?: number;
    health_interval_minutes?: number;
    image_interval_minutes?: number;
  }) => request<SchedulerState>("/api/scheduler", { method: "POST", body: JSON.stringify(body) }),
  setAdminPassword: (body: { username?: string; password?: string }) =>
    request<{ user: string; password: string; env_file: string; restart_required: boolean }>(
      "/api/admin/password",
      { method: "POST", body: JSON.stringify(body) }
    ),
  adminToken: (ttl = 3600) =>
    request<{ token: string; user: string; expires_in: number }>(`/api/admin/token?ttl=${ttl}`, { method: "POST" }),
  mcpCall: (name: string, args: Record<string, unknown> = {}) =>
    request<McpEnvelope>("/mcp", {
      method: "POST",
      body: JSON.stringify({ jsonrpc: "2.0", method: "tools/call", params: { name, arguments: args } }),
    }),
  apiKeys: () => request<ApiKey[]>("/api/apikeys"),
  mcpInfo: () => request<McpInfo>("/mcp"),
  mcpTools: () => request<McpTool[]>("/api/mcp/tools"),
  createApiKey: (body: { name: string; scopes?: string; expires_days?: number | null }) =>
    request<CreatedKey>("/api/apikeys", { method: "POST", body: JSON.stringify(body) }),
  updateApiKey: (id: number, body: { name?: string; scopes?: string; enabled?: boolean; expires_days?: number | null }) =>
    request<ApiKey>(`/api/apikeys/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteApiKey: (id: number) => authorizedFetch(`/api/apikeys/${id}`, { method: "DELETE" }),
};
