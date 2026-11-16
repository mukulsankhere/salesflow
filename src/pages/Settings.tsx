import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, ShieldCheck, Database, Users } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { fmtDateTime } from '../lib/format'
import { Card, Tabs, Spinner, Row, ErrorBox, SectionTitle } from '../components/ui'

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
    queryFn: () => api<{ integrations: { name: string; kind: string; status: string; note: string }[] }>('/integrations/health'),
    enabled: tab === 'integrations',
  })
  const { data: audit } = useQuery({
    queryKey: ['audit'],
    queryFn: () => api<{ audit: { _id: string; who: string; action: string; entity: string; before: unknown; after: unknown; at: string }[] }>('/audit?limit=60'),
    enabled: tab === 'audit',
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ user: { roles: string[]; store_ids: string[] }; stores: { _id: string; name: string }[] }>('/auth/me'),
    enabled: tab === 'users',
  })

  const saveRule = useMutation({
    mutationFn: ({ rule_id, value }: { rule_id: string; value: unknown }) => api('/org/rules', { method: 'PUT', body: { rule_id, value } }),
    onSuccess: () => void qc.invalidateQueries(),
    onError: (e) => alert((e as ApiError).message),
  })
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  if (isLoading) return <Spinner />

  return (
    <div>
      <Tabs active={tab} onChange={setTab} tabs={[
        { key: 'rules', label: 'Business Rules' },
        { key: 'integrations', label: 'Integrations' },
        { key: 'users', label: 'Users & Scope' },
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
                    <button className="btn-primary btn-sm" disabled={drafts[id] === undefined || drafts[id] === String(r.value)}
                      onClick={() => saveRule.mutate({ rule_id: id, value: Number(drafts[id]) })}><Save size={12} /></button>
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
            <Card key={i.name} className="p-4">
              <div className="flex items-center justify-between">
                <span className="font-bold text-sm flex items-center gap-2">
                  {i.kind === 'database' || i.kind === 'queue' ? <Database size={14} className="text-brand-600" /> : <ShieldCheck size={14} className="text-brand-600" />}
                  {i.name}
                </span>
                <span className={`chip ${i.status === 'live' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : i.status === 'demo' ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-sky-50 text-sky-700 border-sky-200'}`}>{i.status}</span>
              </div>
              <div className="text-xs text-slate-500 mt-1.5">{i.note}</div>
            </Card>
          ))}
        </div>
      )}

      {tab === 'users' && (
        <Card className="p-4 max-w-3xl">
          <SectionTitle title="Role Store Scoping (AUTH-03)" subtitle="Managers, Sales and Staff see only assigned stores" />
          {me?.stores.map((s) => <Row key={s._id} label={s.name}>{me.user.store_ids.includes(s._id) ? 'Visible To Your Role' : 'Admin Only'}</Row>)}
          <div className="text-xs text-slate-400 mt-3 flex items-center gap-1"><Users size={12} /> Manage users, roles and data scope here in production (ADM-04).</div>
        </Card>
      )}

      {tab === 'audit' && (
        <Card className="overflow-x-auto">
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
