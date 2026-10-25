const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

function getAdminToken() {
  try { return window.sessionStorage.getItem('nexus_admin_token') || '' } catch { return '' }
}

function setAdminToken(value) {
  try {
    const token = (value || '').trim()
    if (token) window.sessionStorage.setItem('nexus_admin_token', token)
    else window.sessionStorage.removeItem('nexus_admin_token')
    return true
  } catch { return false }
}

async function request(path, options = {}) {
  const { headers: customHeaders = {}, ...fetchOptions } = options
  const isForm = typeof FormData !== 'undefined' && fetchOptions.body instanceof FormData
  const headers = {
    ...(isForm ? {} : { 'Content-Type': 'application/json' }),
    ...(getAdminToken() ? { 'X-Nexus-Admin-Token': getAdminToken() } : {}),
    ...customHeaders,
  }
  const res = await fetch(`${API}${path}`, { ...fetchOptions, headers })
  if (!res.ok) {
    const text = await res.text()
    let parsed = null
    try { parsed = JSON.parse(text) } catch {}
    const detail = parsed?.detail
    const message = (typeof detail === 'string' ? detail : detail?.message) || parsed?.message || `Request failed (${res.status})`
    const err = new Error(message)
    err.status = res.status
    err.data = parsed
    err.retryAfter = res.headers.get('Retry-After')
    throw err
  }
  return res.json()
}

export const api = {
  get: (path) => request(path),
  ask: (query) => request('/api/chat', { method: 'POST', body: JSON.stringify({ query }) }),
  index: (files) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return request('/api/index', { method: 'POST', body: form })
  },
  health: () => request('/healthz'),
  stats: () => request('/api/stats'),
  getProjects: () => request('/api/projects'),
  addProject: (name) => request('/api/projects', { method: 'POST', body: JSON.stringify({ name }) }),
  deleteProject: (index) => request(`/api/projects/${index}`, { method: 'DELETE' }),
  getWorkflows: () => request('/api/workflows'),
  addWorkflow: (name) => request('/api/workflows', { method: 'POST', body: JSON.stringify({ name }) }),
  deleteWorkflow: (index) => request(`/api/workflows/${index}`, { method: 'DELETE' }),
  getSettings: () => request('/api/settings'),
  updateSettings: (settings) => request('/api/settings', { method: 'POST', body: JSON.stringify(settings) }),
  getDocuments: () => request('/api/documents'),
  deleteDocument: (name) => request(`/api/documents/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  seedDemo: () => request('/api/seed_demo', { method: 'POST' }),
  getMcpTools: () => request('/api/tools/mcp'),
  runMcpTool: (tool, args = {}) => request('/api/tools/mcp/run', { method: 'POST', body: JSON.stringify({ tool, arguments: args }) }),
  getAdminToken,
  setAdminToken,
}

export { API }
