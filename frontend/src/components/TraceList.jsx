import { useEffect, useState } from "react";
import { api } from "../api.js";

function TraceDetail({ traceId, onClose }) {
  const [trace, setTrace] = useState(null);

  useEffect(() => {
    api.getTrace(traceId).then(setTrace);
  }, [traceId]);

  if (!trace) return <p>Loading…</p>;

  return (
    <div style={{ border: "1px solid #ccc", padding: 12, marginTop: 12 }}>
      <button onClick={onClose}>close</button>
      <h4>{trace.source_id}</h4>
      <p>
        {trace.test_class} · reliability {trace.reliability} · observed{" "}
        {trace.observed_at} · status {trace.status || "unsnapped"}
      </p>
      <h5>Turns</h5>
      <ol>
        {trace.turns.map((t, i) => (
          <li key={i}>
            {t.direction} → {t.street}
          </li>
        ))}
      </ol>
      <h5>Resolved waypoints</h5>
      <ol>
        {trace.waypoints.map((w, i) => (
          <li key={i}>
            {w.pair_street_a} × {w.pair_street_b} — ({w.lat.toFixed(5)},{" "}
            {w.lon.toFixed(5)})
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function TraceList({ centreId }) {
  const [traces, setTraces] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stale = false;
    setTraces([]);
    setOpenId(null);
    setError(null);
    api
      .getTraces(centreId)
      .then((data) => {
        if (!stale) setTraces(data);
      })
      .catch((err) => {
        if (!stale) setError(err.message);
      });
    return () => {
      stale = true;
    };
  }, [centreId]);

  if (error) return <p style={{ color: "red" }}>Error: {error}</p>;

  return (
    <div>
      <h3>Sources ({traces.length})</h3>
      <ul>
        {traces.map((t) => (
          <li key={t.id}>
            <button onClick={() => setOpenId(t.id)}>
              {t.source_id} — {t.test_class}, {t.observed_at}
            </button>
          </li>
        ))}
      </ul>
      {openId && (
        <TraceDetail traceId={openId} onClose={() => setOpenId(null)} />
      )}
    </div>
  );
}