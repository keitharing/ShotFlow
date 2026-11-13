import { useRef } from "react";
import { SEVERITY_LABEL } from "../format";
import type { QCIssue } from "../types";

interface Props {
  frameCount: number;
  frame: number;
  issues: QCIssue[];
  markers?: { frame: number; label: string }[];
  onSeek: (frame: number) => void;
}

/** Scrub bar with QC issue ranges and edit cut markers; click or drag to move. */
export function QCTimeline({ frameCount, frame, issues, markers = [], onSeek }: Props) {
  const barRef = useRef<HTMLDivElement>(null);
  const pct = (f: number) => `${(Math.min(Math.max(f, 0), frameCount) / frameCount) * 100}%`;
  const located = issues.filter((i) => i.start_frame != null);

  const seekFromPointer = (clientX: number) => {
    const rect = barRef.current?.getBoundingClientRect();
    if (rect) onSeek(((clientX - rect.left) / rect.width) * frameCount);
  };

  return (
    <div
      className="scrub"
      ref={barRef}
      onPointerDown={(e) => {
        seekFromPointer(e.clientX);
        (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => e.buttons === 1 && seekFromPointer(e.clientX)}
    >
      {markers.map((m) => (
        <span key={`c${m.frame}`} className="cut" style={{ left: pct(m.frame) }} title={`${m.label} f${m.frame}`} />
      ))}
      {located.map((issue) => {
        const start = issue.start_frame ?? 0;
        const end = Math.max(issue.end_frame ?? start, start + 1);
        return (
          <button
            type="button"
            key={issue.id}
            className={`mark ${issue.severity}`}
            style={{ left: pct(start), width: `calc(${pct(end)} - ${pct(start)})` }}
            title={`${SEVERITY_LABEL[issue.severity]} · ${issue.message} (f${start}-${end})`}
            onPointerDown={(e) => {
              e.stopPropagation();
              onSeek(start);
            }}
          />
        );
      })}
      <span className="head" style={{ left: pct(frame) }} />
    </div>
  );
}
