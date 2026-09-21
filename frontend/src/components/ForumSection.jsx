import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { MentionText, MentionField } from "../mentions.jsx";
import Reveal from "../Reveal.jsx";

// "Where I messed up" -- Reddit/Fragrantica-style review threads, not a
// data-entry table. Every post still resolves to a real junction (same
// osm.db check as route submissions) and still carries a maneuver
// type/outcome for aggregability, but those are small tags under the
// post now, not the headline -- the written note is the main content.
const MANEUVER_TYPES = [
  { value: "3_point_turn", label: "3-point turn" },
  { value: "parallel_park", label: "Parallel park" },
  { value: "stop_sign", label: "Stop sign" },
  { value: "merge", label: "Merge" },
  { value: "lane_change", label: "Lane change" },
  { value: "roundabout", label: "Roundabout" },
  { value: "pedestrian_yield", label: "Pedestrian yield" },
  { value: "other", label: "Other" },
];

function maneuverLabel(value) {
  return MANEUVER_TYPES.find((m) => m.value === value)?.label || value;
}

const OUTCOMES = [
  { value: "", label: "—" },
  { value: "pass", label: "Passed here" },
  { value: "fail", label: "Failed here" },
];

function outcomeLabel(value) {
  return OUTCOMES.find((o) => o.value === value)?.label || "—";
}

function CommentThread({ postId, username, onCommented }) {
  const { t } = useLang();
  const [comments, setComments] = useState(null);
  const [body, setBody] = useState("");
  const [image, setImage] = useState(null);
  const [error, setError] = useState(null);
  const [sending, setSending] = useState(false);

  function load() {
    api.getForumComments(postId).then(setComments).catch((err) => setError(err.message));
  }

  useEffect(load, [postId]);

  function pickImage(e) {
    const file = e.target.files?.[0];
    if (file && !file.type.startsWith("image/")) {
      setError(t("imageTypeError"));
      e.target.value = "";
      return;
    }
    setError(null);
    setImage(file || null);
  }

  function send() {
    const text = body.trim();
    if (!text || sending) return;
    setSending(true);
    setError(null);
    api
      .addForumComment(postId, text)
      .then((res) => (image ? api.uploadForumCommentImage(res.comment_id, image).catch(() => {}) : null))
      .then(() => {
        setBody("");
        setImage(null);
        load();
        onCommented();
      })
      .catch((err) => setError(err.message))
      .finally(() => setSending(false));
  }

  function remove(commentId) {
    api.deleteForumComment(commentId).then(() => {
      load();
      onCommented();
    }).catch(() => {});
  }

  return (
    <div className="fade-in" style={{ marginTop: 10, paddingLeft: 14, borderLeft: `2px solid var(--border)` }}>
      {comments === null && <p style={{ fontSize: "0.85em", color: "var(--ink-muted)" }}>{t("loading")}</p>}
      {comments && comments.length === 0 && (
        <p style={{ fontSize: "0.85em", color: "var(--ink-muted)" }}>{t("noComments")}</p>
      )}
      {comments &&
        comments.map((c, i) => (
          <div key={c.id} className="rise-in" style={{ margin: "8px 0", fontSize: "0.85em", animationDelay: `${i * 25}ms` }}>
            <b>{c.author}</b>{" "}
            <span style={{ color: "var(--ink-muted)" }}>· {new Date(c.created_at).toLocaleDateString()}</span>
            {c.can_delete && (
              <button
                onClick={() => remove(c.id)}
                style={{ marginLeft: 8, fontSize: "0.85em", border: "none", background: "none", color: "var(--confirmed)", padding: 0 }}
              >
                {t("delete")}
              </button>
            )}
            <p style={{ margin: "3px 0 0" }}>
              <MentionText text={c.body} />
            </p>
            {c.image_id && (
              <img
                src={api.imageUrl(c.image_id)}
                crossOrigin="use-credentials"
                alt={`Photo attached to ${c.author}'s comment`}
                style={{ maxWidth: 200, maxHeight: 150, marginTop: 6, borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}
              />
            )}
          </div>
        ))}
      {username ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "flex", gap: 6 }}>
            <MentionField
              value={body}
              onChange={setBody}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder={t("addComment")}
              style={{ flex: 1, fontSize: "0.85em" }}
            />
            <button onClick={send} disabled={sending || !body.trim()} style={{ fontSize: "0.85em" }}>
              {t("submit")}
            </button>
          </div>
          <input type="file" accept="image/*" onChange={pickImage} style={{ marginTop: 6, fontSize: "0.8em", border: "none", padding: 0 }} />
        </div>
      ) : (
        <p style={{ fontSize: "0.8em", color: "var(--ink-muted)", marginTop: 8 }}>{t("needUsernameToPost")}</p>
      )}
      {error && <p style={{ color: "var(--confirmed)", fontSize: "0.8em" }}>{error}</p>}
    </div>
  );
}

function ForumPostRow({ post, username, onChanged, index }) {
  const { t } = useLang();
  const [openComments, setOpenComments] = useState(false);
  const [reported, setReported] = useState(false);

  function vote(value) {
    const next = post.my_vote === value ? 0 : value;
    api.voteForumPost(post.id, next).then(onChanged).catch(() => {});
  }

  function report() {
    api.reportForumPost(post.id).then(() => setReported(true)).catch(() => {});
  }

  function remove() {
    api.deleteForumPost(post.id).then(onChanged).catch(() => {});
  }

  return (
    <Reveal
      as="div"
      delay={Math.min(index, 6) * 60}
      className="card card-interactive"
      style={{
        borderStyle: post.hidden ? "dashed" : "solid",
        borderColor: post.hidden ? "var(--confirmed)" : "var(--border)",
        padding: 14,
        marginBottom: 10,
      }}
    >
      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", minWidth: 30, gap: 2 }}>
          <button
            onClick={() => vote(1)}
            aria-label="upvote"
            style={{
              border: "none",
              background: "none",
              padding: 2,
              color: post.my_vote === 1 ? "var(--success)" : "var(--ink-faint)",
              transition: "color var(--duration-fast) var(--ease-out-quart), transform var(--duration-fast) var(--ease-out-quart)",
            }}
          >
            ▲
          </button>
          <span className="data" style={{ fontSize: "0.9em", fontWeight: 600 }}>
            {post.score}
          </span>
          <button
            onClick={() => vote(-1)}
            aria-label="downvote"
            style={{
              border: "none",
              background: "none",
              padding: 2,
              color: post.my_vote === -1 ? "var(--confirmed)" : "var(--ink-faint)",
              transition: "color var(--duration-fast) var(--ease-out-quart)",
            }}
          >
            ▼
          </button>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ margin: 0 }}>
            <b>{post.author}</b>{" "}
            <span style={{ color: "var(--ink-muted)", fontSize: "0.85em" }}>
              · {post.centre_name} · {post.test_class} · {new Date(post.created_at).toLocaleDateString()}
            </span>
            {post.hidden && (
              <span className="trust-badge trust-badge--confirmed" style={{ marginLeft: 6 }}>
                {t("hiddenFromPublic")}
              </span>
            )}
            {post.can_delete && (
              <button
                onClick={remove}
                style={{ marginLeft: 8, fontSize: "0.8em", border: "none", background: "none", color: "var(--confirmed)", padding: 0 }}
              >
                {t("delete")}
              </button>
            )}
          </p>
          <div style={{ margin: "8px 0", display: "flex", flexWrap: "wrap", gap: 6 }}>
            <span className="trust-badge trust-badge--gap">📍 {post.street_a} × {post.street_b}</span>
            <span className="trust-badge trust-badge--gap">{maneuverLabel(post.maneuver_type)}</span>
            {post.outcome && (
              <span className={`trust-badge trust-badge--${post.outcome === "pass" ? "success" : "confirmed"}`}>
                {outcomeLabel(post.outcome)}
              </span>
            )}
          </div>
          <p style={{ margin: "4px 0", fontSize: "0.95em" }}>
            {post.note ? <MentionText text={post.note} /> : <i style={{ color: "var(--ink-muted)" }}>{t("noDetailsWritten")}</i>}
          </p>
          {post.image_id && (
            <img
              src={api.imageUrl(post.image_id)}
              crossOrigin="use-credentials"
              alt={`Photo attached to ${post.author}'s forum post`}
              style={{ maxWidth: "100%", maxHeight: 260, marginTop: 6, borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}
            />
          )}
          <div style={{ display: "flex", gap: 14, fontSize: "0.85em", marginTop: 8 }}>
            <button
              onClick={() => setOpenComments((o) => !o)}
              style={{ border: "none", background: "none", padding: 0, color: "var(--ink-muted)", fontWeight: 600 }}
            >
              {t("comments")} ({post.comment_count})
            </button>
            {reported ? (
              <span style={{ color: "var(--ink-muted)" }}>{t("reported")}</span>
            ) : (
              <button onClick={report} style={{ border: "none", background: "none", padding: 0, color: "var(--ink-muted)", fontWeight: 600 }}>
                {t("reportPost")}
              </button>
            )}
          </div>
          {openComments && <CommentThread postId={post.id} username={username} onCommented={onChanged} />}
        </div>
      </div>
    </Reveal>
  );
}

function NewForumPost({ centreId, centreName, username, onPosted }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [testClass, setTestClass] = useState("G");
  const [streetA, setStreetA] = useState("");
  const [streetB, setStreetB] = useState("");
  const [checking, setChecking] = useState(false);
  const [checked, setChecked] = useState(false);
  const [checkError, setCheckError] = useState(null);
  const [maneuverType, setManeuverType] = useState(MANEUVER_TYPES[0].value);
  const [outcome, setOutcome] = useState("");
  const [note, setNote] = useState("");
  const [image, setImage] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [sent, setSent] = useState(false);

  function resetCheck() {
    setChecked(false);
    setCheckError(null);
  }

  function checkJunction() {
    const a = streetA.trim();
    const b = streetB.trim();
    if (!a || !b) return;
    setChecking(true);
    setCheckError(null);
    api
      .validatePair(a, b)
      .then((res) => {
        if (res.ok) setChecked(true);
        else setCheckError(res.error);
      })
      .catch((err) => setCheckError(err.message))
      .finally(() => setChecking(false));
  }

  function pickImage(e) {
    const file = e.target.files?.[0];
    if (file && !file.type.startsWith("image/")) {
      setError(t("imageTypeError"));
      e.target.value = "";
      return;
    }
    setError(null);
    setImage(file || null);
  }

  function submit() {
    if (!checked || submitting) return;
    setSubmitting(true);
    setError(null);
    setSent(false);
    api
      .createForumPost({
        centre_id: centreId,
        centre_name: centreName,
        test_class: testClass,
        street_a: streetA.trim(),
        street_b: streetB.trim(),
        maneuver_type: maneuverType,
        outcome: outcome || null,
        note: note.trim() || null,
      })
      .then((res) => (image ? api.uploadForumImage(res.post_id, image).catch(() => {}) : null))
      .then(() => {
        setSent(true);
        setStreetA("");
        setStreetB("");
        setChecked(false);
        setNote("");
        setImage(null);
        onPosted();
      })
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  return (
    <div style={{ margin: "12px 0" }}>
      <button onClick={() => setOpen((o) => !o)} className={open ? "btn-primary" : undefined}>
        {t("newForumPost")}{" "}
        <span style={{ display: "inline-block", transition: "transform var(--duration-base) var(--ease-out-quart)", transform: open ? "rotate(180deg)" : "none" }}>
          ▾
        </span>
      </button>
      {open && !username && <p style={{ color: "var(--ink-muted)", fontSize: "0.85em" }}>{t("needUsernameToPost")}</p>}
      {open && username && (
        <div className="card scale-in" style={{ marginTop: 10, maxWidth: 480 }}>
          <p style={{ fontSize: "0.8em", color: "var(--ink-muted)" }}>{t("forumPostHint")}</p>

          <label style={{ display: "block", fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>{t("class")}</label>
          <select value={testClass} onChange={(e) => setTestClass(e.target.value)}>
            <option value="G">G</option>
            <option value="G2">G2</option>
          </select>

          <label style={{ display: "block", marginTop: 12, fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>
            {t("atJunction")}
          </label>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <input
              value={streetA}
              onChange={(e) => {
                setStreetA(e.target.value);
                resetCheck();
              }}
              placeholder={t("streetA")}
              style={{ flex: "1 1 140px", borderColor: checkError ? "var(--confirmed)" : undefined }}
            />
            <input
              value={streetB}
              onChange={(e) => {
                setStreetB(e.target.value);
                resetCheck();
              }}
              placeholder={t("streetB")}
              style={{ flex: "1 1 140px", borderColor: checkError ? "var(--confirmed)" : undefined }}
            />
            <button onClick={checkJunction} disabled={checking || !streetA.trim() || !streetB.trim()}>
              {checking ? t("checking") : t("checkJunction")}
            </button>
          </div>
          {checked && (
            <p className="rise-in trust-badge trust-badge--success" style={{ margin: "8px 0 0" }}>
              ✓ {t("realJunction")}
            </p>
          )}
          {checkError && <p style={{ color: "var(--confirmed)", fontSize: "0.85em", margin: "6px 0 0" }}>{checkError}</p>}

          <label style={{ display: "block", marginTop: 12, fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>
            {t("maneuverType")}
          </label>
          <select value={maneuverType} onChange={(e) => setManeuverType(e.target.value)}>
            {MANEUVER_TYPES.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>

          <label style={{ display: "block", marginTop: 12, fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>
            {t("outcome")}
          </label>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            {OUTCOMES.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>

          <label style={{ display: "block", marginTop: 12, fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>
            {t("writeYourExperience")}
          </label>
          <MentionField value={note} onChange={setNote} placeholder={t("notePlaceholder")} rows={3} style={{ width: "100%", minHeight: 60 }} />

          <label style={{ display: "block", marginTop: 12, fontSize: "0.85em", fontWeight: 600, marginBottom: 4 }}>
            {t("addPhoto")}
          </label>
          <input type="file" accept="image/*" onChange={pickImage} style={{ border: "none", padding: 0, fontSize: "0.85em" }} />

          <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginTop: 12 }}>
            {t("postConsent")}{" "}
            <a href="/terms-of-service.html" target="_blank" rel="noopener">{t("terms")}</a>.
          </p>

          <div style={{ marginTop: 8 }}>
            <button className="btn-primary" onClick={submit} disabled={!checked || submitting}>
              {submitting ? t("loading") : t("submit")}
            </button>
          </div>
          {error && <p style={{ color: "var(--confirmed)", fontSize: "0.85em" }}>{error}</p>}
          {sent && <p style={{ color: "var(--success)", fontSize: "0.85em" }}>{t("submissionSent")}</p>}
        </div>
      )}
    </div>
  );
}

export default function ForumSection({ centreId, centreName, username }) {
  const { t } = useLang();
  const [posts, setPosts] = useState(null);
  const [error, setError] = useState(null);

  function load() {
    api.getForumPosts(centreId).then(setPosts).catch((err) => setError(err.message));
  }

  useEffect(() => {
    setPosts(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [centreId]);

  return (
    <div style={{ marginTop: 20 }}>
      <h3>{t("forum")}</h3>
      <NewForumPost centreId={centreId} centreName={centreName} username={username} onPosted={load} />
      {error && <p className="error-banner">{t("error")}: {error}</p>}
      {posts && posts.length === 0 && <p style={{ color: "var(--ink-muted)" }}>{t("noForumPosts")}</p>}
      {posts && posts.map((p, i) => <ForumPostRow key={p.id} post={p} username={username} onChanged={load} index={i} />)}
    </div>
  );
}
