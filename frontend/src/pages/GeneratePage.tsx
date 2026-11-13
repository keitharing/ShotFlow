import { useEffect, useMemo, useState } from "react";
import { ACTIVE_JOB, api, media } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import { link } from "../router";
import type { ExposedInput, GenJob, RecipeInfo, SequenceSummary, Shot } from "../types";
import { useAsync } from "../useAsync";

const KIND_LABEL: Record<string, string> = {
  keyframe: "이미지 생성", img2img: "이미지 변형", inpaint: "부분 수정", t2v: "영상 생성", v2v: "영상 변형",
};
const MAX_JOBS = 12;

function parseList(text: string, asInt: boolean): number[] {
  return text.split(/[,\s]+/).filter(Boolean).map(Number).filter((n) => Number.isFinite(n)).map((n) => (asInt ? Math.round(n) : n));
}

function InputField({ input, value, onChange }: { input: ExposedInput; value: unknown; onChange: (v: unknown) => void }) {
  const label = `${input.label} · ${input.input}`;
  if (input.type === "STRING") {
    return (
      <label className={`field${input.multiline ? " wide" : ""}`}>{label}
        {input.multiline ? <textarea rows={3} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />
          : <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />}
      </label>
    );
  }
  if (input.type === "COMBO") {
    return (
      <label className="field">{label}
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {(input.options ?? []).map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
        </select>
      </label>
    );
  }
  if (input.type === "BOOLEAN") {
    return <label className="check"><input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />{label}</label>;
  }
  const isFloat = input.type === "FLOAT";
  return (
    <label className="field">{label}
      <input
        type="number"
        value={value === "" ? "" : Number(value)}
        min={input.min ?? undefined}
        max={input.max != null && input.max < 1e12 ? input.max : undefined}
        step={isFloat ? input.step ?? 0.01 : input.step ?? 1}
        onChange={(e) => onChange(e.target.value === "" ? "" : isFloat ? Number(e.target.value) : Math.round(Number(e.target.value)))}
        style={{ width: "100%" }}
      />
    </label>
  );
}

export function GeneratePage({ sequences, shotId, sourceId, frame: initialFrame }: {
  sequences: SequenceSummary[];
  shotId: number | null;
  sourceId: number | null;
  frame: number;
}) {
  const recipesQ = useAsync(() => api.recipes(), []);
  const shotsQ = useAsync(() => Promise.all(sequences.map((s) => api.shots(s.id).then((list) => list.filter((x) => x.kind === "shot").map((x) => ({ ...x, seq: s.name }))))).then((x) => x.flat()), [sequences.length]);
  const [recipeId, setRecipeId] = useState<number | null>(null);
  const [shot, setShot] = useState<number | null>(shotId);
  const [source, setSource] = useState<number | null>(sourceId);
  const [frame, setFrame] = useState(initialFrame);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [seedText, setSeedText] = useState(() => String(Math.floor(Math.random() * 100000)));
  const [strengthText, setStrengthText] = useState("");
  const [jobs, setJobs] = useState<GenJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const recipes = (recipesQ.data ?? []).filter((r) => r.kind !== "inpaint" && r.kind !== "guide"); // guide runs from 3D 연동
  const recipe: RecipeInfo | undefined = recipes.find((r) => r.id === recipeId);
  const shots = (shotsQ.data ?? []) as (Shot & { seq: string })[];
  const currentShot = shots.find((s) => s.id === shot);
  const needsSource = !!recipe?.bindings.some((b) => b === "source_image" || b === "source_video");

  useEffect(() => {
    if (recipeId != null || !recipes.length) return;
    const preferred = recipes.find((r) => r.kind === (sourceId ? "img2img" : "keyframe")) ?? recipes[0];
    setRecipeId(preferred.id);
  }, [recipes, recipeId, sourceId]);

  useEffect(() => {
    if (!recipe) return;
    setValues(Object.fromEntries(recipe.exposed_inputs.map((e) => [e.key, e.default])));
    setStrengthText("");
  }, [recipe?.latest_version_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!currentShot) return;
    if (!currentShot.versions.some((v) => v.id === source)) setSource(currentShot.approved_version_id ?? currentShot.versions.at(-1)?.id ?? null);
  }, [currentShot, source]);

  const strengthKey = useMemo(() => {
    const s = recipe?.strength_input;
    return s ? recipe?.exposed_inputs.find((e) => e.node_title === s.node_title && e.input === s.input)?.key ?? null : null;
  }, [recipe]);

  const seeds = parseList(seedText, true);
  const strengths = strengthKey ? parseList(strengthText, false) : [];
  const total = Math.max(seeds.length, 1) * Math.max(strengths.length, 1);
  const visibleInputs = (recipe?.exposed_inputs ?? []).filter((e) => e.input !== "seed" && !(strengths.length && e.key === strengthKey));
  const sourceVersion = currentShot?.versions.find((v) => v.id === source);

  const running = jobs.some((j) => ACTIVE_JOB.has(j.status));
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(async () => setJobs(await Promise.all(jobs.map((j) => api.job(j.id)))), 1500);
    return () => clearInterval(timer);
  }, [jobs, running]);

  if (!recipesQ.data || !shotsQ.data) return <Loading error={recipesQ.error ?? shotsQ.error} loading />;

  const submit = async () => {
    if (!recipe || !shot) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.generate({
        recipe_version_id: recipe.latest_version_id,
        shot_id: shot,
        values: Object.fromEntries(Object.entries(values).filter(([k]) => visibleInputs.some((e) => e.key === k))),
        seeds: seeds.length ? seeds : null,
        strengths: strengths.length ? strengths : null,
        source_version_id: needsSource ? source : null,
        source_frame: frame,
      });
      setJobs(created);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Toolbar crumbs={[{ label: "생성" }]} title={recipe ? recipe.name : "생성"} actions={<a className="button ghost" href={link.recipes(recipe?.id)}><Icon name="graph" />노드 구조</a>} />
      <div className="page">
        <div className="gen-layout">
          <Panel title="레시피" flush>
            <ul className="recipe-list">
              {recipes.map((r) => (
                <li key={r.id}>
                  <button type="button" className={`recipe-item${r.id === recipeId ? " active" : ""}`} onClick={() => setRecipeId(r.id)}>
                    <b>{r.name}</b>
                    <span className="kind">{KIND_LABEL[r.kind] ?? r.kind} · v{r.version} · {r.output_kind === "frames" ? "영상" : "이미지"}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>

          <div className="main-col">
            {recipe && (
              <section className="panel">
                <div className="form-section">
                  <p className="muted">{recipe.description}</p>
                  <p className="faint small">
                    모델 {recipe.required_models.map((m) => `${m.file}${m.present ? "" : " (없음)"}`).join(", ")}
                    {recipe.limits ? ` · 최대 면적 ${recipe.limits.max_area.toLocaleString()}px · 프레임 ${recipe.limits.length_min}~${recipe.limits.length_max} (4n+1)` : ""}
                  </p>
                </div>
                <div className="form-section">
                  <h3>대상</h3>
                  <div className="input-grid">
                    <label className="field">샷
                      <select value={shot ?? ""} onChange={(e) => setShot(Number(e.target.value) || null)}>
                        <option value="">선택</option>
                        {shots.map((s) => <option key={s.id} value={s.id}>{s.seq} · {s.code}</option>)}
                      </select>
                    </label>
                    {needsSource && currentShot && (
                      <>
                        <label className="field">원본 버전
                          <select value={source ?? ""} onChange={(e) => setSource(Number(e.target.value))}>
                            {currentShot.versions.map((v) => <option key={v.id} value={v.id}>{v.code}{v.id === currentShot.approved_version_id ? " (승인)" : ""}</option>)}
                          </select>
                        </label>
                        {sourceVersion?.media_kind === "video" && (
                          <label className="field">{recipe.output_kind === "frames" ? "시작 프레임" : "프레임"}
                            <input type="number" min={0} max={(sourceVersion.frame_count ?? 1) - 1} value={frame} onChange={(e) => setFrame(Number(e.target.value))} style={{ width: "100%" }} />
                          </label>
                        )}
                      </>
                    )}
                  </div>
                  {needsSource && source != null && (
                    <img src={media.frame(source, sourceVersion?.media_kind === "video" ? frame : 0)} alt="원본 프레임" style={{ width: 320, borderRadius: 3, display: "block" }} />
                  )}
                </div>
                <div className="form-section">
                  <h3>입력 <span className="faint">노드 제목이 SF: 로 시작하는 입력</span></h3>
                  <div className="input-grid">
                    {visibleInputs.map((input) => (
                      <InputField key={input.key} input={input} value={values[input.key]} onChange={(v) => setValues({ ...values, [input.key]: v })} />
                    ))}
                  </div>
                </div>
                <div className="form-section">
                  <h3>변형 그리드</h3>
                  <div className="input-grid">
                    <label className="field">시드 (쉼표로 여러 개)<input value={seedText} onChange={(e) => setSeedText(e.target.value)} /></label>
                    {strengthKey && (
                      <label className="field">강도 목록 (비우면 입력값 사용)<input value={strengthText} placeholder="예: 0.35, 0.5, 0.65" onChange={(e) => setStrengthText(e.target.value)} /></label>
                    )}
                  </div>
                  <div className="row">
                    <span className={`small ${total > MAX_JOBS ? "error-text" : "faint"}`}>작업 {total}개 (시드 {Math.max(seeds.length, 1)} × 강도 {Math.max(strengths.length, 1)}, 최대 {MAX_JOBS})</span>
                    <span className="spacer" />
                    <button type="button" className="primary" disabled={busy || running || !shot || total > MAX_JOBS || (needsSource && !source)} onClick={submit}>
                      <Icon name="generate" />{running ? "생성 중" : "생성"}
                    </button>
                  </div>
                  {error && <p className="error-text small">{error}</p>}
                </div>
              </section>
            )}

            {jobs.length > 0 && (
              <Panel title={`결과 · 묶음 ${jobs[0].batch_id}`} actions={currentShot && <a className="button ghost" href={link.shot(currentShot.id)}>샷으로 이동</a>}>
                <div className="media-grid">
                  {jobs.map((j) => (
                    <JobCard key={j.id} job={j} strengthKey={strengthKey} onChange={async () => setJobs(await Promise.all(jobs.map((x) => api.job(x.id))))} />
                  ))}
                </div>
              </Panel>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
