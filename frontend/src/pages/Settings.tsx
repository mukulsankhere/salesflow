import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, ShieldCheck, Database, RefreshCcw, Lock } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { toast } from '../lib/toast'
import { fmtDateTime, timeAgo } from '../lib/format'
import { Card, Tabs, Spinner, Row, ErrorBox, SectionTitle, BtnSpinner } from '../components/ui'

interface UserRow {
  _id: string; name: string; email: string; phone?: string
  roles: string[]; store_ids: string[]; status: string
  created_at?: string; last_login?: string | null
}

const ROLE_LABELS: Record<string, string> = {
  admin: 'Admin', manager: 'Manager', sales: 'Sales', staff: 'Staff', marketing: 'Marketing', readonly: 'Read Only',
}
const ROLE_COLORS: Record<string, string> = {
  admin: 'bg-brand-100 text-brand-700 border-brand-200',
  manager: 'bg-sky-100 text-sky-700 border-sky-200',
  sales: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  staff: 'bg-amber-100 text-amber-700 border-amber-200',
  marketing: 'bg-violet-100 text-violet-700 border-violet-200',
  readonly: 'bg-slate-100 text-slate-600 border-slate-200',
}

export default function Settings() {
  const [tab, setTab] = useState('rules')
  const qc = useQueryClient()
  const { data: org, isLoading } = useQuery({
    queryKey: ['org-settings'],
    queryFn: () => api<{ org: { name: string; city: string }; rules: Record<string, { rule: string; value: unknown; unit: string }> }>('/org/settings'),
  })
  const { data: history } = useQuery({
    queryKey: ['rule-history'],
    queryFn: () => api<{ history: { rule_id: string; value: unknown; at: string; by: string }[] }>('/org/rules/history'),
    enabled: tab === 'rules',
  })
  const { data: integrations } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api<{ integrations: {
      _id: string; name: string; kind: string; status: string; note: string
      last_sync?: string | null; retries?: number
      issues?: { code: string; detail: string; count: number; since: string }[]
    }[] }>('/integrations/health'),
    enabled: tab === 'integrations',
  })
  const retryIntegration = useMutation({
    mutationFn: (int_id: string) => api<{ ok: boolean; message: string }>(`/integrations/${encodeURIComponent(int_id)}/retry`, { method: 'POST' }),
    onSuccess: (r) => {
      void qc.invalidateQueries()
      if (r.ok) toast.success(r.message)
      else toast.error(r.message)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const [auditAction, setAuditAction] = useState('')
  const [auditWho, setAuditWho] = useState('')
  const { data: auditMeta } = useQuery({
    queryKey: ['audit-filters'],
    queryFn: () => api<{ actions: string[]; users: string[] }>('/audit/filters'),
    enabled: tab === 'audit',
  })
  const { data: audit } = useQuery({
    queryKey: ['audit', auditAction, auditWho],
    queryFn: () => api<{ audit: { _id: string; who: string; action: string; entity: string; before: unknown; after: unknown; at: string }[] }>(
      `/audit?limit=60${auditAction ? `&action=${encodeURIComponent(auditAction)}` : ''}${auditWho ? `&who=${encodeURIComponent(auditWho)}` : ''}`),
    enabled: tab === 'audit',
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ user: { _id: string; roles: string[]; store_ids: string[] }; stores: { _id: string; name: string }[] }>('/auth/me'),
    enabled: tab === 'users',
  })
  const { data: users, isLoading: usersLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api<{ users: UserRow[] }>('/auth/users'),
    enabled: tab === 'users',
  })

  const saveRule = useMutation({
    mutationFn: ({ rule_id, value }: { rule_id: string; value: unknown }) => api('/org/rules', { method: 'PUT', body: { rule_id, value } }),
    onSuccess: (_r, v) => {
      setDrafts((d) => { const n = { ...d }; delete n[v.rule_id]; return n })
      void qc.invalidateQueries()
      toast.success(`Rule ${v.rule_id} saved - every section using it recalculates immediately`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  if (isLoading) return <Spinner />

  return (
    <div>
      <Tabs active={tab} onChange={setTab} tabs={[
        { key: 'rules', label: 'Business Rules' },
        { key: 'integrations', label: 'Integrations' },
        { key: 'users', label: 'Admin Access' },
        { key: 'audit', label: 'Audit Log' },
      ]} />

      {tab === 'rules' && (
        <div className="space-y-2 max-w-4xl">
          {Object.entries(org?.rules || {}).map(([id, r]) => (
            <Card key={id} className="p-3">
              <div className="flex flex-wrap items-center gap-3">
                <span className="chip bg-brand-50 text-brand-700 border-brand-200 w-20 justify-center">{id}</span>
                <span className="flex-1 text-sm text-slate-600 min-w-64">{r.rule}</span>
                {typeof r.value === 'number' ? (
                  <div className="flex items-center gap-2">
                    <input type="number" className="input w-24" value={drafts[id] ?? String(r.value)}
                      onChange={(e) => setDrafts({ ...drafts, [id]: e.target.value })} />
                    <span className="text-xs text-slate-400">{r.unit}</span>
                    <button className="btn-primary btn-sm" disabled={drafts[id] === undefined || drafts[id] === String(r.value) || (saveRule.isPending && saveRule.variables?.rule_id === id)}
                      onClick={() => saveRule.mutate({ rule_id: id, value: Number(drafts[id]) })}>
                      {saveRule.isPending && saveRule.variables?.rule_id === id ? <BtnSpinner /> : <Save size={12} />}
                    </button>
                  </div>
                ) : typeof r.value === 'string' ? (
                  <span className="text-sm font-semibold">{r.value}</span>
                ) : (
                  <code className="text-[11px] bg-slate-50 rounded px-2 py-1 max-w-md overflow-x-auto">{JSON.stringify(r.value)}</code>
                )}
              </div>
            </Card>
          ))}
          {!!history?.history.length && (
            <Card className="p-3">
              <div className="text-sm font-bold mb-2">Change History</div>
              {history.history.slice(-10).reverse().map((h, i) => (
                <div key={i} className="text-xs py-1 border-b border-slate-50 flex justify-between">
                  <span className="text-slate-500">{h.rule_id} → {JSON.stringify(h.value)} · {h.by}</span>
                  <span className="text-slate-400">{fmtDateTime(h.at)}</span>
                </div>
              ))}
            </Card>
          )}
        </div>
      )}

      {tab === 'integrations' && (
        <div className="grid sm:grid-cols-2 gap-3 max-w-4xl">
          {(integrations?.integrations || []).map((i) => (
            <Card key={i._id || i.name} className="p-4">
              <div className="flex items-center justify-between">
                <span className="font-bold text-sm flex items-center gap-2">
                  {i.kind === 'database' || i.kind === 'queue' ? <Database size={14} className="text-brand-600" /> : <ShieldCheck size={14} className="text-brand-600" />}
                  {i.name}
                </span>
                <span className={`chip ${i.status === 'live' ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : i.status === 'syncing' ? 'bg-amber-50 text-amber-700 border-amber-200 animate-pulse'
                    : i.status === 'error' ? 'bg-rose-50 text-rose-700 border-rose-200'
                      : 'bg-sky-50 text-sky-700 border-sky-200'}`}>{i.status}</span>
              </div>
              <div className="text-xs text-slate-500 mt-1.5">{i.note}</div>
              <div className="text-[11px] text-slate-400 mt-1.5 flex justify-between">
                <span>Last sync: {i.last_sync ? timeAgo(i.last_sync) : 'never'}</span>
                {!!i.retries && <span>retries: {i.retries}</span>}
              </div>
              {!!i.issues?.length && (
                <div className="mt-2 space-y-1">
                  {i.issues.map((iss, k) => (
                    <div key={k} className="bg-rose-50 border border-rose-200 rounded px-2 py-1 text-[11px] text-rose-700">
                      <b>{iss.code}</b> · {iss.detail} · {iss.count} events · since {fmtDateTime(iss.since)}
                    </div>
                  ))}
                </div>
              )}
              {i.status !== 'live' && i.status !== 'fallback' && (
                <button className="btn-ghost btn-sm mt-2" disabled={retryIntegration.isPending}
                  onClick={() => retryIntegration.mutate(i._id)}>
                  {retryIntegration.isPending && retryIntegration.variables === i._id
                    ? <><BtnSpinner /> Retrying…</> : <><RefreshCcw size={12} /> Retry Sync</>}
                </button>
              )}
            </Card>
          ))}
        </div>
      )}

      {tab === 'users' && (
        <div className="space-y-3 max-w-4xl">
          <Card className="p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h2 className="text-base font-bold text-slate-800">Single Admin Account</h2>
                <p className="text-xs text-slate-500">This deployment has exactly one sign-in - yours. Employees are staff records, not logins.</p>
              </div>
              <span className="chip border-brand-200 bg-brand-50 text-brand-700"><Lock size={12} /> 1 login</span>
            </div>
            <div className="mt-3 space-y-1">
              <Row label="Your account">{me?.user.roles.includes('admin') ? 'Ravi Menon (Admin) · admin@salesflow.app' : 'Admin'}</Row>
              <Row label="Employee sign-ins">Disabled - nobody else can log in</Row>
              <Row label="Password">Demo@123 - change it in production before going live</Row>
            </div>
          </Card>
          <Card className="p-4">
            <SectionTitle title="Team Directory" subtitle="Your team members, visible for assignment and rostering - they cannot sign in" />
            {usersLoading ? <Spinner /> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px]">
                  <thead><tr className="bg-slate-50"><th className="th">Name</th><th className="th">Email</th><th className="th">Role</th><th className="th">Store Scope</th><th className="th">Sign-In</th></tr></thead>
                  <tbody>
                    {(users?.users || []).map((u) => (
                      <tr key={u._id} className="hover:bg-slate-50">
                        <td className="td font-semibold">{u.name}</td>
                        <td className="td text-sm text-slate-600">{u.email}</td>
                        <td className="td"><span className="flex gap-1 flex-wrap">{u.roles.map((r) => (
                          <span key={r} className={`chip ${ROLE_COLORS[r] || 'bg-slate-100 border-slate-200 text-slate-600'}`}>{ROLE_LABELS[r] || r}</span>
                        ))}</span></td>
                        <td className="td text-xs text-slate-500 max-w-44">
                          {u.store_ids.length ? u.store_ids.map((id) => me?.stores.find((s) => s._id === id)?.name || id).join(', ') : 'All stores'}
                        </td>
                        <td className="td">{u.roles.includes('admin')
                          ? <span className="chip bg-emerald-50 text-emerald-700 border-emerald-200">Active</span>
                          : <span className="chip bg-slate-100 text-slate-500 border-slate-200">Disabled</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <Card className="p-4">
            <SectionTitle title="Role Store Scoping (AUTH-03)" subtitle="Applied to assignment routing and AI call routing" />
            {me?.stores.map((s) => <Row key={s._id} label={s.name}>{me.user.store_ids.includes(s._id) ? 'Visible To Your Role' : 'Admin Only'}</Row>)}
          </Card>
        </div>
      )}

      {tab === 'audit' && (
        <Card className="overflow-x-auto">
          <div className="flex flex-wrap gap-2 p-3 border-b border-slate-100">
            <select className="select w-56" value={auditAction} onChange={(e) => setAuditAction(e.target.value)}>
              <option value="">All Actions</option>
              {(auditMeta?.actions || []).map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select className="select w-56" value={auditWho} onChange={(e) => setAuditWho(e.target.value)}>
              <option value="">All Users</option>
              {(auditMeta?.users || []).map((u) => <option key={u} value={u}>{u}</option>)}
            </select>
            <span className="text-xs text-slate-400 self-center">{audit?.audit.length ?? 0} entries</span>
          </div>
          <table className="w-full min-w-[700px]">
            <thead><tr className="bg-slate-50"><th className="th">When</th><th className="th">Who</th><th className="th">Action</th><th className="th">Entity</th><th className="th">Change</th></tr></thead>
            <tbody>
              {(audit?.audit || []).map((a) => (
                <tr key={a._id}>
                  <td className="td text-xs text-slate-400">{fmtDateTime(a.at)}</td>
                  <td className="td text-sm">{a.who}</td>
                  <td className="td text-xs font-mono">{a.action}</td>
                  <td className="td text-xs">{a.entity}</td>
                  <td className="td text-[11px] text-slate-500 max-w-72 truncate">{JSON.stringify(a.after || a.before || {})}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  )
}
