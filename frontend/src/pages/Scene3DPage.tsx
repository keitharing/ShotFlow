import { useEffect, useMemo, useRef, useState } from "react";
import { ACTIVE_JOB, api, compositePreview, media, waitForTask, type CompositeBody } from "../api";
import { Loading, Toolbar } from "../components/Bits";
import { Progress, Step, usePolled } from "../components/Steps";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import { LayoutFlow } from "./LayoutFlow";
import { kst } from "../format";
import { link } from "../router";
import type {
  BlenderJob,
  BlenderScene,
  CompositeJob,
  ExternalPackage,
  GenJob,
  RecipeInfo,
  SequenceSummary,
  Shot,
} from "../types";
import { useAsync } from "../useAsync";

type ShotRow = Shot & { seq: string };
type Tab = "layout" | "keyframe" | "layer" | "external";

const TABS: { key: Tab; label: string }[] = [
  { key: "layout", label: "애니메틱 → 블로킹" },
  { key: "keyframe", label: "3D 키프레임" },
  { key: "layer", label: "비·효과 레이어 합성" },
  { key: "external", label: "외부 생성 (Seedance)" },
];

const MOTION_PROMPT =
  "slow motion rain falls around the perfume bottle, water droplets slide down the glass, gentle slow push-in, cinematic lighting, the bottle shape, label-free glass and cap stay unchanged";

// ------------------------------------------------------------------ small building blocks

function Slider({ label, value, min, max, step, left, right, onChange }: {
  label: string; value: number; min: number; max: number; step: number; left: string; right: string; onChange: (v: number) => void;
}) {
  return (
    <label className="slider">
      <span className="slider-label">{label} <b className="num">{value.toFixed(2)}</b></span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="slider-ends faint"><span>{left}</span><span>{right}</span></span>
    </label>
  );
}

/** Poll a job while it is active; returns the latest copy. */
function sceneSummary(s: BlenderScene): string {
  if (!s.inspected_at) return "아직 확인하지 않은 장면";
  const res = s.resolution ? `${s.resolution[0]}×${s.resolution[1]}` : "";
  return [s.camera ? `카메라 ${s.camera}` : "카메라 없음", `프레임 ${s.frame_start}–${s.frame_end}`, res,
    `물체 ${s.meshes}개`, s.mask_objects ? `마스크 대상 ${s.mask_objects}개` : null].filter(Boolean).join(" · ");
}

// ------------------------------------------------------------------ scene picker (shared)

function ScenePicker({ selected, onSelect }: { selected: BlenderScene | null; onSelect: (s: BlenderScene) => void }) {
  const list = useAsync(() => api.blenderScenes(), []);
  const [pathText, setPathText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = async (path: string) => {
    setError(null);
    const known = list.data?.find((s) => s.path === path);
    if (known?.inspected_at && !known.changed) {
      onSelect(known);
      return;
    }
    setBusy("Blender로 장면을 확인하는 중입니다 · 처음에는 10~40초 걸립니다");
    try {
      const task = await waitForTask((await api.blenderInspect(path)).id);
      if (task.status === "failed") throw new Error(task.error?.split("\n")[0].replace(/^\w+Error: /, "") ?? "확인 실패");
      const fresh = await api.blenderScenes();
      list.reload();
      const found = fresh.find((s) => s.path === (task.result as BlenderScene).path);
      if (found) onSelect(found);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const pick = async () => {
    setError(null);
    try {
      const { path } = await api.blenderPickFile();
      if (path) await open(path);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (!list.data) return <p className="faint">불러오는 중</p>;
  return (
    <div className="col-gap">
      <div className="scene-grid">
        {list.data.filter((s) => s.exists).map((s) => (
          <button key={s.path} type="button" className={`scene-card${selected?.path === s.path ? " selected" : ""}`} onClick={() => open(s.path)} disabled={!!busy}>
            {s.preview ? <img src={`/api/blender/scenes/preview?path=${encodeURIComponent(s.path)}&t=${s.inspected_at}`} alt="" /> : <div className="thumb-empty"><Icon name="cube" size={20} /></div>}
            <span className="scene-name">{s.name}{s.sample && <span className="tag">예시</span>}</span>
            <span className="faint small">{s.changed ? "파일이 바뀜 · 다시 확인 필요" : sceneSummary(s)}</span>
          </button>
        ))}
      </div>
      <div className="row wrap">
        <button type="button" onClick={pick} disabled={!!busy}><Icon name="folder" />Blender 파일 선택</button>
        <span className="faint small">또는 경로 붙여넣기</span>
        <input className="grow" value={pathText} placeholder="예: D:\작업\병.blend" onChange={(e) => setPathText(e.target.value)} />
        <button type="button" disabled={!!busy || !pathText.trim()} onClick={() => open(pathText.trim().replace(/^"|"$/g, ""))}>확인</button>
      </div>
      {busy && <Progress value={0.15} stage={busy} />}
      {error && <p className="error-text small">{error}</p>}
      {selected && <p className="small muted">선택: <b>{selected.name}</b> · {sceneSummary(selected)}</p>}
    </div>
  );
}

function ShotSelect({ shots, value, onChange }: { shots: ShotRow[]; value: number | null; onChange: (id: number) => void }) {
  return (
    <select value={value ?? ""} onChange={(e) => onChange(Number(e.target.value))}>
      <option value="">샷 선택</option>
      {shots.map((s) => <option key={s.id} value={s.id}>{s.seq} · {s.code}</option>)}
    </select>
  );
}

// ------------------------------------------------------------------ external package card (shared)

function PackageCard({ pkg, onChange }: { pkg: ExternalPackage; onChange: (p: ExternalPackage) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [credits, setCredits] = useState("");
  const [externalId, setExternalId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(pkg.prompt);
      setMessage("프롬프트를 복사했습니다");
    } catch {
      setMessage("복사가 막혀 있습니다. 아래 글을 직접 선택해 복사하세요");
    }
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.importPackageResult(pkg.id, file, { credits, external_id: externalId, note: "" });
      onChange(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="package">
      <img src={pkg.start_frame_url} alt="시작 프레임" />
      <div className="col-gap">
        <div className="row">
          <b>#{pkg.id} · {pkg.model_name} · {pkg.kind === "image" ? "그림" : `${pkg.duration_s}초`}</b>
          <span className="spacer" />
          <span className={`status ${pkg.status === "completed" ? "approved" : "in_review"}`}><span>{pkg.status === "completed" ? "결과 등록됨" : "결과 기다리는 중"}</span></span>
        </div>
        {pkg.status === "completed" ? (
          <div className="row wrap">
            <span className="small muted">크레딧 {pkg.credits ?? "-"} · 작업 ID {pkg.external_id ?? "-"} · {kst(pkg.finished_at)}</span>
            <span className="spacer" />
            {pkg.version_id && <a className="button" href={link.shot(pkg.shot_id, pkg.version_id)}>버전으로 보기</a>}
            {pkg.version_id && <a className="button ghost" href={link.review(pkg.source_version_id, pkg.version_id)}><Icon name="compare" />시작 프레임과 비교</a>}
          </div>
        ) : (
          <>
            <ol className="steps-list small">
              <li>Higgsfield에서 <b>{pkg.model_name}</b>을 고르고 이 {pkg.kind === "image" ? "참고 그림 한 장만" : "시작 프레임을"} 올립니다.</li>
              <li>아래 프롬프트를 붙여 넣고, {pkg.kind === "image" ? "16:9 · 2K · 1장으로" : `길이 ${pkg.duration_s}초 · 16:9로`} 생성합니다 (크레딧 차감).</li>
              <li>받은 {pkg.kind === "image" ? "그림" : "영상"}을 아래에 끌어다 놓고, 차감된 크레딧을 적습니다.</li>
            </ol>
            <div className="prompt">{pkg.prompt}</div>
            <div className="row wrap">
              <button type="button" onClick={copy}><Icon name="copy" />프롬프트 복사</button>
              <button type="button" onClick={() => api.openPackageFolder(pkg.id).catch((err) => setError((err as Error).message))}><Icon name="folder" />폴더 열기</button>
              {message && <span className="faint small">{message}</span>}
            </div>
            <label
              className={`dropzone${drag ? " over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => { e.preventDefault(); setDrag(false); setFile(e.dataTransfer.files[0] ?? null); }}
            >
              <input type="file" accept={pkg.kind === "image" ? "image/png,image/jpeg,image/webp" : "video/mp4,video/quicktime,video/webm"} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <Icon name="upload" />
              <span>{file ? `${file.name} · ${(file.size / 1e6).toFixed(1)}MB` : "받은 영상을 여기에 끌어다 놓거나 눌러서 고르세요"}</span>
            </label>
            <div className="form-grid">
              <label>차감된 크레딧<input value={credits} inputMode="decimal" placeholder="예: 60" onChange={(e) => setCredits(e.target.value)} /></label>
              <label>작업 ID (있으면)<input value={externalId} onChange={(e) => setExternalId(e.target.value)} /></label>
            </div>
            <div className="row">
              <span className="faint small">가져오면 시작 프레임의 자식 버전으로 등록되고 자동 검수가 실행됩니다.</span>
              <span className="spacer" />
              <button type="button" className="primary" disabled={!file || busy} onClick={submit}>{busy ? "가져오는 중" : "결과 가져오기"}</button>
            </div>
          </>
        )}
        {error && <p className="error-text small">{error}</p>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ 1) 3D keyframe flow

function KeyframeFlow({ shots }: { shots: ShotRow[] }) {
  const recipeQ = useAsync(() => api.guideRecipe(), []);
  const [scene, setScene] = useState<BlenderScene | null>(null);
  const [shot, setShot] = useState<number | null>(() => shots.find((s) => s.code === "R05")?.id ?? shots[0]?.id ?? null);
  const [frame, setFrame] = useState(60);
  const [passJob, setPassJob] = usePolled<BlenderJob>(null, (j) => api.blenderJob(j.id));
  const previous = useAsync(() => api.blenderJobs("passes"), [passJob?.status === "completed"]);
  const [error, setError] = useState<string | null>(null);

  const [prompt, setPrompt] = useState<string | null>(null);
  const [negative, setNegative] = useState<string | null>(null);
  const [hold, setHold] = useState(0.85);
  const [redraw, setRedraw] = useState(0.62);
  const [refine, setRefine] = useState(0.4);
  const [count, setCount] = useState(2);
  const [jobs, setJobs] = useState<GenJob[]>([]);

  const [motion, setMotion] = useState(MOTION_PROMPT);
  const [duration, setDuration] = useState(5);
  const [keyframe, setKeyframe] = useState<number | null>(null);
  const [pkg, setPkg] = useState<ExternalPackage | null>(null);

  useEffect(() => {
    if (scene?.frame_current != null) setFrame(scene.frame_current);
  }, [scene?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  const recipe: RecipeInfo | null = recipeQ.data;
  const key = useMemo(() => {
    const find = (label: string, input: string) => recipe?.exposed_inputs.find((e) => e.label === label && e.input === input);
    return {
      prompt: find("프롬프트", "text"), negative: find("네거티브", "text"), hold: find("구도 고정", "strength"),
      redraw: find("샘플러", "denoise"), refine: find("다듬기", "denoise"),
    };
  }, [recipe]);

  const running = jobs.some((j) => ACTIVE_JOB.has(j.status));
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => setJobs(await Promise.all(jobs.map((j) => api.job(j.id)))), 2000);
    return () => clearInterval(timer);
  }, [jobs, running]);

  const scenePasses = (previous.data ?? []).filter((j) => j.status === "completed" && (!scene || j.scene === scene.path));
  const registered = jobs.flatMap((j) => j.outputs.filter((o) => o.version_id).map((o) => ({ id: o.version_id!, label: `시드 ${j.seed}` })));
  const earlier = (shots.find((s) => s.id === shot)?.versions ?? [])
    .filter((v) => v.media_kind === "image" && v.code.startsWith("g3d_") && !registered.some((r) => r.id === v.id))
    .map((v) => ({ id: v.id, label: v.code }));
  const startFrames = [...registered, ...earlier];

  const renderPasses = async () => {
    if (!scene || !shot) return;
    setError(null);
    try {
      setPassJob(await api.blenderRender({ purpose: "passes", scene_path: scene.path, shot_id: shot, frame }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const draw = async () => {
    if (!passJob || !recipe) return;
    setError(null);
    const values: Record<string, unknown> = {};
    if (key.prompt) values[key.prompt.key] = prompt ?? key.prompt.default;
    if (key.negative) values[key.negative.key] = negative ?? key.negative.default;
    if (key.hold) values[key.hold.key] = hold;
    if (key.redraw) values[key.redraw.key] = redraw;
    if (key.refine) values[key.refine.key] = refine;
    const seeds = Array.from({ length: count }, () => Math.floor(Math.random() * 1_000_000));
    try {
      setJobs(await api.guide({ pass_job_id: passJob.id, values, seeds }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  // Fill the box with the prompt kept for this shot (프롬프트 화면). Sent unchanged, the job is linked to it.
  const fillPrompt = async (shotId: number | null | undefined, use: string, into: (text: string, negative: string) => void) => {
    if (!shotId) return;
    setError(null);
    try {
      const kept = await api.shotPrompt(shotId, use);
      if (!kept.text) throw new Error(`${kept.code}에 저장된 프롬프트가 없습니다 (프롬프트 화면에서 먼저 적으세요)`);
      into(kept.text, kept.negative);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const makePackage = async () => {
    if (!keyframe) return;
    setError(null);
    try {
      setPkg(await api.createPackage({ version_id: keyframe, prompt: motion, model: "seedance_2_5", duration_s: duration }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const passDone = passJob?.status === "completed";
  return (
    <div className="main-col">
      <Step n={1} title="Blender 장면 고르기" hint="카메라가 있는 .blend 파일. 원본 파일은 열기만 하고 저장하지 않습니다." done={!!scene}>
        <ScenePicker selected={scene} onSelect={(s) => { setScene(s); setPassJob(null); }} />
      </Step>

      <Step n={2} title="깊이 이미지 만들기" hint="Blender가 일반 렌더, 깊이(가까울수록 밝게), 마스크를 뽑습니다. 일반 렌더는 고른 샷의 버전으로 등록됩니다." done={passDone}>
        <div className="row wrap">
          <label className="field inline">붙일 샷<ShotSelect shots={shots} value={shot} onChange={setShot} /></label>
          <label className="field inline">프레임<input type="number" min={0} value={frame} onChange={(e) => setFrame(Number(e.target.value))} /></label>
          <button type="button" className="primary" disabled={!scene || !shot || (!!passJob && ACTIVE_JOB.has(passJob.status))} onClick={renderPasses}>
            <Icon name="cube" />깊이 이미지 만들기
          </button>
          {scenePasses.length > 0 && !passJob && (
            <select defaultValue="" onChange={(e) => { const j = scenePasses.find((x) => x.id === Number(e.target.value)); if (j) setPassJob(j); }}>
              <option value="">이전 결과 다시 쓰기</option>
              {scenePasses.map((j) => <option key={j.id} value={j.id}>#{j.id} · {j.scene_name} · 프레임 {j.frame ?? "-"} · {kst(j.finished_at)}</option>)}
            </select>
          )}
        </div>
        {passJob && ACTIVE_JOB.has(passJob.status) && <Progress value={passJob.progress} stage={passJob.stage} />}
        {passJob?.error && <p className="error-text small">{passJob.error}</p>}
        {passDone && (
          <div className="thumbs-3">
            {(["beauty", "depth", "mask"] as const).map((name) => passJob.files[name] && (
              <figure key={name}>
                <img src={passJob.files[name]} alt={name} />
                <figcaption className="faint small">{{ beauty: "일반 렌더", depth: "깊이 (가까울수록 밝게)", mask: "마스크 (SF_MASK 컬렉션)" }[name]}</figcaption>
              </figure>
            ))}
            <p className="faint small">{passJob.seconds}초 · {passJob.model} · 카메라 {passJob.camera} · 가까운 곳 {passJob.depth?.near_m}m ~ 먼 곳 {passJob.depth?.far_m}m</p>
          </div>
        )}
      </Step>

      <Step n={3} title="AI로 그리기" hint="깊이 이미지로 구도를 고정한 채 사진풍 모델이 질감과 빛을 다시 그립니다 (1536×864). 한 장에 약 1분." done={registered.length > 0}>
        {!recipe ? <Loading error={recipeQ.error} loading={recipeQ.loading} /> : (
          <>
            <label className="field">무엇을 그릴지 (영어 권장)
              <textarea rows={3} value={prompt ?? String(key.prompt?.default ?? "")} onChange={(e) => setPrompt(e.target.value)} />
            </label>
            <button type="button" className="ghost" disabled={!passJob?.shot_id}
              onClick={() => fillPrompt(passJob?.shot_id, "image_local", (text, neg) => { setPrompt(text); if (neg) setNegative(neg); })}>
              <Icon name="edit" />이 컷의 프롬프트 넣기 (그림 · 로컬)
            </button>
            <div className="slider-grid">
              <Slider label="구도 고정" value={hold} min={0.4} max={1} step={0.05} left="AI에게 맡기기" right="Blender 구도 그대로" onChange={setHold} />
              <Slider label="새로 그리는 정도" value={redraw} min={0.3} max={0.85} step={0.01} left="Blender 렌더에 가깝게" right="새로 그리기" onChange={setRedraw} />
            </div>
            <details className="advanced">
              <summary>고급 설정</summary>
              <div className="col-gap">
                <label className="field">빼고 싶은 것 (네거티브)
                  <textarea rows={2} value={negative ?? String(key.negative?.default ?? "")} onChange={(e) => setNegative(e.target.value)} />
                </label>
                <Slider label="고해상도 다듬기 강도" value={refine} min={0.2} max={0.6} step={0.01} left="그대로 확대" right="세부 다시 그리기" onChange={setRefine} />
                <p className="faint small">모델 {recipe.required_models.map((m) => `${m.file}${m.present ? "" : " (없음)"}`).join(" · ")}</p>
              </div>
            </details>
            <div className="row wrap">
              <span className="small muted">몇 장</span>
              <div className="segmented">
                {[1, 2, 4].map((n) => <button key={n} type="button" className={count === n ? "on" : ""} onClick={() => setCount(n)}>{n}장</button>)}
              </div>
              <span className="faint small">연속으로 그릴 때는 GPU가 60°C 이하로 식은 뒤 다음 장을 시작합니다.</span>
              <span className="spacer" />
              <button type="button" className="primary" disabled={!passDone || running} onClick={draw}>
                <Icon name="generate" />{running ? "그리는 중" : `AI로 그리기 · ${count}장`}
              </button>
            </div>
            {!passDone && <p className="faint small">먼저 2단계에서 깊이 이미지를 만드세요.</p>}
            {jobs.length > 0 && (
              <div className="media-grid">
                {jobs.map((j) => <JobCard key={j.id} job={j} onChange={async () => setJobs(await Promise.all(jobs.map((x) => api.job(x.id))))} />)}
              </div>
            )}
          </>
        )}
      </Step>

      <Step n={4} title="영상으로 만들기 (Seedance)" hint="마음에 드는 키프레임을 시작 프레임으로 보낼 패키지를 만듭니다. 업로드와 결제는 직접 합니다." done={pkg?.status === "completed"}>
        {pkg ? <PackageCard pkg={pkg} onChange={setPkg} /> : (
          <>
            <div className="row wrap">
              <label className="field inline">시작 프레임
                <select value={keyframe ?? ""} onChange={(e) => setKeyframe(Number(e.target.value) || null)}>
                  <option value="">등록한 키프레임 선택</option>
                  {startFrames.map((r) => <option key={r.id} value={r.id}>버전 #{r.id} · {r.label}</option>)}
                </select>
              </label>
              <div className="segmented">
                {[5, 10].map((d) => <button key={d} type="button" className={duration === d ? "on" : ""} onClick={() => setDuration(d)}>{d}초</button>)}
              </div>
            </div>
            <label className="field">움직임 프롬프트 (영어)
              <textarea rows={3} value={motion} onChange={(e) => setMotion(e.target.value)} />
              <button type="button" className="ghost" disabled={!keyframe}
                onClick={async () => fillPrompt(keyframe ? (await api.version(keyframe)).shot.id : null, "video", (text) => setMotion(text))}>
                <Icon name="edit" />이 컷의 프롬프트 넣기 (영상)
              </button>
            </label>
            <div className="row">
              {startFrames.length === 0 && <span className="faint small">3단계 결과에서 "버전으로 등록"을 누르면 여기서 고를 수 있습니다. 예전 패키지는 외부 생성 탭에 있습니다.</span>}
              <span className="spacer" />
              <button type="button" className="primary" disabled={!keyframe || !motion.trim()} onClick={makePackage}><Icon name="upload" />보내기 패키지 만들기</button>
            </div>
          </>
        )}
      </Step>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ 2) layer composite flow

function LayerFlow({ shots }: { shots: ShotRow[] }) {
  const [scene, setScene] = useState<BlenderScene | null>(null);
  const [range, setRange] = useState<[number, number]>([1, 121]);
  const [layerJob, setLayerJob] = usePolled<BlenderJob>(null, (j) => api.blenderJob(j.id));
  const previous = useAsync(() => api.blenderJobs("layer"), [layerJob?.status === "completed"]);
  const [shot, setShot] = useState<number | null>(() => shots.find((s) => s.code === "R05")?.id ?? null);
  const [version, setVersion] = useState<number | null>(null);
  const [opacity, setOpacity] = useState(0.8);
  const [mode, setMode] = useState<"screen" | "over">("screen");
  const [offset, setOffset] = useState(0);
  const [frame, setFrame] = useState(60);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [compJob, setCompJob] = usePolled<CompositeJob>(null, (j) => api.compositeJob(j.id));
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (scene?.frame_start != null && scene.frame_end != null) setRange([scene.frame_start, scene.frame_end]);
  }, [scene?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  const currentShot = shots.find((s) => s.id === shot);
  const videoVersions = (currentShot?.versions ?? []).filter((v) => v.media_kind === "video");
  useEffect(() => {
    if (!currentShot) return;
    if (!videoVersions.some((v) => v.id === version)) {
      const approved = videoVersions.find((v) => v.id === currentShot.approved_version_id);
      setVersion((approved ?? videoVersions[0])?.id ?? null);
    }
  }, [currentShot?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const layerDone = layerJob?.status === "completed";
  const target = videoVersions.find((v) => v.id === version);
  const body: CompositeBody | null = layerDone && version ? { version_id: version, layer_job_id: layerJob!.id, opacity, mode, offset, loop: true, frame } : null;

  useEffect(() => {
    if (!body) return;
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      try {
        const url = await compositePreview(body);
        setPreviewError(null);
        setPreview((old) => { if (old) URL.revokeObjectURL(old); return url; });
      } catch (err) {
        setPreviewError((err as Error).message);
      }
    }, 250);
    return () => window.clearTimeout(timer.current);
  }, [body?.version_id, body?.layer_job_id, opacity, mode, offset, frame]); // eslint-disable-line react-hooks/exhaustive-deps

  const renderLayer = async () => {
    if (!scene) return;
    setError(null);
    try {
      setLayerJob(await api.blenderRender({ purpose: "layer", scene_path: scene.path, frame_start: range[0], frame_end: range[1] }));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const composite = async () => {
    if (!body) return;
    setError(null);
    try {
      setCompJob(await api.createComposite(body));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const layers = (previous.data ?? []).filter((j) => j.status === "completed");
  return (
    <div className="main-col">
      <Step n={1} title="레이어 장면 렌더" hint="투명 배경으로 비·불꽃·먼지 같은 효과만 렌더합니다. 물체 뒤에 가려질 부분은 Blender에서 Holdout 재질로 처리합니다." done={layerDone}>
        <ScenePicker selected={scene} onSelect={(s) => { setScene(s); setLayerJob(null); }} />
        <div className="row wrap">
          <label className="field inline">시작 프레임<input type="number" value={range[0]} onChange={(e) => setRange([Number(e.target.value), range[1]])} /></label>
          <label className="field inline">끝 프레임<input type="number" value={range[1]} onChange={(e) => setRange([range[0], Number(e.target.value)])} /></label>
          <button type="button" className="primary" disabled={!scene || (!!layerJob && ACTIVE_JOB.has(layerJob.status))} onClick={renderLayer}><Icon name="cube" />레이어 렌더</button>
          {layers.length > 0 && !layerJob && (
            <select defaultValue="" onChange={(e) => { const j = layers.find((x) => x.id === Number(e.target.value)); if (j) setLayerJob(j); }}>
              <option value="">이전 레이어 다시 쓰기</option>
              {layers.map((j) => <option key={j.id} value={j.id}>#{j.id} · {j.scene_name} · {j.frame_count}프레임 · {kst(j.finished_at)}</option>)}
            </select>
          )}
        </div>
        {layerJob && ACTIVE_JOB.has(layerJob.status) && <Progress value={layerJob.progress} stage={layerJob.stage} />}
        {layerJob?.error && <p className="error-text small">{layerJob.error}</p>}
        {layerDone && layerJob.files.preview && (
          <div className="layer-preview">
            <video src={layerJob.files.preview} muted loop autoPlay playsInline />
            <p className="faint small">#{layerJob.id} · {layerJob.scene_name} · {layerJob.frame_count}프레임 · 렌더 {layerJob.seconds}초 (어두운 회색 위 미리보기)</p>
          </div>
        )}
      </Step>

      <Step n={2} title="합성할 샷과 진하기" hint="슬라이더를 움직이면 바로 한 프레임 미리보기가 바뀝니다. 미리보기와 최종 결과는 같은 계산을 씁니다." done={!!preview && !!body}>
        <div className="row wrap">
          <label className="field inline">샷<ShotSelect shots={shots} value={shot} onChange={(id) => { setShot(id); setVersion(null); }} /></label>
          <label className="field inline">버전
            <select value={version ?? ""} onChange={(e) => setVersion(Number(e.target.value))}>
              {videoVersions.map((v) => <option key={v.id} value={v.id}>{v.code}{v.id === currentShot?.approved_version_id ? " (승인)" : ""}</option>)}
            </select>
          </label>
          <span className="small muted">합성 방식</span>
          <div className="segmented">
            <button type="button" className={mode === "screen" ? "on" : ""} onClick={() => setMode("screen")}>빛 더하기</button>
            <button type="button" className={mode === "over" ? "on" : ""} onClick={() => setMode("over")}>일반</button>
          </div>
          <label className="field inline" title="샷의 첫 프레임에 레이어의 몇 번째 프레임을 놓을지 정합니다. 레이어가 짧으면 반복합니다.">레이어 시작 프레임<input type="number" value={offset} onChange={(e) => setOffset(Number(e.target.value))} /></label>
        </div>
        <div className="slider-grid">
          <Slider label="진하기" value={opacity} min={0} max={1} step={0.05} left="옅게" right="진하게" onChange={setOpacity} />
          <label className="slider">
            <span className="slider-label">미리볼 프레임 <b className="num">{frame}</b></span>
            <input type="range" min={0} max={Math.max(0, (target?.frame_count ?? 1) - 1)} value={frame} onChange={(e) => setFrame(Number(e.target.value))} />
            <span className="slider-ends faint"><span>처음</span><span>끝</span></span>
          </label>
        </div>
        {!layerDone && <p className="faint small">먼저 1단계에서 레이어를 렌더하거나 이전 레이어를 고르세요.</p>}
        {body && (
          <div className="pair">
            <figure><img src={media.frame(body.version_id, frame)} alt="원본" /><figcaption className="faint small">원본</figcaption></figure>
            <figure>{preview ? <img src={preview} alt="합성 미리보기" /> : <div className="thumb-empty" />}<figcaption className="faint small">합성 미리보기</figcaption></figure>
          </div>
        )}
        {previewError && <p className="error-text small">{previewError}</p>}
      </Step>

      <Step n={3} title="합성 만들기" hint="전체 프레임을 합성해 새 자식 버전으로 등록합니다. 원본 영상과 소리는 그대로 두고, 자동 검수까지 실행합니다." done={compJob?.status === "completed"}>
        <div className="row">
          <span className="faint small">{target ? `${currentShot?.seq} ${currentShot?.code} ${target.code} · ${target.frame_count}프레임` : "샷과 버전을 고르세요"}</span>
          <span className="spacer" />
          <button type="button" className="primary" disabled={!body || (!!compJob && ACTIVE_JOB.has(compJob.status))} onClick={composite}><Icon name="layers" />합성 만들기</button>
        </div>
        {compJob && ACTIVE_JOB.has(compJob.status) && <Progress value={compJob.progress} stage={compJob.stage} />}
        {compJob?.error && <p className="error-text small">{compJob.error}</p>}
        {compJob?.status === "completed" && compJob.version_id && (
          <div className="row">
            <span className="ok-text small">완료 · {compJob.seconds}초</span>
            <span className="spacer" />
            <a className="button" href={link.shot(compJob.shot_id, compJob.version_id)}>샷에서 보기</a>
            <a className="button ghost" href={link.review(compJob.source_version_id, compJob.version_id)}><Icon name="compare" />원본과 비교</a>
          </div>
        )}
      </Step>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}

// ------------------------------------------------------------------ 3) external packages

function ExternalList() {
  const list = useAsync(() => api.packages(), []);
  if (!list.data) return <Loading error={list.error} loading={list.loading} />;
  return (
    <div className="main-col">
      <section className="panel">
        <div className="panel-body col-gap">
          <p className="muted small">ShotFlow는 업로드·결제를 하지 않습니다. 패키지를 만든 뒤 Higgsfield에서 직접 생성하고, 받은 영상을 여기서 가져오면 크레딧과 함께 기록됩니다. 새 패키지는 3D 키프레임 4단계나 아래 목록에서 이어서 진행합니다.</p>
          {list.data.length === 0 && <p className="faint">아직 만든 패키지가 없습니다.</p>}
          {list.data.map((p) => <PackageCard key={p.id} pkg={p} onChange={() => list.reload()} />)}
        </div>
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ page

export function Scene3DPage({ sequences, tab }: { sequences: SequenceSummary[]; tab: string }) {
  const status = useAsync(() => api.blenderStatus(), []);
  const shotsQ = useAsync(
    () => Promise.all(sequences.map((s) => api.shots(s.id).then((list) => list.filter((x) => x.kind === "shot").map((x) => ({ ...x, seq: s.name }))))).then((x) => x.flat()),
    [sequences.length],
  );
  const current = (TABS.find((t) => t.key === tab)?.key ?? "keyframe") as Tab;  // the sidebar link keeps opening the keyframe step

  return (
    <>
      <Toolbar
        crumbs={[{ label: "생성" }]}
        title="3D 연동"
        actions={
          <div className="segmented">
            {TABS.map((t) => <button key={t.key} type="button" className={current === t.key ? "on" : ""} onClick={() => (window.location.hash = link.scene3d(t.key))}>{t.label}</button>)}
          </div>
        }
      />
      <div className="page">
        {status.data && !status.data.installed && (
          <section className="panel panel-body error-text">Blender를 찾을 수 없습니다. Blender 4.2 LTS를 설치하거나 SHOTFLOW_BLENDER 환경 변수로 blender.exe 위치를 알려 주세요.</section>
        )}
        {status.data?.installed && <p className="faint small">Blender {status.data.version} · {status.data.exe}</p>}
        {!shotsQ.data ? <Loading error={shotsQ.error} loading={shotsQ.loading} /> : (
          current === "layout" ? <LayoutFlow sequences={sequences} shots={shotsQ.data as ShotRow[]} />
            : current === "keyframe" ? <KeyframeFlow shots={shotsQ.data as ShotRow[]} />
            : current === "layer" ? <LayerFlow shots={shotsQ.data as ShotRow[]} />
              : <ExternalList />
        )}
      </div>
    </>
  );
}
