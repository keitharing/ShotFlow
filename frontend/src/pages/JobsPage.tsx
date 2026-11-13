import { useEffect, useMemo, useState } from "react";
import { ACTIVE_JOB, api } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { JobCard, JobState } from "../components/JobCard";
import { NodeGraph } from "../components/NodeGraph";
import { kst } from "../format";
import { link } from "../router";
import type { GenJob, GraphData } from "../types";
import { useAsync } from "../useAsync";

export function JobsPage() {
  const q = useAsync(() => api.jobs({ limit: 100 }), []);
  const [jobs, setJobs] = useState<GenJob[] | null>(null);
  useEffect(() => setJobs(q.data), [q.data]);

  const active = (jobs ?? []).some((j) => ACTIVE_JOB.has(j.status));
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => api.jobs({ limit: 100 }).then(setJobs), 2000);
    return () => clearInterval(timer);
  }, [active]);

  if (!jobs) return <Loading error={q.error} loading={q.loading} />;
  const done = jobs.filter((j) => j.status === "completed");
  const avg = done.length ? done.reduce((s, j) => s + (j.seconds ?? 0), 0) / done.length : 0;
  const peakTemp = Math.max(0, ...done.map((j) => j.peak_temp_c ?? 0));

  return (
    <>
      <Toolbar crumbs={[{ label: "생성" }]} title="작업 대기열" actions={<span className="faint small num">완료 {done.length} · 평균 {avg.toFixed(1)}초 · 최고 온도 {peakTemp.toFixed(0)}°C</span>} />
      <div className="page">
        <Panel flush>
          <table className="table">
            <thead>
              <tr><th>작업</th><th>레시피</th><th>목적</th><th>샷</th><th className="num">시드</th><th>상태</th><th>단계</th><th className="num">시간</th><th className="num">VRAM</th><th className="num">온도</th><th>요청 (KST)</th></tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id} className="clickable" onClick={() => (window.location.hash = link.job(j.id))}>
                  <td className="mono">#{j.id}</td>
                  <td>{j.recipe_name}{j.recipe_version != null && <span className="faint"> v{j.recipe_version}</span>}</td>
                  <td className="faint">{j.purpose === "fix" ? "부분 수정" : j.purpose === "publish" ? "ComfyUI 등록" : "생성"}</td>
                  <td><a href={link.shot(j.shot_id)} onClick={(e) => e.stopPropagation()}>{j.shot_code ?? `샷 ${j.shot_id}`}</a></td>
                  <td className="num">{j.seed ?? "-"}</td>
                  <td><JobState job={j} /></td>
                  <td className="faint small">{ACTIVE_JOB.has(j.status) ? `${j.stage ?? ""} ${Math.round(j.progress * 100)}%` : j.oom_retry ? "메모리 부족 재시도" : ""}</td>
                  <td className="num">{j.seconds != null ? `${j.seconds}초` : "-"}</td>
                  <td className="num">{j.peak_vram_mb ? `${(j.peak_vram_mb / 1024).toFixed(1)}GB` : "-"}</td>
                  <td className="num">{j.peak_temp_c ? `${j.peak_temp_c.toFixed(0)}°C` : "-"}</td>
                  <td className="faint">{kst(j.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}

function toGraph(graph: NonNullable<GenJob["graph"]>): GraphData {
  const nodes: GraphData["nodes"] = [];
  const links: GraphData["links"] = [];
  for (const [id, node] of Object.entries(graph)) {
    const literal: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(node.inputs)) {
      if (Array.isArray(v) && v.length === 2 && typeof v[0] === "string") links.push({ from: v[0], slot: v[1] as number, to: id, input: k });
      else literal[k] = v;
    }
    nodes.push({ id, class_type: node.class_type, title: node._meta?.title ?? "", inputs: literal });
  }
  return { nodes, links };
}

export function JobDetailPage({ id }: { id: number }) {
  const q = useAsync(() => api.job(id), [id]);
  const [job, setJob] = useState<GenJob | null>(null);
  useEffect(() => setJob(q.data), [q.data]);
  useEffect(() => {
    if (!job || !ACTIVE_JOB.has(job.status)) return;
    const timer = setInterval(() => api.job(id).then(setJob), 1500);
    return () => clearInterval(timer);
  }, [job, id]);
  const graph = useMemo(() => (job?.graph ? toGraph(job.graph) : null), [job?.graph]);

  if (!job) return <Loading error={q.error} loading={q.loading} />;
  return (
    <>
      <Toolbar crumbs={[{ label: "작업 대기열", href: link.jobs() }]} title={`작업 #${job.id} · ${job.recipe_name}`} actions={<JobState job={job} />} />
      <div className="page">
        <div className="cols-2">
          <Panel title="결과">
            <div className="media-grid" style={{ gridTemplateColumns: "1fr" }}>
              <JobCard job={job} onChange={() => api.job(id).then(setJob)} />
            </div>
          </Panel>
          <Panel title="실행 기록">
            <dl className="kv">
              <dt>레시피</dt><dd>{job.recipe_name}{job.recipe ? ` v${job.recipe_version} (${job.recipe})` : ""}</dd>
              <dt>목적</dt><dd>{job.purpose === "fix" ? "부분 수정" : job.purpose === "publish" ? "ComfyUI에서 직접 등록" : "생성"}</dd>
              <dt>샷</dt><dd><a className="link" href={link.shot(job.shot_id)}>{job.shot_code ?? `샷 ${job.shot_id}`}</a>{job.source_version_id ? ` · 원본 버전 ${job.source_version_id} · 프레임 ${job.source_frame}` : ""}</dd>
              <dt>시드</dt><dd className="num">{job.seed ?? "-"}</dd>
              <dt>ComfyUI ID</dt><dd className="mono small">{job.external_id ?? "-"}</dd>
              <dt>소요</dt><dd className="num">{job.seconds != null ? `${job.seconds}초` : "-"}</dd>
              <dt>GPU 최대</dt><dd className="num">{job.peak_vram_mb ? `${job.peak_vram_mb.toFixed(0)}MB · ${job.peak_temp_c?.toFixed(0)}°C` : "-"}</dd>
              {job.oom_retry && <><dt>재시도</dt><dd>메모리 부족 → {job.oom_retry.join(", ")}</dd></>}
              <dt>요청</dt><dd>{kst(job.created_at)}</dd>
              <dt>완료</dt><dd>{kst(job.finished_at)}</dd>
              {job.error && <><dt>오류</dt><dd className="error-text">{job.error}</dd></>}
            </dl>
            <h3>모델 파일</h3>
            <ul className="issues">
              {(job.models ?? []).map((m) => <li key={m.file}><span className="mono small">{m.file}</span><span className="faint mono small" style={{ marginLeft: "auto" }}>{m.sha256 ? `${m.sha256.slice(0, 16)}…` : "-"}</span></li>)}
            </ul>
            <h3>입력값</h3>
            <ul className="issues">
              {Object.entries(job.values ?? {}).map(([k, v]) => <li key={k}><span className="mono small faint">{k}</span><span className="small" style={{ marginLeft: "auto", maxWidth: "70%", textAlign: "right" }}>{String(v)}</span></li>)}
            </ul>
          </Panel>
        </div>
        {graph && (
          <Panel title="실행된 노드 구조" actions={<span className="faint small">테두리가 밝은 노드가 SF: 입력</span>}>
            <NodeGraph graph={graph} />
          </Panel>
        )}
      </div>
    </>
  );
}
