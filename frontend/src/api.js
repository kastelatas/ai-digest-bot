// Тонкая обёртка над fetch: cookie-сессия, JSON, единый формат ошибок.
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

let onUnauthorized = () => {};
export const setUnauthorizedHandler = (fn) => { onUnauthorized = fn; };

async function request(method, url, body) {
  const res = await fetch(url, {
    method,
    credentials: 'same-origin',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch { /* пустое тело */ }
  if (!res.ok) {
    if (res.status === 401 && url !== '/api/login') onUnauthorized();
    const detail = data && data.detail;
    throw new ApiError(res.status, typeof detail === 'string' ? detail : `Ошибка ${res.status}`);
  }
  return data;
}

const qs = (params) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') p.set(k, v); });
  const s = p.toString();
  return s ? `?${s}` : '';
};

export const api = {
  me: () => request('GET', '/api/me'),
  login: (password) => request('POST', '/api/login', { password }),
  logout: () => request('POST', '/api/logout', {}),
  overview: (days) => request('GET', `/api/overview${qs({ days })}`),
  subscribers: (days) => request('GET', `/api/subscribers${qs({ days })}`),
  postsDaily: (days) => request('GET', `/api/posts/daily${qs({ days })}`),
  posts: (params) => request('GET', `/api/posts${qs(params)}`),
  createPost: (text, mode) => request('POST', '/api/posts', { text, mode }),
  updatePost: (id, patch) => request('PATCH', `/api/posts/${id}`, patch),
  publishPost: (id) => request('POST', `/api/posts/${id}/publish`, {}),
  deletePost: (id, force = false) => request('DELETE', `/api/posts/${id}${qs({ force: force ? 'true' : undefined })}`),
};
