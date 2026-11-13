import { useEffect, useState } from "react";
import { api } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { link } from "../router";
import type { PromptBlock, PromptHistory, PromptOverview, PromptShot, SequenceSummary } from "../types";
import { useAsync } from "../useAsync";

const NEW_BLOCK = { key: "", label: "", place: "after", text: "" };

/** One shared block: its wording is typed once and reaches every shot that uses it. */
function BlockRow({ block, places, used, total, busy, onSave, onDelete, onApply }: {
  block: PromptBlock;
  places: { key: string; label: string }[];
  used: number;
  total: number;
  busy: boolean;
  onSave: (b: { label: string; place: string; text: string }) => void;
  onDelete: () => void;
  onApply: (on: boolean) => void;
}) {
  const [label, setLabel] = useState(block.label);
  const [place, setPlace] = useState(block.place);
  const [text, setText] = useState(block.text);
  useEffect(() => {
    setLabel(block.label);
    setPlace(block.place);
    setText(block.text);
  }, [block.label, block.place, block.text]);
  const dirty = label !== block.label || place !== block.place || text !== block.text;
  return (
    <div className="prompt-block">
      <div className="row wrap">
        <span className="tag mono">{block.key}</span>
        <input value={label} onChange={(e) => setLabel(e.target.value)} aria-label="묶음 이름" style={{ width: 160 }} />
        <select value={place} onChange={(e) => setPlace(e.target.value)} aria-label="자리">
          {places.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select>
        <span className="faint small">{text.length}자 · 이 쓰임새에서 {used}/{total}컷</span>
        <span className="spacer" />
        <button type="button" className="ghost" disabled={busy || used === total} onClick={() => onApply(true)}>모든 컷에 넣기</button>
        <button type="button" className="ghost" disabled={busy || used === 0} onClick={() => onApply(false)}>모든 컷에서 빼기</button>
        <button type="button" className="ghost icon-only" disabled={busy} onClick={onDelete} title="묶음 지우기 (쓰는 컷이 없을 때만)"><Icon name="close" /></button>
      </div>
      <textarea rows={Math.min(6, Math.max(2, Math.ceil(text.length / 110)))} value={text} onChange={(e) => setText(e.target.value)} />
      {dirty && (
        <div className="row">
          <button type="button" className="primary" disabled={busy || !text.trim()} onClick={() => onSave({ label, place, text })}>저장</button>
          <span className="warn-text small">저장하면 이 묶음을 쓰는 모든 컷의 다음 프롬프트가 바뀝니다. 이미 생성에 쓴 글은 그대로 남습니다.</span>
        </div>
      )}
    </div>
  );
}

function History({ shotId, use }: { shotId: number; use: string }) {
  const { data, error, loading } = useAsync(() => api.shotPrompt(shotId, use), [shotId, use]);
  if (!data) return <Loading error={error} loading={loading} />;
  const rows: PromptHistory[] = data.history;
  if (rows.length === 0) return <p className="faint small">아직 이 프롬프트로 생성한 기록이 없습니다.</p>;
  return (
    <div className="prompt-history">
      {rows.map((h) => (
        <div key={h.id}>
          <div className="row wrap small">
            <span className="tag mono">#{h.id}</span>
            <span className="faint">{h.created_at?.slice(0, 16).replace("T", " ")}</span>
            {h.jobs.length === 0 ? <span className="faint">고정만 함</span> : h.jobs.map((j) => (
              <a key={j.id} className="tag" href={link.job(j.id)} title={j.recipe_name ?? j.provider}>
                작업 {j.id} · {j.status}{j.credits != null ? ` · ${j.credits}크레딧` : ""}{j.version_ids.length ? ` · 버전 ${j.version_ids.join(", ")}` : ""}
              </a>
            ))}
          </div>
          <div className="prompt">{h.text}{h.negative ? `\n\n넣지 않을 것: ${h.negative}` : ""}</div>
        </div>
      ))}
    </div>
  );
}

/** One shot: only what is special about it is typed here. */
function ShotRow({ shot, use, blocks, busy, onSave }: {
  shot: PromptShot;
  use: string;
  blocks: PromptBlock[];
  busy: boolean;
  onSave: (scene: string, keys: string[]) => void;
}) {
  const made = shot.prompts[use];
  const [scene, setScene] = useState(made.scene);
  const [keys, setKeys] = useState<string[]>(made.block_keys);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    setScene(made.scene);
    setKeys(made.block_keys);
  }, [made.scene, made.block_keys.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = scene !== made.scene || keys.join(",") !== made.block_keys.join(",");
  const toggle = (key: string) => setKeys((k) => (k.includes(key) ? k.filter((x) => x !== key) : blocks.filter((b) => k.includes(b.key) || b.key === key).map((b) => b.key)));
  const copy = async () => {
    await navigator.clipboard.writeText(made.text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };
  return (
    <>
      <tr>
        <td><a href={link.shot(shot.shot_id)}><b>{shot.code}</b></a><div className="faint small">{shot.description}</div></td>
        <td>
          <textarea rows={2} value={scene} placeholder="이 컷에서만 다른 것 (무엇이 어디에 있는지)" onChange={(e) => setScene(e.target.value)} />
          <div className="row wrap tight" style={{ marginTop: 4 }}>
            {blocks.map((b) => (
              <button key={b.key} type="button" className={`chip${keys.includes(b.key) ? " on" : ""}${b.place === "negative" ? " neg" : ""}`}
                onClick={() => toggle(b.key)} title={b.text}>{b.label}</button>
            ))}
          </div>
        </td>
        <td className="num small">
          {made.text ? `${made.chars}자` : <span className="faint">비어 있음</span>}
          {made.scene_missing && made.text && <div className="warn-text">장면 글 없음</div>}
          <div className={made.jobs ? "" : "faint"}>생성 {made.jobs}번</div>
        </td>
        <td>
          <div className="row tight">
            <button type="button" className="primary" disabled={busy || !dirty} onClick={() => onSave(scene, keys)}>저장</button>
            <button type="button" className="ghost" disabled={!made.text || dirty} onClick={copy} title="완성된 프롬프트 복사"><Icon name={copied ? "check" : "copy"} /></button>
            <button type="button" className="ghost" onClick={() => setOpen((o) => !o)} title="완성된 글과 쓴 기록">{open ? "접기" : "보기"}</button>
          </div>
        </td>
      </tr>
      {open && (
        <tr className="prompt-detail">
          <td />
          <td colSpan={3}>
            <p className="faint small">지금 저장된 글로 만든 프롬프트{dirty ? " (고친 내용은 저장해야 반영됩니다)" : ""}</p>
            <div className="prompt">
              {made.parts.filter((p) => p.place !== "negative").map((p, i) => (
                <span key={i} className={p.place === "scene" ? "part-scene" : "part-block"} title={p.label}>{p.text} </span>
              ))}
              {made.negative && <span className="part-neg">{"\n\n"}넣지 않을 것: {made.negative}</span>}
              {!made.text && <span className="faint">장면 글도 묶음도 없습니다.</span>}
            </div>
            <p className="faint small" style={{ marginTop: 10 }}>이 컷에서 생성에 쓴 프롬프트 (새것이 위)</p>
            <History shotId={shot.shot_id} use={use} />
          </td>
        </tr>
      )}
    </>
  );
}

export function PromptsPage({ sequences, selected }: { sequences: SequenceSummary[]; selected: number | null }) {
  const sequenceId = selected ?? sequences.at(-1)?.id ?? null;
  const { data, error, loading, setData } = useAsync<PromptOverview | null>(
    () => (sequenceId == null ? Promise.resolve(null) : api.prompts(sequenceId)), [sequenceId]);
  const [use, setUse] = useState("image");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [draft, setDraft] = useState(NEW_BLOCK);

  if (sequenceId == null) return <div className="page"><p className="muted">영상이 없습니다.</p></div>;
  if (!data) return <Loading error={error} loading={loading} />;

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await fn();
      setData(await api.prompts(sequenceId));
    } catch (err) {
      setProblem((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const total = data.shots.length;
  const usedBy = (key: string) => data.shots.filter((s) => s.prompts[use].block_keys.includes(key)).length;
  const ready = data.shots.filter((s) => s.prompts[use].text && !s.prompts[use].scene_missing).length;

  return (
    <>
      <Toolbar
        crumbs={[{ label: data.sequence.name, href: link.sequence(data.sequence.id) }]}
        title="프롬프트"
        actions={
          <>
            <select value={sequenceId} onChange={(e) => (window.location.hash = link.prompts(Number(e.target.value)))} aria-label="영상">
              {sequences.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <div className="segmented" role="tablist" aria-label="쓰임새">
              {data.uses.map((u) => <button key={u.key} type="button" className={use === u.key ? "on" : ""} onClick={() => setUse(u.key)}>{u.label}</button>)}
            </div>
          </>
        }
      />
      <div className="page">
        {problem && <p className="error-text">{problem}</p>}
        <Panel title="공통 묶음" actions={<span className="faint small">영상 전체가 함께 쓰는 글. 한 번 고치면 이 묶음을 쓰는 모든 컷에 반영됩니다</span>}>
          <div className="prompt-blocks">
            {data.blocks.map((b) => (
              <BlockRow key={b.key} block={b} places={data.places} used={usedBy(b.key)} total={total} busy={busy}
                onSave={(v) => act(() => api.setPromptBlock(sequenceId, b.key, v))}
                onDelete={() => act(() => api.deletePromptBlock(sequenceId, b.key))}
                onApply={(on) => act(() => api.applyPromptBlocks(sequenceId, { use, add: on ? [b.key] : [], remove: on ? [] : [b.key] }))} />
            ))}
            <div className="prompt-block new">
              <div className="row wrap">
                <input value={draft.key} placeholder="이름 (영어 소문자, 예: style)" onChange={(e) => setDraft({ ...draft, key: e.target.value })} style={{ width: 190 }} />
                <input value={draft.label} placeholder="보이는 이름 (예: 그림체)" onChange={(e) => setDraft({ ...draft, label: e.target.value })} style={{ width: 170 }} />
                <select value={draft.place} onChange={(e) => setDraft({ ...draft, place: e.target.value })} aria-label="자리">
                  {data.places.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
                </select>
                <span className="spacer" />
                <button type="button" disabled={busy || !draft.key.trim() || !draft.text.trim()}
                  onClick={() => act(async () => { await api.setPromptBlock(sequenceId, draft.key.trim(), draft); setDraft(NEW_BLOCK); })}><Icon name="plus" />묶음 더하기</button>
              </div>
              <textarea rows={2} value={draft.text} placeholder="묶음의 글 (영어 권장)" onChange={(e) => setDraft({ ...draft, text: e.target.value })} />
            </div>
          </div>
        </Panel>

        <Panel title="컷별 프롬프트" flush actions={<span className="faint small">{data.uses.find((u) => u.key === use)?.label} · 준비된 컷 {ready}/{total} · 완성된 글 = 장면 앞 묶음 + 장면 글 + 장면 뒤 묶음</span>}>
          <table className="table prompt-table">
            <thead><tr><th style={{ width: 150 }}>컷</th><th>장면 글과 쓰는 묶음</th><th className="num" style={{ width: 90 }}>완성된 글</th><th style={{ width: 170 }} /></tr></thead>
            <tbody>
              {data.shots.map((s) => (
                <ShotRow key={`${s.shot_id}-${use}`} shot={s} use={use} blocks={data.blocks} busy={busy}
                  onSave={(scene, keys) => act(() => api.setShotPrompt(s.shot_id, use, { scene, block_keys: keys }))} />
              ))}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}
