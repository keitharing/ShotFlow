import { useEffect, useState } from "react";
import { api } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { kst } from "../format";
import { link } from "../router";
import type { DeliveryInfo, SequenceSummary, VerifyResult } from "../types";
import { useAsync } from "../useAsync";

const SCOPE_LABEL: Record<string, string> = {
  approved_shots: "승인된 샷 버전",
  approved_edits: "승인된 편집본",
};

export function DeliveriesPage({ sequences, selected }: { sequences: SequenceSummary[]; selected: number | null }) {
  const list = useAsync(() => api.deliveries(), []);
  const [name, setName] = useState("");
  const [scope, setScope] = useState("approved_shots");
  const [seqIds, setSeqIds] = useState<number[]>(sequences.map((s) => s.id));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<DeliveryInfo | null>(null);
  const [verify, setVerify] = useState<VerifyResult | null>(null);
  const current = selected ?? list.data?.[0]?.id ?? null;

  useEffect(() => {
    setVerify(null);
    if (current) api.delivery(current).then(setDetail);
  }, [current]);

  if (!list.data) return <Loading error={list.error} loading={list.loading} />;

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createDelivery({ name: name.trim(), scope, sequence_ids: seqIds });
      list.reload();
      window.location.hash = link.deliveries(created.id);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copyPath = async (path: string) => {
    try {
      await navigator.clipboard.writeText(path);
    } catch {
      /* clipboard blocked: the path stays visible for manual copy */
    }
  };

  return (
    <>
      <Toolbar crumbs={[{ label: "납품" }]} title="납품 패키지" />
      <div className="page">
        <div className="gen-layout" style={{ gridTemplateColumns: "minmax(320px, 400px) minmax(0, 1fr)" }}>
          <div className="main-col">
            <Panel title="새 패키지">
              <div className="form-grid">
                <label className="full">이름<input value={name} placeholder="예: RAIN_ATLAS_review_1015" onChange={(e) => setName(e.target.value)} /></label>
                <label className="full">대상
                  <select value={scope} onChange={(e) => setScope(e.target.value)}>
                    {Object.entries(SCOPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </label>
                <div className="full field">시퀀스
                  {sequences.map((s) => (
                    <label key={s.id} className="check">
                      <input type="checkbox" checked={seqIds.includes(s.id)} onChange={(e) => setSeqIds(e.target.checked ? [...seqIds, s.id] : seqIds.filter((x) => x !== s.id))} />
                      {s.name}
                    </label>
                  ))}
                </div>
              </div>
              <p className="faint small" style={{ margin: "10px 0" }}>승인된 버전만 담깁니다. 이름 규칙 {"{project}_{sequence}_{shot}_{version}"} 으로 복사하고, 파일 목록·체크섬·생성 기록 요약을 함께 저장합니다.</p>
              <button type="button" className="primary" disabled={busy || !name.trim() || !seqIds.length} onClick={create}>{busy ? "만드는 중" : "패키지 만들기"}</button>
              {error && <p className="error-text small" style={{ marginTop: 8 }}>{error}</p>}
            </Panel>
            <Panel title="기록" flush>
              <table className="table">
                <thead><tr><th>이름</th><th className="num">파일</th><th>검증</th></tr></thead>
                <tbody>
                  {list.data.length === 0 && <tr><td colSpan={3} className="faint">아직 없음</td></tr>}
                  {list.data.map((d) => (
                    <tr key={d.id} className="clickable" onClick={() => (window.location.hash = link.deliveries(d.id))}>
                      <td><b>{d.name}</b><div className="faint small">{kst(d.created_at)}</div></td>
                      <td className="num">{d.files}</td>
                      <td>{d.verified ? <span className="ok-text small">일치</span> : <span className="error-text small">불일치</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          </div>
          {detail && (
            <div className="main-col">
              <Panel
                title={detail.name}
                actions={
                  <button type="button" onClick={async () => { setVerify(await api.verifyDelivery(detail.id)); list.reload(); }}>
                    <Icon name="check" />체크섬 다시 확인
                  </button>
                }
              >
                <dl className="kv">
                  <dt>폴더</dt>
                  <dd className="row"><span className="mono small">{detail.folder}</span><button type="button" className="ghost" onClick={() => copyPath(detail.folder)}>경로 복사</button></dd>
                  <dt>이름 규칙</dt><dd className="mono small">{detail.naming_rule}</dd>
                  <dt>함께 저장</dt><dd className="mono small">manifest.json · manifest.csv · checksums.sha256</dd>
                  <dt>만든 시각</dt><dd>{kst(detail.created_at)}</dd>
                </dl>
                {verify && (
                  <p className={`small ${verify.ok ? "ok-text" : "error-text"}`} style={{ marginTop: 10 }}>
                    {verify.ok ? `${verify.checked}개 파일 모두 체크섬 일치` : `불일치: ${verify.results.filter((r) => r.status !== "ok").map((r) => `${r.file} (${r.status === "missing" ? "없음" : "바뀜"})`).join(", ")}`}
                    {verify.extra_files.length ? ` · 기록에 없는 파일 ${verify.extra_files.length}개` : ""}
                  </p>
                )}
              </Panel>
              <Panel title="파일" flush>
                <table className="table">
                  <thead><tr><th>파일</th><th>SHA-256</th><th /></tr></thead>
                  <tbody>
                    {detail.items?.map((i) => {
                      const r = verify?.results.find((x) => x.file === i.file);
                      return (
                        <tr key={i.id}>
                          <td className="mono small">{i.file}</td>
                          <td className="mono small faint">{i.sha256.slice(0, 16)}…</td>
                          <td className="small">{r ? (r.status === "ok" ? <span className="ok-text">일치</span> : <span className="error-text">{r.status === "missing" ? "없음" : "바뀜"}</span>) : ""}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Panel>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
