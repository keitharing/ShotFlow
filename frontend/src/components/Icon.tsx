/** Monochrome 16px line icons drawn for ShotFlow (currentColor, no emoji or font glyphs). */
const PATHS: Record<string, string> = {
  play: "M5 3.5v9l7-4.5z",
  pause: "M5 3.5h2v9H5zM9 3.5h2v9H9z",
  stepBack: "M4 3.5v9M12 3.5v9L6 8z",
  stepForward: "M12 3.5v9M4 3.5v9L10 8z",
  toStart: "M3.5 3.5v9M13 3.5v9L7 8zM8.5 3.5v9L3.5 8",
  toEnd: "M12.5 3.5v9M3 3.5v9L9 8zM7.5 3.5v9l5-4.5",
  sound: "M3 6h2.5L9 3v10L5.5 10H3zM11 5.5a3.5 3.5 0 0 1 0 5M12.8 3.8a6 6 0 0 1 0 8.4",
  mute: "M3 6h2.5L9 3v10L5.5 10H3zM11 6l3.5 4M14.5 6L11 10",
  up: "M8 13V3M4 7l4-4 4 4",
  down: "M8 3v10M4 9l4 4 4-4",
  close: "M4 4l8 8M12 4l-8 8",
  plus: "M8 3v10M3 8h10",
  brush: "M10.5 2.5l3 3-6.5 6.5-3-3zM4 9.5L2.5 13.5 6.5 12",
  eraser: "M6 13.5h7.5M2.8 9.8l6.4-6.4a1 1 0 0 1 1.4 0l2.5 2.5a1 1 0 0 1 0 1.4L8 12.4H5.4z",
  refresh: "M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5",
  generate: "M8 2v3M8 11v3M2 8h3M11 8h3M4 4l2 2M10 10l2 2M12 4l-2 2M6 10l-2 2",
  layers: "M8 2.5l6 3-6 3-6-3zM2 9l6 3 6-3",
  queue: "M3 4h10M3 8h10M3 12h6",
  graph: "M3.5 4.5h3v3h-3zM9.5 8.5h3v3h-3zM6.5 6h1.5a1.5 1.5 0 0 1 1.5 1.5V10",
  check: "M3 8.5l3 3 7-7",
  chevron: "M6 3.5l4.5 4.5L6 12.5",
  dashboard: "M2.5 2.5h4.5v6H2.5zM9 2.5h4.5v3.5H9zM9 8h4.5v5.5H9zM2.5 10.5h4.5v3H2.5z",
  film: "M2.5 3h11v10h-11zM2.5 6h11M2.5 10h11M5.5 3v10M10.5 3v10",
  power: "M8 2.5v5M4.8 4.6a4.8 4.8 0 1 0 6.4 0",
  compare: "M8 2.5v11M3 4h3.5v8H3zM9.5 4H13v8H9.5",
  edit: "M2.5 4h11M2.5 8h7M2.5 12h4",
  cube: "M8 2l5.5 3v6L8 14l-5.5-3V5zM2.5 5L8 8l5.5-3M8 8v6",
  folder: "M2 4.5h4l1.5 1.5H14v7H2z",
  copy: "M5.5 5.5h7v8h-7zM3.5 10.5v-8h7",
  upload: "M8 11V3M4.5 6.5L8 3l3.5 3.5M3 13h10",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, title }: { name: IconName; size?: number; title?: string }) {
  const filled = name === "play" || name === "pause";
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill={filled ? "currentColor" : "none"}
      stroke={filled ? "none" : "currentColor"}
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}
