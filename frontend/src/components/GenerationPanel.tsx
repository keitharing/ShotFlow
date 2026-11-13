import { useState } from "react";
import { link } from "../router";
import type { GenerationJob } from "../types";

const PARAM_LABEL: Record<string, string> = {
  mode: "모드",
  resolution: "해상도",
  duration: "길이",
  aspect_ratio: "비율",
  generate_audio: "오디오 생성",
  v03_source: "v0.3 원본",
  recipe_name: "레시피",
  recipe_version: "레시피 버전",
  source_frame: "원본 프레임",
};

export function GenerationPanel({ job }: { job: GenerationJob | null }) {
  const [open, setOpen] = useState(false);
  if (!job) return <p className="faint">생성 기록이 없습니다 (편집 결과물 또는 수동 등록).</p>;
  const params = job.params ?? {};
  const shown = Object.keys(PARAM_LABEL).filter((k) => params[k] !== undefined && params[k] !== null);
  return (
    <div>
      <dl className="kv">
        <dt>서비스</dt><dd>{job.provider === "comfyui" ? "ComfyUI (로컬)" : job.provider === "comfyui-node" ? "ComfyUI 직접 작업 (ShotFlow 노드)" : job.provider}</dd>
        <dt>모델</dt><dd className="mono">{job.model ?? "-"}</dd>
        <dt>상태</dt><dd>{job.status}</dd>
        {job.credits != null && <><dt>크레딧</dt><dd className="num">{job.credits}</dd></>}
        <dt>시드</dt><dd className="num">{job.seed ?? "서비스가 제공하지 않음"}</dd>
        <dt>작업 ID</dt><dd className="mono small">{job.external_id ?? "-"}</dd>
        {shown.map((k) => (
          <Row key={k} label={PARAM_LABEL[k]} value={String(params[k])} />
        ))}
        {job.provider.startsWith("comfyui") && (
          <><dt>생성 작업</dt><dd><a className="link" href={link.job(job.id)}>작업 #{job.id} 노드 구조 보기</a></dd></>
        )}
        {job.source_record && <><dt>원본 기록</dt><dd className="mono small">{job.source_record}</dd></>}
      </dl>
      {job.prompt && (
        <div style={{ marginTop: 8 }}>
          <button type="button" className="link" onClick={() => setOpen(!open)}>{open ? "프롬프트 접기" : "프롬프트 보기"}</button>
          {open && <p className="prompt">{job.prompt}</p>}
        </div>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}
