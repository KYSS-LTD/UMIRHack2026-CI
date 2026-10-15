const circle = (cx: number, cy: number, r: number) => `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;

const PATHS = {
  attach: ["M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"],
  send: ["M12 19V5", "M5 12l7-7 7 7"],
  stop: ["M7 7h10v10H7z"],
  sun: [circle(12, 12, 4), "M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"],
  moon: ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"],
  system: ["M3 5h18v11H3z", "M8 20h8M12 16v4"],
  search: [circle(11, 11, 7), "M20 20l-3.5-3.5"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  x: ["M6 6l12 12M18 6L6 18"],
  alert: ["M12 3l10 18H2z", "M12 10v5M12 18h.01"],
  question: [circle(12, 12, 9), "M9.6 9.6a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1.1 1-1.1 1.7M12 17h.01"],
  doc: ["M7 3h7l5 5v13H7z", "M14 3v5h5M10 13h6M10 17h6"],
  more: [circle(6, 12, 1.4), circle(12, 12, 1.4), circle(18, 12, 1.4)],
  camera: ["M4 8h3l2-3h6l2 3h3v11H4z", circle(12, 13.5, 3.5)],
  arrowRight: ["M5 12h14M13 6l6 6-6 6"],
  book: ["M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z", "M5 17a3 3 0 0 1 3-3h10"],
  layers: ["M12 3l9 5-9 5-9-5z", "M3 13l9 5 9-5"],
  zoomIn: [circle(11, 11, 7), "M20 20l-3.5-3.5M11 8v6M8 11h6"],
  zoomOut: [circle(11, 11, 7), "M20 20l-3.5-3.5M8 11h6"],
  fit: ["M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"],
  eye: ["M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z", circle(12, 12, 3)],
  command: ["M9 9V6a3 3 0 1 0-3 3zm0 0h6m-6 0v6m6-6V6a3 3 0 1 1 3 3h-3zm0 0v6m0 0h3a3 3 0 1 1-3 3zm0 0H9m0 0v3a3 3 0 1 1-3-3z"],
  plus: ["M12 5v14M5 12h14"],
  trash: ["M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"],
  edit: ["M4 20h4L19 9l-4-4L4 16zM13 7l4 4"],
  chevronDown: ["M6 9l6 6 6-6"],
  chevronRight: ["M9 6l6 6-6 6"],
  print: ["M7 9V3h10v6M6 17H4v-6h16v6h-2M7 14h10v7H7z"],
  download: ["M12 4v11M7 11l5 5 5-5M5 20h14"],
  history: ["M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5", "M12 8v5l3 2"],
  keyboard: ["M3 6h18v12H3z", "M7 10h.01M11 10h.01M15 10h.01M7 14h10"],
  refresh: ["M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"],
  link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
  arrowUp: ["M12 19V5", "M5 12l7-7 7 7"],
  copy: ["M9 9h11v11H9z", "M5 15V4h11"],
  swap: ["M17 3l4 4-4 4", "M21 7H9", "M7 21l-4-4 4-4", "M3 17h12"],
  contrast: [circle(12, 12, 9), "M12 3a9 9 0 0 1 0 18z"],
  image: ["M3 5h18v14H3z", circle(8.5, 10, 1.5), "M21 16l-5-5-8 8"],
  inbox: ["M3 13l3-8h12l3 8v6H3z", "M3 13h5l1 3h6l1-3h5"],
  key: [circle(15, 9, 4), "M12.2 11.8L4 20M7 17l2 2M9.5 14.5l2 2"],
  telegram: ["M21.5 3.5L2.8 10.8l6 2.3 2.3 6 3.2-4.2 4.8 3.6z", "M9 13.1l12.5-9.6"],
  user: [circle(12, 8, 4), "M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"],
  bell: ["M6 16V11a6 6 0 1 1 12 0v5l2 2H4z", "M10 21h4"],
  logout: ["M10 4H5v16h5", "M15 8l4 4-4 4M19 12H9"],
  broom: ["M20.5 3.5l-7.2 7.2", "M14.8 12.6l-3.4-3.4c-2.6 1.7-5.6 2.4-8.1 2.6 1.1 4.3 4.7 7.9 9 9 .2-2.5.9-5.5 2.5-8.2z", "M7 13.6l3.4 3.4M5.2 16l2.8 2.8"],
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 18, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill={name === "stop" ? "currentColor" : "none"} stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      {PATHS[name].map((d, i) => (
        <path key={i} d={d} />
      ))}
    </svg>
  );
}
