// One small, consistent icon set (Hallmark audit finding: mixed
// OS-rendered emoji as functional icons, each with a different visual
// weight/color depending on platform). Same stroke voice everywhere:
// 1.5px stroke, round caps/joins, 16px viewBox, currentColor -- so an
// icon always matches the text color around it instead of carrying its
// own baked-in emoji color.

const base = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round",
  strokeLinejoin: "round",
};

export function BellIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 6.5a4 4 0 0 1 8 0c0 3 1.2 4 1.2 4H2.8S4 9.5 4 6.5Z" />
      <path d="M6.5 13a1.5 1.5 0 0 0 3 0" />
    </svg>
  );
}

export function ClockIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 5v3.2l2.2 1.3" />
    </svg>
  );
}

export function FlagIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M3.5 2v12" />
      <path d="M3.5 2.8h6.3l-1.4 2.2 1.4 2.2H3.5" />
    </svg>
  );
}

export function LiveDotIcon(props) {
  return (
    <svg width={10} height={10} viewBox="0 0 10 10" {...props}>
      <circle cx="5" cy="5" r="4" fill="currentColor" />
    </svg>
  );
}

// Restrained maneuver glyphs -- same stroke voice as the rest of this
// file. Supplement the instruction text (per DESIGN.md/UX spec: icons
// never replace the words, only reinforce them at a glance).
export function TurnLeftIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M11 12V6.5A2.5 2.5 0 0 0 8.5 4H4" />
      <path d="M6.3 6.3 4 4l2.3-2.3" />
    </svg>
  );
}

export function TurnRightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M5 12V6.5A2.5 2.5 0 0 1 7.5 4H12" />
      <path d="M9.7 6.3 12 4 9.7 1.7" />
    </svg>
  );
}

export function StraightIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M8 13V3" />
      <path d="M5.3 5.7 8 3l2.7 2.7" />
    </svg>
  );
}

export function MergeIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 13V8.5A3 3 0 0 1 7 5.5h5" />
      <path d="M4 6V4" />
      <path d="M9.5 3.2 12 5.5l-2.5 2.3" />
    </svg>
  );
}

export function RoundaboutIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="8" cy="8" r="4" />
      <path d="M8 2v3" />
      <path d="M8 2 6.3 3.3" />
      <path d="M8 13v-2" />
    </svg>
  );
}

export function DestinationIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 14V2" />
      <path d="M4 2.8h7l-1.6 2.4 1.6 2.4H4" />
    </svg>
  );
}

export function InfoIcon(props) {
  return (
    <svg {...base} {...props}>
      <circle cx="8" cy="8" r="5.5" />
      <path d="M8 7.3v3.6" />
      <circle cx="8" cy="5.3" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ChevronDownIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M4 6l4 4 4-4" />
    </svg>
  );
}

export function LayersIcon(props) {
  return (
    <svg {...base} {...props}>
      <path d="M8 2.5 2.5 5.5 8 8.5l5.5-3L8 2.5Z" />
      <path d="M2.5 8.5 8 11.5l5.5-3" />
      <path d="M2.5 11.5 8 14.5l5.5-3" />
    </svg>
  );
}
