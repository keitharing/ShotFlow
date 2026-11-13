import { useState } from "react";
import { api } from "../api";
import { DECISION_LABEL, kst } from "../format";
import type { Decision, Note, VersionDetail } from "../types";

const AUTHOR_KEY = "shotflow.author";

function savedAuthor(): string {
  try {
    return localStorage.getItem(AUTHOR_KEY) ?? "안우혁";
  } catch {
    return "안우혁";
  }
}

interface Props {
  version: VersionDetail;
  currentFrame: number | null;
  onChanged: (v: VersionDetail) => void;
  onSeek?: (frame: number) => void;
}

export function NotePanel({ version, currentFrame, onChanged, onSeek }: Props) {
  const [author, setAuthor] = useState(savedAuthor);
  const [body, setBody] = useState("");
  const [pinFrame, setPinFrame] = useState(version.media_kind !== "image");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (decision: Decision) => {
    setBusy(true);
    setError(null);
    try {
      try {
        localStorage.setItem(AUTHOR_KEY, author);
      } catch {
        /* storage unavailable: keep the name for this session only */
      }
      const updated = await api.addNote(version.id, {
        author, body, decision, frame: pinFrame && currentFrame != null ? currentFrame : null,
      });
      setBody("");
      onChanged(updated);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="notes">
      <ul className="note-list">
        {version.notes.length === 0 && <li className="faint">노트가 없습니다.</li>}
        {version.notes.map((n: Note) => (
          <li key={n.id}>
            <div className="note-head">
              <b>{n.author}</b>
              {n.decision && n.decision !== "comment" && <span className={`decision ${n.decision}`}>{DECISION_LABEL[n.decision]}</span>}
              {n.frame != null && <button type="button" className="link mono" onClick={() => onSeek?.(n.frame!)}>f{n.frame}</button>}
              <span className="faint small">{kst(n.created_at)}</span>
            </div>
            <p className="note-body">{n.body}</p>
          </li>
        ))}
      </ul>
      <div className="row">
        <input value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="작성자" aria-label="작성자" style={{ width: 120 }} />
        {version.media_kind !== "image" && (
          <label className="check">
            <input type="checkbox" checked={pinFrame} onChange={(e) => setPinFrame(e.target.checked)} />
            현재 프레임{currentFrame != null ? ` ${currentFrame}` : ""}에 고정
          </label>
        )}
      </div>
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} placeholder="코멘트 또는 수정 지시" />
      <div className="row wrap">
        <button type="button" disabled={busy || !body.trim()} onClick={() => submit("comment")}>코멘트</button>
        <span className="spacer" />
        <button type="button" className="decision-revise" disabled={busy} onClick={() => submit("revise")}>수정요청</button>
        <button type="button" className="decision-reject" disabled={busy} onClick={() => submit("reject")}>반려</button>
        <button type="button" className="decision-approve" disabled={busy} onClick={() => submit("approve")}>승인</button>
      </div>
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}
