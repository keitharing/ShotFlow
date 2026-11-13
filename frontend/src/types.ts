export type Severity = "error" | "warning" | "info";
export type VersionStatus = "pending" | "in_review" | "approved" | "rejected" | "revision_requested";
export type Decision = "approve" | "reject" | "revise" | "comment";

export interface Project {
  id: number;
  code: string;
  name: string;
  fps: number;
  width: number;
  height: number;
  target_lufs: number;
  sequences: number;
}

export interface SequenceSummary {
  id: number;
  code: string;
  name: string;
  order: number;
  target_duration_s: number | null;
  shots: number;
  approved: number;
  edits: number[];
}

export interface SequenceDetail extends SequenceSummary {
  project: Project;
  edit_list: { id: number; name: string; result_version_id: number | null; entries: number }[];
}

export interface QCCounts {
  error: number;
  warning: number;
  info: number;
}

export interface VersionBrief {
  id: number;
  code: string;
  status: VersionStatus;
  origin: string;
  parent_id: number | null;
  duration_s: number | null;
  frame_count: number | null;
  sha_verified: boolean | null;
  qc_status: string | null;
  qc_counts: QCCounts;
  media_kind: "image" | "video";
}

export interface Shot {
  id: number;
  code: string;
  kind: "shot" | "edit";
  order: number;
  description: string | null;
  approved_version_id: number | null;
  versions: VersionBrief[];
}

export interface QCIssue {
  id: number;
  check: string;
  severity: Severity;
  start_frame: number | null;
  end_frame: number | null;
  value: number | null;
  threshold: number | null;
  message: string;
}

export interface QCRun {
  id: number;
  status: string;
  created_at: string;
  duration_s: number | null;
  error: string | null;
  measurements: Record<string, unknown>;
  issues: QCIssue[];
}

export interface GenerationJob {
  id: number;
  provider: string;
  model: string | null;
  status: string;
  external_id: string | null;
  prompt: string | null;
  params: Record<string, unknown>;
  seed: number | null;
  credits: number | null;
  error: string | null;
  source_record: string | null;
}

export interface Note {
  id: number;
  author: string;
  body: string;
  frame: number | null;
  decision: Decision | null;
  source: string;
  created_at: string;
}

export interface VersionDetail extends VersionBrief {
  shot: { id: number; code: string; kind: string };
  sequence: { id: number; code: string; name: string };
  file_name: string;
  sha256: string | null;
  sha256_expected: string | null;
  bytes: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  video_codec: string | null;
  has_audio: boolean | null;
  created_at: string;
  generation_job: GenerationJob | null;
  notes: Note[];
  qc: QCRun | null;
  media_url: string;
}

export interface EditEntry {
  order: number;
  version_id: number;
  shot_code: string;
  version_code: string;
  src_in: number;
  src_out: number;
  frames: number;
  record_in: number;
  transition: "cut" | "dissolve";
  transition_frames: number;
  color: Record<string, number>;
}

export interface EndCard {
  title: string;
  subtitle: string;
  duration_s: number;
  transition: "cut" | "dissolve";
  transition_frames: number;
}

export interface RenderInfo {
  version_id: number;
  rendered_at: string;
  seconds: number;
  planned_frames: number;
  actual_frames: number;
  cut_frames: number[];
  dissolve_ranges: number[][];
  static_ranges: number[][];
}

export interface EditSettings {
  fade_in_s?: number;
  fade_out_s?: number;
  end_card?: EndCard | null;
  entry_notes?: Record<string, string>;
  based_on_edit_id?: number;
  renders?: Record<string, RenderInfo>;
  boundary_sheet?: string;
  boundaries?: { from_shot: string; to_shot: string; delta_e: number; flagged: boolean }[];
}

export interface Edit {
  id: number;
  name: string;
  sequence_id: number;
  result_version_id: number | null;
  total_frames: number;
  settings: EditSettings;
  entries: EditEntry[];
  boundary_sheet_url: string;
}

export interface Task {
  id: string;
  kind: string;
  target: string;
  status: "queued" | "running" | "done" | "failed";
  result: unknown;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Activity {
  at: string;
  actor: string;
  action: string;
  target_type: string | null;
  target_id: number | null;
  detail: Record<string, unknown>;
}

export interface Dashboard {
  shots: number;
  shots_approved: number;
  versions: number;
  version_status: Record<string, number>;
  credits_total: number;
  generation_jobs: Record<string, number>;
  qc_versions_checked: number;
  qc_issues: Partial<Record<Severity, number>>;
  recent_activity: Activity[];
}

export interface ExposedInput {
  key: string;
  node_id: string;
  node_title: string;
  label: string;
  input: string;
  type: string;
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  options: unknown[] | null;
  multiline: boolean;
}

export interface RecipeInfo {
  id: number;
  key: string;
  name: string;
  kind: string;
  description: string | null;
  latest_version_id: number;
  version: number;
  versions: { id: number; number: number; created_at: string }[];
  exposed_inputs: ExposedInput[];
  required_models: { node: string; input: string; file: string; folder: string; present: boolean; bytes: number | null }[];
  bindings: string[];
  output_kind: "image" | "frames";
  strength_input: { node_title: string; input: string } | null;
  limits: { max_area: number; length_min: number; length_max: number; multiple: number } | null;
}

export interface JobOutput {
  index: number;
  url: string;
  kind: "image" | "video";
  version_id: number | null;
}

export interface GenJob {
  id: number;
  batch_id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "cancel_requested" | "interrupted";
  recipe: string;
  recipe_name: string;
  recipe_version: number;
  kind: string;
  purpose: "generate" | "fix" | "publish";
  shot_id: number;
  shot_code: string | null;
  source_version_id: number | null;
  source_frame: number;
  seed: number | null;
  values: Record<string, unknown>;
  progress: number;
  stage: string | null;
  error: string | null;
  prompt: string | null;
  model: string | null;
  models: { file: string; sha256: string | null; bytes?: number }[] | null;
  oom_retry: string[] | null;
  seconds: number | null;
  peak_vram_mb: number | null;
  peak_temp_c: number | null;
  external_id: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  outputs: JobOutput[];
  graph?: Record<string, { class_type: string; inputs: Record<string, unknown>; _meta?: { title?: string } }>;
}

export interface GpuSample {
  temp_c: number;
  vram_used_mb: number;
  vram_total_mb: number;
  util_pct: number;
  name: string;
  ram_free_gib: number;
}

export interface ComfyStatus {
  running: boolean;
  starting?: boolean;
  installed?: boolean;
  url?: string;
  pid?: number;
  version?: string | null;
  extra_args?: string[];
  gpu: GpuSample | null;
}

export interface GraphData {
  nodes: { id: string; class_type: string; title: string; inputs: Record<string, unknown> }[];
  links: { from: string; slot: number; to: string; input: string }[];
}

export interface DeliveryInfo {
  id: number;
  name: string;
  naming_rule: string;
  folder: string;
  verified: boolean | null;
  files: number;
  created_at: string | null;
  items?: { id: number; file: string; sha256: string; version_id: number }[];
}

export interface VerifyResult {
  ok: boolean;
  checked: number;
  results: { file: string; status: "ok" | "missing" | "changed" }[];
  extra_files: string[];
  verified_at: string;
}

export interface ProductionStats {
  local_jobs: number;
  local_completed: number;
  local_failed: number;
  gpu_seconds: number;
  peak_temp_c: number | null;
  peak_vram_mb: number | null;
  recipes: { recipe: string; jobs: number; completed: number; failed: number; avg_seconds: number | null }[];
  external_jobs: number;
  external_credits: number;
  review_decisions: Record<string, number>;
  deliveries: number;
}

/** 3D integration (Blender, layer composites, external generation) */
export interface BlenderStatus {
  installed: boolean;
  exe: string | null;
  version: string | null;
}

export interface BlenderScene {
  path: string;
  name: string;
  exists: boolean;
  sample: boolean;
  changed?: boolean;
  inspected_at: string | null;
  preview: string | null;
  camera?: string | null;
  cameras?: string[];
  frame_start?: number;
  frame_end?: number;
  frame_current?: number;
  fps?: number;
  resolution?: [number, number, number];
  meshes?: number;
  mask_objects?: number;
  transparent?: boolean;
  blender?: string;
}

export interface BlenderJob {
  id: number;
  purpose: "passes" | "layer";
  status: GenJob["status"];
  scene: string;
  scene_name: string;
  shot_id: number | null;
  frame: number | null;
  frame_start: number | null;
  frame_end: number | null;
  width: number;
  height: number;
  progress: number;
  stage: string | null;
  error: string | null;
  seconds: number | null;
  model: string | null;
  camera: string | null;
  scene_sha256: string | null;
  peak_vram_mb: number | null;
  peak_temp_c: number | null;
  frame_count: number | null;
  depth: { near_m: number; far_m: number; coverage: number } | null;
  beauty_version_id: number | null;
  files: Partial<Record<"beauty" | "depth" | "mask" | "preview", string>>;
  created_at: string | null;
  finished_at: string | null;
}

export interface CompositeJob {
  id: number;
  status: GenJob["status"];
  progress: number;
  stage: string | null;
  error: string | null;
  seconds: number | null;
  shot_id: number;
  source_version_id: number;
  layer_job_id: number;
  layer_scene: string | null;
  opacity: number;
  mode: "screen" | "over";
  offset: number;
  version_id: number | null;
  created_at: string | null;
}

// ---- prompt management (week 8)
export interface PromptBlock {
  id: number;
  key: string;
  label: string;
  place: string;
  order: number;
  text: string;
  chars: number;
  updated_at: string | null;
}

export interface PromptPart {
  key: string;
  label: string;
  place: "before" | "scene" | "after" | "negative";
  text: string;
}

export interface ComposedPrompt {
  use: string;
  scene: string;
  block_keys: string[];
  note: string | null;
  text: string;
  negative: string;
  chars: number;
  parts: PromptPart[];
  scene_missing: boolean;
  updated_at: string | null;
}

export interface PromptShot {
  shot_id: number;
  code: string;
  description: string | null;
  prompts: Record<string, ComposedPrompt & { jobs: number }>;
}

export interface PromptOverview {
  sequence: { id: number; code: string; name: string };
  uses: { key: string; label: string }[];
  places: { key: string; label: string }[];
  blocks: PromptBlock[];
  shots: PromptShot[];
}

export interface PromptHistory {
  id: number;
  use: string;
  sha256: string;
  text: string;
  negative: string | null;
  created_at: string | null;
  jobs: { id: number; provider: string; model: string | null; status: string; purpose: string | null; recipe_name: string | null; credits: number | null; version_ids: number[] }[];
}

export interface ShotPromptDetail extends ComposedPrompt {
  shot_id: number;
  code: string;
  history: PromptHistory[];
}

export interface ExternalPackage {
  id: number;
  kind: "video" | "image";
  status: "awaiting_result" | "completed";
  model: string;
  model_name: string;
  prompt: string;
  shot_id: number;
  source_version_id: number;
  duration_s: number;
  folder: string;
  credits: number | null;
  external_id: string | null;
  version_id: number | null;
  start_frame_url: string;
  created_at: string | null;
  finished_at: string | null;
}
