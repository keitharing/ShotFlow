import { useCallback, useEffect, useRef, useState } from "react";
import { api, waitForTask } from "../api";
import { Loading, Panel, QCLabel, QCList, StatusDot, Toolbar } from "../components/Bits";
import { GenerationPanel } from "../components/GenerationPanel";
import { Icon } from "../components/Icon";
import { JobCard } from "../components/JobCard";
import { MaskEditor } from "../components/MaskEditor";
import { NotePanel } from "../components/NotePanel";
import { Player, type PlayerHandle } from "../components/Player";
import { ImportVersionForm } from "../components/StructureForms";
import { ORIGIN_LABEL, bytes, kst, seconds } from "../format";
import { link } from "../router";
import type { GenJob, VersionBrief, VersionDetail } from "../types";
import { useAsync } from "../useAsync";

/** Versions ordered as a lineage: children appear under their parent. */
function lineage(versions: VersionBrief[]): { v: VersionBrief; depth: number }[] {
  const ids = new Set(versions.map((v) => v.id));
  const byParent = new Map<number | null, VersionBrief[]>();
  for (const v of versions) {
    const key = v.parent_id && ids.has(v.parent_id) ? v.parent_id : null;
    byParent.set(key, [...(byParent.get(key) ?? []), v]);
  }
  const out: { v: VersionBrief; depth: number }[] = [];
  const walk = (parent: number | null, depth: number) => {
    for (const v of byParent.get(parent) ?? []) {
      out.push({ v, depth });
      walk(v.id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

export function ShotPage({ id, initialVersion }: { id: number; initialVersion: number | null }) {
  const shotQ = useAsync(() => api.shot(id), [id]);
  const [selected, setSelected] = useState<number | null>(initialVersion);
  const [version, setVersion] = useState<VersionDetail | null>(null);
  const [frame, setFrame] = useState(0);
  const [qcBusy, setQcBusy] = useState(false);
  const [fixOpen, setFixOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [jobs, setJobs] = useState<GenJob[]>([]);
  const player = useRef<PlayerHandle>(null);

  const shot = shotQ.data;
  const activeId = selected ?? shot?.approved_version_id ?? shot?.versions.at(-1)?.id ?? null;

  useEffect(() => {
    if (activeId == null) return;
    let alive = true;
    api.version(activeId).then((v) => alive && setVersion(v));
    return () => {
      alive = false;
    };
  }, [activeId]);

  const loadJobs = useCallback(() => {
    if (shot?.kind === "shot") api.jobs({ shot_id: id, limit: 12 }).then(setJobs);
  }, [id, shot?.kind]);
  useEffect(loadJobs, [loadJobs]);
  // 생성 중인 작업이 있으면 끝날 때까지 2초마다 다시 불러옴
  const jobsActive = jobs.some((j) => j.status === "queued" || j.status === "running" || j.status === "cancel_requested");
  useEffect(() => {
    if (!jobsActive) return;
    const t = window.setTimeout(loadJobs, 2000);
    return () => window.clearTimeout(t);
  }, [jobsActive, jobs, loadJobs]);

  if (!shot) return <Loading error={shotQ.error} loading={shotQ.loading} />;
  const approvedId = shot.approved_version_id;
  const fps = version?.fps ?? 24;
  const isShot = shot.kind === "shot";

  const rerunQC = async () => {
    if (!version) return;
    setQcBusy(true);
    const task = await api.runQC(version.id);
    await waitForTask(task.id);
    setVersion(await api.version(version.id));
    shotQ.reload();
    setQcBusy(false);
  };

  return (
    <>
      <Toolbar
        crumbs={version ? [{ label: version.sequence.name, href: link.sequence(version.sequence.id) }] : []}
        title={isShot ? `샷 ${shot.code}` : "편집본"}
        actions={
          <>
            {version && approvedId && approvedId !== version.id && (
              <a className="button" href={link.review(approvedId, version.id)}><Icon name="compare" />승인 버전과 비교</a>
            )}
            {isShot && (
              <button type="button" onClick={() => setImportOpen((o) => !o)}><Icon name="upload" />밖에서 만든 파일 등록</button>
            )}
            {isShot && version && (
              <>
                <button type="button" onClick={() => { player.current?.pause(); setFixOpen(true); }}>
                  <Icon name="brush" />부분 수정{version.media_kind === "video" ? ` (프레임 ${frame})` : ""}
                </button>
                <a className="button primary" href={link.generate(shot.id, version.id, frame)}><Icon name="generate" />이 버전으로 생성</a>
              </>
            )}
          </>
        }
      />
      <div className="page">
        {isShot && importOpen && (
          <ImportVersionForm
            shotId={shot.id}
            versions={shot.versions}
            defaultParent={activeId}
            onClose={() => setImportOpen(false)}
            onDone={(vid) => { setImportOpen(false); setSelected(vid); shotQ.reload(); }}
          />
        )}
        <div className="split">
          <Panel title="버전" flush>
            <ul className="version-list">
              {lineage(shot.versions).map(({ v, depth }) => (
                <li key={v.id}>
                  <button
                    type="button"
                    className={`version-row${v.id === activeId ? " active" : ""}`}
                    style={{ paddingLeft: 8 + depth * 14 }}
                    onClick={() => setSelected(v.id)}
                  >
                    <span className="vcode">{v.code}</span>
                    <span className="meta">
                      {v.id === approvedId ? <span className="tag">현재 승인</span> : <span className="faint small">{ORIGIN_LABEL[v.origin] ?? v.origin}</span>}
                      <StatusDot status={v.status} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Panel>

          <div className="main-col">
            {version ? (
              <>
                <section className="panel">
                  <div className="panel-head">
                    <h2 className="mono">{version.code}</h2>
                    <span className="faint small">{ORIGIN_LABEL[version.origin] ?? version.origin} · {version.width}×{version.height}{version.media_kind === "video" ? ` · ${seconds(version.duration_s, 2)}` : ""}</span>
                    <div className="actions">
                      <StatusDot status={version.status} />
                      <QCLabel status={version.qc_status} counts={version.qc_counts} />
                    </div>
                  </div>
                  <Player
                    ref={player}
                    key={version.id}
                    versionId={version.id}
                    kind={version.media_kind}
                    fps={fps}
                    frameCount={version.frame_count ?? 1}
                    issues={version.qc?.issues ?? []}
                    onFrame={setFrame}
                  />
                </section>

                <div className="cols-2">
                  <Panel title="자동 검수" actions={<button type="button" className="ghost" disabled={qcBusy} onClick={rerunQC}><Icon name="refresh" />{qcBusy ? "검수 중" : "다시 검수"}</button>}>
                    <QCList run={version.qc} onSeek={(f) => player.current?.seekFrame(f)} />
                  </Panel>
                  <Panel title="리뷰 노트">
                    <NotePanel
                      version={version}
                      currentFrame={version.media_kind === "video" ? frame : null}
                      onChanged={(v) => { setVersion(v); shotQ.reload(); }}
                      onSeek={(f) => player.current?.seekFrame(f)}
                    />
                  </Panel>
                </div>

                <div className="cols-2">
                  <Panel title="생성 기록"><GenerationPanel job={version.generation_job} /></Panel>
                  <Panel title="파일">
                    <dl className="kv">
                      <dt>파일</dt><dd className="mono small">{version.file_name}</dd>
                      <dt>규격</dt><dd>{version.width}×{version.height}{version.fps ? ` · ${version.fps}fps` : ""} · {version.video_codec}</dd>
                      {version.media_kind === "video" && <><dt>길이</dt><dd className="num">{seconds(version.duration_s, 2)} · {version.frame_count}프레임</dd></>}
                      <dt>크기</dt><dd className="num">{bytes(version.bytes)}</dd>
                      <dt>SHA-256</dt>
                      <dd className="mono small">
                        {version.sha256?.slice(0, 16)}…
                        {version.sha_verified === true && <span className="ok-text"> 기록과 일치</span>}
                        {version.sha_verified === false && <span className="error-text"> 기록과 불일치</span>}
                      </dd>
                      <dt>등록</dt><dd>{kst(version.created_at)}</dd>
                    </dl>
                  </Panel>
                </div>

                {isShot && jobs.length > 0 && (
                  <Panel title="이 샷의 생성 작업" actions={<a className="button ghost" href={link.jobs()}>대기열</a>}>
                    <div className="media-grid">
                      {jobs.map((j) => <JobCard key={j.id} job={j} onChange={() => { loadJobs(); shotQ.reload(); }} />)}
                    </div>
                  </Panel>
                )}
              </>
            ) : (
              <div className="panel panel-body faint">버전을 선택하세요.</div>
            )}
          </div>
        </div>
      </div>
      {fixOpen && version && (
        <MaskEditor
          version={version}
          frame={version.media_kind === "video" ? frame : 0}
          onClose={() => setFixOpen(false)}
          onDone={() => { shotQ.reload(); loadJobs(); }}
        />
      )}
    </>
  );
}
