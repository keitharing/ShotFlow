import { useEffect, useState, type ReactNode } from "react";
import { ACTIVE_JOB } from "../api";
import { Icon } from "./Icon";

// Numbered step boxes, a progress line and a job that refreshes itself while it runs (3D integration pages).

export function Step({ n, title, hint, done, children }: { n: number; title: string; hint?: string; done?: boolean; children: ReactNode }) {
  return (
    <section className="panel step">
      <div className="step-head">
        <span className={`step-num${done ? " done" : ""}`}>{done ? <Icon name="check" size={12} /> : n}</span>
        <div>
          <h2>{title}</h2>
          {hint && <p className="faint small">{hint}</p>}
        </div>
      </div>
      <div className="step-body">{children}</div>
    </section>
  );
}

export function Progress({ value, stage, error }: { value: number; stage: string | null; error?: string | null }) {
  if (error) return <p className="error-text small">{error}</p>;
  return (
    <div className="progress-line">
      <div className="progress running"><div style={{ width: `${Math.round(value * 100)}%` }} /></div>
      <span className="faint small">{stage ?? "대기"}</span>
    </div>
  );
}

export function usePolled<T extends { status: string }>(initial: T | null, load: (job: T) => Promise<T>): [T | null, (j: T | null) => void] {
  const [job, setJob] = useState<T | null>(initial);
  const active = !!job && ACTIVE_JOB.has(job.status);
  useEffect(() => {
    if (!job || !active) return;
    const timer = setInterval(async () => setJob(await load(job)), 1500);
    return () => clearInterval(timer);
  }, [job, active]); // eslint-disable-line react-hooks/exhaustive-deps
  return [job, setJob];
}

