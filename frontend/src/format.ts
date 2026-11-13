import type { Decision, Severity, VersionStatus } from "./types";

export const STATUS_LABEL: Record<VersionStatus, string> = {
  pending: "대기",
  in_review: "검토중",
  approved: "승인",
  rejected: "반려",
  revision_requested: "수정요청",
};

export const DECISION_LABEL: Record<Decision, string> = {
  approve: "승인",
  reject: "반려",
  revise: "수정요청",
  comment: "코멘트",
};

export const SEVERITY_LABEL: Record<Severity, string> = { error: "오류", warning: "경고", info: "정보" };

export const QC_STATUS_LABEL: Record<string, string> = {
  pass: "통과",
  warning: "경고",
  error: "오류",
  failed: "검수 실패",
};

export const ORIGIN_LABEL: Record<string, string> = {
  imported: "가져옴",
  generated: "생성",
  fix: "수정",
  edit: "편집",
  fx: "FX",
  blender: "3D 렌더",
};

/** Frame number → SMPTE-style timecode (non-drop). */
export function timecode(frame: number, fps: number): string {
  const f = Math.max(0, Math.round(frame));
  const r = Math.round(fps);
  const totalSeconds = Math.floor(f / r);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(totalSeconds / 60))}:${pad(totalSeconds % 60)}:${pad(f % r)}`;
}

export function seconds(value: number | null | undefined, digits = 1): string {
  return value == null ? "-" : `${value.toFixed(digits)}초`;
}

/** Server stores UTC without a zone suffix; show it in Korea time. */
export function kst(iso: string | null | undefined): string {
  if (!iso) return "-";
  const utc = /[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`;
  return new Date(utc).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function bytes(value: number | null | undefined): string {
  if (value == null) return "-";
  if (value > 1e9) return `${(value / 1e9).toFixed(2)} GB`;
  if (value > 1e6) return `${(value / 1e6).toFixed(1)} MB`;
  return `${(value / 1e3).toFixed(0)} KB`;
}
