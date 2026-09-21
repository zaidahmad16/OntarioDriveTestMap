import { useMemo, useState } from "react";
import { useLang } from "../i18n.jsx";
import { formatTrafficControl, formatSpeedLimit } from "../format.js";

// Distractor pools only used to pad out multiple-choice options when a
// route doesn't have enough distinct real values of its own -- every
// pool value is a real speed limit / traffic control kind seen
// elsewhere in this app, never an invented category.
const SPEED_POOL = ["30", "40", "50", "60", "70", "80", "90", "100"];
const CONTROL_POOL = ["stop", "traffic_signals"];

function shuffle(arr) {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

// Questions come straight from real per-step traffic_control/speed_limit
// data already fetched with the route -- no new backend endpoint, no new
// data collection, matches the Notion backlog note for this item.
function buildQuestions(steps) {
  const speedValues = [...new Set(steps.map((s) => s.speed_limit).filter(Boolean))];
  const controlValues = [...new Set(steps.map((s) => s.traffic_control).filter(Boolean))];
  const questions = [];

  for (const s of steps) {
    if (s.speed_limit) {
      const distractors = shuffle(
        [...new Set([...speedValues, ...SPEED_POOL])].filter((v) => v !== s.speed_limit)
      ).slice(0, 3);
      questions.push({
        kind: "speed",
        prompt: s.instruction,
        answer: s.speed_limit,
        options: shuffle([s.speed_limit, ...distractors]),
        format: formatSpeedLimit,
      });
    }
    if (s.traffic_control) {
      const distractors = shuffle(
        [...new Set([...controlValues, ...CONTROL_POOL])].filter((v) => v !== s.traffic_control)
      ).slice(0, 3);
      questions.push({
        kind: "control",
        prompt: s.instruction,
        answer: s.traffic_control,
        options: shuffle([s.traffic_control, ...distractors]),
        format: formatTrafficControl,
      });
    }
  }
  return questions;
}

export default function QuizMode({ lineFeatures }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [order, setOrder] = useState(null);
  const [index, setIndex] = useState(0);
  const [selected, setSelected] = useState(null);
  const [score, setScore] = useState(0);

  const steps = useMemo(
    () => lineFeatures.flatMap((f) => f.properties.steps || []),
    [lineFeatures]
  );
  const available = useMemo(() => buildQuestions(steps).length, [steps]);

  function start() {
    setOrder(shuffle(buildQuestions(steps)));
    setIndex(0);
    setSelected(null);
    setScore(0);
    setOpen(true);
  }

  function choose(opt) {
    if (selected != null) return;
    setSelected(opt);
    if (opt === order[index].answer) setScore((s) => s + 1);
  }

  function next() {
    setSelected(null);
    setIndex((i) => i + 1);
  }

  // No speed-limit/traffic-control data on this route -- nothing real to
  // quiz on. Stay silent rather than show a button that always fails.
  if (available === 0) return null;

  if (!open) {
    return (
      <button onClick={start}>
        {t("startQuiz")}
      </button>
    );
  }

  if (index >= order.length) {
    const pct = Math.round((score / order.length) * 100);
    const tier = pct >= 80 ? "success" : pct >= 50 ? "warning" : "confirmed";
    return (
      <div className="card scale-in" style={{ margin: "var(--space-md) 0" }}>
        <p style={{ margin: "0 0 var(--space-sm)" }}>
          {t("quizDone")}:{" "}
          <span className={`trust-badge trust-badge--${tier}`} style={{ fontSize: "0.875rem" }}>
            {score} / {order.length}
          </span>
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-primary" onClick={start}>
            {t("retryQuiz")}
          </button>
          <button onClick={() => setOpen(false)}>{t("close")}</button>
        </div>
      </div>
    );
  }

  const q = order[index];
  const progress = ((index + (selected != null ? 1 : 0)) / order.length) * 100;

  return (
    <div className="card scale-in" style={{ margin: "var(--space-md) 0" }}>
      <div style={{ background: "var(--surface-sunken)", borderRadius: "var(--radius-pill)", height: 6, overflow: "hidden", marginBottom: "var(--space-md)" }}>
        <div
          style={{
            width: `${progress}%`,
            background: "var(--accent)",
            height: "100%",
            transition: "width var(--duration-slow) var(--ease-out-quart)",
          }}
        />
      </div>
      <p style={{ fontSize: "0.8125rem", color: "var(--ink-muted)" }}>
        {t("quizQuestion")} <span className="data">{index + 1}</span>/<span className="data">{order.length}</span> · {t("score")}:{" "}
        <span className="data">{score}</span>
      </p>
      <p>
        {q.kind === "speed" ? t("quizSpeedPrompt") : t("quizControlPrompt")}
        <br />
        <i>&quot;{q.prompt}&quot;</i>
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {q.options.map((opt) => {
          const isAnswer = opt === q.answer;
          const isChosen = opt === selected;
          const revealed = selected != null;
          const feedback = revealed && isAnswer ? "success" : revealed && isChosen ? "confirmed" : null;
          return (
            <button
              key={opt}
              onClick={() => choose(opt)}
              disabled={revealed}
              className={feedback ? `trust-badge trust-badge--${feedback} pop-in` : undefined}
              style={
                feedback
                  ? { fontSize: "0.875rem", padding: "8px 14px", border: "none" }
                  : { transition: "border-color var(--duration-fast) var(--ease-out-quart)" }
              }
            >
              {q.format(opt)}
            </button>
          );
        })}
      </div>
      {selected != null && (
        <button className="btn-primary rise-in" onClick={next} style={{ marginTop: "var(--space-md)" }}>
          {index + 1 < order.length ? t("nextQuestion") : t("seeScore")}
        </button>
      )}
      <button onClick={() => setOpen(false)} style={{ marginTop: "var(--space-md)", marginLeft: 8 }}>
        {t("close")}
      </button>
    </div>
  );
}
