import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Loading, Panel, QCLabel, QCList, StatusDot, Toolbar } from "../components/Bits";
import { Icon } from "../components/Icon";
import { NotePanel } from "../components/NotePanel";
import { Player, type PlayerHandle } from "../components/Player";
import { QCTimeline } from "../components/QCTimeline";
import { timecode } from "../format";
import { link } from "../router";
import type { VersionDetail } from "../types";
import { useAsync } from "../useAsync";

type Mode = "side" | "wipe";
const DRIFT_FRAMES = 2;

export function ReviewPage({ a, b }: { a: number; b: number }) {
  const pair = useAsync(() => Promise.all([api.version(a), api.version(b)]), [a, b]);
  const siblings = useAsync(
    () => (pair.data ? api.shot(pair.data[0].shot.id) : Promise.resolve(null)),
    [pair.data?.[0].shot.id],
  );
  const [versions, setVersions] = useState<[VersionDetail, VersionDetail] | null>(null);
  const [mode, setMode] = useState<Mode>("side");
  const [wipe, setWipe] = useState(50);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [target, setTarget] = useState<"a" | "b">("b");
  const pa = useRef<PlayerHandle>(null);
  const pb = useRef<PlayerHandle>(null);

  useEffect(() => {
    if (pair.data) setVersions(pair.data);
  }, [pair.data]);

  const fps = versions?.[0].fps || 24;
  const frames = Math.max(versions?.[0].frame_count ?? 1, versions?.[1].frame_count ?? 1);
  const stills = versions?.[0].media_kind === "image" && versions?.[1].media_kind === "image";

  const seekBoth = useCallback((f: number) => {
    pa.current?.seekFrame(f);
    pb.current?.seekFrame(f);
    setFrame(Math.max(0, Math.round(f)));
  }, []);

  const toggle = useCallback(() => {
    if (playing) {
      pa.current?.pause();
      pb.current?.pause();
      seekBoth(pa.current?.frame() ?? frame); // land both on the same frame when stopping
    } else {
      pa.current?.play();
      pb.current?.play();
    }
  }, [playing, frame, seekBoth]);

  const onFrameA = useCallback((f: number) => {
    setFrame(f);
    const fb = pb.current?.frame() ?? f;
    if (playing && Math.abs(fb - f) > DRIFT_FRAMES) pb.current?.seekFrame(f);
  }, [playing]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input, textarea, select")) return;
      if (e.code === "Space") { e.preventDefault(); toggle(); }
      if (e.code === "ArrowLeft") { e.preventDefault(); seekBoth(frame - 1); }
      if (e.code === "ArrowRight") { e.preventDefault(); seekBoth(frame + 1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, seekBoth, frame]);

  if (!versions) return <Loading error={pair.error} loading={pair.loading} />;
  const [va, vb] = versions;
  const choose = (side: "a" | "b", id: number) => {
    window.location.hash = side === "a" ? link.review(id, vb.id) : link.review(va.id, id);
  };
  const options = siblings.data?.versions ?? [];
  const noteTarget = target === "a" ? va : vb;

  const header = (v: VersionDetail, side: "a" | "b") => (
    <div className="review-head">
      <span className="ab">{side.toUpperCase()}</span>
      <select value={v.id} onChange={(e) => choose(side, Number(e.target.value))} aria-label={`${side.toUpperCase()} 버전`}>
        {options.map((o) => <option key={o.id} value={o.id}>{o.code}</option>)}
        {!options.some((o) => o.id === v.id) && <option value={v.id}>{v.code}</option>}
      </select>
      <StatusDot status={v.status} />
      <QCLabel status={v.qc_status} counts={v.qc_counts} />
      <span className="faint small num" style={{ marginLeft: "auto" }}>{v.media_kind === "image" ? `${v.width}×${v.height}` : `${v.frame_count}f`}</span>
    </div>
  );

  const playerA = (key: string) => (
    <Player ref={pa} key={key} versionId={va.id} kind={va.media_kind} fps={fps} frameCount={va.frame_count ?? 1} showControls={false} onFrame={onFrameA} onPlayingChange={setPlaying} />
  );
  const playerB = (key: string) => (
    <Player ref={pb} key={key} versionId={vb.id} kind={vb.media_kind} fps={fps} frameCount={vb.frame_count ?? 1} showControls={false} muted />
  );

  return (
    <>
      <Toolbar
        crumbs={[
          { label: va.sequence.name, href: link.sequence(va.sequence.id) },
          { label: va.shot.kind === "edit" ? "편집본" : va.shot.code, href: link.shot(va.shot.id) },
        ]}
        title="리뷰"
        actions={
          <div className="segmented">
            <button type="button" className={mode === "side" ? "on" : ""} onClick={() => setMode("side")}>좌우</button>
            <button type="button" className={mode === "wipe" ? "on" : ""} onClick={() => setMode("wipe")}>와이프</button>
          </div>
        }
      />
      <div className="page">
        <section className="panel">
          {mode === "side" ? (
            <div className="review-pair">
              <div>{header(va, "a")}{playerA(`a${va.id}`)}</div>
              <div>{header(vb, "b")}{playerB(`b${vb.id}`)}</div>
            </div>
          ) : (
            <>
              <div className="review-pair"><div>{header(va, "a")}</div><div>{header(vb, "b")}</div></div>
              <div className="wipe">
                {playerA(`wa${va.id}`)}
                <div className="wipe-top" style={{ clipPath: `inset(0 0 0 ${wipe}%)` }}>{playerB(`wb${vb.id}`)}</div>
                <span className="wipe-line" style={{ left: `${wipe}%` }} />
                <span className="wipe-label left">A</span><span className="wipe-label right">B</span>
              </div>
              <input className="wipe-slider" type="range" min={0} max={100} value={wipe} onChange={(e) => setWipe(Number(e.target.value))} aria-label="와이프 위치" />
            </>
          )}
          {!stills && (
            <>
              <div className="transport">
                <button type="button" className="ghost icon-only" onClick={() => seekBoth(0)} title="처음"><Icon name="toStart" /></button>
                <button type="button" className="ghost icon-only" onClick={() => seekBoth(frame - 1)} title="이전 프레임"><Icon name="stepBack" /></button>
                <button type="button" className="ghost icon-only" onClick={toggle} title="재생 / 정지 (Space)"><Icon name={playing ? "pause" : "play"} /></button>
                <button type="button" className="ghost icon-only" onClick={() => seekBoth(frame + 1)} title="다음 프레임"><Icon name="stepForward" /></button>
                <span className="tc">{timecode(frame, fps)}</span>
                <span className="frame">{frame}</span>
                <div className="right faint small">소리는 A · Space 재생 · ← → 프레임</div>
              </div>
              <div className="dual-scrub">
                <span className="ab">A</span><QCTimeline frameCount={frames} frame={frame} issues={va.qc?.issues ?? []} onSeek={seekBoth} />
                <span className="ab">B</span><QCTimeline frameCount={frames} frame={frame} issues={vb.qc?.issues ?? []} onSeek={seekBoth} />
              </div>
            </>
          )}
        </section>

        <div className="cols-2">
          <Panel
            title="노트와 결정"
            actions={
              <div className="segmented">
                <button type="button" className={target === "a" ? "on" : ""} onClick={() => setTarget("a")}>A {va.code}</button>
                <button type="button" className={target === "b" ? "on" : ""} onClick={() => setTarget("b")}>B {vb.code}</button>
              </div>
            }
          >
            <NotePanel
              key={noteTarget.id}
              version={noteTarget}
              currentFrame={stills ? null : frame}
              onChanged={(v) => setVersions(target === "a" ? [v, vb] : [va, v])}
              onSeek={seekBoth}
            />
          </Panel>
          <Panel title="검수 비교">
            <h3 style={{ marginTop: 0 }}>A · {va.code}</h3>
            <QCList run={va.qc} onSeek={seekBoth} />
            <h3>B · {vb.code}</h3>
            <QCList run={vb.qc} onSeek={seekBoth} />
          </Panel>
        </div>
      </div>
    </>
  );
}
