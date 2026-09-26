import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useLang } from "../i18n.jsx";
import { MentionText, MentionField } from "../mentions.jsx";
import Reveal from "../Reveal.jsx";
import { useSeo, breadcrumbList, SITE_URL } from "../seo.js";
import Breadcrumbs from "../Breadcrumbs.jsx";

// Minimal Reddit-style IA (feed -> filter -> open -> vote -> comment ->
// create post) on the app's own restrained design system -- neutral
// greys only, never the trust-colour palette (DESIGN.md's Never-Blur
// Rule: community popularity must never look like route confidence).
// Deliberately excludes karma/awards/profiles/followers/DMs/deep comment
// trees per the 2026-09-21 redesign brief -- every control here maps to
// a real backend capability, nothing invented.

const AVATAR_COLORS = ["#e74c3c", "#e67e22", "#d4ac0d", "#27ae60", "#16a085", "#2980b9", "#8e44ad", "#c0392b"];

function avatarColor(name) {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function Avatar({ name, size = 32 }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: avatarColor(name || "?"),
        color: "#fff",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: "bold",
        fontSize: size * 0.45,
        flexShrink: 0,
      }}
    >
      {(name || "?").charAt(0).toUpperCase()}
    </div>
  );
}

function timeAgo(iso) {
  const d = new Date(iso);
  const diffMin = Math.round((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.round(diffH / 24);
  if (diffD < 30) return `${diffD}d ago`;
  return d.toLocaleDateString();
}

function getParams() {
  return new URLSearchParams(window.location.search);
}

function setParams(patch) {
  const p = getParams();
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") p.delete(k);
    else p.set(k, v);
  }
  const qs = p.toString();
  window.history.pushState({}, "", qs ? `?${qs}` : window.location.pathname);
}

function VoteRail({ score, myVote, onVote, size = "md" }) {
  const { t } = useLang();
  return (
    <div className="vote-rail" style={size === "sm" ? { minWidth: 0 } : undefined}>
      <button aria-label={t("upvote") || "Upvote"} aria-pressed={myVote === 1} onClick={() => onVote(1)}>
        ▲
      </button>
      <span className="vote-rail__score">{score}</span>
      <button aria-label={t("downvote") || "Downvote"} aria-pressed={myVote === -1} onClick={() => onVote(-1)}>
        ▼
      </button>
    </div>
  );
}

// Destructive items require a second click ("Confirm delete?") inside the
// same popover instead of a native window.confirm() -- a blocking browser
// dialog is worse UX here and this keeps the confirmation in-flow.
function OverflowMenu({ items }) {
  const { t } = useLang();
  const [open, setOpen] = useState(false);
  const [confirmingAt, setConfirmingAt] = useState(null);
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  const visible = items.filter(Boolean);

  // Close on outside click or Escape -- NOT on mouseleave, which closed
  // the menu mid-reach (cursor crossing the gap from the button) and
  // never fired at all on touch screens.
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
        setConfirmingAt(null);
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        setConfirmingAt(null);
        btnRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (visible.length === 0) return null;
  return (
    <div className={`overflow-menu${open ? " overflow-menu--open" : ""}`} ref={rootRef}>
      <button
        ref={btnRef}
        type="button"
        aria-label={t("moreActions")}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => !o);
          setConfirmingAt(null);
        }}
      >
        •••
      </button>
      {open && (
        <div className="overflow-menu__panel popover" role="menu">
          {visible.map((it, i) => (
            <button
              key={i}
              type="button"
              role="menuitem"
              className={it.danger ? "danger" : undefined}
              onClick={() => {
                if (it.danger && confirmingAt !== i) {
                  setConfirmingAt(i);
                  return;
                }
                setOpen(false);
                setConfirmingAt(null);
                it.onClick();
              }}
            >
              {it.danger && confirmingAt === i ? t("confirmDelete") : it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function postTypeLabel(t, v) {
  return { question: t("postTypeQuestion"), experience: t("postTypeExperience"), tip: t("postTypeTip") }[v];
}

// One level of replies only (spec: no infinite Reddit-style nesting) --
// enforced simply by never rendering a Reply control below depth 0, so
// every reply attaches to a real top-level comment.
function CommentRow({ comment, depth, postId, username, isAdmin, onChanged }) {
  const { t } = useLang();
  const [replying, setReplying] = useState(false);
  const [replyBody, setReplyBody] = useState("");
  const [sending, setSending] = useState(false);

  function vote(value) {
    const next = comment.my_vote === value ? 0 : value;
    api.voteDiscussionComment(comment.id, next).then(onChanged).catch(() => {});
  }

  function remove() {
    api.deleteDiscussionComment(comment.id).then(onChanged).catch(() => {});
  }

  function sendReply() {
    const text = replyBody.trim();
    if (!text || sending) return;
    setSending(true);
    api
      .addDiscussionComment(postId, text, comment.id)
      .then(() => {
        setReplyBody("");
        setReplying(false);
        onChanged();
      })
      .finally(() => setSending(false));
  }

  const canDelete = comment.can_delete || isAdmin;

  return (
    <div className={depth > 0 ? "comment-row comment-row--reply" : "comment-row"}>
      <Avatar name={comment.author} size={26} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: "0.85em" }}>
          <b>{comment.author}</b> <span style={{ color: "var(--ink-muted)" }}>· {timeAgo(comment.created_at)}</span>
        </div>
        <p style={{ margin: "2px 0 6px", fontSize: "0.9em" }}>
          <MentionText text={comment.body} />
        </p>
        {comment.image_id && (
          <img
            src={api.discussionImageUrl(comment.image_id)}
            crossOrigin="use-credentials"
            alt={`Photo attached to ${comment.author}'s comment`}
            style={{ maxWidth: 200, maxHeight: 150, marginBottom: 6, borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}
          />
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: "0.8em" }}>
          <button
            onClick={() => vote(1)}
            aria-pressed={comment.my_vote === 1}
            style={{ border: "none", background: "none", padding: 0, color: comment.my_vote === 1 ? "var(--ink)" : "var(--ink-muted)", fontWeight: 600 }}
          >
            ▲ {comment.score}
          </button>
          <button
            onClick={() => vote(-1)}
            aria-pressed={comment.my_vote === -1}
            style={{ border: "none", background: "none", padding: 0, color: comment.my_vote === -1 ? "var(--ink)" : "var(--ink-muted)", fontWeight: 600 }}
          >
            ▼
          </button>
          {depth === 0 && username && (
            <button onClick={() => setReplying((r) => !r)} style={{ border: "none", background: "none", padding: 0, color: "var(--ink-muted)", fontWeight: 600 }}>
              {t("reply")}
            </button>
          )}
          {canDelete && (
            <OverflowMenu items={[{ label: t("delete"), danger: true, onClick: remove }]} />
          )}
        </div>
        {replying && (
          <div className="scale-in" style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <MentionField
              value={replyBody}
              onChange={setReplyBody}
              onKeyDown={(e) => e.key === "Enter" && sendReply()}
              placeholder={t("addComment")}
              style={{ flex: 1, fontSize: "0.85em" }}
            />
            <button onClick={sendReply} disabled={sending || !replyBody.trim()} style={{ fontSize: "0.85em" }}>
              {t("submit")}
            </button>
          </div>
        )}
        {/* Second-level replies (a reply to a reply) collapse onto the same
            indent as their parent -- keeps the thread at one visual reply
            level regardless of what the flat data technically allows. */}
        {comment.replies.map((r) => (
          <CommentRow key={r.id} comment={r} depth={1} postId={postId} username={username} isAdmin={isAdmin} onChanged={onChanged} />
        ))}
      </div>
    </div>
  );
}

function buildCommentTree(rows) {
  const byId = new Map(rows.map((r) => [r.id, { ...r, replies: [] }]));
  const roots = [];
  for (const row of byId.values()) {
    if (row.parent_comment_id && byId.has(row.parent_comment_id)) {
      byId.get(row.parent_comment_id).replies.push(row);
    } else {
      roots.push(row);
    }
  }
  return roots;
}

function CommentThread({ postId, username, isAdmin, onCommented }) {
  const { t } = useLang();
  const [comments, setComments] = useState(null);
  const [sort, setSort] = useState("newest");
  const [body, setBody] = useState("");
  const [image, setImage] = useState(null);
  const [sending, setSending] = useState(false);

  function load() {
    api.getDiscussionComments(postId, sort).then(setComments);
  }

  useEffect(load, [postId, sort]);

  function pickImage(e) {
    const file = e.target.files?.[0];
    if (file && !file.type.startsWith("image/")) {
      e.target.value = "";
      return;
    }
    setImage(file || null);
  }

  function send() {
    const text = body.trim();
    if (!text || sending) return;
    setSending(true);
    api
      .addDiscussionComment(postId, text)
      .then((res) => (image ? api.uploadDiscussionCommentImage(res.comment_id, image).catch(() => {}) : null))
      .then(() => {
        setBody("");
        setImage(null);
        load();
        onCommented();
      })
      .finally(() => setSending(false));
  }

  const tree = comments ? buildCommentTree(comments) : null;

  return (
    <div>
      {comments && comments.length > 0 && (
        <div className="segmented" style={{ marginBottom: 8 }}>
          <button aria-pressed={sort === "newest"} onClick={() => setSort("newest")} style={{ fontSize: "0.8em" }}>
            {t("sortNewest")}
          </button>
          <button aria-pressed={sort === "score"} onClick={() => setSort("score")} style={{ fontSize: "0.8em" }}>
            {t("sortTop")}
          </button>
        </div>
      )}
      {comments === null && <p style={{ fontSize: "0.85em", color: "var(--ink-muted)" }}>{t("loading")}</p>}
      {tree && tree.length === 0 && <p style={{ fontSize: "0.85em", color: "var(--ink-muted)" }}>{t("noComments")}</p>}
      {tree && tree.map((c) => (
        <CommentRow key={c.id} comment={c} depth={0} postId={postId} username={username} isAdmin={isAdmin} onChanged={() => { load(); onCommented(); }} />
      ))}
      {username ? (
        <div className="comment-composer">
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
          <input type="file" accept="image/*" onChange={pickImage} style={{ fontSize: "0.75em", border: "none", padding: 0, maxWidth: 90 }} />
        </div>
      ) : (
        <p style={{ fontSize: "0.8em", color: "var(--ink-muted)", marginTop: 6 }}>{t("needUsernameToPost")}</p>
      )}
    </div>
  );
}

function Badges({ post, onFilterCentre, onFilterType }) {
  const { t } = useLang();
  return (
    <div className="post-card__badges">
      {post.centre_name ? (
        <button className="badge-neutral badge-neutral--link" onClick={() => onFilterCentre(post.centre_id)}>
          {post.centre_name}
        </button>
      ) : (
        <span className="badge-neutral">{t("general")}</span>
      )}
      {post.test_type && (
        <button className="badge-neutral badge-neutral--link" onClick={() => onFilterType(post.test_type)}>
          {post.test_type}
        </button>
      )}
      {post.post_type && <span className="badge-neutral">{postTypeLabel(t, post.post_type)}</span>}
      {post.route_line_id && (
        <a className="badge-neutral badge-neutral--link" href={`/?centre=${encodeURIComponent(post.centre_id || "")}`}>
          {t("route")}
        </a>
      )}
    </div>
  );
}

// Compact feed card -- title strongest element, then context badges, then
// author/time, then a short preview, then comment-count/share/overflow.
function PostCard({ post, username, isAdmin, onChanged, onOpen, onFilterCentre, onFilterType, index }) {
  const { t } = useLang();
  const [reported, setReported] = useState(false);

  function vote(value) {
    const next = post.my_vote === value ? 0 : value;
    api.voteDiscussion(post.id, next).then(onChanged).catch(() => {});
  }

  function remove() {
    api.deleteDiscussion(post.id).then(onChanged).catch(() => {});
  }

  function report() {
    api.reportDiscussion(post.id).then(() => setReported(true)).catch(() => {});
  }

  function share() {
    const url = `${window.location.origin}${window.location.pathname}?post=${post.id}`;
    navigator.clipboard?.writeText(url).catch(() => {});
  }

  const canDelete = post.can_delete || isAdmin;

  return (
    <Reveal as="div" delay={Math.min(index, 6) * 40} className="post-card">
      <VoteRail score={post.score} myVote={post.my_vote} onVote={vote} />
      <div className="post-card__body">
        <Badges post={post} onFilterCentre={onFilterCentre} onFilterType={onFilterType} />
        <h3 className="post-card__title">
          <a href={`?post=${post.id}`} onClick={(e) => { e.preventDefault(); onOpen(post.id); }}>
            {post.title}
          </a>
        </h3>
        <div className="post-card__meta">
          <b style={{ color: "var(--ink)" }}>{post.author}</b> · {timeAgo(post.created_at)}
          {post.hidden && <span className="badge-neutral" style={{ marginLeft: 8 }}>{t("hiddenFromPublic")}</span>}
        </div>
        <p className="post-card__preview">
          <MentionText text={post.body} />
        </p>
        {post.image_id && (
          <img src={api.discussionImageUrl(post.image_id)} crossOrigin="use-credentials" alt={`Photo attached to "${post.title}"`} className="post-card__thumb" />
        )}
        <div className="post-card__footer">
          <button onClick={() => onOpen(post.id)}>
            {post.comment_count} {t("comments")}
          </button>
          <button onClick={share}>{t("share")}</button>
          <OverflowMenu
            items={[
              !reported && { label: t("reportPost"), onClick: report },
              canDelete && { label: t("delete"), danger: true, onClick: remove },
            ]}
          />
        </div>
      </div>
    </Reveal>
  );
}

function Toast({ text, onDone }) {
  useEffect(() => {
    const id = setTimeout(onDone, 2200);
    return () => clearTimeout(id);
  }, [onDone]);
  return <div className="toast">{text}</div>;
}

function Composer({ centres, username, initial, onClose, onPosted }) {
  const { t } = useLang();
  const [centreId, setCentreId] = useState(initial.centreId || "");
  const [testType, setTestType] = useState(initial.testType || "");
  const [postType, setPostType] = useState("");
  const [routeLineId, setRouteLineId] = useState(initial.routeLineId || "");
  const [routeOptions, setRouteOptions] = useState([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [image, setImage] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!centreId) {
      setRouteOptions([]);
      return;
    }
    // Same stale-response guard as MapView's centre-switch effect --
    // without it, quickly switching the composer's centre dropdown could
    // let an earlier centre's slower response land after a later centre's
    // faster one and silently overwrite it with the wrong route list
    // (found in a 2026-09-21 cleanup pass).
    let stale = false;
    api
      .getMap(centreId)
      .then((geo) => {
        if (stale) return;
        const lines = geo.features.filter((f) => f.properties.kind === "route_line");
        setRouteOptions(
          lines.map((f) => ({
            id: f.properties.route_line_id,
            label: `${f.properties.test_class || ""} · ${(f.properties.distance_m / 1000).toFixed(1)} km`,
          }))
        );
      })
      .catch(() => {
        if (!stale) setRouteOptions([]);
      });
    return () => {
      stale = true;
    };
  }, [centreId]);

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
    const t2 = title.trim();
    const b2 = body.trim();
    if (!t2 || !b2 || submitting) return;
    setSubmitting(true);
    setError(null);
    const matched = centres.find((c) => c.id === centreId);
    api
      .createDiscussion({
        centre_id: matched ? matched.id : null,
        centre_name: matched ? matched.name : null,
        title: t2,
        body: b2,
        test_type: testType || null,
        post_type: postType || null,
        route_line_id: routeLineId ? Number(routeLineId) : null,
      })
      .then((res) => (image ? api.uploadDiscussionImage(res.post_id, image).catch(() => {}) : null))
      .then(() => onPosted())
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-panel composer-panel" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>{t("createPost")}</h2>

        <label>{t("centreOptional")}</label>
        <select value={centreId} onChange={(e) => { setCentreId(e.target.value); setRouteLineId(""); }}>
          <option value="">{t("generalNotCentreSpecific")}</option>
          {centres.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>

        <div className="composer-panel__row">
          <div>
            <label>{t("testType")}</label>
            <select value={testType} onChange={(e) => setTestType(e.target.value)}>
              <option value="">{t("general")}</option>
              <option value="G">G</option>
              <option value="G2">G2</option>
            </select>
          </div>
          <div>
            <label>{t("postType")}</label>
            <select value={postType} onChange={(e) => setPostType(e.target.value)}>
              <option value="">—</option>
              <option value="question">{t("postTypeQuestion")}</option>
              <option value="experience">{t("postTypeExperience")}</option>
              <option value="tip">{t("postTypeTip")}</option>
            </select>
          </div>
        </div>

        {centreId && routeOptions.length > 0 && (
          <>
            <label>{t("routeOptional")}</label>
            <select value={routeLineId} onChange={(e) => setRouteLineId(e.target.value)}>
              <option value="">{t("noneOption")}</option>
              {routeOptions.map((r) => (
                <option key={r.id} value={r.id}>{r.label}</option>
              ))}
            </select>
          </>
        )}

        <label>{t("title")}</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("titlePlaceholder")} />

        <label>{t("body")}</label>
        <MentionField value={body} onChange={setBody} placeholder={t("bodyPlaceholder")} rows={4} style={{ width: "100%", marginTop: 4, minHeight: 90 }} />

        <label>{t("addPhoto")}</label>
        <input type="file" accept="image/*" onChange={pickImage} style={{ marginTop: 4, fontSize: "0.85em", border: "none", padding: 0 }} />

        {error && <p style={{ color: "var(--danger)", fontSize: "0.85em" }}>{error}</p>}

        <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", marginTop: 12 }}>
          {t("postConsent")}{" "}
          <a href="/terms-of-service.html" target="_blank" rel="noopener">{t("terms")}</a>.
        </p>

        <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
          <button className="btn-primary" onClick={submit} disabled={!title.trim() || !body.trim() || submitting}>
            {submitting ? t("loading") : t("postToDiscussion")}
          </button>
          <button onClick={onClose}>{t("cancel")}</button>
        </div>
      </div>
    </div>
  );
}

function PostDetail({ post, username, isAdmin, onBack, onChanged }) {
  const { t } = useLang();

  function vote(value) {
    const next = post.my_vote === value ? 0 : value;
    api.voteDiscussion(post.id, next).then(onChanged).catch(() => {});
  }

  const preview = (post.body || "").slice(0, 180);
  useSeo({
    title: `${post.title} — OntarioDriveTestMap Discussion`,
    description: preview || `Discussion post${post.centre_name ? ` about ${post.centre_name}` : ""} on OntarioDriveTestMap.`,
    path: `/discussion.html?post=${post.id}`,
    ogType: "article",
    breadcrumbJsonLd: breadcrumbList([
      { name: t("discussion"), path: "/discussion.html" },
      ...(post.centre_name ? [{ name: post.centre_name, path: `/discussion.html?centre=${encodeURIComponent(post.centre_id || "")}` }] : []),
      { name: post.title },
    ]),
    jsonLd: {
      "@context": "https://schema.org",
      "@type": "DiscussionForumPosting",
      headline: post.title,
      text: post.body || "",
      url: `${SITE_URL}/discussion.html?post=${post.id}`,
      datePublished: post.created_at,
      author: { "@type": "Person", name: post.author },
      commentCount: post.comment_count || 0,
      interactionStatistic: {
        "@type": "InteractionCounter",
        interactionType: "https://schema.org/LikeAction",
        userInteractionCount: post.score || 0,
      },
    },
  });

  return (
    <div>
      <a className="post-detail__back link-btn" href="?" onClick={(e) => { e.preventDefault(); onBack(); }}>
        {t("backToDiscussion")}
      </a>
      <Breadcrumbs
        items={[
          { name: t("discussion"), href: "/discussion.html", onClick: (e) => { e.preventDefault(); onBack(); } },
          { name: post.title },
        ]}
      />
      <Badges post={post} onFilterCentre={() => {}} onFilterType={() => {}} />
      <h2 className="post-detail__title">{post.title}</h2>
      <div className="post-card__meta">
        <b style={{ color: "var(--ink)" }}>{post.author}</b> · {timeAgo(post.created_at)}
      </div>
      <div style={{ display: "inline-flex" }}>
        <VoteRail score={post.score} myVote={post.my_vote} onVote={vote} />
      </div>
      <p style={{ marginTop: 12 }}>
        <MentionText text={post.body} />
      </p>
      {post.image_id && (
        <img src={api.discussionImageUrl(post.image_id)} crossOrigin="use-credentials" alt={`Photo attached to "${post.title}"`} className="post-detail__image" />
      )}
      <h3 style={{ marginTop: 24 }}>{post.comment_count} {t("comments")}</h3>
      <CommentThread postId={post.id} username={username} isAdmin={isAdmin} onCommented={onChanged} />
    </div>
  );
}

export default function DiscussionSection({ centres, username, isAdmin }) {
  const { t } = useLang();
  const initialParams = useMemo(getParams, []);
  const [posts, setPosts] = useState(null);
  const [error, setError] = useState(null);
  const [centreFilter, setCentreFilter] = useState(initialParams.get("centre") || "");
  const [typeFilter, setTypeFilter] = useState(initialParams.get("tt") || "");
  const [categoryFilter, setCategoryFilter] = useState(initialParams.get("pt") || "");
  const [sort, setSort] = useState(initialParams.get("sort") || "newest");
  const [openPostId, setOpenPostId] = useState(initialParams.get("post") ? Number(initialParams.get("post")) : null);
  const [openPost, setOpenPost] = useState(null);
  const [composerOpen, setComposerOpen] = useState(initialParams.get("compose") === "1");
  const [toast, setToast] = useState(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  function load() {
    setError(null);
    api
      .getDiscussions({ centreId: centreFilter || null, testType: typeFilter || null, postType: categoryFilter || null, sort })
      .then(setPosts)
      .catch((err) => setError(err.message));
  }

  useEffect(load, [centreFilter, typeFilter, categoryFilter, sort]);

  useEffect(() => {
    setParams({ centre: centreFilter, tt: typeFilter, pt: categoryFilter, sort: sort === "newest" ? null : sort });
  }, [centreFilter, typeFilter, categoryFilter, sort]);

  useEffect(() => {
    if (!openPostId) {
      setOpenPost(null);
      return;
    }
    setParams({ post: openPostId });
    // The list endpoint already carries everything the detail view needs;
    // avoid a second endpoint for a single post.
    api.getDiscussions({}).then((all) => setOpenPost(all.find((p) => p.id === openPostId) || null));
  }, [openPostId]);

  function reloadOpenPost() {
    if (!openPostId) return;
    api.getDiscussions({}).then((all) => setOpenPost(all.find((p) => p.id === openPostId) || null));
  }

  function openPostById(id) {
    setOpenPostId(id);
  }

  function backToFeed() {
    setOpenPostId(null);
    setParams({ post: null });
    load();
  }

  function clearFilters() {
    setCentreFilter("");
    setTypeFilter("");
    setCategoryFilter("");
  }

  const anyFilterActive = !!(centreFilter || typeFilter || categoryFilter);

  useSeo(
    !openPostId
      ? {
          title: centreFilter
            ? `${centres.find((c) => c.id === centreFilter)?.name || centreFilter} Discussion — OntarioDriveTestMap`
            : "Discussion — OntarioDriveTestMap",
          description: t("discussionSub"),
          path: centreFilter ? `/discussion.html?centre=${encodeURIComponent(centreFilter)}` : "/discussion.html",
        }
      : undefined
  );

  if (openPostId) {
    return (
      <div className="discussion-shell">
        {openPost === null ? <p>{t("loading")}</p> : <PostDetail post={openPost} username={username} isAdmin={isAdmin} onBack={backToFeed} onChanged={reloadOpenPost} />}
      </div>
    );
  }

  const activeFilterCount = [centreFilter, typeFilter, categoryFilter].filter(Boolean).length;

  return (
    <div className="discussion-shell">
      <div>
        <div className="discussion-header">
          <div>
            <h1>{t("discussionTitle")}</h1>
            <p>{t("discussionSub")}</p>
          </div>
          <button type="button" className="btn-primary" onClick={() => setComposerOpen(true)}>
            {t("createPost")}
          </button>
        </div>

        <div className={`filter-toolbar${filtersOpen ? " filter-toolbar--open" : ""}`}>
          <div className="filter-toolbar__primary">
            <div className="seg" role="group" aria-label={t("testClass")}>
              <button type="button" aria-pressed={typeFilter === ""} onClick={() => setTypeFilter("")}>{t("filterAll")}</button>
              <button type="button" aria-pressed={typeFilter === "G"} onClick={() => setTypeFilter("G")}>G</button>
              <button type="button" aria-pressed={typeFilter === "G2"} onClick={() => setTypeFilter("G2")}>G2</button>
            </div>
            <label className="filter-toolbar__sort">
              <span className="visually-hidden">{t("sortLabel")}</span>
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="newest">{t("sortNewest")}</option>
                <option value="top">{t("sortTop")}</option>
                <option value="discussed">{t("sortDiscussed")}</option>
              </select>
            </label>
            <button
              type="button"
              className="filter-toolbar__more"
              aria-expanded={filtersOpen}
              aria-controls="more-filters"
              onClick={() => setFiltersOpen((o) => !o)}
            >
              {t("filters")}
              {activeFilterCount > 0 && <span className="count-dot">{activeFilterCount}</span>}
            </button>
          </div>
          <div className="filter-toolbar__secondary" id="more-filters">
            <div className="seg" role="group" aria-label={t("category")}>
              <button type="button" aria-pressed={categoryFilter === ""} onClick={() => setCategoryFilter("")}>{t("allCategories")}</button>
              <button type="button" aria-pressed={categoryFilter === "question"} onClick={() => setCategoryFilter("question")}>{t("filterQuestions")}</button>
              <button type="button" aria-pressed={categoryFilter === "experience"} onClick={() => setCategoryFilter("experience")}>{t("filterExperiences")}</button>
              <button type="button" aria-pressed={categoryFilter === "tip"} onClick={() => setCategoryFilter("tip")}>{t("filterTips")}</button>
            </div>
            <label className="filter-toolbar__centre">
              <span className="visually-hidden">{t("centre")}</span>
              <select value={centreFilter} onChange={(e) => setCentreFilter(e.target.value)}>
                <option value="">{t("allCentres")}</option>
                {centres.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </label>
            {anyFilterActive && (
              <button type="button" className="link-btn" onClick={clearFilters}>
                {t("clearFilters")}
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="state-panel state-panel--error" role="alert">
            <p>{t("postsLoadFailed")}</p>
            <button type="button" onClick={load}>{t("retry")}</button>
          </div>
        )}

        {!error && posts === null && (
          <div aria-busy="true">
            <div className="skeleton-block" />
            <div className="skeleton-block" />
          </div>
        )}

        {!error && posts && posts.length === 0 && anyFilterActive && (
          <div className="empty-state">
            <h2>{t("emptyFilteredTitle")}</h2>
            <p>{t("emptyFilteredBody")}</p>
            <button type="button" onClick={clearFilters}>{t("clearFilters")}</button>
          </div>
        )}
        {!error && posts && posts.length === 0 && !anyFilterActive && (
          <div className="empty-state">
            <h2>{t("emptyBoardTitle")}</h2>
            <p>{t("emptyBoardBody")}</p>
            {username && <button type="button" className="btn-primary" onClick={() => setComposerOpen(true)}>{t("askFirstQuestion")}</button>}
          </div>
        )}

        {!error && posts && posts.length > 0 && (
          <div className="post-feed">
            {posts.map((p, i) => (
              <PostCard
                key={p.id}
                post={p}
                username={username}
                isAdmin={isAdmin}
                onChanged={load}
                onOpen={openPostById}
                onFilterCentre={setCentreFilter}
                onFilterType={setTypeFilter}
                index={i}
              />
            ))}
            <p className="post-feed__guidelines">
              {t("guidelinesShort")} <a href="/terms-of-service.html">{t("communityGuidelines")}</a>
            </p>
          </div>
        )}
      </div>

      {composerOpen && username && (
        <Composer
          centres={centres}
          username={username}
          initial={{
            centreId: initialParams.get("centre") || "",
            testType: initialParams.get("type") || "",
            routeLineId: initialParams.get("route") || "",
          }}
          onClose={() => setComposerOpen(false)}
          onPosted={() => {
            setComposerOpen(false);
            setToast(t("postPublished"));
            load();
          }}
        />
      )}
      {composerOpen && !username && (
        <div className="modal-backdrop" onClick={() => setComposerOpen(false)}>
          <div className="modal-panel" style={{ padding: "var(--space-lg)" }} onClick={(e) => e.stopPropagation()}>
            <p>{t("needUsernameToPost")}</p>
            <button onClick={() => setComposerOpen(false)}>{t("cancel")}</button>
          </div>
        </div>
      )}

      {toast && <Toast text={toast} onDone={() => setToast(null)} />}
    </div>
  );
}
