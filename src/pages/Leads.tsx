import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Upload, Download, Phone, MessageCircle, Search, CalendarPlus, XCircle, RotateCcw, Pencil, Image, Video, Share2 } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { useFilter, filterParams } from '../lib/store'
import { fmtDateTime, STAGE_LABELS, STAGE_COLORS, bandColor, fmtHour } from '../lib/format'
import { Card, Modal, Tabs, Empty, ErrorBox, Spinner, Bar } from '../components/ui'
import BookingPicker, { type BookingPerson } from '../components/BookingPicker'

interface Lead {
  _id: string; name: string; phone_display: string; store_name?: string; service_name?: string
  store_id?: string; service_id?: string; source: string; source_label?: string; stage: string
  score: number; band: string; follow_up_at?: string; lost_reason?: string; created_at: string
  sla_breached?: boolean; consent?: Record<string, boolean>; notes?: string; age_group?: string; gender?: string
}

const PIPE_STAGES = ['new', 'contacted', 'interested', 'offered', 'booked']

export default function Leads() {
  const [params] = useSearchParams()
  const qc = useQueryClient()
  const filter = useFilter()
  const fp = filterParams(filter)
  const [tab, setTab] = useState(params.get('tab') || 'pipeline')
  const [q, setQ] = useState(params.get('source') ? '' : '')
  const [source, setSource] = useState(params.get('source') || '')
  const [page, setPage] = useState(1)
  const [addOpen, setAddOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importResult, setImportResult] = useState<{ created: number; duplicates: number; rejected: { row: number; reason: string }[] } | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [bookingFor, setBookingFor] = useState<BookingPerson | null>(null)

  const listQuery = qs({ ...fp, q: q || undefined, source: source || undefined, page, limit: 12 })
  const { data: list, isLoading } = useQuery({
    queryKey: ['leads', tab, listQuery],
    queryFn: () => api<{ leads: Lead[]; total: number }>('/leads' + listQuery),
    enabled: tab === 'all' || tab === 'hot',
  })
  const { data: pipeline } = useQuery({
    queryKey: ['pipeline', fp.period, fp.store_id],
    queryFn: () => api<{ stages: Record<string, { total: number; leads: Lead[] }> }>('/leads/pipeline'),
    enabled: tab === 'pipeline',
  })
  const { data: followups } = useQuery({
    queryKey: ['followups', fp.period, fp.store_id],
    queryFn: () => api<{ leads: Lead[] }>('/leads/followups?when=overdue'),
    enabled: tab === 'followups',
  })
  const { data: analytics } = useQuery({
    queryKey: ['lead-analytics', fp.period, fp.store_id],
    queryFn: () => api<{ funnel: Record<string, number>; sources: Record<string, { source_label: string; leads: number; booked: number; conversion: number; avg_score: number; cost_per_booking: number | null }> }>('/leads/analytics' + qs(fp)),
    enabled: tab === 'analytics',
  })

  const tabCounts = useMemo(() => ({
    pipeline: pipeline ? Object.values(pipeline.stages).reduce((a, s) => a + s.total, 0) : undefined,
    all: list?.total, hot: list?.total, followups: followups?.leads.length,
  }), [pipeline, list, followups])

  return (
    <div>
      <Tabs active={tab} onChange={(k) => { setTab(k); setPage(1) }} tabs={[
        { key: 'pipeline', label: 'Pipeline', count: tabCounts.pipeline },
        { key: 'all', label: 'All Leads', count: tab === 'all' ? list?.total : undefined },
        { key: 'followups', label: 'Follow-Ups', count: tab === 'followups' ? followups?.leads.length : undefined },
        { key: 'hot', label: 'Hot Leads (80+)' },
        { key: 'lost', label: 'Lost' },
        { key: 'analytics', label: 'Analytics' },
      ]} />

      {(tab === 'all' || tab === 'hot' || tab === 'lost' || tab === 'followups') && (
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <div className="relative flex-1 min-w-52">
            <Search size={14} className="absolute left-2.5 top-2.5 text-slate-400" />
            <input className="input pl-8" placeholder="Search name or phone…" value={q} onChange={(e) => setQ(e.target.value)} />
          </div>
          <select className="select w-44" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All Sources</option>
            <option value="instagram_ads">Instagram Ads</option>
            <option value="google_ads">Google Ads</option>
            <option value="website_form">Website / Form</option>
            <option value="referral">Referral</option>
            <option value="walk_in_call">Walk-In / Call</option>
            <option value="justdial">Justdial</option>
            <option value="others">Others</option>
          </select>
          <button className="btn-ghost" onClick={() => setImportOpen(true)}><Upload size={14} /> Import CSV</button>
          <a className="btn-ghost" href={`/api/v1/leads/export${qs(fp)}`}
            onClick={(e) => { e.preventDefault(); downloadCsv(fp) }}><Download size={14} /> Export CSV</a>
          <button className="btn-primary" onClick={() => setAddOpen(true)}><Plus size={15} /> Add Lead</button>
        </div>
      )}

      {tab === 'pipeline' && <Pipeline stages={pipeline?.stages} onOpen={setDetailId} onBook={(l) => setBookingFor(leadPerson(l))} />}
      {tab === 'all' && <LeadTable leads={list?.leads || []} total={list?.total} page={page} setPage={setPage}
        onOpen={setDetailId} onBook={(l) => setBookingFor(leadPerson(l))} loading={isLoading} showLost />}
      {tab === 'hot' && <LeadTable leads={(list?.leads || []).filter((l) => l.score >= 80 && l.stage !== 'booked' && l.stage !== 'lost')}
        onOpen={setDetailId} onBook={(l) => setBookingFor(leadPerson(l))} loading={isLoading} />}
      {tab === 'lost' && <LostTable onOpen={setDetailId} />}
      {tab === 'followups' && <LeadTable leads={followups?.leads || []} onOpen={setDetailId} onBook={(l) => setBookingFor(leadPerson(l))} overdue />}
      {tab === 'analytics' && <Analytics data={analytics} />}

      <AddLeadModal open={addOpen} onClose={() => setAddOpen(false)} />
      <Modal open={importOpen} onClose={() => { setImportOpen(false); setImportResult(null) }} title="Import Leads From CSV" wide>
        <p className="text-xs text-slate-500 mb-2">Columns: name, phone, store(area), service, source. Rows are validated (name + 10-digit phone), duplicates by phone are skipped, and rejected rows are reported with reasons.</p>
        <textarea className="input font-mono text-xs h-40" placeholder={'name,phone,area,service,source\nRiya Verma,9827011001,Vijay Nagar,Skin Glow,instagram_ads'}
          value={importText} onChange={(e) => setImportText(e.target.value)} />
        <button className="btn-primary mt-3" disabled={!importText.trim()}
          onClick={async () => {
            const res = await api<{ created: number; duplicates: number; rejected: { row: number; reason: string }[] }>('/leads/import', { method: 'POST', body: { csv: importText } })
            setImportResult(res)
            void qc.invalidateQueries()
          }}>Import</button>
        {importResult && (
          <div className="mt-3 text-sm">
            <div className="text-emerald-700 font-semibold">Created {importResult.created} · Duplicates skipped {importResult.duplicates} · Rejected {importResult.rejected.length}</div>
            {importResult.rejected.map((r) => <div key={r.row} className="text-rose-600 text-xs">Row {r.row}: {r.reason}</div>)}
          </div>
        )}
      </Modal>

      {detailId && <LeadDetail id={detailId} onClose={() => setDetailId(null)} onBook={(l) => { setDetailId(null); setBookingFor(leadPerson(l)) }} />}
      {bookingFor && <BookingPicker open onClose={() => setBookingFor(null)} person={bookingFor} />}
    </div>
  )
}

function leadPerson(l: Lead): BookingPerson {
  return { id: l._id, name: l.name, type: 'lead', store_id: l.store_id, service_id: l.service_id }
}

async function downloadCsv(fp: Record<string, string>) {
  const u = new URLSearchParams(fp)
  const res = await fetch(`/api/v1/leads/export?${u.toString()}`, {
    headers: { Authorization: `Bearer ${localStorage.getItem('sf_token')}` },
  })
  const text = await res.text()
  const blob = new Blob([text], { type: 'text/csv' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'leads.csv'
  a.click()
}

/* ---------------------------------- pipeline kanban */
function Pipeline({ stages, onOpen, onBook }: {
  stages?: Record<string, { total: number; leads: Lead[] }>
  onOpen: (id: string) => void
  onBook: (l: Lead) => void
}) {
  const qc = useQueryClient()
  const move = useMutation({
    mutationFn: ({ id, stage }: { id: string; stage: string }) =>
      api(`/leads/${id}/stage`, { method: 'POST', body: { stage } }),
    onSuccess: () => void qc.invalidateQueries(),
    onError: (e) => alert((e as ApiError).message),
  })
  if (!stages) return <Spinner />
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-3">
      {PIPE_STAGES.map((stage) => {
        const s = stages[stage]
        return (
          <div key={stage} className="card p-3 min-h-64">
            <div className="flex items-center justify-between mb-2">
              <span className={`chip ${STAGE_COLORS[stage]}`}>{STAGE_LABELS[stage]}</span>
              <span className="text-xs font-bold text-slate-500">{s.total}</span>
            </div>
            <div className="space-y-2">
              {s.leads.map((l) => (
                <div key={l._id} className="border border-slate-100 rounded-lg p-2 hover:border-brand-300 cursor-pointer"
                  onClick={() => onOpen(l._id)}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-slate-800 truncate">{l.name}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${bandColor(l.band)}`}>{l.score}</span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5">{l.service_name} · {l.store_name}</div>
                  <div className="text-[10px] text-slate-400">{l.source_label}</div>
                  <div className="flex gap-1 mt-1.5" onClick={(e) => e.stopPropagation()}>
                    {stage === 'interested' && (
                      <button className="btn-primary btn-sm" onClick={() => onBook(l)}><CalendarPlus size={11} /> Offer</button>
                    )}
                    {stage !== 'booked' && stage !== 'offered' && (
                      <select className="text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white"
                        value={stage} onChange={(e) => move.mutate({ id: l._id, stage: e.target.value })}>
                        {PIPE_STAGES.filter((x) => x !== 'booked').map((x) => <option key={x} value={x}>→ {STAGE_LABELS[x]}</option>)}
                      </select>
                    )}
                  </div>
                </div>
              ))}
              {s.total > s.leads.length && <div className="text-[11px] text-slate-400 text-center py-1">+{s.total - s.leads.length} more</div>}
              {!s.total && <div className="text-[11px] text-slate-300 text-center py-4">Empty</div>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/* ---------------------------------- table */
function LeadTable({ leads, total, page, setPage, onOpen, onBook, loading, overdue, showLost }: {
  leads: Lead[]; total?: number; page?: number; setPage?: (p: number) => void
  onOpen: (id: string) => void; onBook: (l: Lead) => void; loading?: boolean; overdue?: boolean; showLost?: boolean
}) {
  if (loading) return <Spinner />
  if (!leads.length) return <Card className="p-6"><Empty text="No leads match the current filter" /></Card>
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[860px]">
        <thead><tr className="bg-slate-50">
          <th className="th">Lead</th><th className="th">Service / Store</th><th className="th">Source</th>
          <th className="th">Stage</th><th className="th">Score</th>
          <th className="th">{overdue ? 'Follow-Up (Overdue)' : 'Follow-Up'}</th>
          {showLost && <th className="th">Lost Reason</th>}
          <th className="th text-right">Actions</th>
        </tr></thead>
        <tbody>
          {leads.map((l) => (
            <tr key={l._id} className="hover:bg-slate-50 cursor-pointer" onClick={() => onOpen(l._id)}>
              <td className="td">
                <div className="font-semibold text-slate-800">{l.name}</div>
                <div className="text-xs text-slate-400">{l.phone_display}</div>
              </td>
              <td className="td text-sm">{l.service_name || '-'}<div className="text-xs text-slate-400">{l.store_name || '-'}</div></td>
              <td className="td text-sm">{l.source_label || l.source}</td>
              <td className="td"><span className={`chip ${STAGE_COLORS[l.stage]}`}>{STAGE_LABELS[l.stage]}</span></td>
              <td className="td"><span className={`text-xs px-1.5 py-0.5 rounded font-bold ${bandColor(l.band)}`}>{l.score}</span></td>
              <td className={`td text-xs ${l.follow_up_at && l.follow_up_at < new Date().toISOString() && overdue ? 'text-rose-600 font-semibold' : ''}`}>
                {l.follow_up_at ? fmtDateTime(l.follow_up_at) : '-'}
              </td>
              {showLost && <td className="td text-sm text-slate-500">{l.lost_reason || '-'}</td>}
              <td className="td text-right" onClick={(e) => e.stopPropagation()}>
                <div className="flex justify-end gap-1">
                  {l.stage !== 'booked' && l.stage !== 'lost' && (
                    <button className="btn-primary btn-sm" onClick={() => onBook(l)}><CalendarPlus size={11} /> Book</button>
                  )}
                  <button className="btn-ghost btn-sm" onClick={() => onOpen(l._id)}><Pencil size={11} /></button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {setPage && total !== undefined && (
        <div className="flex items-center justify-between px-3 py-2 border-t border-slate-100">
          <span className="text-xs text-slate-400">{total} leads</span>
          <div className="flex gap-1">
            <button className="btn-ghost btn-sm" disabled={(page || 1) <= 1} onClick={() => setPage?.((page || 1) - 1)}>Prev</button>
            <button className="btn-ghost btn-sm" disabled={(page || 1) * 12 >= total} onClick={() => setPage?.((page || 1) + 1)}>Next</button>
          </div>
        </div>
      )}
    </Card>
  )
}

/* ---------------------------------- lost leads with reopen */
function LostTable({ onOpen }: { onOpen: (id: string) => void }) {
  const filter = useFilter()
  const fp = filterParams(filter)
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['lost', fp.period, fp.store_id],
    queryFn: () => api<{ leads: Lead[] }>('/leads' + qs({ ...fp, stage: 'lost', limit: 100 })),
  })
  const reopen = useMutation({
    mutationFn: (id: string) => api(`/leads/${id}/reopen`, { method: 'POST' }),
    onSuccess: () => void qc.invalidateQueries(),
  })
  if (isLoading) return <Spinner />
  const leads = data?.leads || []
  const byReason: Record<string, Lead[]> = {}
  leads.forEach((l) => { const r = l.lost_reason || 'Unknown'; (byReason[r] = byReason[r] || []).push(l) })
  return (
    <div className="space-y-3">
      {Object.entries(byReason).map(([reason, ls]) => (
        <Card key={reason} className="p-3">
          <div className="text-sm font-bold text-slate-700 mb-2">{reason} · {ls.length}</div>
          <div className="flex flex-wrap gap-2">
            {ls.map((l) => (
              <span key={l._id} className="chip border-slate-200 bg-slate-50 text-slate-600 cursor-pointer" onClick={() => onOpen(l._id)}>
                {l.name} · {l.score}
                <button onClick={(e) => { e.stopPropagation(); reopen.mutate(l._id) }}
                  className="text-brand-600 hover:text-brand-800" title="Reopen"><RotateCcw size={11} /></button>
              </span>
            ))}
          </div>
        </Card>
      ))}
      {!leads.length && <Card className="p-6"><Empty text="No lost leads in this period" /></Card>}
    </div>
  )
}

/* ---------------------------------- analytics */
function Analytics({ data }: { data?: { funnel: Record<string, number>; sources: Record<string, { source_label: string; leads: number; booked: number; conversion: number; avg_score: number; cost_per_booking: number | null }> } }) {
  if (!data) return <Spinner />
  const funnel = ['new', 'contacted', 'interested', 'offered', 'booked', 'lost'].map((s) => ({ stage: s, label: STAGE_LABELS[s], n: data.funnel[s] || 0 }))
  const max = Math.max(...funnel.map((f) => f.n), 1)
  const srcs = Object.entries(data.sources).map(([k, v]) => ({ key: k, ...v })).sort((a, b) => b.leads - a.leads)
  return (
    <div className="grid lg:grid-cols-2 gap-5">
      <Card className="p-4">
        <div className="text-sm font-bold mb-3">Stage Funnel</div>
        <div className="space-y-2">
          {funnel.map((f) => (
            <div key={f.stage}>
              <div className="flex justify-between text-xs mb-0.5"><span className="font-medium">{f.label}</span><span className="text-slate-400">{f.n}</span></div>
              <Bar value={(f.n / max) * 100} color={f.stage === 'booked' ? 'bg-emerald-500' : f.stage === 'lost' ? 'bg-slate-400' : 'bg-brand-500'} />
            </div>
          ))}
        </div>
      </Card>
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[520px]">
          <thead><tr className="bg-slate-50">
            <th className="th">Source</th><th className="th">Leads</th><th className="th">Booked</th>
            <th className="th">Conversion</th><th className="th">Avg Score</th><th className="th">Cost / Booking</th>
          </tr></thead>
          <tbody>
            {srcs.map((s) => (
              <tr key={s.key}>
                <td className="td font-medium">{s.source_label}</td>
                <td className="td">{s.leads}</td>
                <td className="td">{s.booked}</td>
                <td className="td">{s.conversion}%</td>
                <td className="td">{s.avg_score}</td>
                <td className="td">{s.cost_per_booking !== null ? `Rs ${s.cost_per_booking.toLocaleString('en-IN')}` : '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  )
}

/* ---------------------------------- add lead */
function AddLeadModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', phone: '', store_id: '', service_id: '', source: 'instagram_ads', age_group: '', gender: '', consent_whatsapp: true, consent_sms: false, notes: '' })
  const [error, setError] = useState('')
  const { data: services } = useQuery({ queryKey: ['services'], queryFn: () => api<{ services: { _id: string; name: string }[] }>('/services'), staleTime: 300_000 })
  const { data: stores } = useQuery({ queryKey: ['stores'], queryFn: () => api<{ stores: { _id: string; name: string }[] }>('/stores'), staleTime: 300_000 })
  const create = useMutation({
    mutationFn: () => api('/leads', { method: 'POST', body: form }),
    onSuccess: () => { void qc.invalidateQueries(); onClose() },
    onError: (e) => setError((e as ApiError).message),
  })
  return (
    <Modal open={open} onClose={onClose} title="Add Lead">
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2"><label className="label">Name *</label><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="label">Phone (10 Digits) *</label><input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
        <div><label className="label">Source</label>
          <select className="select" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })}>
            <option value="instagram_ads">Instagram Ads</option><option value="google_ads">Google Ads</option>
            <option value="website_form">Website / Form</option><option value="referral">Referral</option>
            <option value="walk_in_call">Walk-In / Call</option><option value="justdial">Justdial</option><option value="others">Others</option>
          </select></div>
        <div><label className="label">Store</label>
          <select className="select" value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
            <option value="">- Select -</option>
            {stores?.stores.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div><label className="label">Service</label>
          <select className="select" value={form.service_id} onChange={(e) => setForm({ ...form, service_id: e.target.value })}>
            <option value="">- Select -</option>
            {services?.services.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div><label className="label">Age Group</label>
          <select className="select" value={form.age_group} onChange={(e) => setForm({ ...form, age_group: e.target.value })}>
            <option value="">-</option><option>18-25</option><option>26-35</option><option>36-45</option><option>45+</option>
          </select></div>
        <div><label className="label">Gender</label>
          <select className="select" value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
            <option value="">-</option><option>Female</option><option>Male</option>
          </select></div>
        <div className="col-span-2"><label className="label">Notes</label><textarea className="input" rows={2} value={form.notes || ''} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.consent_whatsapp} onChange={(e) => setForm({ ...form, consent_whatsapp: e.target.checked })} /> WhatsApp Consent</label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={form.consent_sms} onChange={(e) => setForm({ ...form, consent_sms: e.target.checked })} /> SMS Consent</label>
      </div>
      {error && <div className="mt-3"><ErrorBox error={new Error(error)} /></div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!form.name || !form.phone || create.isPending}
          onClick={() => { setError(''); create.mutate() }}>{create.isPending ? 'Saving…' : 'Create Lead'}</button>
      </div>
    </Modal>
  )
}

/* ---------------------------------- lead detail drawer */
function LeadDetail({ id, onClose, onBook }: { id: string; onClose: () => void; onBook: (l: Lead) => void }) {
  const qc = useQueryClient()
  const [lostOpen, setLostOpen] = useState(false)
  const [lostReason, setLostReason] = useState('Price')
  const [editOpen, setEditOpen] = useState(false)
  const [fuDate, setFuDate] = useState(new Date().toISOString().slice(0, 10))
  const { data: lead, isLoading } = useQuery({
    queryKey: ['lead', id],
    queryFn: () => api<Lead & { interactions: { _id: string; channel: string; direction: string; content: string; outcome: string; at: string }[]; appointments: { _id: string; date: string; start_hour: number; service_name: string; status: string; store_name: string }[]; owner_name?: string; sla_deadline: string; score_factors: { f: string; v: number; d?: string }[]; customer?: unknown }>(`/leads/${id}`),
  })

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: object }) => api(`/leads/${id}/${path}`, { method: 'POST', body }),
    onSuccess: () => void qc.invalidateQueries(),
    onError: (e) => alert((e as ApiError).message),
  })

  if (isLoading || !lead) return <Modal open onClose={onClose} title="Lead"><Spinner /></Modal>

  const msgPreview = `Hi ${lead.name}, thanks for your interest in ${lead.service_name || 'our services'} at ${lead.store_name || 'our store'}! When can we call you to fix a slot?`

  return (
    <Modal open onClose={onClose} wide title={`Lead - ${lead.name}`}>
      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${STAGE_COLORS[lead.stage]}`}>{STAGE_LABELS[lead.stage]}</span>
            <span className={`text-xs px-2 py-0.5 rounded font-bold ${bandColor(lead.band)}`}>Score {lead.score}</span>
            <span className="text-xs text-slate-400">SLA {lead.sla_deadline?.slice(11, 16)} · Owner {lead.owner_name || '-'}</span>
            {lead.sla_breached && <span className="chip bg-rose-100 text-rose-700 border-rose-200">SLA Breached</span>}
          </div>

          <div className="flex flex-wrap gap-1.5">
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'call', direction: 'out', outcome: 'call_made', content: 'Click-to-call logged' } })}><Phone size={12} /> Call</button>
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'whatsapp_sent', content: msgPreview } })}><MessageCircle size={12} /> WhatsApp</button>
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'sms', direction: 'out', outcome: 'sms_sent', content: msgPreview } })}><MessageCircle size={12} /> SMS</button>
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ad_shared', content: 'Image ad shared' } })}><Image size={12} /> Image Ad</button>
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ugc_shared', content: 'UGC video shared' } })}><Video size={12} /> UGC</button>
            <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ugc_shared', content: 'Customer story shared' } })}><Share2 size={12} /> Story</button>
            {lead.stage !== 'booked' && lead.stage !== 'lost' && (
              <button className="btn-primary btn-sm" onClick={() => onBook(lead)}><CalendarPlus size={12} /> Offer Appointment</button>
            )}
            {lead.stage !== 'booked' && lead.stage !== 'lost' && (
              <button className="btn-danger btn-sm" onClick={() => setLostOpen(true)}><XCircle size={12} /> Mark Lost</button>
            )}
            <button className="btn-ghost btn-sm" onClick={() => setEditOpen(true)}><Pencil size={12} /> Edit</button>
          </div>

          {lostOpen && (
            <div className="border border-rose-200 bg-rose-50 rounded-lg p-3 flex items-center gap-2">
              <select className="select w-40" value={lostReason} onChange={(e) => setLostReason(e.target.value)}>
                {['Price', 'Distance', 'No Response', 'Chose Competitor', 'Timing'].map((r) => <option key={r}>{r}</option>)}
              </select>
              <button className="btn-danger btn-sm" onClick={() => { act.mutate({ path: 'lost', body: { reason: lostReason } }); setLostOpen(false) }}>Confirm Lost</button>
              <button className="btn-ghost btn-sm" onClick={() => setLostOpen(false)}>Cancel</button>
            </div>
          )}

          {/* follow-up + stage moves */}
          <div className="card p-3">
            <div className="text-sm font-bold mb-2">Stage & Follow-Up</div>
            <div className="flex flex-wrap gap-2 items-center">
              {['new', 'contacted', 'interested', 'offered'].map((s) => (
                <button key={s} disabled={lead.stage === s || lead.stage === 'booked'}
                  onClick={() => act.mutate({ path: 'stage', body: { stage: s } })}
                  className={`btn-sm border rounded px-2 py-1 ${lead.stage === s ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>
                  {STAGE_LABELS[s]}
                </button>
              ))}
              <span className="mx-2 text-slate-300">|</span>
              <input type="date" className="input w-36" value={fuDate} onChange={(e) => setFuDate(e.target.value)} />
              <button className="btn-ghost btn-sm" onClick={() => act.mutate({ path: 'follow-up', body: { follow_up_at: new Date(fuDate + 'T10:00:00+00:00').toISOString() } })}>
                Schedule Follow-Up
              </button>
              {lead.follow_up_at && <span className="text-xs text-slate-500">Next: {fmtDateTime(lead.follow_up_at)}</span>}
            </div>
          </div>

          {/* message preview */}
          <div className="card p-3">
            <div className="text-sm font-bold mb-2">Message Preview (Standard Customer Message)</div>
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg rounded-tl-none px-3 py-2 text-sm max-w-lg">{msgPreview}</div>
          </div>

          {/* activity log */}
          <div className="card p-3">
            <div className="text-sm font-bold mb-2">Activity Log</div>
            <div className="space-y-1 max-h-48 overflow-y-auto">
              {(lead.interactions || []).map((i) => (
                <div key={i._id} className="flex items-start gap-2 text-xs py-1 border-b border-slate-50">
                  <span className={`chip ${i.direction === 'in' ? 'bg-sky-100 text-sky-700 border-sky-200' : 'bg-slate-100 border-slate-200 text-slate-500'}`}>{i.channel}</span>
                  <span className="flex-1 text-slate-600">{i.content}</span>
                  <span className="text-slate-400 shrink-0">{fmtDateTime(i.at)}</span>
                </div>
              ))}
              {!(lead.interactions || []).length && <Empty text="No interactions yet" />}
            </div>
          </div>

          {/* appointments */}
          {!!lead.appointments?.length && (
            <div className="card p-3">
              <div className="text-sm font-bold mb-2">Appointments</div>
              {lead.appointments.map((a) => (
                <div key={a._id} className="flex justify-between text-sm py-1 border-b border-slate-50">
                  <span>{a.date} {fmtHour(a.start_hour)} · {a.service_name} @ {a.store_name}</span>
                  <span className="chip bg-slate-100 border-slate-200 text-slate-600">{a.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* right column: score factors + cross-sell */}
        <div className="space-y-4">
          <div className="card p-3">
            <div className="text-sm font-bold mb-2">Score Factors (Explainable)</div>
            {(lead.score_factors || []).map((f, i) => (
              <div key={i} className="flex justify-between text-xs py-1 border-b border-slate-50">
                <span className="text-slate-500">{f.d || f.f}</span><span className="font-bold text-slate-700">+{f.v}</span>
              </div>
            ))}
          </div>
          <div className="card p-3">
            <div className="text-sm font-bold mb-1">Cross-Sell Suggestion</div>
            <div className="text-xs text-slate-500">
              {lead.service_name === 'Skin Glow' ? 'Wellness Spa converts well after Skin Glow visits.'
                : lead.service_name === 'Hair Restore' ? 'Skin Glow is the natural next service.'
                  : lead.service_name === 'Fitness Pro' ? 'Wellness Spa for recovery days converts 1 in 4.'
                    : 'Dental Care pairs well with Skin Glow packages.'}
            </div>
          </div>
          {lead.consent && (
            <div className="card p-3">
              <div className="text-sm font-bold mb-2">Consent</div>
              <div className="text-xs space-y-1">
                <div className="flex justify-between"><span className="text-slate-500">WhatsApp</span><span className={lead.consent.whatsapp ? 'text-emerald-600 font-semibold' : 'text-slate-400'}>{lead.consent.whatsapp ? 'Opted In' : 'Not Given'}</span></div>
                <div className="flex justify-between"><span className="text-slate-500">SMS</span><span className={lead.consent.sms ? 'text-emerald-600 font-semibold' : 'text-slate-400'}>{lead.consent.sms ? 'Opted In' : 'Not Given'}</span></div>
              </div>
            </div>
          )}
        </div>
      </div>
      <EditLeadModal id={id} open={editOpen} onClose={() => setEditOpen(false)} lead={lead} />
    </Modal>
  )
}

function EditLeadModal({ id, open, onClose, lead }: { id: string; open: boolean; onClose: () => void; lead: Lead }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: lead.name, phone: lead.phone_display, store_id: lead.store_id || '', service_id: lead.service_id || '', source: lead.source, notes: lead.notes || '' })
  const [error, setError] = useState('')
  const { data: services } = useQuery({ queryKey: ['services'], queryFn: () => api<{ services: { _id: string; name: string }[] }>('/services'), staleTime: 300_000, enabled: open })
  const { data: stores } = useQuery({ queryKey: ['stores'], queryFn: () => api<{ stores: { _id: string; name: string }[] }>('/stores'), staleTime: 300_000, enabled: open })
  const save = useMutation({
    mutationFn: () => api(`/leads/${id}`, { method: 'PUT', body: form }),
    onSuccess: () => { void qc.invalidateQueries(); onClose() },
    onError: (e) => setError((e as ApiError).message),
  })
  return (
    <Modal open={open} onClose={onClose} title="Edit Lead">
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label">Name</label><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="label">Phone</label><input className="input" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
        <div><label className="label">Store {lead.stage === 'booked' && '(Locked While Booked)'}</label>
          <select className="select" disabled={lead.stage === 'booked'} value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
            {stores?.stores.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div><label className="label">Service {lead.stage === 'booked' && '(Locked While Booked)'}</label>
          <select className="select" disabled={lead.stage === 'booked'} value={form.service_id} onChange={(e) => setForm({ ...form, service_id: e.target.value })}>
            {services?.services.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div className="col-span-2"><label className="label">Notes</label><textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></div>
      </div>
      {error && <div className="mt-3"><ErrorBox error={new Error(error)} /></div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={() => save.mutate()} disabled={save.isPending}>Save</button>
      </div>
    </Modal>
  )
}
