import type {
  BlenderJob,
  BlenderScene,
  BlenderStatus,
  ComfyStatus,
  CompositeJob,
  Dashboard,
  DeliveryInfo,
  Decision,
  ExternalPackage,
  Edit,
  GenJob,
  GraphData,
  ProductionStats,
  PromptBlock,
  PromptOverview,
  ShotPromptDetail,
  Project,
  RecipeInfo,
  SequenceDetail,
  SequenceSummary,
  Shot,
  Task,
  VersionDetail,
  VerifyResult,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
const put = <T>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const api = {
  projects: () => request<Project[]>("/api/projects"),
  sequences: (projectId: number) => request<SequenceSummary[]>(`/api/projects/${projectId}/sequences`),
  sequence: (id: number) => request<SequenceDetail>(`/api/sequences/${id}`),
  shots: (sequenceId: number) => request<Shot[]>(`/api/sequences/${sequenceId}/shots`),
  shot: (id: number) => request<Shot>(`/api/shots/${id}`),
  createSequence: (projectId: number, body: {
    code: string;
    name: string;
    target_duration_s: number | null;
    shots: { code: string; description: string | null }[];
  }) => post<{ id: number; code: string; name: string }>(`/api/projects/${projectId}/sequences`, body),
  setFrameSize: (sequenceId: number, body: { width: number | null; height: number | null }) =>
    put<{ id: number; width: number; height: number }>(`/api/sequences/${sequenceId}/frame-size`, body),
  addShot: (sequenceId: number, body: { code: string; description: string | null }) =>
    post<Shot>(`/api/sequences/${sequenceId}/shots`, body),
  createEdit: (sequenceId: number, name: string) =>
    post<{ id: number; name: string; sequence_id: number }>(`/api/sequences/${sequenceId}/edits`, { name }),
  importVersion: async (shotId: number, file: File, fields: { parent_version_id: string; note: string; author: string }) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(fields).forEach(([k, v]) => form.append(k, v));
    const res = await fetch(`/api/shots/${shotId}/versions/import`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    }
    return res.json() as Promise<{ version_id: number; version_code: string; qc: string | null }>;
  },
  version: (id: number) => request<VersionDetail>(`/api/versions/${id}`),
  dashboard: () => request<Dashboard>("/api/dashboard"),
  addNote: (versionId: number, note: { author: string; body: string; decision: Decision; frame: number | null }) =>
    post<VersionDetail>(`/api/versions/${versionId}/notes`, note),
  runQC: (versionId: number) => post<Task>(`/api/versions/${versionId}/qc`, {}),
  edit: (id: number) => request<Edit>(`/api/edits/${id}`),
  cloneEdit: (id: number, name: string) => post<Edit>(`/api/edits/${id}/clone`, { name }),
  updateEdit: (id: number, body: { entries?: unknown[]; settings?: unknown }) =>
    request<Edit>(`/api/edits/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  colorSuggestion: (id: number, reference: string) =>
    request<{ reference: string; brightness: Record<string, number> }>(
      `/api/edits/${id}/color-suggestion?reference=${encodeURIComponent(reference)}`,
    ),
  assemble: (id: number, versionCode: string | null) =>
    post<Task>(`/api/edits/${id}/assemble`, { version_code: versionCode || null }),
  task: (id: string) => request<Task>(`/api/tasks/${id}`),
  comfyStatus: () => request<ComfyStatus>("/api/comfy/status"),
  comfyStart: () => post<ComfyStatus>("/api/comfy/start", {}),
  comfyStop: () => post<{ stopped: boolean }>("/api/comfy/stop", {}),
  recipes: () => request<RecipeInfo[]>("/api/recipes"),
  syncRecipes: () => post<unknown>("/api/recipes/sync", {}),
  recipeGraph: (recipeVersionId: number) => request<GraphData>(`/api/recipe-versions/${recipeVersionId}/graph`),
  generate: (body: {
    recipe_version_id: number;
    shot_id: number;
    values: Record<string, unknown>;
    seeds?: number[] | null;
    strengths?: number[] | null;
    source_version_id?: number | null;
    source_frame?: number;
  }) => post<GenJob[]>("/api/generate", body),
  fix: (versionId: number, body: { frame: number; mask_png: string; values: Record<string, unknown>; seeds: number[] }) =>
    post<GenJob[]>(`/api/versions/${versionId}/fix`, body),
  jobs: (params: { batch?: string; shot_id?: number; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.batch) q.set("batch", params.batch);
    if (params.shot_id) q.set("shot_id", String(params.shot_id));
    if (params.limit) q.set("limit", String(params.limit));
    return request<GenJob[]>(`/api/generation/jobs?${q}`);
  },
  job: (id: number) => request<GenJob>(`/api/generation/jobs/${id}`),
  registerOutput: (jobId: number, index: number) =>
    post<{ version_id: number }>(`/api/generation/jobs/${jobId}/register/${index}`, {}),
  cancelJob: (jobId: number) => post<GenJob>(`/api/generation/jobs/${jobId}/cancel`, {}),
  deliveries: () => request<DeliveryInfo[]>("/api/deliveries"),
  delivery: (id: number) => request<DeliveryInfo>(`/api/deliveries/${id}`),
  createDelivery: (body: { name: string; scope: string; sequence_ids?: number[]; version_ids?: number[] }) =>
    post<DeliveryInfo>("/api/deliveries", body),
  verifyDelivery: (id: number) => post<VerifyResult>(`/api/deliveries/${id}/verify`, {}),
  productionStats: () => request<ProductionStats>("/api/stats/production"),
  blenderStatus: () => request<BlenderStatus>("/api/blender/status"),
  blenderPickFile: () => post<{ path: string | null }>("/api/blender/pick-file", {}),
  blenderScenes: () => request<BlenderScene[]>("/api/blender/scenes"),
  blenderInspect: (path: string) => post<Task>("/api/blender/scenes/inspect", { path }),
  blenderJobs: (purpose?: "passes" | "layer") => request<BlenderJob[]>(`/api/blender/jobs${purpose ? `?purpose=${purpose}` : ""}`),
  blenderJob: (id: number) => request<BlenderJob>(`/api/blender/jobs/${id}`),
  blenderRender: (body: {
    purpose: "passes" | "layer";
    scene_path: string;
    shot_id?: number | null;
    frame?: number | null;
    frame_start?: number | null;
    frame_end?: number | null;
  }) => post<BlenderJob>("/api/blender/jobs", body),
  cancelBlenderJob: (id: number) => post<BlenderJob>(`/api/blender/jobs/${id}/cancel`, {}),
  guideRecipe: () => request<RecipeInfo>("/api/blender/guide-recipe"),
  guide: (body: { pass_job_id: number; values: Record<string, unknown>; seeds: number[] }) =>
    post<GenJob[]>("/api/blender/guide", body),
  createComposite: (body: CompositeBody) => post<CompositeJob>("/api/composite", body),
  compositeJob: (id: number) => request<CompositeJob>(`/api/composite/jobs/${id}`),
  compositeJobs: () => request<CompositeJob[]>("/api/composite/jobs"),
  prompts: (sequenceId: number) => request<PromptOverview>(`/api/sequences/${sequenceId}/prompts`),
  setPromptBlock: (sequenceId: number, key: string, body: { label: string; place: string; text: string }) =>
    put<PromptBlock>(`/api/sequences/${sequenceId}/prompt-blocks/${encodeURIComponent(key)}`, body),
  deletePromptBlock: (sequenceId: number, key: string) =>
    request<{ deleted: string }>(`/api/sequences/${sequenceId}/prompt-blocks/${encodeURIComponent(key)}`, { method: "DELETE" }),
  applyPromptBlocks: (sequenceId: number, body: { use: string; add: string[]; remove: string[] }) =>
    post<{ changed: number }>(`/api/sequences/${sequenceId}/prompts/apply-blocks`, body),
  shotPrompt: (shotId: number, use: string) => request<ShotPromptDetail>(`/api/shots/${shotId}/prompt/${use}`),
  setShotPrompt: (shotId: number, use: string, body: { scene: string; block_keys: string[] }) =>
    put<ShotPromptDetail>(`/api/shots/${shotId}/prompt/${use}`, body),
  packages: () => request<ExternalPackage[]>("/api/external/packages"),
  createPackage: (body: { version_id: number; prompt: string; model: string; duration_s: number }) =>
    post<ExternalPackage>("/api/external/packages", body),
  openPackageFolder: (id: number) => post<{ opened: string }>(`/api/external/packages/${id}/open-folder`, {}),
  importPackageResult: async (id: number, file: File, fields: { credits: string; external_id: string; note: string }) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(fields).forEach(([k, v]) => form.append(k, v));
    const res = await fetch(`/api/external/packages/${id}/result`, { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail));
    }
    return res.json() as Promise<ExternalPackage & { version_code: string; qc: string | null }>;
  },
};

export interface CompositeBody {
  version_id: number;
  layer_job_id: number;
  opacity: number;
  mode: "screen" | "over";
  offset: number;
  loop: boolean;
  frame: number;
}

/** Composite preview is a POST that returns a JPEG; shown through an object URL. */
export async function compositePreview(body: CompositeBody): Promise<string> {
  const res = await fetch("/api/composite/preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, typeof detail.detail === "string" ? detail.detail : JSON.stringify(detail.detail));
  }
  return URL.createObjectURL(await res.blob());
}

export const media = {
  proxy: (versionId: number) => `/api/versions/${versionId}/proxy`,
  thumbnail: (versionId: number) => `/api/versions/${versionId}/thumbnail`,
  frame: (versionId: number, frame: number) => `/api/versions/${versionId}/frame/${frame}`,
};

export const ACTIVE_JOB = new Set(["queued", "running", "cancel_requested"]);

/** Poll a background task until it finishes. */
export async function waitForTask(id: string, onUpdate?: (t: Task) => void): Promise<Task> {
  for (;;) {
    const task = await api.task(id);
    onUpdate?.(task);
    if (task.status === "done" || task.status === "failed") return task;
    await new Promise((r) => setTimeout(r, 1000));
  }
}
