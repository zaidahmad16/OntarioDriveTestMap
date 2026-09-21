// Shared display formatting for real per-step data (traffic_control /
// speed_limit tags from route_line_steps). Split out of MapView.jsx so
// QuizMode.jsx can reuse the exact same display text instead of a second
// copy that could drift from it.

export function formatTrafficControl(kind) {
  if (kind === "traffic_signals") return "Traffic light";
  if (kind === "stop") return "Stop sign";
  return "";
}

export function formatSpeedLimit(v) {
  if (!v) return "";
  return /^\d+$/.test(v) ? `${v} km/h` : v; // plain number = km/h in this region; "45 mph" etc. kept as-is
}

// Reads the maneuver straight out of the instruction's own wording --
// never a separate guess. If the text doesn't clearly say a direction,
// return null so the turn feed shows the step number alone rather than
// a wrong or invented icon.
export function inferManeuver(instruction) {
  if (!instruction) return null;
  const s = instruction.toLowerCase();
  if (s.includes("arrive") || s.includes("destination")) return "destination";
  if (s.includes("roundabout")) return "roundabout";
  if (s.includes("merge")) return "merge";
  if (s.includes("turn left") || s.includes("sharp left") || s.includes("slight left")) return "left";
  if (s.includes("turn right") || s.includes("sharp right") || s.includes("slight right")) return "right";
  if (s.includes("continue") || s.includes("straight")) return "straight";
  return null;
}
