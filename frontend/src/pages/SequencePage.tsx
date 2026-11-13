import { useState } from "react";
import { api, media } from "../api";
import { Loading, Panel, QCLabel, StatusDot, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { AddShotForm, NewEditForm } from "../components/StructureForms";
import { seconds } from "../format";
import { link } from "../router";
import { useAsync } from "../useAsync";

export function SequencePage({ id }: { id: number }) {
  const [addOpen, setAddOpen] = useState(false);
  const { data, error, loading, reload } = useAsync(
    () => Promise.all([api.sequence(id), api.shots(id)]).then(([sequence, shots]) => ({ sequence, shots })),
    [id],
  );
  if (!data) return <Loading error={error} loading={loading} />;
  const { sequence, shots } = data;
  const realShots = shots.filter((s) => s.kind === "shot");
  const editShot = shots.find((s) => s.kind === "edit");
  const nextEdit = `rough_v${String(sequence.edit_list.length + 1).padStart(2, "0")}`;

  return (
    <>
      <Toolbar
        crumbs={[{ label: sequence.project.name, href: link.dashboard() }]}
        title={sequence.name}
        actions={<>
          <span className="faint small">샷 {realShots.length} · 승인 {sequence.approved} · 목표 {seconds(sequence.target_duration_s, 0)}</span>
          <a className="button ghost" href={link.prompts(id)}><Icon name="edit" />프롬프트</a>
        </>}
      />
      <div className="page">
        <Panel title="샷" flush actions={<button type="button" className="ghost" onClick={() => setAddOpen((o) => !o)}><Icon name="plus" />샷 추가</button>}>
          {addOpen && <div className="panel-body"><AddShotForm sequenceId={id} onDone={() => { setAddOpen(false); reload(); }} /></div>}
          <table className="table">
            <thead>
              <tr><th className="thumb" /><th>샷</th><th>현재 승인</th><th>상태</th><th className="num">버전</th><th>QC</th><th className="num">길이</th><th /></tr>
            </thead>
            <tbody>
              {realShots.map((shot) => {
                const current = shot.versions.find((v) => v.id === shot.approved_version_id) ?? shot.versions.at(-1);
                return (
                  <tr key={shot.id} className="clickable" onClick={() => (window.location.hash = link.shot(shot.id))}>
                    <td className="thumb">{current ? <img src={media.thumbnail(current.id)} alt="" loading="lazy" /> : null}</td>
                    <td><b>{shot.code}</b></td>
                    <td className="mono small">{current?.code ?? "-"}</td>
                    <td>{current && <StatusDot status={current.status} />}</td>
                    <td className="num">{shot.versions.length}</td>
                    <td>{current && <QCLabel status={current.qc_status} counts={current.qc_counts} />}</td>
                    <td className="num">{current?.duration_s ? seconds(current.duration_s, 2) : "-"}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <a className="button ghost" href={link.generate(shot.id, current?.id)} title="이 샷으로 생성"><Icon name="generate" />생성</a>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>

        <Panel title="편집본" flush actions={<NewEditForm sequenceId={id} suggested={nextEdit} />}>
          <table className="table">
            <thead><tr><th className="thumb" /><th>편집</th><th>결과 버전</th><th>상태</th><th className="num">샷</th><th>QC</th><th className="num">길이</th></tr></thead>
            <tbody>
              {sequence.edit_list.map((edit) => {
                const result = editShot?.versions.find((v) => v.id === edit.result_version_id);
                return (
                  <tr key={edit.id} className="clickable" onClick={() => (window.location.hash = link.edit(edit.id))}>
                    <td className="thumb">{result ? <img src={media.thumbnail(result.id)} alt="" loading="lazy" /> : null}</td>
                    <td><b>{edit.name}</b></td>
                    <td className="mono small">{result?.code ?? "조립 전"}</td>
                    <td>{result && <StatusDot status={result.status} />}</td>
                    <td className="num">{edit.entries}</td>
                    <td>{result && <QCLabel status={result.qc_status} counts={result.qc_counts} />}</td>
                    <td className="num">{result ? seconds(result.duration_s, 2) : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Panel>
      </div>
    </>
  );
}
