const API_URL = import.meta.env.VITE_API_URL;

async function request(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    credentials: "include", // required to send/receive the httpOnly session cookie
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

function uploadImage(path, file) {
  const form = new FormData();
  form.append("file", file);
  return fetch(`${API_URL}${path}`, {
    method: "POST",
    credentials: "include",
    body: form, // no Content-Type header -- the browser sets the multipart boundary itself
  }).then(async (res) => {
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed: ${res.status}`);
    }
    return res.json();
  });
}

export const api = {
  loginWithGoogle: (credential) =>
    request("/auth/google", {
      method: "POST",
      body: JSON.stringify({ credential }),
    }),
  logout: () => request("/auth/logout", { method: "POST" }),
  deleteAccount: () => request("/account/delete", { method: "POST" }),
  me: () => request("/auth/me"),
  setDigestOptIn: (optIn) =>
    request("/account/digest-opt-in", {
      method: "PATCH",
      body: JSON.stringify({ opt_in: optIn }),
    }),
  // Fire-and-forget: a page view failing to log must never surface to
  // the user or block navigation. No credentials/cookie needed -- the
  // page_views table records path+timestamp only, nothing tied to who
  // was looking.
  recordPageView: (path) => {
    fetch(`${API_URL}/analytics/pageview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }).catch(() => {});
  },
  getCentres: () => request("/centres"),
  getCentre: (id) => request(`/centres/${id}`),
  getMap: (id) => request(`/centres/${id}/map`),
  getTraces: (id) => request(`/centres/${id}/traces`),
  getTrace: (id) => request(`/traces/${id}`),
  compareCentres: () => request(`/centres/compare`),
  reportError: (routeLineId, stepOrder, note) =>
    request(`/report-error`, {
      method: "POST",
      body: JSON.stringify({ route_line_id: routeLineId, step_order: stepOrder, note }),
    }),
  submitRoute: (centreId, centreName, testClass, turns) =>
    request(`/submissions`, {
      method: "POST",
      body: JSON.stringify({
        centre_id: centreId,
        centre_name: centreName,
        test_class: testClass,
        turns,
      }),
    }),
  getSubmissions: () => request(`/submissions`),
  validateStreet: (street) =>
    request(`/submissions/validate-street`, {
      method: "POST",
      body: JSON.stringify({ street }),
    }),
  validatePair: (streetA, streetB) =>
    request(`/submissions/validate-pair`, {
      method: "POST",
      body: JSON.stringify({ street_a: streetA, street_b: streetB }),
    }),
  getForumPosts: (centreId) =>
    request(`/forum${centreId ? `?centre_id=${encodeURIComponent(centreId)}` : ""}`),
  createForumPost: (post) =>
    request(`/forum`, { method: "POST", body: JSON.stringify(post) }),
  voteForumPost: (postId, value) =>
    request(`/forum/${postId}/vote`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),
  reportForumPost: (postId) => request(`/forum/${postId}/report`, { method: "POST" }),
  getForumComments: (postId) => request(`/forum/${postId}/comments`),
  addForumComment: (postId, body) =>
    request(`/forum/${postId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body }),
    }),
  deleteForumPost: (postId) => request(`/forum/${postId}`, { method: "DELETE" }),
  deleteForumComment: (commentId) =>
    request(`/forum/comments/${commentId}`, { method: "DELETE" }),
  uploadForumCommentImage: (commentId, file) => uploadImage(`/forum/comments/${commentId}/image`, file),
  setUsername: (username) =>
    request(`/auth/username`, {
      method: "POST",
      body: JSON.stringify({ username }),
    }),
  searchUsers: (q) => request(`/users/search?q=${encodeURIComponent(q)}`),
  imageUrl: (imageId) => `${API_URL}/forum/image/${imageId}`,
  uploadForumImage: (postId, file) => uploadImage(`/forum/${postId}/image`, file),
  getDiscussions: ({ centreId, testType, postType, sort } = {}) => {
    const params = new URLSearchParams();
    if (centreId) params.set("centre_id", centreId);
    if (testType) params.set("test_type", testType);
    if (postType) params.set("post_type", postType);
    if (sort) params.set("sort", sort);
    const qs = params.toString();
    return request(`/discussions${qs ? `?${qs}` : ""}`);
  },
  createDiscussion: (post) =>
    request(`/discussions`, { method: "POST", body: JSON.stringify(post) }),
  deleteDiscussion: (postId) => request(`/discussions/${postId}`, { method: "DELETE" }),
  voteDiscussion: (postId, value) =>
    request(`/discussions/${postId}/vote`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),
  reportDiscussion: (postId) => request(`/discussions/${postId}/report`, { method: "POST" }),
  getDiscussionComments: (postId, sort = "newest") =>
    request(`/discussions/${postId}/comments?sort=${encodeURIComponent(sort)}`),
  addDiscussionComment: (postId, body, parentCommentId = null) =>
    request(`/discussions/${postId}/comments`, {
      method: "POST",
      body: JSON.stringify({ body, parent_comment_id: parentCommentId }),
    }),
  deleteDiscussionComment: (commentId) =>
    request(`/discussions/comments/${commentId}`, { method: "DELETE" }),
  voteDiscussionComment: (commentId, value) =>
    request(`/discussions/comments/${commentId}/vote`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),
  discussionImageUrl: (imageId) => `${API_URL}/discussions/image/${imageId}`,
  uploadDiscussionImage: (postId, file) => uploadImage(`/discussions/${postId}/image`, file),
  uploadDiscussionCommentImage: (commentId, file) =>
    uploadImage(`/discussions/comments/${commentId}/image`, file),
  getNotifications: () => request(`/notifications`),
  getUnreadNotificationCount: () => request(`/notifications/unread-count`),
  markNotificationRead: (id) => request(`/notifications/${id}/read`, { method: "POST" }),
  markAllNotificationsRead: () => request(`/notifications/read-all`, { method: "POST" }),
  createBookingReminder: (body) =>
    request(`/booking-reminders`, { method: "POST", body: JSON.stringify(body) }),
  getBookingReminders: () => request(`/booking-reminders`),
  deleteBookingReminder: (id) => request(`/booking-reminders/${id}`, { method: "DELETE" }),
};