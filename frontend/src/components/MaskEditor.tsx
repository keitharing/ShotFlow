import { useEffect, useMemo, useRef, useState } from "react";
import { ACTIVE_JOB, api, media } from "../api";
import type { GenJob, RecipeInfo, VersionDetail } from "../types";
import { Icon } from "./Icon";
import { JobCard } from "./JobCard";

interface Props {
  version: VersionDetail;
  frame: number;
  onClose: () => void;
  onDone: () => void;
}

/** Paint a mask on one frame, describe the fix, and run the inpaint recipe as N candidates. */
export function MaskEditor({ version, frame, onClose, onDone }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);
  const [tool, setTool] = useState<"brush" | "eraser">("brush");
  const [size, setSize] = useState(60);
  const [painted, setPainted] = useState(false);
  const [recipe, setRecipe] = useState<RecipeInfo | null>(null);
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [strength, setStrength] = useState(0.75);
  const [count, setCount] = useState(3);
  const [jobs, setJobs] = useState<GenJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const width = version.width ?? 1920;
  const height = version.height ?? 1080;

  useEffect(() => {
    api.recipes().then((list) => {
      const r = list.find((x) => x.kind === "inpaint") ?? null;
      setRecipe(r);
      const find = (label: string) => r?.exposed_inputs.find((e) => e.label === label && e.input === "text");
      setPrompt(String(find("프롬프트")?.default ?? ""));
      setNegative(String(find("네거티브")?.default ?? ""));
    });
  }, []);

  const keys = useMemo(() => {
    const find = (label: string, input: string) => recipe?.exposed_inputs.find((e) => e.label === label && e.input === input)?.key;
    return { prompt: find("프롬프트", "text"), negative: find("네거티브", "text"), strength: find("샘플러", "denoise") };
  }, [recipe]);

  const point = (e: React.PointerEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return { x: ((e.clientX - rect.left) / rect.width) * width, y: ((e.clientY - rect.top) / rect.height) * height };
  };

  const stroke = (from: { x: number; y: number }, to: { x: number; y: number }) => {
    const ctx = canvasRef.current!.getContext("2d")!;
    ctx.globalCompositeOperation = tool === "brush" ? "source-over" : "destination-out";
    ctx.strokeStyle = "rgba(255, 70, 70, 1)";
    ctx.lineWidth = size;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(to.x, to.y);
    ctx.stroke();
    if (tool === "brush") setPainted(true);
  };

  const clear = () => {
    canvasRef.current!.getContext("2d")!.clearRect(0, 0, width, height);
    setPainted(false);
  };

  const submit = async () => {
    if (!recipe) return;
    setError(null);
    const png = canvasRef.current!.toDataURL("image/png");
    const values: Record<string, unknown> = {};
    if (keys.prompt) values[keys.prompt] = prompt;
    if (keys.negative) values[keys.negative] = negative;
    if (keys.strength) values[keys.strength] = strength;
    const base = Math.floor(Math.random() * 1_000_000);
    try {
      const created = await api.fix(version.id, { frame, mask_png: png, values, seeds: Array.from({ length: count }, (_, i) => base + i) });
      setJobs(created);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const running = jobs.some((j) => ACTIVE_JOB.has(j.status));
  useEffect(() => {
    if (!jobs.length || !running) return;
    const timer = setInterval(async () => {
      const next = await Promise.all(jobs.map((j) => api.job(j.id)));
      setJobs(next);
      if (!next.some((j) => ACTIVE_JOB.has(j.status))) onDone();
    }, 1500);
    return () => clearInterval(timer);
  }, [jobs, running, onDone]);

  return (
    <div className="modal-back" role="dialog" aria-modal="true" aria-label="부분 수정">
      <div className="modal">
        <div className="panel-head">
          <h2>부분 수정 · {version.shot.code} {version.code} · 프레임 {frame}</h2>
          <div className="actions"><button type="button" className="ghost icon-only" onClick={onClose} title="닫기"><Icon name="close" /></button></div>
        </div>
        <div className="mask-side">
          <div>
            <div className="mask-tools">
              <div className="segmented">
                <button type="button" className={tool === "brush" ? "on" : ""} onClick={() => setTool("brush")}><Icon name="brush" />칠하기</button>
                <button type="button" className={tool === "eraser" ? "on" : ""} onClick={() => setTool("eraser")}><Icon name="eraser" />지우기</button>
              </div>
              <label className="check">크기<input type="range" min={10} max={300} value={size} onChange={(e) => setSize(Number(e.target.value))} /></label>
              <span className="faint small num">{size}px</span>
              <span className="spacer" />
              <button type="button" className="ghost" onClick={clear}>모두 지우기</button>
            </div>
            <div className="mask-stage">
              <img src={media.frame(version.id, frame)} alt="" draggable={false} />
              <canvas
                ref={canvasRef}
                width={width}
                height={height}
                onPointerDown={(e) => {
                  drawing.current = true;
                  const p = point(e);
                  last.current = p;
                  stroke(p, p);
                  (e.target as HTMLElement).setPointerCapture(e.pointerId);
                }}
                onPointerMove={(e) => {
                  if (!drawing.current || !last.current) return;
                  const p = point(e);
                  stroke(last.current, p);
                  last.current = p;
                }}
                onPointerUp={() => { drawing.current = false; last.current = null; }}
              />
            </div>
          </div>
          <div>
            <div className="form-section">
              <label className="field">수정 지시 (프롬프트)<textarea rows={4} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></label>
              <label className="field">피할 것 (네거티브)<textarea rows={2} value={negative} onChange={(e) => setNegative(e.target.value)} /></label>
              <div className="row">
                <label className="field" style={{ flex: 1 }}>강도 {strength.toFixed(2)}
                  <input type="range" min={0.3} max={1} step={0.05} value={strength} onChange={(e) => setStrength(Number(e.target.value))} />
                </label>
                <label className="field">후보 수
                  <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
                    {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}개</option>)}
                  </select>
                </label>
              </div>
              <p className="faint small">칠한 영역 주변만 잘라 SD1.5로 다시 그린 뒤 원래 프레임에 붙입니다. 칠하지 않은 픽셀은 바뀌지 않으며, 결과마다 자동 검수로 확인합니다.</p>
              <button type="button" className="primary" disabled={!painted || !recipe || running} onClick={submit}>
                {running ? "생성 중" : `후보 ${count}개 만들기`}
              </button>
              {error && <p className="error-text small">{error}</p>}
            </div>
            {jobs.length > 0 && (
              <div className="form-section">
                <h3>후보 (원본 {version.code}의 자식 버전으로 자동 등록)</h3>
                <div className="media-grid" style={{ gridTemplateColumns: "1fr" }}>
                  {jobs.map((j) => <JobCard key={j.id} job={j} />)}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
