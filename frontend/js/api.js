// Thin client for the FastAPI backend (app.py). GETs are cached per page load; POSTs clear the cache.
const cache = new Map();

async function req(method, path, body) {
  const r = await fetch(path, {
    method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) {
    const err = new Error((data && (data.refused || data.detail)) || `${r.status} ${r.statusText}`);
    err.status = r.status; err.refused = data && data.refused;
    throw err;
  }
  return data;
}

export const api = {
  get(path, { fresh = false } = {}) {
    if (!fresh && cache.has(path)) return cache.get(path);
    const p = req("GET", path).catch((e) => { cache.delete(path); throw e; });
    cache.set(path, p);
    return p;
  },
  async post(path, body) { cache.clear(); return req("POST", path, body || {}); },
  clear() { cache.clear(); },
};
