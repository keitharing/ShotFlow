import { useState } from "react";
import { api, media } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { JobState } from "../components/JobCard";
import { NewSequenceForm } from "../components/StructureForms";
import { kst } from "../format";
import { link } from "../router";
import type { Project, SequenceSummary } from "../types";
import { useAsync } from "../useAsync";

const ACTION_LABEL: Record<string, string> = {
  import_v04: "v0.4 기록 가져오기",
  qc_run: "자동 검수",
  qc_failed: "검수 실패",
  boundary_sheet: "경계 비교 시트",
  assemble: "편집본 조립",
  edit_cloned: "편집 복제",
  edit_updated: "편집 수정",
  auto_color: "자동 밝기 맞춤",
  review_approve: "승인",
  review_reject: "반려",
  review_revise: "수정요청",
  review_comment: "코멘트",
  superseded: "이전 승인 해제",
  recipe_registered: "레시피 등록",
  generation_queued: "생성 요청",
  generation_registered: "생성 결과 등록",
  comfyui_published: "ComfyUI에서 등록",
  delivery_created: "납품 패키지",
  delivery_verified: "납품 검증",
  external_imported: "외부 생성 결과 등록",
  image_imported: "그림 등록",
  video_imported: "영상 등록",
};

export function Dashboard({ project, sequences }: { project: Project; sequences: SequenceSummary[] }) {
  const [newOpen, setNewOpen] = useState(false);
  const { data, error, loading } = useAsync(
    () => Promise.all([api.dashboard(), Promise.all(sequences.map((s) => api.sequence(s.id))), api.jobs({ limit: 8 }), api.productionStats()]),
    [sequences.map((s) => s.id).join(",")],
  );
  if (!data) return <Loading error={error} loading={loading} />;
  const [dash, details, jobs, stats] = data;
  const issues = dash.qc_issues;

  return (
    <>
      <Toolbar
        title={project.name}
        crumbs={[{ label: `${project.width}×${project.height} · ${project.fps}fps · 목표 ${project.target_lufs} LUFS` }]}
        actions={<button type="button" className="primary" onClick={() => setNewOpen((o) => !o)}><Icon name="plus" />새 영상</button>}
      />
      <div className="page">
        {newOpen && <NewSequenceForm projectId={project.id} onClose={() => setNewOpen(false)} />}
        <div className="stats">
          <div className="stat"><b>{dash.shots_approved} / {dash.shots}</b><span>샷 승인</span></div>
          <div className="stat"><b>{dash.versions}</b><span>버전</span></div>
          <div className="stat"><b>{dash.credits_total.toLocaleString()}</b><span>외부 생성 크레딧 누적</span></div>
          <div className="stat"><b>{dash.qc_versions_checked}</b><span>검수된 버전</span></div>
          <div className="stat"><b className={issues.error ? "error-text" : ""}>{issues.error ?? 0} / {issues.warning ?? 0}</b><span>최신 검수 오류 / 경고</span></div>
        </div>

        <Panel title="시퀀스">
          <div className="media-grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
            {sequences.map((s, i) => {
              const latest = details[i]?.edit_list.filter((e) => e.result_version_id).at(-1);
              return (
                <a className="media-card" key={s.id} href={link.sequence(s.id)}>
                  {latest?.result_version_id ? <img src={media.thumbnail(latest.result_version_id)} alt="" loading="lazy" /> : <div className="thumb-empty" />}
                  <div className="body">
                    <div className="line"><span className="name">{s.name}</span><span className="faint small">{latest ? latest.name : ""}</span></div>
                    <span className="faint small">샷 {s.shots} · 승인 {s.approved} · 편집 {s.edits.length}</span>
                  </div>
                </a>
              );
            })}
          </div>
        </Panel>

        <Panel title="로컬 생성 통계" actions={<span className="faint small num">GPU 사용 {Math.round(stats.gpu_seconds / 60)}분 · 최고 {stats.peak_temp_c?.toFixed(0) ?? "-"}°C · 최대 VRAM {stats.peak_vram_mb ? (stats.peak_vram_mb / 1024).toFixed(1) : "-"}GB · 납품 {stats.deliveries}건</span>} flush>
          <table className="table">
            <thead><tr><th>레시피</th><th className="num">작업</th><th className="num">완료</th><th className="num">실패·중단</th><th className="num">평균 시간</th></tr></thead>
            <tbody>
              {stats.recipes.length === 0 && <tr><td colSpan={5} className="faint">아직 없음</td></tr>}
              {stats.recipes.map((r) => (
                <tr key={r.recipe}>
                  <td>{r.recipe}</td>
                  <td className="num">{r.jobs}</td>
                  <td className="num">{r.completed}</td>
                  <td className="num">{r.failed}</td>
                  <td className="num">{r.avg_seconds != null ? `${r.avg_seconds}초` : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <div className="cols-2">
          <Panel title="최근 생성 작업" actions={<a className="button ghost" href={link.jobs()}>전체</a>} flush>
            <table className="table">
              <thead><tr><th>작업</th><th>레시피</th><th>상태</th><th className="num">시간</th><th>요청 (KST)</th></tr></thead>
              <tbody>
                {jobs.length === 0 && <tr><td colSpan={5} className="faint">아직 없음</td></tr>}
                {jobs.map((j) => (
                  <tr key={j.id} className="clickable" onClick={() => (window.location.hash = link.job(j.id))}>
                    <td className="mono">#{j.id}</td>
                    <td>{j.recipe_name}{j.purpose === "fix" ? " (부분 수정)" : ""}</td>
                    <td><JobState job={j} /></td>
                    <td className="num">{j.seconds != null ? `${j.seconds}초` : "-"}</td>
                    <td className="faint">{kst(j.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title="최근 활동" flush>
            <table className="table">
              <thead><tr><th>시각 (KST)</th><th>작업</th><th>대상</th><th>작업자</th></tr></thead>
              <tbody>
                {dash.recent_activity.slice(0, 10).map((a, i) => (
                  <tr key={i}>
                    <td className="faint">{kst(a.at)}</td>
                    <td>{ACTION_LABEL[a.action] ?? a.action}</td>
                    <td className="faint mono small">{a.target_type ? `${a.target_type} ${a.target_id}` : "-"}</td>
                    <td className="faint">{a.actor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </div>
      </div>
    </>
  );
}
