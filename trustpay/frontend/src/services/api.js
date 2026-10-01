const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000/api/v1').replace(/\/$/, '')
const ACCESS_TOKEN_KEY = 'sharipay.accessToken'
const REFRESH_TOKEN_KEY = 'sharipay.refreshToken'
let refreshPromise = null

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const readError = async response => {
  const body = await response.json().catch(() => null)
  if (Array.isArray(body?.detail)) return body.detail.map(error => error.msg || 'Invalid value').join(', ')
  return body?.detail || `Request failed (${response.status})`
}

async function send(path, { method = 'GET', body, authenticated = false } = {}) {
  const token = sessionStorage.getItem(ACCESS_TOKEN_KEY)
  return fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(authenticated && token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  })
}

async function request(path, options = {}, canRefresh = true) {
  const response = await send(path, options)
  if (response.status === 401 && options.authenticated && canRefresh && path !== '/auth/refresh') {
    const refreshToken = sessionStorage.getItem(REFRESH_TOKEN_KEY)
    if (refreshToken) {
      refreshPromise ||= send('/auth/refresh', { method: 'POST', body: { refresh_token: refreshToken } })
        .then(async refreshResponse => {
          if (!refreshResponse.ok) throw new ApiError(await readError(refreshResponse), refreshResponse.status)
          saveTokens(await refreshResponse.json())
        })
        .finally(() => { refreshPromise = null })
      try {
        await refreshPromise
        return request(path, options, false)
      } catch {}
    }
    sessionStorage.removeItem(ACCESS_TOKEN_KEY)
    sessionStorage.removeItem(REFRESH_TOKEN_KEY)
  }
  if (!response.ok) throw new ApiError(await readError(response), response.status)
  return response.status === 204 ? null : response.json()
}

const saveTokens = tokens => {
  sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token)
  sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token)
}

export const authApi = {
  isAuthenticated: () => Boolean(sessionStorage.getItem(ACCESS_TOKEN_KEY)),
  me: () => request('/auth/me', { authenticated: true }),
  async login(email, password) {
    const tokens = await request('/auth/login', { method: 'POST', body: { email, password } })
    saveTokens(tokens)
    return request('/auth/me', { authenticated: true })
  },
  async register(email, password) {
    await request('/auth/register', { method: 'POST', body: { email, password } })
    return this.login(email, password)
  },
  async logout() {
    const refresh_token = sessionStorage.getItem(REFRESH_TOKEN_KEY)
    try {
      if (refresh_token) await request('/auth/logout', { method: 'POST', body: { refresh_token } })
    } finally {
      sessionStorage.removeItem(ACCESS_TOKEN_KEY)
      sessionStorage.removeItem(REFRESH_TOKEN_KEY)
    }
  },
}

export const paymentsApi = {
  getAccounts: () => request('/accounts', { authenticated: true }),
  getBeneficiaries: () => request('/beneficiaries', { authenticated: true }),
  createBeneficiary: beneficiary => request('/beneficiaries', { method: 'POST', body: beneficiary, authenticated: true }),
  getTransactions: () => request('/payments', { authenticated: true }),
  getPayment: transactionId => request(`/payments/${encodeURIComponent(transactionId)}`, { authenticated: true }),
  getDrunixPayment: transactionId => request(`/payments/${encodeURIComponent(transactionId)}/drunix`, { authenticated: true }),
  getDrunixHistory: transactionId => request(`/payments/${encodeURIComponent(transactionId)}/drunix/history`, { authenticated: true }),
  create: payment => request('/payments', { method: 'POST', body: payment, authenticated: true }),
}
