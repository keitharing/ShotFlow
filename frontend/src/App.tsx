import { useEffect, useState } from "react";
import { api } from "./api";
import { Icon, type IconName } from "./components/Icon";
import { Loading } from "./components/Bits";
import { Dashboard } from "./pages/Dashboard";
import { DeliveriesPage } from "./pages/DeliveriesPage";
import { EditPage } from "./pages/EditPage";
import { GeneratePage } from "./pages/GeneratePage";
import { JobDetailPage, JobsPage } from "./pages/JobsPage";
import { PromptsPage } from "./pages/PromptsPage";
import { RecipesPage } from "./pages/RecipesPage";
import { Scene3DPage } from "./pages/Scene3DPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SequencePage } from "./pages/SequencePage";
import { ShotPage } from "./pages/ShotPage";
import { link, useRoute } from "./router";
import type { ComfyStatus } from "./types";
import { useAsync } from "./useAsync";

function NavItem({ href, icon, label, active, count }: { href: string; icon: IconName; label: string; active: boolean; count?: string }) {
  return (
    <a className={`nav-item${active ? " active" : ""}`} href={href}>
      <Icon name={icon} />
      <span>{label}</span>
      {count && <span className="count">{count}</span>}
    </a>
  );
}

function EnginePanel() {
  const [status, setStatus] = useState<ComfyStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.comfyStatus().then((s) => alive && setStatus(s)).catch(() => undefined);
    load();
    const timer = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setStatus(await api.comfyStatus());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const gpu = status?.gpu;
  return (
    <div className="engine">
      <div className="engine-row">
        <span className={`status ${status?.running ? "approved" : "pending"}`}>
          <span>ComfyUI {status?.running ? "실행 중" : busy ? "시작하는 중" : "꺼짐"}</span>
        </span>
        {status?.running ? (
          <button type="button" className="ghost icon-only" disabled={busy} onClick={() => act(api.comfyStop)} title="ComfyUI 종료"><Icon name="power" /></button>
        ) : (
          <button type="button" disabled={busy || status?.installed === false} onClick={() => act(api.comfyStart)}>시작</button>
        )}
      </div>
      {gpu && (
        <div className="engine-meter">
          <span>GPU</span><b>{gpu.temp_c.toFixed(0)}°C</b>
          <span>VRAM</span><b>{(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(0)} GB</b>
          <span>RAM 여유</span><b>{gpu.ram_free_gib.toFixed(1)} GB</b>
        </div>
      )}
      {error && <p className="error-text small">{error}</p>}
    </div>
  );
}

export function App() {
  const route = useRoute();
  const { data, error, loading } = useAsync(async () => {
    const projects = await api.projects();
    const project = projects[0] ?? null;
    return { project, sequences: project ? await api.sequences(project.id) : [] };
  }, []);

  let page = null;
  if (data?.project) {
    const q = route.query;
    const num = (key: string) => (q.get(key) ? Number(q.get(key)) : null);
    switch (route.page) {
      case "seq":
        page = route.id != null && <SequencePage key={route.id} id={route.id} />;
        break;
      case "shot":
        page = route.id != null && <ShotPage key={route.id} id={route.id} initialVersion={num("v")} />;
        break;
      case "review":
        page = num("a") && num("b") && <ReviewPage key={`${num("a")}-${num("b")}`} a={num("a")!} b={num("b")!} />;
        break;
      case "edit":
        page = route.id != null && <EditPage key={route.id} id={route.id} />;
        break;
      case "generate":
        page = <GeneratePage key={`${num("shot")}-${num("source")}`} sequences={data.sequences} shotId={num("shot")} sourceId={num("source")} frame={num("frame") ?? 0} />;
        break;
      case "jobs":
        page = <JobsPage />;
        break;
      case "job":
        page = route.id != null && <JobDetailPage key={route.id} id={route.id} />;
        break;
      case "recipes":
        page = <RecipesPage key={route.id ?? 0} selected={route.id} />;
        break;
      case "prompts":
        page = <PromptsPage key={route.id ?? 0} sequences={data.sequences} selected={route.id} />;
        break;
      case "scene3d":
        page = <Scene3DPage key={q.get("tab") ?? "keyframe"} sequences={data.sequences} tab={q.get("tab") ?? "keyframe"} />;
        break;
      case "deliveries":
        page = <DeliveriesPage key={route.id ?? 0} sequences={data.sequences} selected={route.id} />;
        break;
      default:
        page = <Dashboard project={data.project} sequences={data.sequences} />;
    }
  }

  const p = route.page;
  return (
    <div className="app">
      <nav className="sidebar" aria-label="주 메뉴">
        <a className="brand" href={link.dashboard()}>
          <span className="brand-mark">SF</span>
          <span>
            <span className="brand-name">ShotFlow</span>
            <br />
            <span className="brand-sub">{data?.project?.name ?? "로컬 제작 관리"}</span>
          </span>
        </a>
        <div className="nav">
          <NavItem href={link.dashboard()} icon="dashboard" label="현황판" active={p === "dashboard"} />
          <p className="nav-label">시퀀스</p>
          {data?.sequences.map((s) => (
            <NavItem key={s.id} href={link.sequence(s.id)} icon="film" label={s.name} active={p === "seq" && route.id === s.id} count={`${s.approved}/${s.shots}`} />
          ))}
          <p className="nav-label">생성</p>
          <NavItem href={link.generate()} icon="generate" label="생성" active={p === "generate"} />
          <NavItem href={link.prompts()} icon="edit" label="프롬프트" active={p === "prompts"} />
          <NavItem href={link.jobs()} icon="queue" label="작업 대기열" active={p === "jobs" || p === "job"} />
          <NavItem href={link.recipes()} icon="graph" label="레시피" active={p === "recipes"} />
          <NavItem href={link.scene3d()} icon="cube" label="3D 연동" active={p === "scene3d"} />
          <p className="nav-label">납품</p>
          <NavItem href={link.deliveries()} icon="layers" label="납품 패키지" active={p === "deliveries"} />
        </div>
        <EnginePanel />
      </nav>
      <main className="content">
        {!data ? <Loading error={error} loading={loading} /> : data.project ? page : (
          <div className="page">
            <div className="panel panel-body">
              <h2>프로젝트가 없습니다</h2>
              <p className="muted" style={{ marginTop: 6 }}>backend 폴더에서 <code>..\.venv\Scripts\python.exe -m shotflow import-v04</code> 를 먼저 실행하세요.</p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
