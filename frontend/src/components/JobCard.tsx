import { useState } from "react";
import { api } from "../api";
import { link } from "../router";
import type { GenJob } from "../types";

const STATE_LABEL: Record<string, string> = {
  queued: "대기",
  running: "생성 중",
  completed: "완료",
  failed: "실패",
  cancelled: "취소됨",
  cancel_requested: "취소 중",
  interrupted: "중단됨",
};

export function jobLabel(job: GenJob, strengthKey?: string | null): string {
  const parts = [job.seed != null ? `시드 ${job.seed}` : null];
  if (strengthKey && job.values?.[strengthKey] != null) parts.push(`강도 ${Number(job.values[strengthKey]).toFixed(2)}`);
  return parts.filter(Boolean).join(" · ");
}

export function JobState({ job }: { job: GenJob }) {
  return <span className={`job-state ${job.status}`}>{STATE_LABEL[job.status] ?? job.status}</span>;
}

export function JobCard({ job, strengthKey, onChange }: { job: GenJob; strengthKey?: string | null; onChange?: () => void }) {
  const [busy, setBusy] = useState(false);
  const out = job.outputs[0];
  const active = job.status === "queued" || job.status === "running" || job.status === "cancel_requested";

  const register = async () => {
    setBusy(true);
    try {
      await api.registerOutput(job.id, 0);
      onChange?.();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="media-card">
      {out ? (
        out.kind === "image" ? <img src={out.url} alt="" loading="lazy" /> : <video src={out.url} muted loop playsInline autoPlay />
      ) : (
        <div className="thumb-empty" />
      )}
      {active && <div className={`progress ${job.status === "running" ? "running" : ""}`}><div style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>}
      <div className="body">
        <div className="line">
          <span className="name num">{jobLabel(job, strengthKey) || `작업 ${job.id}`}</span>
          <JobState job={job} />
        </div>
        <div className="line small faint">
          <span>{active ? job.stage ?? "" : job.seconds != null ? `${job.seconds}초` : job.error ? job.error.slice(0, 40) : ""}</span>
          <a className="faint" href={link.job(job.id)}>#{job.id}</a>
        </div>
        {job.status === "completed" && out && (
          out.version_id ? (
            <a className="button" href={link.shot(job.shot_id, out.version_id)}>버전으로 보기</a>
          ) : (
            <button type="button" disabled={busy} onClick={register}>버전으로 등록</button>
          )
        )}
        {active && (
          <button type="button" className="ghost" onClick={async () => { await api.cancelJob(job.id); onChange?.(); }}>취소</button>
        )}
      </div>
    </div>
  );
}
