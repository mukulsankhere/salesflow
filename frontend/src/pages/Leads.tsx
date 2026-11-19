import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Upload, Download, Phone, PhoneCall, MessageCircle, Search, CalendarPlus, XCircle, RotateCcw, Pencil, Image, Video, Share2, StickyNote, CalendarCheck2, PhoneMissed, Link2 } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useDebouncedValue } from '../lib/hooks'
import { useFilter, filterParams } from '../lib/store'
import { fmtDateTime, STAGE_LABELS, STAGE_COLORS, bandColor, fmtHour } from '../lib/format'
import { Card, Modal, Tabs, Empty, ErrorBox, Spinner, Bar, BtnSpinner, ConfirmModal, SectionTitle } from '../components/ui'
import BookingPicker, { type BookingPerson } from '../components/BookingPicker'

interface Lead {
  _id: string; name: string; phone_display: string; store_name?: string; service_name?: string
  store_id?: string; service_id?: string; source: string; source_label?: string; stage: string
  score: number; band: string; follow_up_at?: string; lost_reason?: string; created_at: string
  sla_breached?: boolean; consent?: Record<string, boolean>; notes?: string; age_group?: string; gender?: string
  priority?: 'hot' | 'warm' | 'low'
  priority_reasons?: string[]
  archived?: boolean
  merged_into?: string
  customer_id?: string | null
}

const TIER_META: Record<string, { label: string; chip: string }> = {
  hot: { label: 'Hot', chip: 'bg-rose-100 text-rose-700 border-rose-200' },
  warm: { label: 'Warm', chip: 'bg-amber-100 text-amber-700 border-amber-200' },
  low: { label: 'Low', chip: 'bg-slate-100 text-slate-500 border-slate-200' },
}

/** Rule-based priority chip with the explanation as tooltip. */
function TierChip({ lead }: { lead: Lead }) {
  const tier = lead.priority || 'low'
  const meta = TIER_META[tier]
  return (
    <span className={`chip border ${meta.chip} cursor-help`}
      title={(lead.priority_reasons || []).join(' · ') || 'Rule-based priority'}
      aria-label={`Priority ${meta.label}: ${(lead.priority_reasons || []).join('; ')}`}>
      {meta.label}
    </span>
  )
}

const PIPE_STAGES = ['new', 'contacted', 'interested', 'offered', 'booked']

const SOURCE_LABELS: Record<string, string> = {
  instagram_ads: 'Instagram Ads', google_ads: 'Google Ads', website_form: 'Website / Form',
  referral: 'Referral', walk_in_call: 'Walk-In / Call', justdial: 'Justdial', others: 'Others',
}

export default function Leads() {
  const [params] = useSearchParams()
  const qc = useQueryClient()
  const filter = useFilter()
  const fp = filterParams(filter)
  const [tab, setTab] = useState(params.get('tab') || 'pipeline')
  const [q, setQ] = useState('')
  const dq = useDebouncedValue(q)
  const [source, setSource] = useState(params.get('source') || '')
  const [page, setPage] = useState(1)
  const [addOpen, setAddOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importResult, setImportResult] = useState<{ created: number; duplicates: number; rejected: { row: number; reason: string }[] } | null>(null)
  const [exporting, setExporting] = useState(false)
  const [detailId, setDetailId] = useState<string | null>(params.get('lead'))
  const [bookingFor, setBookingFor] = useState<BookingPerson | null>(null)

  // searching or switching source always restarts at page 1
  useEffect(() => { setPage(1) }, [dq, source])

  const listQuery = qs({ ...fp, q: dq || undefined, source: source || undefined, page, limit: 12 })
  const { data: list, isLoading } = useQuery({
    queryKey: ['leads', tab, listQuery],
    queryFn: () => api<{ leads: Lead[]; total: number }>('/leads' + listQuery),
    enabled: tab === 'all' || tab === 'hot',
  })
  const { data: pipeline } = useQuery({
    queryKey: ['pipeline', fp.period, fp.store_id],
    queryFn: () => api<{ stages: Record<string, { total: number; leads: Lead[] }> }>('/leads/pipeline' + qs(fp)),
    enabled: tab === 'pipeline',
  })
  const { data: followups } = useQuery({
    queryKey: ['followups', fp.period, fp.store_id],
    queryFn: () => api<{ leads: Lead[] }>('/leads/followups' + qs({ when: 'overdue', ...fp })),
    enabled: tab === 'followups',
  })
  const { data: analytics } = useQuery({
    queryKey: ['lead-analytics', fp.period, fp.store_id],
    queryFn: () => api<{ funnel: Record<string, number>; sources: Record<string, { source_label: string; leads: number; booked: number; conversion: number; avg_score: number; cost_per_booking: number | null }>; report: LeadReport }>('/leads/analytics' + qs(fp)),
    enabled: tab === 'analytics',
  })
  const { data: sla } = useQuery({
    queryKey: ['sla', fp.period, fp.store_id],
    queryFn: () => api<SLAResponse>('/sla/report' + qs(fp)),
    enabled: tab === 'sla',
  })
  const { data: hygiene } = useQuery({
    queryKey: ['hygiene', fp.period, fp.store_id],
    queryFn: () => api<HygieneResponse>('/leads/hygiene' + qs(fp)),
    enabled: tab === 'hygiene',
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
        { key: 'hygiene', label: 'Hygiene', count: tab === 'hygiene' && hygiene ? (hygiene.counts.duplicates + hygiene.counts.incomplete + hygiene.counts.stale) : undefined },
        { key: 'analytics', label: 'Analytics' },
        { key: 'sla', label: 'Response SLA' },
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
            onClick={(e) => { e.preventDefault(); downloadCsv(fp, setExporting) }}>
            {exporting ? <BtnSpinner /> : <Download size={14} />} {exporting ? 'Preparing…' : 'Export CSV'}
          </a>
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
      {tab === 'analytics' && (
        <>
          <Outcomes />
          <Analytics data={analytics} />
        </>
      )}
      {tab === 'sla' && <SLAReport data={sla} onOpen={setDetailId} />}
      {tab === 'hygiene' && <HygieneTab data={hygiene} onOpen={setDetailId} />}

      <AddLeadModal open={addOpen} onClose={() => setAddOpen(false)} />
      <Modal open={importOpen} onClose={() => { setImportOpen(false); setImportResult(null) }} title="Import Leads From CSV" wide>
        <p className="text-xs text-slate-500 mb-2">Columns: name, phone, store(area), service, source. Rows are validated (name + 10-digit phone), duplicates by phone are skipped, and rejected rows are reported with reasons.</p>
        <textarea className="input font-mono text-xs h-40" placeholder={'name,phone,area,service,source\nRiya Verma,9827011001,Vijay Nagar,Skin Glow,instagram_ads'}
          value={importText} onChange={(e) => setImportText(e.target.value)} />
        <button className="btn-primary mt-3" disabled={!importText.trim()}
          onClick={async () => {
            try {
              const res = await api<{ created: number; duplicates: number; rejected: { row: number; reason: string }[] }>('/leads/import', { method: 'POST', body: { csv: importText } })
              setImportResult(res)
              void qc.invalidateQueries()
              toast.success(`Imported ${res.created} leads (${res.duplicates} duplicates skipped, ${res.rejected.length} rejected)`)
            } catch (e) {
              toast.error((e as ApiError).message)
            }
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

async function downloadCsv(fp: Record<string, string>, setBusy: (b: boolean) => void) {
  setBusy(true)
  try {
    const u = new URLSearchParams(fp)
    const res = await fetch(`/api/v1/leads/export?${u.toString()}`, {
      headers: { Authorization: `Bearer ${localStorage.getItem('sf_token')}` },
    })
    if (!res.ok) throw new Error(`Export failed (${res.status})`)
    const text = await res.text()
    const blob = new Blob([text], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'leads.csv'
    a.click()
    URL.revokeObjectURL(url)
    toast.success('leads.csv downloaded')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    setBusy(false)
  }
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
    onSuccess: (_r, v) => { void qc.invalidateQueries(); toast.success(`${STAGE_LABELS[v.stage] || v.stage}: stage updated`) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  if (!stages) return <Spinner />
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-5 gap-3">
      {PIPE_STAGES.map((stage, si) => {
        const s = stages[stage]
        return (
          <div key={stage} className="card p-3 min-h-64 anim-fade-up" style={{ '--d': `${si * 50}ms` } as CSSProperties}>
            <div className="flex items-center justify-between mb-2">
              <span className={`chip ${STAGE_COLORS[stage]}`}>{STAGE_LABELS[stage]}</span>
              <span className="text-xs font-bold text-slate-500">{s.total}</span>
            </div>
            <div className="space-y-2">
              {s.leads.map((l, li) => (
                <div key={l._id} className="border border-slate-100 rounded-lg p-2 hover:border-brand-300 cursor-pointer anim-fade-up"
                  style={{ '--d': `${Math.min(li, 8) * 30}ms` } as CSSProperties}
                  onClick={() => onOpen(l._id)}>
                  <div className="flex items-center justify-between gap-1">
                    <span className="text-sm font-semibold text-slate-800 truncate">{l.name}</span>
                    <span className="flex items-center gap-1 shrink-0">
                      <TierChip lead={l} />
                      <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${bandColor(l.band)}`}>{l.score}</span>
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-500 mt-0.5">{l.service_name} · {l.store_name}</div>
                  <div className="text-[10px] text-slate-400">{l.source_label}</div>
                  <div className="flex gap-1 mt-1.5" onClick={(e) => e.stopPropagation()}>
                    {stage === 'interested' && (
                      <button className="btn-primary btn-sm" onClick={() => onBook(l)}><CalendarPlus size={11} /> Offer</button>
                    )}
                    {stage !== 'booked' && stage !== 'offered' && (
                      <select className="text-[10px] border border-slate-200 rounded px-1 py-0.5 bg-white disabled:opacity-50"
                        value={stage} disabled={move.isPending && move.variables?.id === l._id}
                        onChange={(e) => move.mutate({ id: l._id, stage: e.target.value })}>
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
          <th className="th">Stage</th><th className="th">Priority</th>
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
              <td className="td"><TierChip lead={l} /></td>
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
    onSuccess: () => { void qc.invalidateQueries(); toast.success('Lead reopened - back in the pipeline') },
    onError: (e) => toast.error((e as ApiError).message),
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
                <button onClick={(e) => { e.stopPropagation(); reopen.mutate(l._id) }} disabled={reopen.isPending && reopen.variables === l._id}
                  className="text-brand-600 hover:text-brand-800 disabled:opacity-40" title="Reopen"><RotateCcw size={11} /></button>
              </span>
            ))}
          </div>
        </Card>
      ))}
      {!leads.length && <Card className="p-6"><Empty text="No lost leads in this period" /></Card>}
    </div>
  )
}

/* ---------------------------------- lead-to-booking outcomes (LEAD-15) */
interface OutcomesFunnel {
  leads: number; contacted: number; booked: number; attended: number
  renewed: number; lost: number; booking_rate: number; attendance_rate: number
}
interface OutcomesResponse {
  funnel: OutcomesFunnel
  by_source: Record<string, OutcomesFunnel>
  by_owner: Record<string, OutcomesFunnel>
  owners: { _id: string; name: string }[]
}

function Outcomes() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const [svc, setSvc] = useState('')
  const [src, setSrc] = useState('')
  const [staff, setStaff] = useState('')
  const q = qs({ ...fp, service_id: svc || undefined, source: src || undefined, owner_id: staff || undefined })
  const { data, isLoading } = useQuery({
    queryKey: ['outcomes', q],
    queryFn: () => api<OutcomesResponse>('/leads/outcomes' + q),
  })
  const { data: services } = useQuery({
    queryKey: ['services-flat'],
    queryFn: () => api<{ services: { _id: string; name: string }[] }>('/services'),
    staleTime: 300_000,
  })
  if (isLoading) return <Spinner />
  if (!data) return <ErrorBox error={new Error('Could not load outcomes')} />
  const f = data.funnel
  const steps = [
    { label: 'Leads Captured', n: f.leads, tone: 'text-slate-800' },
    { label: 'Contacted', n: f.contacted, tone: 'text-sky-700' },
    { label: 'Booked', n: f.booked, tone: 'text-brand-700' },
    { label: 'Attended Visits', n: f.attended, tone: 'text-emerald-700' },
    { label: 'Renewals', n: f.renewed, tone: 'text-fuchsia-700' },
    { label: 'Lost', n: f.lost, tone: 'text-rose-600' },
  ]
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionTitle title="Lead-To-Booking Results"
          subtitle="Where this window's leads ended up - contacted, booked, attended, renewed or lost. All counts from real records." />
        <div className="flex flex-wrap gap-2 mb-4">
          <select className="select w-44" value={svc} onChange={(e) => setSvc(e.target.value)} aria-label="Filter by service">
            <option value="">All Services</option>
            {(services?.services || []).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select>
          <select className="select w-40" value={src} onChange={(e) => setSrc(e.target.value)} aria-label="Filter by source">
            <option value="">All Sources</option>
            {Object.entries(SOURCE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select className="select w-44" value={staff} onChange={(e) => setStaff(e.target.value)} aria-label="Filter by staff member">
            <option value="">All Staff</option>
            {(data.owners || []).map((o) => <option key={o._id} value={o._id}>{o.name}</option>)}
          </select>
          {(svc || src || staff) && (
            <button className="btn-ghost btn-sm" onClick={() => { setSvc(''); setSrc(''); setStaff('') }}>Clear Filters</button>
          )}
        </div>
        <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
          {steps.map((s, i) => (
            <div key={s.label} className="border border-slate-100 rounded-lg p-3 text-center">
              <div className={`text-xl font-extrabold ${s.tone}`}>{s.n}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">{s.label}</div>
              {i > 0 && i < 5 && f.leads > 0 && (
                <div className="text-[10px] text-slate-400">{Math.round((100 * s.n) / f.leads)}% of leads</div>
              )}
            </div>
          ))}
        </div>
        <div className="text-xs text-slate-500 mt-3">
          Booking rate <b>{f.booking_rate}%</b> · Attendance rate <b>{f.attendance_rate}%</b> of bookings
          (renewals can exceed the lead cohort - they count renewal bookings by these leads' customers)
        </div>
      </Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[480px]">
            <thead><tr className="bg-slate-50">
              <th className="th">By Source</th><th className="th">Leads</th><th className="th">Contacted</th>
              <th className="th">Booked</th><th className="th">Attended</th><th className="th">Lost</th>
              <th className="th">Booking Rate</th>
            </tr></thead>
            <tbody>
              {Object.entries(data.by_source).map(([label, s]) => (
                <tr key={label} className="hover:bg-slate-50">
                  <td className="td font-medium">{label}</td>
                  <td className="td">{s.leads}</td><td className="td">{s.contacted}</td>
                  <td className="td">{s.booked}</td><td className="td">{s.attended}</td>
                  <td className="td">{s.lost}</td>
                  <td className="td"><span className={`chip ${s.booking_rate >= 40 ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : s.booking_rate >= 20 ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-rose-50 text-rose-700 border-rose-200'}`}>{s.booking_rate}%</span></td>
                </tr>
              ))}
              {!Object.keys(data.by_source).length && <tr><td className="td text-slate-400" colSpan={7}>No leads in this window</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[480px]">
            <thead><tr className="bg-slate-50">
              <th className="th">By Staff Member</th><th className="th">Leads</th><th className="th">Contacted</th>
              <th className="th">Booked</th><th className="th">Attended</th><th className="th">Lost</th>
              <th className="th">Booking Rate</th>
            </tr></thead>
            <tbody>
              {Object.entries(data.by_owner).map(([name, s]) => (
                <tr key={name} className="hover:bg-slate-50">
                  <td className="td font-medium">{name}</td>
                  <td className="td">{s.leads}</td><td className="td">{s.contacted}</td>
                  <td className="td">{s.booked}</td><td className="td">{s.attended}</td>
                  <td className="td">{s.lost}</td>
                  <td className="td"><span className={`chip ${s.booking_rate >= 40 ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : s.booking_rate >= 20 ? 'bg-amber-50 text-amber-700 border-amber-200' : 'bg-rose-50 text-rose-700 border-rose-200'}`}>{s.booking_rate}%</span></td>
                </tr>
              ))}
              {!Object.keys(data.by_owner).length && <tr><td className="td text-slate-400" colSpan={7}>No owned leads in this window</td></tr>}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  )
}

/* ---------------------------------- analytics */
interface LeadReport {
  priority_counts: { hot: number; warm: number; low: number }
  overdue_followups: number
  avg_first_contact_min: number | null
  responded: number
  conversion_rate: number
  leads_in_range: number
}

function Analytics({ data }: { data?: { funnel: Record<string, number>; sources: Record<string, { source_label: string; leads: number; booked: number; conversion: number; avg_score: number; cost_per_booking: number | null }>; report?: LeadReport } }) {
  if (!data) return <Spinner />
  const funnel = ['new', 'contacted', 'interested', 'offered', 'booked', 'lost'].map((s) => ({ stage: s, label: STAGE_LABELS[s], n: data.funnel[s] || 0 }))
  const max = Math.max(...funnel.map((f) => f.n), 1)
  const srcs = Object.entries(data.sources).map(([k, v]) => ({ key: k, ...v })).sort((a, b) => b.leads - a.leads)
  const rep = data.report
  return (
    <div className="space-y-4">
      {rep && (
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <StatCard label="Hot / Warm / Low" value={`${rep.priority_counts.hot} / ${rep.priority_counts.warm} / ${rep.priority_counts.low}`} sub="rule-based priority" />
          <StatCard label="Overdue Follow-Ups" value={rep.overdue_followups} sub="waiting longer than promised"
            tone={rep.overdue_followups ? 'rose' : 'emerald'} />
          <StatCard label="Avg First Contact" value={rep.avg_first_contact_min !== null ? `${rep.avg_first_contact_min} min` : '-'}
            sub={`${rep.responded} of ${rep.leads_in_range} leads contacted`} />
          <StatCard label="Conversion Rate" value={`${rep.conversion_rate}%`} sub="booked / leads in range"
            tone={rep.conversion_rate >= 30 ? 'emerald' : 'amber'} />
          <StatCard label="Leads In Range" value={rep.leads_in_range} sub="current filter" />
        </div>
      )}
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
    </div>
  )
}

interface SLAStore {
  store_id: string; store_name: string; total: number; responded: number
  within_sla_pct: number | null; avg_response_min: number | null; breached: number
}
interface SLARisk {
  lead_id: string; name: string; phone?: string; store_name: string; source_label: string
  score: number; waited_min: number; sla_min: number; urgency: number
}
interface SLAResponse {
  rule: Record<string, number>
  summary: { avg_response_min: number | null; within_sla_pct: number | null
             open_breaches: number; at_risk_count: number; leads_in_window: number }
  stores: SLAStore[]
  at_risk: SLARisk[]
}

/* ---------------------------------- SLA tracking (SLA-01..03) */
function SLAReport({ data, onOpen }: { data?: SLAResponse; onOpen: (id: string) => void }) {
  const qc = useQueryClient()
  const call = useMutation({
    mutationFn: (lead_id: string) =>
      api<{ message: string }>('/queue/action', { method: 'POST', body: { kind: 'lead', ref_id: lead_id, action: 'log_call' } }),
    onSuccess: (r) => { void qc.invalidateQueries(); toast.success(r.message || 'Call logged') },
    onError: (e) => toast.error((e as ApiError).message),
  })
  if (!data) return <Spinner />
  const s = data.summary
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Avg Response Time" value={s.avg_response_min !== null ? `${s.avg_response_min} min` : '-'} sub="first outbound contact" />
        <StatCard label="Within SLA" value={s.within_sla_pct !== null ? `${s.within_sla_pct}%` : '-'} sub="of responded leads"
          tone={s.within_sla_pct !== null && s.within_sla_pct >= 80 ? 'emerald' : s.within_sla_pct !== null && s.within_sla_pct >= 50 ? 'amber' : 'rose'} />
        <StatCard label="Open Breaches" value={s.open_breaches} sub="new leads past target" tone={s.open_breaches ? 'rose' : 'emerald'} />
        <StatCard label="At Risk Of Going Cold" value={s.at_risk_count} sub={`>=50% of target elapsed`} tone={s.at_risk_count ? 'amber' : 'emerald'} />
      </div>

      <Card className="p-3">
        <div className="text-sm font-bold mb-2">Response-Time Targets (BR-16 - editable in Settings → Business Rules)</div>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(data.rule).map(([k, v]) => (
            <span key={k} className="chip bg-slate-50 border-slate-200 text-slate-600">{SOURCE_LABELS[k] || k}: <b>{v} min</b></span>
          ))}
        </div>
      </Card>

      <Card className="overflow-x-auto">
        <table className="w-full min-w-[640px]">
          <thead><tr className="bg-slate-50">
            <th className="th">Store</th><th className="th">Leads (window)</th><th className="th">Responded</th>
            <th className="th">Avg Response</th><th className="th">Within SLA</th><th className="th">Open Breaches</th>
          </tr></thead>
          <tbody>
            {data.stores.map((st) => (
              <tr key={st.store_id} className="hover:bg-slate-50">
                <td className="td font-semibold">{st.store_name}</td>
                <td className="td">{st.total}</td>
                <td className="td">{st.responded}</td>
                <td className="td">{st.avg_response_min !== null ? `${st.avg_response_min} min` : '-'}</td>
                <td className="td">
                  <span className={`chip ${st.within_sla_pct === null ? 'bg-slate-100 border-slate-200 text-slate-400'
                    : st.within_sla_pct >= 80 ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : st.within_sla_pct >= 50 ? 'bg-amber-50 text-amber-700 border-amber-200'
                        : 'bg-rose-50 text-rose-700 border-rose-200'}`}>
                    {st.within_sla_pct === null ? 'no data' : `${st.within_sla_pct}%`}
                  </span>
                </td>
                <td className="td">{st.breached ? <span className="text-rose-600 font-bold">{st.breached}</span> : 0}</td>
              </tr>
            ))}
            {!data.stores.length && <tr><td className="td text-slate-400" colSpan={6}>No leads in this window</td></tr>}
          </tbody>
        </table>
      </Card>

      <Card className="p-3">
        <div className="text-sm font-bold mb-2">Leads At Risk Of Going Cold</div>
        {!data.at_risk.length && <Empty text="Nobody at risk - every new lead is inside its SLA window" />}
        <div className="space-y-2">
          {data.at_risk.map((l) => (
            <div key={l.lead_id} className="border border-slate-100 rounded-lg p-2 flex flex-wrap items-center gap-2">
              <div className="flex-1 min-w-52">
                <button className="font-semibold text-slate-800 hover:text-brand-700" onClick={() => onOpen(l.lead_id)}>{l.name}</button>
                <div className="text-[11px] text-slate-400">{l.source_label} · {l.store_name} · score {l.score}</div>
              </div>
              <div className="w-40">
                <Bar value={Math.min(100, l.urgency / 2)} color={l.urgency >= 100 ? 'bg-rose-500' : 'bg-amber-500'} />
                <div className="text-[10px] text-slate-400 mt-0.5">{l.waited_min} min waited · target {l.sla_min} min</div>
              </div>
              <div className="flex gap-1.5">
                {l.phone && <a href={`tel:${l.phone}`} className="btn-ghost btn-sm !px-2"><Phone size={12} /></a>}
                <button className="btn-primary btn-sm" disabled={call.isPending} onClick={() => call.mutate(l.lead_id)}>
                  {call.isPending && call.variables === l.lead_id ? <BtnSpinner /> : <PhoneCall size={12} />} Log Call
                </button>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

function StatCard({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: 'emerald' | 'amber' | 'rose' }) {
  const toneCls = tone === 'rose' ? 'text-rose-600' : tone === 'amber' ? 'text-amber-600'
    : tone === 'emerald' ? 'text-emerald-600' : 'text-slate-800'
  return (
    <Card className="p-3">
      <div className="text-[11px] text-slate-400">{label}</div>
      <div className={`text-xl font-extrabold ${toneCls}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-400">{sub}</div>}
    </Card>
  )
}

interface HygieneItem { lead: Lead; missing?: string[]; reason?: string }
interface HygieneResponse {
  duplicates: { kind: 'phone' | 'name'; key: string; leads: Lead[] }[]
  incomplete: HygieneItem[]
  stale: HygieneItem[]
  counts: { duplicates: number; incomplete: number; stale: number }
  owners: { _id: string; name: string }[]
}

/* ---------------------------------- hygiene review queue (LEAD-14) */
function HygieneTab({ data, onOpen }: { data?: HygieneResponse; onOpen: (id: string) => void }) {
  const qc = useQueryClient()
  const [mergePair, setMergePair] = useState<{ primary: Lead; dup: Lead } | null>(null)
  const [archiveLead, setArchiveLead] = useState<Lead | null>(null)
  const merge = useMutation({
    mutationFn: (v: { primary_id: string; duplicate_id: string }) =>
      api<{ ok: boolean }>('/leads/merge', { method: 'POST', body: v }),
    onSuccess: () => {
      void qc.invalidateQueries()
      setMergePair(null)
      toast.success('Leads merged - activity history kept on the primary record')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const archive = useMutation({
    mutationFn: (v: { id: string; undo?: boolean }) =>
      api(`/leads/${v.id}/${v.undo ? 'unarchive' : 'archive'}`, { method: 'POST' }),
    onSuccess: () => { void qc.invalidateQueries(); toast.success('Done') },
    onError: (e) => toast.error((e as ApiError).message),
  })
  if (!data) return <Spinner />
  return (
    <div className="space-y-4">
      <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
        Rule-based checks only. Nothing is changed automatically - merging and archiving always ask for confirmation,
        and archived records keep their history and can be restored.
      </div>

      <Card className="p-3">
        <div className="text-sm font-bold mb-1">Possible Duplicates ({data.counts.duplicates})</div>
        <div className="text-xs text-slate-500 mb-2">Same name on different numbers, or the same phone twice. Verify with the customer, then merge.</div>
        {!data.duplicates.length && <Empty text="No duplicate candidates - phone identity is already unique in this app" />}
        <div className="space-y-2">
          {data.duplicates.map((d, i) => (
            <div key={i} className="border border-slate-100 rounded-lg p-2">
              <div className="text-xs font-semibold text-slate-600 mb-1">
                {d.kind === 'phone' ? 'Same phone' : 'Same name'} · {d.key}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {d.leads.map((l) => (
                  <span key={l._id} className="chip border-slate-200 bg-slate-50 text-slate-600">
                    <button className="hover:text-brand-700" onClick={() => onOpen(l._id)}>{l.name}</button>
                    · {l.phone_display}
                    <button className="text-brand-600 hover:underline ml-1"
                      onClick={() => setMergePair({ primary: d.leads[0], dup: l === d.leads[0] ? d.leads[1] : d.leads[0] })}>
                      merge pair
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-3">
        <div className="text-sm font-bold mb-1">Incomplete Records ({data.counts.incomplete})</div>
        <div className="text-xs text-slate-500 mb-2">Open leads missing fields the workflow depends on.</div>
        {!data.incomplete.length && <Empty text="Every open lead has store, service, owner and consent" />}
        <div className="space-y-1.5">
          {data.incomplete.map(({ lead, missing }) => (
            <div key={lead._id} className="flex flex-wrap items-center gap-2 border border-slate-100 rounded-lg p-2">
              <button className="font-semibold text-slate-800 hover:text-brand-700 text-sm" onClick={() => onOpen(lead._id)}>{lead.name}</button>
              <span className="text-xs text-rose-600">Missing: {(missing || []).join(', ')}</span>
              <button className="btn-ghost btn-sm ml-auto" onClick={() => onOpen(lead._id)}>Open & fix</button>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-3">
        <div className="text-sm font-bold mb-1">Stale Open Leads ({data.counts.stale})</div>
        <div className="text-xs text-slate-500 mb-2">Open for 10+ days with no logged contact - follow up or archive.</div>
        {!data.stale.length && <Empty text="Nothing stale in this scope" />}
        <div className="space-y-1.5">
          {data.stale.map(({ lead, reason }) => (
            <div key={lead._id} className="flex flex-wrap items-center gap-2 border border-slate-100 rounded-lg p-2">
              <button className="font-semibold text-slate-800 hover:text-brand-700 text-sm" onClick={() => onOpen(lead._id)}>{lead.name}</button>
              <span className="text-xs text-slate-500">{reason}</span>
              <div className="ml-auto flex gap-1.5">
                <button className="btn-ghost btn-sm" onClick={() => onOpen(lead._id)}>Open</button>
                <button className="btn-danger btn-sm" onClick={() => setArchiveLead(lead)}>Archive</button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* destructive actions are always behind an explicit confirmation */}
      <ConfirmModal open={!!mergePair} onClose={() => setMergePair(null)} danger busy={merge.isPending}
        title="Merge these two leads?"
        confirmLabel="Merge"
        message={`Everything on "${mergePair?.dup.name}" (${mergePair?.dup.phone_display}) - calls, messages, appointments - moves to "${mergePair?.primary.name}" (${mergePair?.primary.phone_display}) and the duplicate is archived with a pointer. The duplicate keeps its history and cannot be lost. This cannot be undone by a single click.`}
        onConfirm={() => mergePair && merge.mutate({ primary_id: mergePair.primary._id, duplicate_id: mergePair.dup._id })} />
      <ConfirmModal open={!!archiveLead} onClose={() => setArchiveLead(null)} danger busy={archive.isPending}
        title={`Archive ${archiveLead?.name}?`}
        confirmLabel="Archive lead"
        message="The lead disappears from the pipeline and queue but keeps its full history. You can restore it from the audit log or by unarchiving."
        onConfirm={() => archiveLead && archive.mutate({ id: archiveLead._id })} />
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
    onSuccess: () => { void qc.invalidateQueries(); toast.success(`${form.name} added to the pipeline`); onClose() },
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
  const nav = useNavigate()
  const [lostOpen, setLostOpen] = useState(false)
  const [lostReason, setLostReason] = useState('Price')
  const [editOpen, setEditOpen] = useState(false)
  const [fuDate, setFuDate] = useState(new Date().toISOString().slice(0, 10))
  const [noteOpen, setNoteOpen] = useState(false)
  const [noteText, setNoteText] = useState('')
  const { data: lead, isLoading } = useQuery({
    queryKey: ['lead', id],
    queryFn: () => api<Lead & { interactions: { _id: string; channel: string; direction: string; content: string; outcome: string; at: string }[]; appointments: { _id: string; date: string; start_hour: number; service_name: string; status: string; store_name: string }[]; owner_name?: string; sla_deadline: string; score_factors: { f: string; v: number; d?: string }[]; customer?: unknown }>(`/leads/${id}`),
  })

  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: object }) => api(`/leads/${id}/${path}`, { method: 'POST', body }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      const msgs: Record<string, string> = {
        interactions: 'Logged - visible in the activity log',
        stage: 'Stage updated',
        'follow-up': 'Follow-up scheduled',
        lost: 'Lead marked lost',
        reopen: 'Lead reopened',
      }
      toast.success(msgs[v.path] || 'Done')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading || !lead) return <Modal open onClose={onClose} title="Lead"><Spinner /></Modal>

  const msgPreview = `Hi ${lead.name}, thanks for your interest in ${lead.service_name || 'our services'} at ${lead.store_name || 'our store'}! When can we call you to fix a slot?`

  return (
    <Modal open onClose={onClose} wide title={`Lead - ${lead.name}`}>
      <div className="grid lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`chip ${STAGE_COLORS[lead.stage]}`}>{STAGE_LABELS[lead.stage]}</span>
            <TierChip lead={lead} />
            <span className={`text-xs px-2 py-0.5 rounded font-bold ${bandColor(lead.band)}`}>Score {lead.score}</span>
            <span className="text-xs text-slate-400">
              SLA {lead.sla_deadline?.slice(11, 16)} · Owner <b className="text-slate-600">{lead.owner_name || 'Unassigned'}</b>
              {lead.follow_up_at ? <> · Next follow-up <b className="text-slate-600">{fmtDateTime(lead.follow_up_at)}</b></> : ' · No follow-up set'}
            </span>
            {lead.sla_breached && <span className="chip bg-rose-100 text-rose-700 border-rose-200">SLA Breached</span>}
            {lead.archived && <span className="chip bg-slate-100 text-slate-500 border-slate-200">Archived{lead.merged_into ? ' (merged)' : ''}</span>}
            {lead.customer_id && (
              <button className="btn-ghost btn-sm" onClick={() => nav(`/customers?customer=${lead.customer_id}`)}>
                <Link2 size={12} /> Customer Timeline
              </button>
            )}
          </div>

          <div className="flex flex-wrap gap-1.5">
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'call', direction: 'out', outcome: 'call_made', content: 'Click-to-call logged' } })}>{act.isPending && act.variables?.path === 'interactions' && (act.variables?.body as { outcome?: string })?.outcome === 'call_made' ? <BtnSpinner /> : <Phone size={12} />} Call</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'whatsapp_sent', content: msgPreview } })}>{act.isPending && act.variables?.path === 'interactions' && (act.variables?.body as { outcome?: string })?.outcome === 'whatsapp_sent' ? <BtnSpinner /> : <MessageCircle size={12} />} WhatsApp</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'sms', direction: 'out', outcome: 'sms_sent', content: msgPreview } })}>{act.isPending && act.variables?.path === 'interactions' && (act.variables?.body as { outcome?: string })?.outcome === 'sms_sent' ? <BtnSpinner /> : <MessageCircle size={12} />} SMS</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending || !noteText.trim()}
              onClick={() => { act.mutate({ path: 'interactions', body: { channel: 'note', direction: 'out', outcome: 'note', content: noteText.trim() } }); setNoteText('') }}>
              <StickyNote size={12} /> Log Note
            </button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'visit', direction: 'out', outcome: 'meeting', content: `Meeting held - ${lead.service_name || 'service'} discussed` } })}><CalendarCheck2 size={12} /> Meeting</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'call', direction: 'out', outcome: 'no_answer', content: 'Call attempt - no answer' } })}><PhoneMissed size={12} /> No Answer</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ad_shared', content: 'Image ad shared' } })}><Image size={12} /> Image Ad</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ugc_shared', content: 'UGC video shared' } })}><Video size={12} /> UGC</button>
            <button className="btn-ghost btn-sm" disabled={act.isPending} onClick={() => act.mutate({ path: 'interactions', body: { channel: 'whatsapp', direction: 'out', outcome: 'ugc_shared', content: 'Customer story shared' } })}><Share2 size={12} /> Story</button>
            {lead.stage !== 'booked' && lead.stage !== 'lost' && (
              <button className="btn-primary btn-sm" onClick={() => onBook(lead)}><CalendarPlus size={12} /> Offer Appointment</button>
            )}
            {lead.stage !== 'booked' && lead.stage !== 'lost' && (
              <button className="btn-danger btn-sm" onClick={() => setLostOpen(true)}><XCircle size={12} /> Mark Lost</button>
            )}
            <button className="btn-ghost btn-sm" onClick={() => setEditOpen(true)}><Pencil size={12} /> Edit</button>
          </div>

          <div className="flex gap-1.5">
            <input className="input flex-1" placeholder="Type a note and press Enter to log it against this lead…"
              value={noteText} onChange={(e) => setNoteText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && noteText.trim() && !act.isPending) { act.mutate({ path: 'interactions', body: { channel: 'note', direction: 'out', outcome: 'note', content: noteText.trim() } }); setNoteText('') } }} />
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
                <button key={s} disabled={lead.stage === s || lead.stage === 'booked' || act.isPending}
                  onClick={() => act.mutate({ path: 'stage', body: { stage: s } })}
                  className={`btn-sm border rounded px-2 py-1 ${lead.stage === s ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>
                  {STAGE_LABELS[s]}
                </button>
              ))}
              <span className="mx-2 text-slate-300">|</span>
              <input type="date" className="input w-36" value={fuDate} onChange={(e) => setFuDate(e.target.value)} />
              <button className="btn-ghost btn-sm" disabled={act.isPending}
                onClick={() => act.mutate({ path: 'follow-up', body: { follow_up_at: new Date(fuDate + 'T10:00:00+00:00').toISOString() } })}>
                {act.isPending && act.variables?.path === 'follow-up' ? <BtnSpinner /> : null} Schedule Follow-Up
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

        {/* right column: priority + score factors + cross-sell */}
        <div className="space-y-4">
          <div className="card p-3">
            <div className="text-sm font-bold mb-1.5">Priority (Rule-Based)</div>
            <div className="flex items-center gap-2 mb-1.5">
              <TierChip lead={lead} />
              <span className="text-xs text-slate-400">score {lead.score}/100 · fixed rules, no AI</span>
            </div>
            <ul className="text-xs text-slate-500 space-y-1">
              {(lead.priority_reasons || []).map((r, i) => (
                <li key={i} className="flex gap-1.5"><span className="text-brand-500">•</span>{r}</li>
              ))}
              {!(lead.priority_reasons || []).length && <li>No factors recorded yet</li>}
            </ul>
          </div>
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
    onSuccess: () => { void qc.invalidateQueries(); toast.success('Lead updated'); onClose() },
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
