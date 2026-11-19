import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Eye, Play, Trash2, ScrollText } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { toast } from '../lib/toast'
import { timeAgo } from '../lib/format'
import { Card, Modal, Empty, Spinner, BtnSpinner, ConfirmModal } from './ui'

interface Rule {
  _id: string
  name: string
  type: 'assign_new_leads' | 'no_contact_reminder'
  source?: string
  store_id?: string
  service_id?: string
  store_name?: string
  service_name?: string
  owner_id?: string
  owner_name?: string
  days?: number
  enabled: boolean
  stats: { runs: number; matched: number }
  meta?: { label: string; effect: string }
}
interface LogEntry {
  _id: string; at: string; rule_name: string; type: string
  action: string; affected: number; sample: string[]; by: string
}
interface Preview { matches: number; sample: string[]; effect: string }

const SOURCES: Record<string, string> = {
  instagram_ads: 'Instagram Ads', google_ads: 'Google Ads', website_form: 'Website / Form',
  referral: 'Referral', walk_in_call: 'Walk-In / Call', justdial: 'Justdial', others: 'Others',
}

/** Rule-based follow-up rules. They only assign owners or set follow-up dates -
 *  they never send messages and never delete records. */
export default function AutomationRules() {
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [form, setForm] = useState<{ name: string; type: string; source: string; store_id: string; service_id: string; owner_id: string; days: number }>(
    { name: '', type: 'assign_new_leads', source: 'instagram_ads', store_id: '', service_id: '', owner_id: '', days: 1 })
  const [preview, setPreview] = useState<Preview & { rule: Rule } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Rule | null>(null)
  const [previewing, setPreviewing] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['automation-rules'],
    queryFn: () => api<{ rules: Rule[]; owners: { _id: string; name: string }[]; stores: { _id: string; name: string }[]; services: { _id: string; name: string }[] }>('/automation-rules'),
  })
  const { data: log } = useQuery({
    queryKey: ['automation-log'],
    queryFn: () => api<{ log: LogEntry[] }>('/automation-log'),
    refetchInterval: 60_000,
  })

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['automation-rules'] })
    void qc.invalidateQueries({ queryKey: ['automation-log'] })
    void qc.invalidateQueries({ queryKey: ['queue'] })
    void qc.invalidateQueries({ queryKey: ['leads'] })
  }

  const create = useMutation({
    mutationFn: () => api<Rule>('/automation-rules', {
      method: 'POST',
      body: form.type === 'assign_new_leads'
        ? { name: form.name, type: form.type,
            source: form.source || undefined,
            store_id: form.store_id || undefined,
            service_id: form.service_id || undefined,
            owner_id: form.owner_id || undefined }
        : { name: form.name, type: form.type, days: form.days },
    }),
    onSuccess: () => {
      invalidate()
      setCreateOpen(false)
      toast.success('Rule created - it is paused. Preview it, then enable it.')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const toggle = useMutation({
    mutationFn: (v: { rule: Rule; enabled: boolean }) =>
      api(`/automation-rules/${v.rule._id}`, { method: 'PUT', body: { enabled: v.enabled } }),
    onSuccess: (_r, v) => {
      invalidate()
      toast.success(v.enabled ? `${v.rule.name} enabled` : `${v.rule.name} paused`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const doPreview = useMutation({
    mutationFn: (rule: Rule) => api<Preview>(`/automation-rules/${rule._id}/preview`, { method: 'POST' }),
    onSuccess: (r, rule) => { setPreview({ ...r, rule }); setPreviewing(false) },
    onError: (e) => { setPreviewing(false); toast.error((e as ApiError).message) },
  })
  const run = useMutation({
    mutationFn: (rule: Rule) => api<{ affected: number; message: string }>(`/automation-rules/${rule._id}/run`, { method: 'POST' }),
    onSuccess: (r) => { invalidate(); toast.success(r.message) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/automation-rules/${id}`, { method: 'DELETE' }),
    onSuccess: () => { invalidate(); setConfirmDelete(null); toast.success('Rule deleted') },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading) return <Spinner />
  const rules = data?.rules || []

  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        These rules are <b>rule-based and safe</b>: they assign an owner or set a follow-up date. They never send
        messages and never delete records. Assignment runs when a matching lead is created; reminders run only when
        you click Run Now. The message automations below are separate - they send WhatsApp/SMS only when you click
        Run, after consent checks.
      </div>

      <Card className="p-4">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-base font-bold text-slate-800">Follow-Up Rules</div>
            <div className="text-xs text-slate-500">Assign new leads and remind owners about silent leads</div>
          </div>
          <button className="btn-primary btn-sm" onClick={() => setCreateOpen(true)}><Plus size={13} /> New Rule</button>
        </div>
        {!rules.length && <Empty text="No rules yet - create one, preview who it affects, then enable it" />}
        <div className="space-y-2">
          {rules.map((r) => (
            <div key={r._id} className="border border-slate-200 rounded-lg p-3">
              <div className="flex flex-wrap items-start gap-2">
                <div className="flex-1 min-w-56">
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-sm text-slate-800">{r.name}</span>
                    <span className={`chip border ${r.enabled ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-slate-100 text-slate-500 border-slate-200'}`}>
                      {r.enabled ? 'Active' : 'Paused'}
                    </span>
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">{r.meta?.label}
                    {r.type === 'assign_new_leads'
                      ? ` · ${[SOURCES[r.source || ''] || r.source, r.store_name, r.service_name].filter(Boolean).join(' / ')} → ${r.owner_name || 'least-loaded salesperson'}`
                      : ` · no contact for ${r.days} day(s)`}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">Runs {r.stats?.runs || 0} · matched {r.stats?.matched || 0} leads</div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  <button className="btn-ghost btn-sm" disabled={previewing || doPreview.isPending}
                    onClick={() => { setPreviewing(true); doPreview.mutate(r) }}>
                    {doPreview.isPending && doPreview.variables?._id === r._id ? <BtnSpinner /> : <Eye size={12} />} Preview
                  </button>
                  <button className="btn-primary btn-sm" disabled={!r.enabled || run.isPending}
                    onClick={() => run.mutate(r)}
                    title={r.enabled ? 'Run the rule now' : 'Paused rules cannot run - enable first'}>
                    {run.isPending && run.variables?._id === r._id ? <BtnSpinner /> : <Play size={12} />} Run Now
                  </button>
                  <button onClick={() => toggle.mutate({ rule: r, enabled: !r.enabled })}
                    disabled={toggle.isPending}
                    title={r.enabled ? 'Pause this rule' : 'Enable this rule (preview first!)'}
                    className={`w-9 h-5 rounded-full relative transition shrink-0 mt-1 ${r.enabled ? 'bg-emerald-500' : 'bg-slate-300'}`}
                    aria-label={r.enabled ? 'Pause rule' : 'Enable rule'}>
                    <span className="absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all" style={{ left: r.enabled ? 18 : 2 }} />
                  </button>
                  <button className="btn-ghost btn-sm text-rose-600" onClick={() => setConfirmDelete(r)}><Trash2 size={12} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <div className="flex items-center gap-2 mb-2">
          <ScrollText size={15} className="text-slate-500" />
          <span className="text-sm font-bold text-slate-700">Automation Activity Log</span>
          <span className="text-xs text-slate-400">every rule action is recorded - nothing happens silently</span>
        </div>
        <div className="space-y-1 max-h-64 overflow-y-auto">
          {(log?.log || []).map((e) => (
            <div key={e._id} className="text-xs py-1.5 border-b border-slate-50 flex flex-wrap gap-2">
              <span className={`chip border shrink-0 ${e.action.includes('enable') ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : e.action.includes('pause') || e.action.includes('delete') ? 'bg-amber-50 text-amber-700 border-amber-200'
                  : 'bg-slate-100 text-slate-600 border-slate-200'}`}>{e.action.replace(/_/g, ' ')}</span>
              <span className="font-medium text-slate-700">{e.rule_name}</span>
              <span className="text-slate-500 flex-1">{e.sample.join('; ')}</span>
              <span className="text-slate-400 shrink-0">{e.by} · {timeAgo(e.at)}</span>
            </div>
          ))}
          {!(log?.log || []).length && <Empty text="No automation activity yet" />}
        </div>
      </Card>

      {/* create rule */}
      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New Follow-Up Rule">
        <div className="space-y-3">
          <div><label className="label">Rule Name</label>
            <input className="input" value={form.name} placeholder="e.g. Instagram Ads -> Priya"
              onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div><label className="label">Rule Type</label>
            <select className="select" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              <option value="assign_new_leads">Assign new leads</option>
              <option value="no_contact_reminder">Remind owner when no contact is logged</option>
            </select></div>
          {form.type === 'assign_new_leads' ? (
            <>
              <div><label className="label">Lead Source (optional)</label>
                <select className="select" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
                  <option value="">Any source</option>
                  {Object.entries(SOURCES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select></div>
              <div className="grid grid-cols-2 gap-3">
                <div><label className="label">Store (optional)</label>
                  <select className="select" value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
                    <option value="">Any store</option>
                    {(data?.stores || []).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
                  </select></div>
                <div><label className="label">Service (optional)</label>
                  <select className="select" value={form.service_id} onChange={(e) => setForm({ ...form, service_id: e.target.value })}>
                    <option value="">Any service</option>
                    {(data?.services || []).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
                  </select></div>
              </div>
              <div><label className="label">Assign To (optional)</label>
                <select className="select" value={form.owner_id} onChange={(e) => setForm({ ...form, owner_id: e.target.value })}>
                  <option value="">Least-loaded salesperson of the store (auto)</option>
                  {(data?.owners || []).map((u) => <option key={u._id} value={u._id}>{u.name}</option>)}
                </select></div>
              <div className="text-xs text-slate-500 bg-brand-50 border border-brand-100 rounded-lg p-2">
                When a new lead matching <b>{[form.source ? SOURCES[form.source] : 'any source', (data?.stores || []).find((s) => s._id === form.store_id)?.name || 'any store', (data?.services || []).find((s) => s._id === form.service_id)?.name || 'any service'].join(' · ')}</b> is created, it is assigned
                {form.owner_id ? ' to the chosen salesperson.' : ' to the least-loaded salesperson of that store.'}
                Internal only - no messages are sent, and reassignment stays possible. Pick at least one filter.
              </div>
            </>
          ) : (
            <>
              <div><label className="label">Days Without Contact (1-30)</label>
                <input className="input" type="number" min={1} max={30} value={form.days}
                  onChange={(e) => setForm({ ...form, days: Number(e.target.value) })} /></div>
              <div className="text-xs text-slate-500 bg-brand-50 border border-brand-100 rounded-lg p-2">
                Open leads with no logged contact for <b>{form.days} day(s)</b> get a follow-up date and appear in the
                Action Queue. No messages are sent.
              </div>
            </>
          )}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setCreateOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={!form.name.trim() || create.isPending || (form.type === 'assign_new_leads' && !form.source && !form.store_id && !form.service_id)}
            onClick={() => create.mutate()}>{create.isPending ? 'Creating…' : 'Create (Paused)'}</button>
        </div>
      </Modal>

      {/* preview before enabling */}
      <Modal open={!!preview} onClose={() => setPreview(null)} title={`Preview - ${preview?.rule.name || ''}`}>
        {preview && (
          <div className="space-y-3">
            <div className="bg-brand-50 border border-brand-100 rounded-lg p-3 text-sm text-slate-700">{preview.effect}</div>
            <div className="text-sm">Would affect <b>{preview.matches}</b> lead(s) right now</div>
            {!!preview.sample.length && (
              <div className="text-xs text-slate-500">Examples: {preview.sample.join(', ')}</div>
            )}
            <div className="text-xs text-slate-400">Nothing has been changed. Enable the rule to activate it.</div>
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setPreview(null)}>Close</button>
          {preview && !preview.rule.enabled && (
            <button className="btn-primary" disabled={toggle.isPending}
              onClick={() => { toggle.mutate({ rule: preview.rule, enabled: true }); setPreview(null) }}>
              Enable Rule
            </button>
          )}
        </div>
      </Modal>

      <ConfirmModal open={!!confirmDelete} onClose={() => setConfirmDelete(null)} danger busy={remove.isPending}
        title={`Delete rule "${confirmDelete?.name}"?`}
        confirmLabel="Delete rule"
        message="The rule stops applying. Leads already assigned or already given a follow-up keep their current state. The activity log keeps the history."
        onConfirm={() => confirmDelete && remove.mutate(confirmDelete._id)} />
    </div>
  )
}
