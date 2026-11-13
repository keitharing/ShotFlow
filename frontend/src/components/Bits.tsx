import type { ReactNode } from "react";
import { QC_STATUS_LABEL, SEVERITY_LABEL, STATUS_LABEL } from "../format";
import type { QCCounts, QCIssue, QCRun, Severity, VersionStatus } from "../types";

export function StatusDot({ status }: { status: VersionStatus }) {
  return (
    <span className={`status ${status}`}>
      <span>{STATUS_LABEL[status] ?? status}</span>
    </span>
  );
}

export function QCLabel({ status, counts }: { status: string | null; counts?: QCCounts }) {
  if (!status) return <span className="qc">QC 전</span>;
  const n = counts ? counts.error + counts.warning : 0;
  return (
    <span className={`qc ${status}`}>
      QC {QC_STATUS_LABEL[status] ?? status}
      {n ? ` ${n}` : ""}
    </span>
  );
}

export function SeverityTag({ severity }: { severity: Severity }) {
  return <span className={`sev ${severity}`}>{SEVERITY_LABEL[severity]}</span>;
}

export function Panel({ title, actions, children, flush }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
}) {
  return (
    <section className="panel">
      {(title || actions) && (
        <div className="panel-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      <div className={`panel-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}

export function Toolbar({ crumbs, title, actions }: {
  crumbs?: { label: string; href?: string }[];
  title: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="toolbar">
      <div className="title">
        {crumbs && crumbs.length > 0 && (
          <nav className="crumbs" aria-label="경로">
            {crumbs.map((c, i) => (
              <span key={i}>
                {c.href ? <a href={c.href}>{c.label}</a> : c.label}
                {i < crumbs.length - 1 ? " /" : ""}
              </span>
            ))}
          </nav>
        )}
        <h1>{title}</h1>
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function QCList({ run, onSeek }: { run: QCRun | null; onSeek?: (frame: number) => void }) {
  if (!run) return <p className="faint">아직 검수하지 않았습니다.</p>;
  if (run.error) return <p className="error-text">검수 실패: {run.error}</p>;
  const m = run.measurements as Record<string, number | string | null>;
  const isImage = m.integrated_lufs === undefined && m.luma_std !== undefined;
  return (
    <div>
      <div className="metrics">
        <span>상태 <b>{QC_STATUS_LABEL[run.status] ?? run.status}</b></span>
        {isImage ? (
          <>
            <span>크기 <b>{m.width}×{m.height}</b></span>
            <span>밝기 표준편차 <b>{m.luma_std}</b></span>
          </>
        ) : (
          <>
            <span>음량 <b>{m.integrated_lufs != null ? `${Number(m.integrated_lufs).toFixed(1)} LUFS` : "-"}</b></span>
            <span>트루 피크 <b>{m.true_peak_dbfs != null ? `${Number(m.true_peak_dbfs).toFixed(1)} dBFS` : "-"}</b></span>
            <span>평균 움직임 <b>{m.mean_motion ?? "-"}</b></span>
          </>
        )}
        <span>검수 <b>{run.duration_s?.toFixed(1)}초</b></span>
      </div>
      {run.issues.length === 0 ? (
        <p className="faint">발견된 문제가 없습니다.</p>
      ) : (
        <ul className="issues">
          {run.issues.map((i: QCIssue) => (
            <li key={i.id}>
              <SeverityTag severity={i.severity} />
              {i.start_frame != null && onSeek && (
                <button type="button" className="link mono" onClick={() => onSeek(i.start_frame!)}>f{i.start_frame}</button>
              )}
              <span>{i.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <div className="page"><div className="panel panel-body error-text">불러오기 실패: {error}</div></div>;
  if (loading) return <div className="page"><div className="faint">불러오는 중</div></div>;
  return null;
}
