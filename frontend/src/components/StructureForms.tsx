import { useState } from "react";
import { ApiError, api } from "../api";
import { ORIGIN_LABEL } from "../format";
import { link } from "../router";
import type { VersionBrief } from "../types";
import { Icon } from "./Icon";

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

/** "C1 | 설명" lines → shots. A line without "|" is just a code. */
function parseShots(text: string) {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const [code, ...rest] = l.split("|");
      return { code: code.trim(), description: rest.join("|").trim() || null };
    });
}

const SIZES: Record<string, [number, number] | null> = {
  project: null,
  "1280x720": [1280, 720],
  "1920x1080": [1920, 1080],
};

/** New film (sequence) with its shots — the first step of a new production. */
export function NewSequenceForm({ projectId, onClose }: { projectId: number; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [duration, setDuration] = useState("");
  const [size, setSize] = useState("1280x720");
  const [shots, setShots] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const parsed = parseShots(shots);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const seq = await api.createSequence(projectId, {
        code: code.trim().toUpperCase(),
        name: name.trim(),
        target_duration_s: duration.trim() ? Number(duration) : null,
        shots: parsed,
      });
      const wh = SIZES[size];
      if (wh) await api.setFrameSize(seq.id, { width: wh[0], height: wh[1] });
      window.location.hash = link.sequence(seq.id);
      window.location.reload();
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  };

  return (
    <section className="panel" data-demo="new-sequence">
      <div className="panel-head">
        <h2>새 영상 등록</h2>
        <div className="actions"><button type="button" className="ghost" onClick={onClose}><Icon name="close" />닫기</button></div>
      </div>
      <div className="panel-body">
        <div className="form-grid">
          <label>영상 코드 (영문, 폴더 이름에 씀)<input value={code} placeholder="예: FIRST_DROP" onChange={(e) => setCode(e.target.value)} /></label>
          <label>제목<input value={name} placeholder="예: FIRST DROP" onChange={(e) => setName(e.target.value)} /></label>
          <label>목표 길이 (초)<input value={duration} inputMode="decimal" placeholder="예: 13" onChange={(e) => setDuration(e.target.value)} /></label>
          <label>규격
            <select value={size} onChange={(e) => setSize(e.target.value)}>
              <option value="1280x720">1280×720 (720p)</option>
              <option value="1920x1080">1920×1080 (1080p)</option>
              <option value="project">프로젝트 기본값</option>
            </select>
          </label>
          <label className="full">샷 목록 (한 줄에 하나: 코드 | 설명)
            <textarea rows={4} value={shots} placeholder={"A1 | 아주 가까이. 음료 줄기가 쏟아진다 (4초)\nA2 | 과일 조각이 떨어져 튀어 오른다 (4초)"} onChange={(e) => setShots(e.target.value)} />
          </label>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button type="button" className="primary" disabled={busy || !code.trim() || !name.trim() || parsed.length === 0} onClick={submit}>
            <Icon name="plus" />{busy ? "등록 중" : `영상과 샷 ${parsed.length}개 등록`}
          </button>
          {error && <span className="error-text small">{error}</span>}
        </div>
      </div>
    </section>
  );
}

/** One more shot in an existing film. */
export function AddShotForm({ sequenceId, onDone }: { sequenceId: number; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [desc, setDesc] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setError(null);
    try {
      await api.addShot(sequenceId, { code: code.trim(), description: desc.trim() || null });
      setCode("");
      setDesc("");
      onDone();
    } catch (e) {
      setError(message(e));
    }
  };
  return (
    <div className="row wrap" data-demo="add-shot">
      <label className="field inline">샷 코드<input value={code} style={{ width: 70 }} onChange={(e) => setCode(e.target.value)} /></label>
      <label className="field inline grow">설명<input value={desc} style={{ width: 280 }} onChange={(e) => setDesc(e.target.value)} /></label>
      <button type="button" disabled={!code.trim()} onClick={submit}><Icon name="plus" />샷 추가</button>
      {error && <span className="error-text small">{error}</span>}
    </div>
  );
}

/** Empty edit for a film; cuts are added on the edit page. */
export function NewEditForm({ sequenceId, suggested }: { sequenceId: number; suggested: string }) {
  const [name, setName] = useState(suggested);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setError(null);
    try {
      const edit = await api.createEdit(sequenceId, name.trim());
      window.location.hash = link.edit(edit.id);
    } catch (e) {
      setError(message(e));
    }
  };
  return (
    <div className="row" data-demo="new-edit">
      <input value={name} style={{ width: 120 }} onChange={(e) => setName(e.target.value)} />
      <button type="button" disabled={!name.trim()} onClick={submit}><Icon name="plus" />새 편집본</button>
      {error && <span className="error-text small">{error}</span>}
    </div>
  );
}

/** Register a still or clip made outside ShotFlow (image model, compositing) as a version of this shot. */
export function ImportVersionForm({ shotId, versions, defaultParent, onClose, onDone }: {
  shotId: number;
  versions: VersionBrief[];
  defaultParent: number | null;
  onClose: () => void;
  onDone: (versionId: number) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [parent, setParent] = useState(defaultParent ? String(defaultParent) : "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importVersion(shotId, file, { parent_version_id: parent, note, author: "user" });
      onDone(r.version_id);
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel" data-demo="import-version">
      <div className="panel-head">
        <h2>밖에서 만든 파일 등록</h2>
        <div className="actions"><button type="button" className="ghost" onClick={onClose}><Icon name="close" />닫기</button></div>
      </div>
      <div className="panel-body">
        <p className="faint small" style={{ marginTop: 0 }}>이미지 모델로 그린 시작 그림은 그림 버전(ext_)으로, 효과를 합성한 영상은 FX 버전(fx_)으로 등록되고 자동 검수가 돌아갑니다.</p>
        <div className="form-grid">
          <label className="full">파일 (png · jpg · webp · mp4 · mov)
            <input type="file" accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </label>
          <label>어디서 나온 것인지 (원본 버전)
            <select value={parent} onChange={(e) => setParent(e.target.value)}>
              <option value="">없음</option>
              {versions.map((v) => <option key={v.id} value={v.id}>{v.code} · {ORIGIN_LABEL[v.origin] ?? v.origin}</option>)}
            </select>
          </label>
          <label>메모<input value={note} placeholder="예: Nano Banana Pro로 다시 그림" onChange={(e) => setNote(e.target.value)} /></label>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button type="button" className="primary" disabled={!file || busy} onClick={submit}><Icon name="upload" />{busy ? "등록 중" : "등록하고 검수"}</button>
          {error && <span className="error-text small">{error}</span>}
        </div>
      </div>
    </section>
  );
}
