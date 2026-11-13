import { useEffect, useState } from "react";
import { api } from "../api";
import { Loading, Panel, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { NodeGraph } from "../components/NodeGraph";
import { bytes, kst } from "../format";
import { link } from "../router";
import type { GraphData } from "../types";
import { useAsync } from "../useAsync";

export function RecipesPage({ selected }: { selected: number | null }) {
  const q = useAsync(() => api.recipes(), []);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [syncing, setSyncing] = useState(false);
  const recipes = q.data ?? [];
  const recipe = recipes.find((r) => r.id === selected) ?? recipes[0];

  useEffect(() => {
    if (recipe) api.recipeGraph(recipe.latest_version_id).then(setGraph);
  }, [recipe?.latest_version_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!q.data) return <Loading error={q.error} loading={q.loading} />;

  return (
    <>
      <Toolbar
        crumbs={[{ label: "생성" }]}
        title="레시피"
        actions={
          <button type="button" disabled={syncing} onClick={async () => { setSyncing(true); await api.syncRecipes(); q.reload(); setSyncing(false); }}>
            <Icon name="refresh" />{syncing ? "확인 중" : "recipes 폴더 다시 읽기"}
          </button>
        }
      />
      <div className="page">
        <div className="gen-layout">
          <Panel title="목록" flush>
            <ul className="recipe-list">
              {recipes.map((r) => (
                <li key={r.id}>
                  <a className={`button recipe-item${r.id === recipe?.id ? " active" : ""}`} href={link.recipes(r.id)}>
                    <b>{r.name}</b>
                    <span className="kind">{r.key} · v{r.version}</span>
                  </a>
                </li>
              ))}
            </ul>
          </Panel>
          {recipe && (
            <div className="main-col">
              <Panel title={recipe.name} actions={<a className="button primary" href={link.generate()}><Icon name="generate" />이 레시피로 생성</a>}>
                <p className="muted">{recipe.description}</p>
                <div className="cols-2" style={{ marginTop: 12 }}>
                  <div>
                    <h3 style={{ marginTop: 0 }}>버전 기록</h3>
                    <ul className="issues">
                      {[...recipe.versions].reverse().map((v) => (
                        <li key={v.id}><span className="num">v{v.number}</span><span className="faint" style={{ marginLeft: "auto" }}>{kst(v.created_at)}</span></li>
                      ))}
                    </ul>
                    <p className="faint small" style={{ marginTop: 6 }}>api.json 또는 recipe.json이 바뀌면 다시 읽을 때 새 버전이 됩니다. 지난 작업은 당시 버전을 그대로 가리킵니다.</p>
                  </div>
                  <div>
                    <h3 style={{ marginTop: 0 }}>필요한 모델</h3>
                    <ul className="issues">
                      {recipe.required_models.map((m) => (
                        <li key={m.file}>
                          <span className={m.present ? "" : "error-text"}>{m.present ? "있음" : "없음"}</span>
                          <span className="mono small">{m.file}</span>
                          <span className="faint small num" style={{ marginLeft: "auto" }}>{bytes(m.bytes)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </Panel>
              <Panel title="화면에 노출되는 입력" flush>
                <table className="table">
                  <thead><tr><th>노드</th><th>입력</th><th>종류</th><th>기본값</th><th>범위</th></tr></thead>
                  <tbody>
                    {recipe.exposed_inputs.map((e) => (
                      <tr key={e.key}>
                        <td>{e.node_title} <span className="faint mono">#{e.node_id}</span></td>
                        <td className="mono small">{e.input}</td>
                        <td className="faint">{e.type}</td>
                        <td className="small" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(e.default)}</td>
                        <td className="faint small num">{e.options ? `${e.options.length}개 선택지` : e.min != null ? `${e.min} ~ ${e.max != null && e.max < 1e12 ? e.max : "∞"}` : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
              {graph && <Panel title="노드 구조" actions={<span className="faint small">테두리가 밝은 노드가 SF: 입력</span>}><NodeGraph graph={graph} /></Panel>}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
