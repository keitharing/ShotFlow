import { useCallback, useEffect, useImperativeHandle, useRef, useState, type ReactNode, type Ref } from "react";
import { media } from "../api";
import { timecode } from "../format";
import type { QCIssue } from "../types";
import { Icon } from "./Icon";
import { QCTimeline } from "./QCTimeline";

export interface PlayerHandle {
  play: () => void;
  pause: () => void;
  seekFrame: (frame: number) => void;
  frame: () => number;
  video: () => HTMLVideoElement | null;
}

interface Props {
  versionId: number;
  fps: number;
  frameCount: number;
  kind?: "image" | "video";
  issues?: QCIssue[];
  markers?: { frame: number; label: string }[];
  muted?: boolean;
  showControls?: boolean;
  extra?: ReactNode;
  onFrame?: (frame: number) => void;
  onPlayingChange?: (playing: boolean) => void;
  ref?: Ref<PlayerHandle>;
}

export function Player({ versionId, fps, frameCount, kind = "video", issues = [], markers = [], muted = false,
  showControls = true, extra, onFrame, onPlayingChange, ref }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const [soundOn, setSoundOn] = useState(!muted);
  const isImage = kind === "image";

  // The element always loads muted (browsers reliably fetch the first frame of muted media);
  // sound is switched on only when playback starts.
  const start = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !soundOn;
    void video.play();
  }, [soundOn]);

  const toFrame = useCallback((t: number) => Math.min(frameCount - 1, Math.max(0, Math.floor(t * fps + 1e-3))), [fps, frameCount]);

  const seekFrame = useCallback((target: number) => {
    const video = videoRef.current;
    if (!video) return;
    const f = Math.min(frameCount - 1, Math.max(0, Math.round(target)));
    video.currentTime = (f + 0.5) / fps; // middle of the frame avoids landing on the previous one
    setFrame(f);
    onFrame?.(f);
  }, [fps, frameCount, onFrame]);

  useImperativeHandle(ref, () => ({
    play: () => !isImage && start(),
    pause: () => videoRef.current?.pause(),
    seekFrame,
    frame: () => (isImage ? 0 : toFrame(videoRef.current?.currentTime ?? 0)),
    video: () => videoRef.current,
  }), [isImage, start, seekFrame, toFrame]);

  // A cached proxy can finish loading before this effect runs, so read the element's real state.
  useEffect(() => {
    const video = videoRef.current;
    setFrame(0);
    setFailed(null);
    setReady(isImage ? false : !!video && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA);
  }, [versionId, isImage]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !playing) return;
    let handle = 0;
    const hasRVFC = "requestVideoFrameCallback" in video;
    const tick = () => {
      const f = toFrame(video.currentTime);
      setFrame(f);
      onFrame?.(f);
      handle = hasRVFC ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
    };
    handle = hasRVFC ? video.requestVideoFrameCallback(tick) : requestAnimationFrame(tick);
    return () => (hasRVFC ? video.cancelVideoFrameCallback(handle) : cancelAnimationFrame(handle));
  }, [playing, toFrame, onFrame]);

  const toggle = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) start();
    else video.pause();
  };

  const toggleSound = () => {
    const next = !soundOn;
    setSoundOn(next);
    if (videoRef.current && !videoRef.current.paused) videoRef.current.muted = !next;
  };

  return (
    <div className="player">
      <div className="player-screen">
        {isImage ? (
          <img src={media.proxy(versionId)} alt="" onLoad={() => setReady(true)} onError={() => setFailed("이미지를 불러오지 못했습니다")} />
        ) : (
          <video
            ref={videoRef}
            src={media.proxy(versionId)}
            preload="auto"
            muted
            playsInline
            onLoadedMetadata={() => {
              // React sets src before preload, so Chrome may stop after metadata (readyState 1).
              // Seeking to the first frame forces it to fetch and show a picture.
              const video = videoRef.current;
              if (video && video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) video.currentTime = 0.5 / fps;
            }}
            onLoadedData={() => setReady(true)}
            onSeeked={() => setReady(true)}
            onError={() => setFailed("영상을 불러오지 못했습니다. 프록시를 만드는 중이면 잠시 후 다시 여세요.")}
            onPlay={() => { setPlaying(true); onPlayingChange?.(true); }}
            onPause={() => {
              setPlaying(false);
              onPlayingChange?.(false);
              const f = toFrame(videoRef.current?.currentTime ?? 0);
              setFrame(f);
              onFrame?.(f);
            }}
            onClick={showControls ? toggle : undefined}
          />
        )}
        {!ready && !failed && <div className="player-overlay">불러오는 중</div>}
        {failed && <div className="player-overlay error">{failed}</div>}
      </div>
      {showControls && !isImage && (
        <>
          <div className="transport">
            <button type="button" className="ghost icon-only" onClick={() => seekFrame(0)} title="처음 (Home)"><Icon name="toStart" /></button>
            <button type="button" className="ghost icon-only" onClick={() => seekFrame(frame - 1)} title="이전 프레임"><Icon name="stepBack" /></button>
            <button type="button" className="ghost icon-only" onClick={toggle} title="재생 / 정지">
              <Icon name={playing ? "pause" : "play"} />
            </button>
            <button type="button" className="ghost icon-only" onClick={() => seekFrame(frame + 1)} title="다음 프레임"><Icon name="stepForward" /></button>
            <button type="button" className="ghost icon-only" onClick={() => seekFrame(frameCount - 1)} title="끝"><Icon name="toEnd" /></button>
            <span className="tc">{timecode(frame, fps)}</span>
            <span className="frame">{frame} / {frameCount - 1}</span>
            <div className="right">
              {extra}
              <button type="button" className="ghost icon-only" onClick={toggleSound} title={soundOn ? "소리 끄기" : "소리 켜기"}>
                <Icon name={soundOn ? "sound" : "mute"} />
              </button>
            </div>
          </div>
          <QCTimeline frameCount={frameCount} frame={frame} issues={issues} markers={markers} onSeek={seekFrame} />
        </>
      )}
      {showControls && isImage && extra && <div className="transport"><span className="faint small">정지 이미지</span><div className="right">{extra}</div></div>}
    </div>
  );
}
