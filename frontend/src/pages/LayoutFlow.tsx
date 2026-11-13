import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { api, media } from "../api";
import { Loading } from "../components/Bits";
import { Icon } from "../components/Icon";
import { Progress, Step, usePolled } from "../components/Steps";
import { kst } from "../format";
import { link } from "../router";
import type {
  Animatic,
  AnimaticCut,
  BlenderJob,
  LayoutFrameMarks,
  LayoutKind,
  LayoutMarks,
  LayoutObjectMark,
  LayoutRole,
  LayoutSolution,
  SequenceSummary,
  Shot,
  ShotLayout,
} from "../types";
import { useAsync } from "../useAsync";

// Animatic -> Blender blocking (D-015): split the animatic into cuts, mark one drawing per cut (boxes, horizon, lens),
// read the camera numbers while marking, then build a new .blend and lay its render over the drawing.

type ShotRow = Shot & { seq: string };

const KIND_NAMES: Record<LayoutKind, string> = { bottle: "병", person: "사람", box: "상자", round: "둥근 물체", building: "건물", other: "기타" };
const KIND_HEIGHT: Partial<Record<LayoutKind, number>> = { bottle: 0.25, person: 1.7 };
const LENSES = [18, 24, 35, 50, 60, 85, 100, 135];
const ROLE_NAME: Record<LayoutRole, string> = { start: "처음", end: "끝" };

function clock(frame: number, fps: number): string {
  const s = frame / fps;
  return `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

function blankMarks(cut: AnimaticCut): LayoutMarks {
  return { lens_mm: 50, frames: [{ role: "start", frame: cut.frames.start, horizon: 0.5, objects: [] }] };
}

// ------------------------------------------------------------------ drawing with marks

function MarkCanvas({ src, aspect, frame, selected, onSelect, onChange }: {
  src: string;
  aspect: number;
  frame: LayoutFrameMarks;
  selected: number | null;
  onSelect: (id: number | null) => void;
  onChange: (f: LayoutFrameMarks) => void;
}) {
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<{ what: "box" | "horizon"; x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<[number, number, number, number] | null>(null);
  const W = 1000 * aspect;

  const at = (e: PointerEvent): [number, number] => {
    const r = svg.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  };

  const down = (e: PointerEvent<SVGSVGElement>) => {
    const target = e.target as Element;
    const [x, y] = at(e);
    if (target.getAttribute("data-handle") === "horizon") {
      drag.current = { what: "horizon", x, y };
    } else if (target.getAttribute("data-box")) {
      onSelect(Number(target.getAttribute("data-box")));
      return;
    } else {
      drag.current = { what: "box", x, y };
      setDraft([x, y, x, y]);
    }
    svg.current!.setPointerCapture(e.pointerId);
  };

  const move = (e: PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const [x, y] = at(e);
    if (drag.current.what === "horizon") onChange({ ...frame, horizon: Math.round(Math.min(1.5, Math.max(-0.5, y)) * 1000) / 1000 });
    else setDraft([drag.current.x, drag.current.y, x, y]);
  };

  const up = () => {
    const was = drag.current;
    drag.current = null;
    if (!was || was.what !== "box" || !draft) return;
    setDraft(null);
    const box: [number, number, number, number] = [
      Math.min(draft[0], draft[2]), Math.min(draft[1], draft[3]), Math.max(draft[0], draft[2]), Math.max(draft[1], draft[3]),
    ].map((v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000) as [number, number, number, number];
    if (box[2] - box[0] < 0.01 || box[3] - box[1] < 0.01) {
      onSelect(null);
      return;
    }
    const id = Math.max(-1, ...frame.objects.map((o) => o.id)) + 1;
    const first = frame.objects.length === 0;
    const kind: LayoutKind = first ? "bottle" : "box";
    const object: LayoutObjectMark = {
      id, kind, label: `${KIND_NAMES[kind]} ${id + 1}`, box, height_m: KIND_HEIGHT[kind] ?? null,
      grounded: true, mask: first, anchor: first,
    };
    onChange({ ...frame, objects: [...frame.objects, object] });
    onSelect(id);
  };

  const h = frame.horizon;
  return (
    <div className="mark-canvas" style={{ aspectRatio: String(aspect) }}>
      <img src={src} alt="" draggable={false} />
      <svg ref={svg} viewBox={`0 0 ${W} 1000`} preserveAspectRatio="none" onPointerDown={down} onPointerMove={move} onPointerUp={up}>
        {h >= 0 && h <= 1 && (
          <g>
            <line x1={0} x2={W} y1={h * 1000} y2={h * 1000} className="mark-horizon" vectorEffect="non-scaling-stroke" />
            <rect data-handle="horizon" x={0} y={h * 1000 - 18} width={110} height={36} className="mark-handle" />
            <text x={14} y={h * 1000 + 8} className="mark-text">수평선</text>
          </g>
        )}
        {frame.objects.map((o) => (
          <g key={o.id}>
            <rect data-box={o.id} x={o.box[0] * W} y={o.box[1] * 1000} width={(o.box[2] - o.box[0]) * W} height={(o.box[3] - o.box[1]) * 1000}
              className={`mark-box${o.anchor ? " anchor" : ""}${selected === o.id ? " selected" : ""}`} vectorEffect="non-scaling-stroke" />
            <text x={o.box[0] * W + 6} y={o.box[1] * 1000 - 8} className="mark-text">{o.label}{o.grounded ? "" : " (떠 있음)"}</text>
          </g>
        ))}
        {draft && (
          <rect x={Math.min(draft[0], draft[2]) * W} y={Math.min(draft[1], draft[3]) * 1000} width={Math.abs(draft[2] - draft[0]) * W}
            height={Math.abs(draft[3] - draft[1]) * 1000} className="mark-box draft" vectorEffect="non-scaling-stroke" />
        )}
      </svg>
    </div>
  );
}

function ObjectFields({ frame, selected, onSelect, onChange, endFrame }: {
  frame: LayoutFrameMarks;
  selected: number | null;
  onSelect: (id: number | null) => void;
  onChange: (f: LayoutFrameMarks) => void;
  endFrame: boolean;
}) {
  const set = (id: number, patch: Partial<LayoutObjectMark>) =>
    onChange({
      ...frame,
      objects: frame.objects.map((o) => {
        if (patch.anchor && o.id !== id) return { ...o, anchor: false };
        return o.id === id ? { ...o, ...patch } : o;
      }),
    });
  if (!frame.objects.length) {
    return <p className="faint small">그림 위를 끌어서 물체를 감싸는 상자를 그리세요. 상자 아래 끝은 물체가 바닥에 닿는 곳에 맞춥니다.</p>;
  }
  return (
    <div className="mark-objects">
      {endFrame && <p className="faint small">끝 그림은 카메라 움직임만 계산합니다. 기준 물체 상자만 맞추면 됩니다.</p>}
      {frame.objects.map((o) => (
        <div key={o.id} className={`mark-object${selected === o.id ? " selected" : ""}`} onClick={() => onSelect(o.id)}>
          <div className="row wrap">
            <input value={o.label} onChange={(e) => set(o.id, { label: e.target.value })} style={{ width: 110 }} />
            <select value={o.kind} onChange={(e) => {
              const kind = e.target.value as LayoutKind;
              const keepHeight = o.height_m !== null && o.height_m !== KIND_HEIGHT[o.kind];
              set(o.id, { kind, height_m: keepHeight ? o.height_m : KIND_HEIGHT[kind] ?? null });
            }}>
              {(Object.keys(KIND_NAMES) as LayoutKind[]).map((k) => <option key={k} value={k}>{KIND_NAMES[k]}</option>)}
            </select>
            <label className="field inline">실제 높이
              <input type="number" min={0} step={0.01} value={o.height_m ?? ""} placeholder="모름"
                onChange={(e) => set(o.id, { height_m: e.target.value === "" ? null : Number(e.target.value) })} />m
            </label>
            <button type="button" className="ghost icon-only" title="상자 지우기" onClick={(e) => {
              e.stopPropagation();
              onChange({ ...frame, objects: frame.objects.filter((x) => x.id !== o.id) });
              onSelect(null);
            }}>×</button>
          </div>
          <div className="row wrap small">
            <label className="field inline"><input type="checkbox" checked={o.anchor} onChange={(e) => set(o.id, { anchor: e.target.checked })} />기준 물체 (원점)</label>
            <label className="field inline"><input type="checkbox" checked={!o.grounded} onChange={(e) => set(o.id, { grounded: !e.target.checked })} />떠 있음</label>
            <label className="field inline"><input type="checkbox" checked={o.mask} onChange={(e) => set(o.id, { mask: e.target.checked })} />마스크</label>
          </div>
        </div>
      ))}
    </div>
  );
}

function CameraReadout({ solution, error }: { solution: LayoutSolution | null; error: string | null }) {
  if (error) return <p className="error-text small">{error}</p>;
  if (!solution) return <p className="faint small">상자를 그리면 카메라 위치가 바로 계산됩니다.</p>;
  return (
    <div className="col-gap small">
      {solution.cameras.map((c) => (
        <div key={c.role} className="camera-line">
          <b>{ROLE_NAME[c.role]} 카메라</b>
          <span>높이 <b className="num">{c.height_m.toFixed(3)}m</b></span>
          <span>기준 물체까지 <b className="num">{c.distance_m.toFixed(3)}m</b></span>
          <span>기울기 <b className="num">{c.pitch_deg.toFixed(1)}°</b> {c.pitch_deg > 0.05 ? "(내려다봄)" : c.pitch_deg < -0.05 ? "(올려다봄)" : "(수평)"}</span>
          <span>렌즈 <b className="num">{c.lens_mm}mm</b></span>
        </div>
      ))}
      {solution.warnings.map((w) => <p key={w} className="warn-text">{w}</p>)}
    </div>
  );
}

function Overlay({ animatic, layout, role }: { animatic: Animatic; layout: ShotLayout; role: LayoutRole }) {
  const [opacity, setOpacity] = useState(0.6);
  const frame = layout.marks.frames.find((f) => f.role === role) ?? layout.marks.frames[0];
  const preview = layout.job?.files[role === "end" ? "preview_end" : "preview_start"] ?? layout.job?.files.preview_start;
  return (
    <div className="col-gap">
      <div className="overlay-box" style={{ aspectRatio: String(animatic.aspect) }}>
        <img src={media.animaticFrame(animatic.id, frame.frame)} alt="애니메틱 그림" />
        {preview && <img src={`${preview}?t=${layout.id}`} alt="Blender 블로킹" style={{ opacity }} />}
      </div>
      <label className="slider">
        <span className="slider-label">Blender 렌더 겹치기 <b className="num">{Math.round(opacity * 100)}%</b></span>
        <input type="range" min={0} max={1} step={0.01} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
        <span className="slider-ends faint"><span>애니메틱 그림</span><span>Blender 블로킹</span></span>
      </label>
    </div>
  );
}

// ------------------------------------------------------------------ the flow

export function LayoutFlow({ sequences, shots }: { sequences: SequenceSummary[]; shots: ShotRow[] }) {
  const [seqId, setSeqId] = useState<number | null>(sequences[0]?.id ?? null);
  const [reload, setReload] = useState(0);
  const list = useAsync(() => (seqId ? api.animatics(seqId) : Promise.resolve([])), [seqId, reload]);
  const [animatic, setAnimatic] = useState<Animatic | null>(null);
  const [cutIndex, setCutIndex] = useState<number | null>(null);
  const [marksByCut, setMarksByCut] = useState<Record<string, LayoutMarks>>({});
  const [role, setRole] = useState<LayoutRole>("start");
  const [selected, setSelected] = useState<number | null>(null);
  const [solution, setSolution] = useState<LayoutSolution | null>(null);
  const [solveError, setSolveError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = usePolled<BlenderJob>(null, (j) => api.blenderJob(j.id));
  const history = useAsync(() => (animatic ? api.layouts(animatic.id) : Promise.resolve([])), [animatic?.id, job?.status === "completed"]);
  const [shown, setShown] = useState<ShotLayout | null>(null);

  useEffect(() => {
    if (list.data && (!animatic || animatic.sequence_id !== seqId)) {
      setAnimatic(list.data[0] ?? null);
      setCutIndex(null);
    }
  }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const seqShots = useMemo(() => {
    const seq = sequences.find((s) => s.id === seqId);
    return shots.filter((s) => s.seq === seq?.name);
  }, [sequences, shots, seqId]);
  const cut = animatic && cutIndex !== null ? animatic.cuts[cutIndex] : null;
  const key = animatic && cut ? `${animatic.id}:${cut.index}` : "";
  const marks = cut ? marksByCut[key] ?? blankMarks(cut) : null;
  const frame = marks?.frames.find((f) => f.role === role) ?? null;
  const cutLayouts = (history.data ?? []).filter((l) => l.cut_index === cutIndex);

  const setMarks = (m: LayoutMarks) => setMarksByCut((all) => ({ ...all, [key]: m }));
  const setFrame = (f: LayoutFrameMarks) => marks && setMarks({ ...marks, frames: marks.frames.map((x) => (x.role === f.role ? f : x)) });

  // camera numbers while marking
  useEffect(() => {
    if (!animatic || !cut || !marks || !marks.frames[0].objects.length) {
      setSolution(null);
      setSolveError(null);
      return;
    }
    const timer = setTimeout(() => {
      api.solveLayout({ animatic_id: animatic.id, cut_index: cut.index, marks })
        .then((s) => { setSolution(s); setSolveError(null); })
        .catch((e) => { setSolution(null); setSolveError(String(e.message ?? e)); });
    }, 250);
    return () => clearTimeout(timer);
  }, [JSON.stringify(marks), animatic?.id, cut?.index]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (job?.status === "completed" && job.layout_id) api.layout(job.layout_id).then(setShown);
  }, [job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (file: File | undefined) => {
    if (!file || !seqId) return;
    setBusy("upload");
    setError(null);
    try {
      const made = await api.uploadAnimatic(seqId, file);
      setAnimatic(made);
      setCutIndex(made.cuts.find((c) => c.has_panel)?.index ?? null);
      setReload((n) => n + 1);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const linkShot = async (index: number, shotId: number | null) => {
    if (!animatic) return;
    setAnimatic(await api.updateCuts(animatic.id, [{ index, shot_id: shotId }]));
  };

  const toggleEnd = (on: boolean) => {
    if (!marks || !cut) return;
    if (on) {
      const start = marks.frames[0];
      setMarks({ ...marks, frames: [start, { ...start, role: "end", frame: cut.frames.end, objects: start.objects.map((o) => ({ ...o })) }] });
      setRole("end");
    } else {
      setMarks({ ...marks, frames: marks.frames.filter((f) => f.role === "start") });
      setRole("start");
    }
  };

  const build = async () => {
    if (!animatic || !cut || !marks) return;
    setBusy("build");
    setError(null);
    try {
      const made = await api.createLayout({ animatic_id: animatic.id, cut_index: cut.index, marks });
      setJob(made.job);
      setShown(null);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const shotCode = (id: number | null) => seqShots.find((s) => s.id === id)?.code ?? null;

  return (
    <div className="col-gap" data-demo="layout-flow">
      <Step n={1} title="애니메틱 올리기" hint="스토리보드 애니메틱을 올리면 컷 경계를 찾고, 컷마다 그림 영역을 잘라 둡니다. 제목 화면처럼 그림이 없는 컷은 빠집니다." done={!!animatic}>
        <div className="row wrap">
          <label className="field inline">영상
            <select value={seqId ?? ""} onChange={(e) => { setSeqId(Number(e.target.value)); setAnimatic(null); }}>
              {sequences.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          {(list.data?.length ?? 0) > 0 && (
            <label className="field inline">애니메틱
              <select value={animatic?.id ?? ""} onChange={(e) => { setAnimatic(list.data!.find((a) => a.id === Number(e.target.value)) ?? null); setCutIndex(null); }}>
                {list.data!.map((a) => <option key={a.id} value={a.id}>{a.original_name} · {kst(a.created_at)}</option>)}
              </select>
            </label>
          )}
          <label className="button">
            <Icon name="upload" />{busy === "upload" ? "컷 나누는 중…" : "애니메틱 올리기"}
            <input type="file" accept="video/*" hidden disabled={!!busy} onChange={(e) => upload(e.target.files?.[0])} />
          </label>
        </div>
        {list.loading && <Loading error={list.error} loading />}
        {animatic && (
          <p className="faint small">
            {animatic.original_name} · {animatic.width}×{animatic.height} · {(animatic.frame_count / animatic.fps).toFixed(1)}초 ·
            컷 {animatic.cuts.length}개 (그림 있는 컷 {animatic.cuts.filter((c) => c.has_panel).length}개) · 그림 영역 {animatic.crop[2]}×{animatic.crop[3]}
          </p>
        )}
        {error && <p className="error-text small">{error}</p>}
      </Step>

      {animatic && (
        <Step n={2} title="컷과 샷 연결" hint="그림이 있는 컷이 순서대로 샷에 연결됩니다. 다르면 고르세요. 표시할 컷을 누르세요." done={animatic.cuts.some((c) => c.has_panel && c.shot_id)}>
          <div className="scene-grid">
            {animatic.cuts.map((c) => (
              <div key={c.index} className={`scene-card cut-card${cutIndex === c.index ? " selected" : ""}${c.has_panel ? "" : " muted"}`}
                onClick={() => c.has_panel && (setCutIndex(c.index), setRole("start"), setSelected(null))}>
                <img src={media.animaticFrame(animatic.id, c.frames.start)} alt="" />
                <div className="scene-name">컷 {c.index + 1}{c.has_panel ? "" : " · 그림 없음"}</div>
                <div className="small faint">{clock(c.start, animatic.fps)} ~ {clock(c.end, animatic.fps)} · {c.end - c.start}프레임</div>
                {c.has_panel && (
                  <div className="small row" onClick={(e) => e.stopPropagation()}>
                    샷
                    <select value={c.shot_id ?? ""} onChange={(e) => linkShot(c.index, e.target.value ? Number(e.target.value) : null)}>
                      <option value="">연결 안 함</option>
                      {seqShots.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}
                    </select>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Step>
      )}

      {animatic && cut && marks && frame && (
        <Step n={3} title={`그림 위에 표시 · 컷 ${cut.index + 1}${shotCode(cut.shot_id) ? ` → ${shotCode(cut.shot_id)}` : ""}`}
          hint="물체마다 상자를 그리고(아래 끝 = 바닥 닿는 곳), 수평선을 카메라 눈높이에 맞춥니다. 수평선이 가운데면 카메라가 수평입니다. 실제 높이를 아는 물체가 하나 있으면 거리와 카메라 높이가 정해집니다."
          done={!!solution}>
          <div className="row wrap">
            <div className="segmented">
              {marks.frames.map((f) => (
                <button key={f.role} type="button" className={role === f.role ? "on" : ""} onClick={() => { setRole(f.role); setSelected(null); }}>
                  {ROLE_NAME[f.role]} 그림
                </button>
              ))}
            </div>
            <label className="field inline">
              <input type="checkbox" checked={marks.frames.length > 1} onChange={(e) => toggleEnd(e.target.checked)} />끝 그림도 표시 (카메라 움직임)
            </label>
            <label className="field inline">렌즈
              <select value={LENSES.includes(marks.lens_mm) ? marks.lens_mm : ""} onChange={(e) => e.target.value && setMarks({ ...marks, lens_mm: Number(e.target.value) })}>
                {LENSES.map((l) => <option key={l} value={l}>{l}mm</option>)}
                {!LENSES.includes(marks.lens_mm) && <option value="">{marks.lens_mm}mm</option>}
              </select>
              <input type="number" min={8} max={300} value={marks.lens_mm} onChange={(e) => setMarks({ ...marks, lens_mm: Number(e.target.value) || 50 })} />
            </label>
          </div>
          <div className="mark-layout">
            <div className="col-gap">
              <MarkCanvas src={media.animaticFrame(animatic.id, frame.frame)} aspect={animatic.aspect} frame={frame}
                selected={selected} onSelect={setSelected} onChange={setFrame} />
              <label className="slider">
                <span className="slider-label">{ROLE_NAME[frame.role]} 그림 프레임 <b className="num">{frame.frame}</b></span>
                <input type="range" min={cut.start} max={cut.end - 1} value={frame.frame} onChange={(e) => setFrame({ ...frame, frame: Number(e.target.value) })} />
                <span className="slider-ends faint"><span>{clock(cut.start, animatic.fps)}</span><span>{clock(cut.end - 1, animatic.fps)}</span></span>
              </label>
              <label className="slider">
                <span className="slider-label">수평선 (카메라 눈높이) <b className="num">{frame.horizon.toFixed(3)}</b></span>
                <input type="range" min={-0.5} max={1.5} step={0.005} value={frame.horizon} onChange={(e) => setFrame({ ...frame, horizon: Number(e.target.value) })} />
                <span className="slider-ends faint"><span>위 (내려다봄)</span><span>아래 (올려다봄)</span></span>
              </label>
            </div>
            <div className="col-gap">
              <ObjectFields frame={frame} selected={selected} onSelect={setSelected} onChange={setFrame} endFrame={frame.role === "end"} />
              <CameraReadout solution={solution} error={solveError} />
            </div>
          </div>
        </Step>
      )}

      {animatic && cut && marks && (
        <Step n={4} title="Blender 블로킹 만들기" hint="계산한 카메라와 회색 대역 물체로 새 Blender 파일을 만들고, 카메라에서 본 렌더를 그림 위에 겹쳐 봅니다. 만든 파일은 '3D 키프레임' 탭의 장면 목록에 나타납니다."
          done={job?.status === "completed" || cutLayouts.some((l) => l.job?.status === "completed")}>
          <div className="row wrap">
            <button type="button" className="primary" disabled={!!busy || !solution || !cut.shot_id || (!!job && ["queued", "running"].includes(job.status))} onClick={build}>
              <Icon name="cube" />블로킹 만들기
            </button>
            {!cut.shot_id && <span className="warn-text small">이 컷을 먼저 샷에 연결하세요</span>}
            <button type="button" className="ghost" onClick={() => (window.location.hash = link.scene3d("keyframe"))}>3D 키프레임으로 →</button>
          </div>
          {job && job.status !== "completed" && <Progress value={job.progress} stage={job.stage} error={job.error} />}
          {shown && shown.cut_index === cut.index && (
            <div className="col-gap">
              <p className="small">
                <b>{shown.code}</b> · {shown.blend_path?.split(/[\\/]/).pop()} · {shown.job?.seconds ? `${shown.job.seconds}초` : ""}
              </p>
              <div className={shown.solution.cameras.length > 1 ? "pair" : ""}>
                {shown.solution.cameras.map((c) => <Overlay key={c.role} animatic={animatic} layout={shown} role={c.role} />)}
              </div>
            </div>
          )}
          {cutLayouts.length > 0 && (
            <div className="col-gap">
              <h3 className="small">이 컷의 블로킹 기록</h3>
              <div className="layout-history">
                {cutLayouts.map((l) => (
                  <div key={l.id} className={`row wrap small layout-row${shown?.id === l.id ? " selected" : ""}`}>
                    <b>{l.code}</b>
                    <span className="faint">{kst(l.created_at)}</span>
                    <span>높이 {l.solution.cameras[0].height_m.toFixed(3)}m · 거리 {l.solution.cameras[0].distance_m.toFixed(3)}m · {l.solution.lens_mm}mm</span>
                    <span className={l.job?.status === "completed" ? "ok-text" : "faint"}>{l.job?.status === "completed" ? "완료" : l.job?.status}</span>
                    <button type="button" className="link" onClick={() => setShown(l)}>겹쳐 보기</button>
                    <button type="button" className="link" onClick={() => { setMarks(l.marks); setRole("start"); }}>이 표시 불러오기</button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </Step>
      )}
    </div>
  );
}
