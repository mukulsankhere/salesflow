import { useQuery } from '@tanstack/react-query'

export class ApiError extends Error {
  code: string
  status: number
  details?: unknown
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

let token: string | null = localStorage.getItem('sf_token')
export function setToken(t: string | null) {
  token = t
  if (t) localStorage.setItem('sf_token', t)
  else localStorage.removeItem('sf_token')
}
export function getToken() {
  return token
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api/v1${path}`, {
    method: opts.method || 'GET',
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (res.status === 204) return undefined as T
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } })?.error || {}
    if (res.status === 401) {
      setToken(null)
      window.location.href = '/login'
    }
    throw new ApiError(res.status, err.code || 'ERROR', err.message || `Request failed (${res.status})`, err.details)
  }
  return data as T
}

export function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  }
  const s = u.toString()
  return s ? `?${s}` : ''
}

/** Query helper that merges the global filter (period + store) into params. */
export function useFilteredQuery<T>(key: unknown[], path: string, extra?: Record<string, unknown>) {
  return useQuery<T>({
    queryKey: [...key, path, extra],
    queryFn: async () => {
      const params = new URLSearchParams(path.split('?')[1] || '')
      const base = path.split('?')[0]
      // filter params are injected by callers via extra; here we just fetch
      void params
      return api<T>(base + qs((extra || {}) as Record<string, string>))
    },
  })
}
