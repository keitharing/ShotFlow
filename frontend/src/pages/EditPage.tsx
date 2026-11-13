import { useEffect, useMemo, useRef, useState } from "react";
import { api, waitForTask } from "../api";
import { Loading, Panel, QCList, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { Player, type PlayerHandle } from "../components/Player";
import { kst, seconds } from "../format";
import { link } from "../router";
import type { Edit, EndCard, Shot, Task, VersionDetail } from "../types";
import { useAsync } from "../useAsync";

interface DraftEntry {
  shotId: number;
  version_id: number;
  src_in: number;
  src_out: number;
  transition: "cut" | "dissolve";
  transition_frames: number;
  brightness: number;
}

const DEFAULT_CARD: EndCard = { title: "", subtitle: "AI Product Film", duration_s: 2, transition: "dissolve", transition_frames: 12 };

function toDraft(edit: Edit, shots: Shot[]): DraftEntry[] {
  return edit.entries.map((e) => ({
    shotId: shots.find((s) => s.versions.some((v) => v.id === e.version_id))?.id ?? 0,
    version_id: e.version_id,
    src_in: e.src_in,
    src_out: e.src_out,
    transition: e.transition,
    transition_frames: e.transition_frames,
    brightness: e.color?.brightness ?? 0,
  }));
}

export function EditPage({ id }: { id: number }) {
  const editQ = useAsync(() => api.edit(id), [id]);
  const shotsQ = useAsync(
    () => (editQ.data ? api.shots(editQ.data.sequence_id) : Promise.resolve([] as Shot[])),
    [editQ.data?.sequence_id],
  );
  const [draft, setDraft] = useState<DraftEntry[] | null>(null);
  const [fadeIn, setFadeIn] = useState(0.12);
  const [fadeOut, setFadeOut] = useState(0.25);
  const [card, setCard] = useState<EndCard | null>(null);
  const [dirty, setDirty] = useState(false);
  const [result, setResult] = useState<VersionDetail | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [versionCode, setVersionCode] = useState("");
  const [reference, setReference] = useState("");
  const [cloneName, setCloneName] = useState("");
  const player = useRef<PlayerHandle>(null);

  const edit = editQ.data;
  const shots = useMemo(() => (shotsQ.data ?? []).filter((s) => s.kind === "shot"), [shotsQ.data]);

  useEffect(() => {
    if (!edit || !shots.length) return;
    setDraft(toDraft(edit, shots));
    setFadeIn(edit.settings.fade_in_s ?? 0);
    setFadeOut(edit.settings.fade_out_s ?? 0);
    setCard(edit.settings.end_card ?? null);
    setReference((r) => r || shots.at(-1)?.code || "");
    setDirty(false);
  }, [edit, shots]);

  useEffect(() => {
    if (!edit?.result_version_id) return setResult(null);
    api.version(edit.result_version_id).then(setResult);
  }, [edit?.result_version_id]);

  if (!edit || !draft) return <Loading error={editQ.error ?? shotsQ.error} loading />;
  const fps = result?.fps ?? 24;
  const shotById = (sid: number) => shots.find((s) => s.id === sid);
  const versionFrames = (vid: number) =>
    shots.flatMap((s) => s.versions).find((v) => v.id === vid)?.frame_count ?? 0;

  const update = (i: number, patch: Partial<DraftEntry>) => {
    setDraft(draft.map((e, j) => (j === i ? { ...e, ...patch } : e)));
    setDirty(true);
  };
  const move = (i: number, delta: number) => {
    const j = i + delta;
    if (j < 0 || j >= draft.length) return;
    const next = [...draft];
    [next[i], next[j]] = [next[j], next[i]];
    setDraft(next);
    setDirty(true);
  };

  // Timeline positions mirror services/assemble.build_plan (dissolves overlap the previous clip).
  const layout = (() => {
    let pos = 0;
    const blocks = draft.map((e, i) => {
      const frames = e.src_out - e.src_in;
      const overlap = i > 0 && e.transition === "dissolve" ? e.transition_frames : 0;
      const start = i === 0 ? 0 : pos - overlap;
      pos = start + frames;
      return { start, frames, overlap, code: shotById(e.shotId)?.code ?? "?" };
    });
    if (card) {
      const frames = Math.round(card.duration_s * fps);
      const overlap = card.transition === "dissolve" ? card.transition_frames : 0;
      blocks.push({ start: pos - overlap, frames, overlap, code: "END" });
      pos = pos - overlap + frames;
    }
    return { blocks, total: pos };
  })();

  const save = async (): Promise<boolean> => {
    setMessage(null);
    try {
      const saved = await api.updateEdit(edit.id, {
        entries: draft.map((e) => ({
          version_id: e.version_id, src_in: e.src_in, src_out: e.src_out, transition: e.transition,
          transition_frames: e.transition === "dissolve" ? e.transition_frames : 0,
          color: e.brightness ? { brightness: e.brightness } : {},
        })),
        settings: { fade_in_s: fadeIn, fade_out_s: fadeOut, end_card: card },
      });
      editQ.setData(saved);
      setDirty(false);
      return true;
    } catch (err) {
      setMessage(`저장 실패: ${(err as Error).message}`);
      return false;
    }
  };

  const runAssemble = async () => {
    if (dirty && !(await save())) return;
    const started = await api.assemble(edit.id, versionCode.trim() || null);
    setTask(started);
    const done = await waitForTask(started.id, setTask);
    if (done.status === "failed") {
      setMessage(`조립 실패: ${done.error?.split("\n")[0]}`);
    } else {
      setMessage("조립 · 검수 · 경계 시트 완료");
      editQ.reload();
    }
    setTask(null);
  };

  const autoColor = async () => {
    const res = await api.colorSuggestion(edit.id, reference);
    setDraft(draft.map((e) => ({ ...e, brightness: res.brightness[shotById(e.shotId)?.code ?? ""] ?? e.brightness })));
    setDirty(true);
  };

  const clone = async () => {
    try {
      const created = await api.cloneEdit(edit.id, cloneName.trim());
      window.location.hash = link.edit(created.id);
    } catch (err) {
      setMessage(`복제 실패: ${(err as Error).message}`);
    }
  };

  const renders = Object.values(edit.settings.renders ?? {}).sort((x, y) => y.version_id - x.version_id);
  const notes = edit.settings.entry_notes ?? {};
  const baseResultId = edit.settings.based_on_edit_id ? result?.parent_id : null;

  return (
    <>
      <Toolbar
        crumbs={result ? [{ label: result.sequence.name, href: link.sequence(result.sequence.id) }, { label: "편집" }] : [{ label: "편집" }]}
        title={edit.name}
        actions={
          <>
            <span className="faint small num">{layout.total}프레임 · {seconds(layout.total / fps, 2)}{dirty ? " · 저장 안 됨" : ""}</span>
            <input value={cloneName} onChange={(e) => setCloneName(e.target.value)} placeholder="새 편집 이름" aria-label="새 편집 이름" style={{ width: 140 }} />
            <button type="button" disabled={!cloneName.trim()} onClick={clone}>복제</button>
          </>
        }
      />
      <div className="page">
        <Panel title="타임라인">
          <div className="timeline">
            {layout.blocks.map((b, i) => (
              <div
                key={i}
                className={`clip${b.code === "END" ? " card" : ""}`}
                style={{ left: `${(b.start / layout.total) * 100}%`, width: `${(b.frames / layout.total) * 100}%` }}
                title={`${b.code} · ${b.frames}f${b.overlap ? ` · 디졸브 ${b.overlap}f` : ""}`}
              >
                {b.overlap > 0 && <span className="overlap" style={{ width: `${(b.overlap / b.frames) * 100}%` }} />}
                <span>{b.code}</span>
              </div>
            ))}
          </div>
          <table className="table edit-table" style={{ marginTop: 10 }}>
            <thead>
              <tr><th className="num">#</th><th>샷</th><th>버전</th><th>시작</th><th>끝</th><th className="num">길이</th><th>전환</th><th>밝기</th><th>이유</th><th /></tr>
            </thead>
            <tbody>
              {draft.map((e, i) => {
                const shot = shotById(e.shotId);
                const max = versionFrames(e.version_id);
                return (
                  <tr key={i}>
                    <td className="num faint">{i + 1}</td>
                    <td>
                      <select value={e.shotId} onChange={(ev) => {
                        const s = shotById(Number(ev.target.value));
                        const v = s?.versions.find((x) => x.id === s.approved_version_id) ?? s?.versions.at(-1);
                        if (s && v) update(i, { shotId: s.id, version_id: v.id, src_in: 0, src_out: v.frame_count ?? 0 });
                      }}>
                        {shots.map((s) => <option key={s.id} value={s.id}>{s.code}</option>)}
                      </select>
                    </td>
                    <td>
                      <select value={e.version_id} onChange={(ev) => update(i, { version_id: Number(ev.target.value) })}>
                        {shot?.versions.filter((v) => v.media_kind === "video").map((v) => (
                          <option key={v.id} value={v.id}>{v.code}{v.id === shot.approved_version_id ? " (승인)" : ""}</option>
                        ))}
                      </select>
                    </td>
                    <td><input type="number" min={0} max={e.src_out - 1} value={e.src_in} onChange={(ev) => update(i, { src_in: Number(ev.target.value) })} /></td>
                    <td><input type="number" min={e.src_in + 1} max={max} value={e.src_out} onChange={(ev) => update(i, { src_out: Number(ev.target.value) })} /></td>
                    <td className="num faint">{seconds((e.src_out - e.src_in) / fps, 2)}</td>
                    <td>
                      {i === 0 ? <span className="faint">-</span> : (
                        <div className="row tight">
                          <select value={e.transition} onChange={(ev) => update(i, { transition: ev.target.value as "cut" | "dissolve", transition_frames: ev.target.value === "dissolve" ? e.transition_frames || 12 : 0 })}>
                            <option value="cut">컷</option>
                            <option value="dissolve">디졸브</option>
                          </select>
                          {e.transition === "dissolve" && (
                            <input type="number" min={1} max={48} value={e.transition_frames} onChange={(ev) => update(i, { transition_frames: Number(ev.target.value) })} title="디졸브 프레임" style={{ width: 60 }} />
                          )}
                        </div>
                      )}
                    </td>
                    <td><input type="number" step={0.005} min={-0.2} max={0.2} value={e.brightness} onChange={(ev) => update(i, { brightness: Number(ev.target.value) })} /></td>
                    <td className="reason">{notes[shot?.code ?? ""] ?? ""}</td>
                    <td>
                      <div className="row tight">
                        <button type="button" className="ghost icon-only" onClick={() => move(i, -1)} title="위로"><Icon name="up" /></button>
                        <button type="button" className="ghost icon-only" onClick={() => move(i, 1)} title="아래로"><Icon name="down" /></button>
                        <button type="button" className="ghost icon-only" onClick={() => { setDraft(draft.filter((_, j) => j !== i)); setDirty(true); }} title="삭제"><Icon name="close" /></button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="row wrap" style={{ marginTop: 10 }}>
            <button type="button" onClick={() => {
              const s = shots[0];
              const v = s?.versions.find((x) => x.id === s.approved_version_id);
              if (s && v) { setDraft([...draft, { shotId: s.id, version_id: v.id, src_in: 0, src_out: v.frame_count ?? 0, transition: "cut", transition_frames: 0, brightness: 0 }]); setDirty(true); }
            }}><Icon name="plus" />샷 추가</button>
            <span className="spacer" />
            <span className="faint small">기준 샷</span>
            <select value={reference} onChange={(e) => setReference(e.target.value)} aria-label="기준 샷">
              {shots.map((s) => <option key={s.id} value={s.code}>{s.code}</option>)}
            </select>
            <button type="button" onClick={autoColor} title="기준 샷의 밝기(L*) 쪽으로 각 샷을 절반만큼, 최대 ±0.04 보정">자동 밝기 맞춤</button>
          </div>
        </Panel>

        <div className="cols-2">
          <Panel title="마감">
            <div className="form-grid">
              <label>페이드 인 (초)<input type="number" step={0.01} min={0} value={fadeIn} onChange={(e) => { setFadeIn(Number(e.target.value)); setDirty(true); }} /></label>
              <label>페이드 아웃 (초)<input type="number" step={0.01} min={0} value={fadeOut} onChange={(e) => { setFadeOut(Number(e.target.value)); setDirty(true); }} /></label>
              <label className="check full">
                <input type="checkbox" checked={!!card} onChange={(e) => { setCard(e.target.checked ? { ...DEFAULT_CARD, title: result?.sequence.name ?? "" } : null); setDirty(true); }} />
                엔드카드 사용
              </label>
              {card && (
                <>
                  <label>제목<input value={card.title} onChange={(e) => { setCard({ ...card, title: e.target.value }); setDirty(true); }} /></label>
                  <label>부제<input value={card.subtitle} onChange={(e) => { setCard({ ...card, subtitle: e.target.value }); setDirty(true); }} /></label>
                  <label>길이 (초)<input type="number" step={0.5} min={0.5} value={card.duration_s} onChange={(e) => { setCard({ ...card, duration_s: Number(e.target.value) }); setDirty(true); }} /></label>
                  <label>전환
                    <select value={card.transition} onChange={(e) => { setCard({ ...card, transition: e.target.value as "cut" | "dissolve" }); setDirty(true); }}>
                      <option value="dissolve">디졸브</option><option value="cut">컷</option>
                    </select>
                  </label>
                </>
              )}
            </div>
          </Panel>
          <Panel title="조립">
            <p className="faint small">저장 후 FFmpeg로 조립하고, 새 버전 등록, 자동 검수, 경계 시트까지 한 번에 실행합니다. 원본 파일은 읽기만 합니다.</p>
            <div className="row wrap" style={{ marginTop: 10 }}>
              <button type="button" disabled={!dirty} onClick={save}>저장</button>
              <input value={versionCode} onChange={(e) => setVersionCode(e.target.value)} placeholder={`버전 이름 (기본 ${edit.name})`} aria-label="버전 이름" style={{ width: 160 }} />
              <button type="button" className="primary" disabled={!!task} onClick={runAssemble}>
                {task ? (task.status === "running" ? "조립 중" : "대기 중") : "조립 실행"}
              </button>
            </div>
            {message && <p className={`small ${message.includes("실패") ? "error-text" : "ok-text"}`} style={{ marginTop: 8 }}>{message}</p>}
            <h3>조립 기록</h3>
            <ul className="render-list">
              {renders.length === 0 && <li className="faint">아직 없음</li>}
              {renders.map((r) => (
                <li key={r.version_id}>
                  <span className="num">버전 {r.version_id} · {r.actual_frames}/{r.planned_frames}f · {r.seconds}초</span>
                  <span className="faint num">{r.rendered_at.replace(/_/, " ")} UTC · 컷 {r.cut_frames.length} · 디졸브 {r.dissolve_ranges.length}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        {result && (
          <section className="panel">
            <div className="panel-head">
              <h2>결과 <span className="mono">{result.code}</span></h2>
              <div className="actions">
                {baseResultId && <a className="button" href={link.review(baseResultId, result.id)}><Icon name="compare" />이전 편집본과 비교</a>}
                <a className="button ghost" href={link.shot(result.shot.id, result.id)}>버전 상세</a>
              </div>
            </div>
            <Player
              ref={player}
              key={result.id}
              versionId={result.id}
              fps={fps}
              frameCount={result.frame_count ?? 1}
              issues={result.qc?.issues ?? []}
              markers={(edit.settings.renders?.[String(result.id)]?.cut_frames ?? []).map((f) => ({ frame: f, label: "컷" }))}
            />
            <div className="cols-2 panel-body">
              <div>
                <h3 style={{ marginTop: 0 }}>자동 검수 · {kst(result.qc?.created_at)}</h3>
                <QCList run={result.qc} onSeek={(f) => player.current?.seekFrame(f)} />
              </div>
              <div>
                <h3 style={{ marginTop: 0 }}>컷 경계 비교 시트</h3>
                <img className="sheet" src={`${edit.boundary_sheet_url}?v=${result.id}`} alt="컷 경계 비교 시트" loading="lazy" />
              </div>
            </div>
          </section>
        )}
      </div>
    </>
  );
}
