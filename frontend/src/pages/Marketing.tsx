import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Play, Pause, Square, Copy, Wand2, Send, Clock, Lightbulb } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useFilter, filterParams } from '../lib/store'
import { fmtMoney, fmtDateTime } from '../lib/format'
import { Card, Modal, Tabs, Empty, Spinner, ErrorBox, SectionTitle, Bar, BtnSpinner, ConfirmModal } from '../components/ui'
import AutomationRules from '../components/AutomationRules'

interface Campaign {
  _id: string; name: string; kind: string; segment_key: string; channel: string
  status: string; offer_pct: number; template_body: string
  stats: { audience: number; sent: number; delivered: number; read: number; replied: number; booked: number; revenue: number }
  created_at: string; created_by: string
}
interface Preview {
  audience_size: number; excluded_consent: number; excluded_frequency_cap: number
  expected_bookings: number; expected_bookings_range: number[]; expected_revenue: number
  expected_revenue_range: number[]; samples: { person: string; text: string }[]
  best_send_time: string; quiet_hours_ok: boolean
}

const WIZARD_TEMPLATES = [
  { name: 'Hot Lead Offer', body: 'Hi {name}, your {service} slot at {store} is ready! This week {discount} off - final price {price}. Shall I hold it?' },
  { name: 'Win-Back Offer', body: 'Hi {name}, we miss you at {store}! Come back for {service} at a special price of {price} this week.' },
  { name: 'Renewal Reminder', body: 'Hi {name}, your plan at {store} ends soon. Renew now and keep your preferred slot and benefits!' },
  { name: 'New Lead Welcome', body: 'Hi {name}, thanks for your interest in {service} at {store}! When can we call you to fix a slot?' },
]

export default function Marketing() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const qc = useQueryClient()
  const location = useLocation()
  const nav = useNavigate()
  const [tab, setTab] = useState('campaigns')
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizard, setWizard] = useState<Partial<{
    kind: 'leads' | 'customers'; segment_key: string; sub_key: string | null; name: string
    template_body: string; offer_pct: number; channel: string; schedule: string; period: string
  }>>({ kind: 'leads', segment_key: 'hot', offer_pct: 10, channel: 'whatsapp', period: '30' })
  const [step, setStep] = useState(1)
  const [launchErr, setLaunchErr] = useState('')
  const [launching, setLaunching] = useState(false)

  const { data: leadSegs } = useQuery({
    queryKey: ['lead-segments', fp.period, fp.store_id],
    queryFn: () => api<{ segments: { key: string; label: string; count: number; kind: string; options?: { key: string; label: string; count: number }[] }[] }>('/lead-segments' + qs(fp)),
  })
  const { data: custSegs } = useQuery({
    queryKey: ['customer-segments', fp.period, fp.store_id],
    queryFn: () => api<{ segments: { key: string; label: string; count: number }[]; ai_recommendation: { segment_key: string; label: string; text: string } | null }>('/customer-segments' + qs(fp)),
  })
  const { data: campaigns } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<{ campaigns: Campaign[] }>('/campaigns'),
    refetchInterval: 30_000,
  })
  const { data: automations } = useQuery({
    queryKey: ['automations'],
    queryFn: () => api<{ automations: { name: string; trigger: string; enabled: boolean; kind: string; segment_key: string; stats: { runs: number; messages_sent: number } }[] }>('/automations'),
  })
  const { data: templates } = useQuery({
    queryKey: ['templates'],
    queryFn: () => api<{ templates: { _id: string; name: string; channel: string; body: string; provider_status: string }[] }>('/templates'),
  })

  const action = useMutation({
    mutationFn: ({ id, act }: { id: string; act: string }) => api(`/campaigns/${id}/${act}`, { method: 'POST' }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      const msgs: Record<string, string> = {
        pause: 'Campaign paused - sending stops immediately',
        resume: 'Campaign resumed',
        stop: 'Campaign stopped - audience members are not re-contacted',
        duplicate: 'Campaign duplicated as a draft',
      }
      toast.success(msgs[v.act] || 'Campaign updated')
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const toggleAuto = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) => api(`/automations/${encodeURIComponent(name)}`, { method: 'PUT', body: { enabled } }),
    // optimistic: the switch flips instantly, rolls back if the server rejects
    onMutate: async ({ name, enabled }) => {
      await qc.cancelQueries({ queryKey: ['automations'] })
      const prev = qc.getQueryData(['automations'])
      qc.setQueryData<{ automations: { name: string; enabled: boolean }[] }>(['automations'], (old) =>
        old ? { ...old, automations: old.automations.map((a) => (a.name === name ? { ...a, enabled } : a)) } : old)
      return { prev }
    },
    onSuccess: (_r, v) => toast.success(v.enabled ? `${v.name} enabled` : `${v.name} paused`),
    onError: (e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(['automations'], ctx.prev)
      toast.error((e as ApiError).message)
    },
    onSettled: () => void qc.invalidateQueries({ queryKey: ['automations'] }),
  })
  const runAuto = useMutation({
    mutationFn: (name: string) => api<{ sent: number; audience: number; excluded_consent: number }>('/automations/run', { method: 'POST', body: { name } }),
    onSuccess: (r) => {
      void qc.invalidateQueries()
      toast.success(`Sent ${r.sent} of ${r.audience} (excluded ${r.excluded_consent} without consent)`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })

  const { data: preview } = useQuery({
    queryKey: ['campaign-preview', wizard],
    queryFn: () => api<Preview>('/campaigns/preview', {
      method: 'POST',
      body: { kind: wizard.kind, segment_key: wizard.segment_key, sub_key: wizard.sub_key || undefined, offer_pct: wizard.offer_pct, channel: wizard.channel, template_body: wizard.template_body },
    }),
    enabled: wizardOpen && step === 5 && !!wizard.template_body,
  })

  function openWizard(kind: 'leads' | 'customers', segment_key: string, sub_key: string | null = null) {
    setWizard({ kind, segment_key, sub_key, offer_pct: kind === 'leads' ? 10 : 5, channel: 'whatsapp', period: '30', name: '', template_body: WIZARD_TEMPLATES[0].body })
    setStep(1)
    setWizardOpen(true)
  }

  // deep link: Customer 360 "Add To Campaign" opens the wizard pre-targeted
  useEffect(() => {
    const st = (location.state || {}) as { wizard?: { kind: 'leads' | 'customers'; segment_key: string; sub_key?: string | null; label?: string } }
    if (st.wizard?.segment_key) {
      openWizard(st.wizard.kind || 'customers', st.wizard.segment_key, st.wizard.sub_key || null)
      if (st.wizard.label) toast.info(`Campaign wizard opened for the "${st.wizard.label}" segment`)
      nav(location.pathname, { replace: true, state: null })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <Tabs active={tab} onChange={setTab} tabs={[
        { key: 'campaigns', label: 'Active Campaigns', count: campaigns?.campaigns.filter((c) => c.status === 'live').length },
        { key: 'segments', label: 'Audience Segments' },
        { key: 'automations', label: 'Automations' },
        { key: 'rules', label: 'Follow-Up Rules' },
        { key: 'templates', label: 'Templates' },
        { key: 'quality', label: 'Source Quality' },
        { key: 'history', label: 'History', count: campaigns?.campaigns.length },
      ]} />

      {tab === 'segments' && (
        <div className="grid lg:grid-cols-2 gap-5">
          <Card className="p-4">
            <SectionTitle title="Lead Campaign Segments" subtitle="Built from live lead data inside the global filter"
              right={<button className="btn-primary btn-sm" onClick={() => openWizard('leads', 'hot')}><Wand2 size={12} /> New Campaign</button>} />
            <div className="space-y-2">
              {(leadSegs?.segments || []).filter((s) => s.kind === 'fixed').map((s) => (
                <div key={s.key} className="flex items-center justify-between border border-slate-200 rounded-lg px-3 py-2">
                  <span className="text-sm font-medium">{s.label}</span>
                  <span className="flex items-center gap-2">
                    <span className="text-sm font-bold">{s.count}</span>
                    <button className="btn-primary btn-sm" onClick={() => openWizard('leads', s.key)}>Campaign →</button>
                  </span>
                </div>
              ))}
              {(leadSegs?.segments || []).filter((s) => s.kind === 'group').map((g) => (
                <div key={g.key} className="border border-slate-200 rounded-lg px-3 py-2">
                  <div className="text-sm font-bold text-slate-700 mb-1.5">{g.label}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {(g.options || []).slice(0, 8).map((o) => (
                      <button key={o.key} onClick={() => openWizard('leads', g.key, o.key)}
                        className="chip bg-slate-50 border-slate-200 text-slate-600 hover:border-brand-400">
                        {o.label} · {o.count}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card className="p-4">
            <SectionTitle title="Customer Segments" subtitle="With a rule-based recommendation" />
            {custSegs?.ai_recommendation && (
              <div className="bg-gradient-to-r from-brand-50 to-violet-50 border border-brand-100 rounded-lg p-3 mb-3">
                <div className="text-xs font-bold text-brand-700 flex items-center gap-1"><Lightbulb size={12} /> Rule-Based Recommendation</div>
                <div className="text-sm mt-1">{custSegs.ai_recommendation.text}</div>
                <button className="btn-primary btn-sm mt-2" onClick={() => openWizard('customers', custSegs.ai_recommendation!.segment_key)}>
                  Start This Campaign
                </button>
              </div>
            )}
            <div className="space-y-2">
              {(custSegs?.segments || []).map((s) => (
                <div key={s.key} className="flex items-center justify-between border border-slate-200 rounded-lg px-3 py-2">
                  <span className="text-sm font-medium">{s.label}</span>
                  <span className="flex items-center gap-2">
                    <span className="text-sm font-bold">{s.count}</span>
                    <button className="btn-primary btn-sm" onClick={() => openWizard('customers', s.key)}>Campaign →</button>
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {tab === 'campaigns' && (
        <div className="space-y-3">
          <button className="btn-primary" onClick={() => openWizard('leads', 'hot')}><Wand2 size={14} /> New Campaign (5-Step Wizard)</button>
          {(campaigns?.campaigns || []).filter((c) => c.status !== 'completed' && c.status !== 'stopped').map((c) => (
            <CampaignRow key={c._id} c={c} pendingAct={action.isPending && action.variables?.id === c._id ? action.variables?.act : undefined}
              onAction={(a) => action.mutate({ id: c._id, act: a })} />
          ))}
          {!campaigns?.campaigns.filter((c) => c.status !== 'completed' && c.status !== 'stopped').length && <Card className="p-6"><Empty text="No active campaigns - launch one from a segment" /></Card>}
        </div>
      )}

      {tab === 'history' && (
        <div className="space-y-3">
          {(campaigns?.campaigns || []).map((c) => <CampaignRow key={c._id} c={c} results
            pendingAct={action.isPending && action.variables?.id === c._id ? action.variables?.act : undefined}
            onAction={(a) => action.mutate({ id: c._id, act: a })} />)}
        </div>
      )}

      {tab === 'automations' && (
        <div className="space-y-3">
          <div className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
            These automations <b>send WhatsApp/SMS messages</b> - only when you click Run Now, and only to customers
            with consent. For internal lead rules (assignment, reminders) use the <b>Follow-Up Rules</b> tab - those
            never send messages.
          </div>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {(automations?.automations || []).map((a) => (
              <Card key={a.name} className="p-4">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-sm">{a.name}</span>
                  <button onClick={() => toggleAuto.mutate({ name: a.name, enabled: !a.enabled })}
                    disabled={toggleAuto.isPending && toggleAuto.variables?.name === a.name}
                    title={a.enabled ? 'Click to pause this automation' : 'Click to enable this automation'}
                    className={`w-9 h-5 rounded-full relative transition ${a.enabled ? 'bg-emerald-500' : 'bg-slate-300'}`}>
                    <span className={`absolute top-0.5 w-4 h-4 bg-white rounded-full transition-all ${a.enabled ? 'left-4.5' : 'left-0.5'}`} style={{ left: a.enabled ? 18 : 2 }} />
                  </button>
                </div>
                <div className="text-xs text-slate-500 mt-1">{a.trigger}</div>
                <div className="text-[11px] text-slate-400 mt-0.5">Runs: {a.stats.runs} · Messages: {a.stats.messages_sent}</div>
                <button className="btn-ghost btn-sm mt-2" onClick={() => runAuto.mutate(a.name)}
                  disabled={!a.enabled || (runAuto.isPending && runAuto.variables === a.name)}>
                  {runAuto.isPending && runAuto.variables === a.name ? <><BtnSpinner /> Running…</> : <><Play size={11} /> Run Now</>}
                </button>
                {runAuto.isSuccess && runAuto.variables === a.name && (
                  <div className="text-[11px] text-emerald-600 mt-1">Sent {runAuto.data?.sent} of {runAuto.data?.audience} (excl. {runAuto.data?.excluded_consent} without consent)</div>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}

      {tab === 'rules' && <AutomationRules />}

      {tab === 'quality' && <SourceQuality />}

      {tab === 'templates' && (
        <div className="grid sm:grid-cols-2 gap-3">
          {(templates?.templates || []).map((t) => (
            <Card key={t._id} className="p-4">
              <div className="flex items-center justify-between">
                <span className="font-bold text-sm">{t.name}</span>
                <span className={`chip ${t.provider_status === 'approved' ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-amber-50 text-amber-700 border-amber-200'}`}>
                  WhatsApp {t.provider_status}
                </span>
              </div>
              <div className="text-xs text-slate-600 bg-slate-50 rounded-lg p-2 mt-2">{t.body}</div>
            </Card>
          ))}
        </div>
      )}

      {/* 5-step wizard */}
      <Modal open={wizardOpen} onClose={() => setWizardOpen(false)} wide title={`Campaign Wizard - Step ${step} Of 5`}>
        <div className="flex gap-1 mb-4">
          {['Audience', 'Message', 'Offer', 'Schedule', 'Review'].map((s, i) => (
            <div key={s} className={`flex-1 text-center text-xs font-semibold py-1.5 rounded ${step === i + 1 ? 'bg-brand-600 text-white' : step > i + 1 ? 'bg-brand-100 text-brand-700' : 'bg-slate-100 text-slate-400'}`}>{i + 1}. {s}</div>
          ))}
        </div>

        {step === 1 && (
          <div>
            <div className="flex gap-2 mb-3">
              <button onClick={() => setWizard({ ...wizard, kind: 'leads' })} className={`btn-sm px-3 py-1.5 rounded border ${wizard.kind === 'leads' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>Lead Campaign</button>
              <button onClick={() => setWizard({ ...wizard, kind: 'customers' })} className={`btn-sm px-3 py-1.5 rounded border ${wizard.kind === 'customers' ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>Customer Campaign</button>
            </div>
            <label className="label">Audience Segment</label>
            <select className="select" value={wizard.segment_key || ''} onChange={(e) => setWizard({ ...wizard, segment_key: e.target.value, sub_key: null })}>
              {wizard.kind === 'leads'
                ? (leadSegs?.segments || []).filter((s) => s.kind === 'fixed').map((s) => <option key={s.key} value={s.key}>{s.label} ({s.count})</option>)
                : (custSegs?.segments || []).map((s) => <option key={s.key} value={s.key}>{s.label} ({s.count})</option>)}
            </select>
            <div className="text-xs text-slate-400 mt-2">The audience is limited to consented people inside the global filter (BR-21); quiet hours and frequency caps are enforced at send time.</div>
          </div>
        )}

        {step === 2 && (
          <div>
            <label className="label">Template</label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {WIZARD_TEMPLATES.map((t) => (
                <button key={t.name} className={`btn-sm border rounded px-2 py-1 ${wizard.template_body === t.body ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}
                  onClick={() => setWizard({ ...wizard, template_body: t.body })}>{t.name}</button>
              ))}
            </div>
            <label className="label">Message (placeholders: {'{name} {service} {store} {price} {discount} {alt_store} {source}'})</label>
            <textarea className="input h-28" value={wizard.template_body || ''} onChange={(e) => setWizard({ ...wizard, template_body: e.target.value })} />
            <label className="label mt-2">Channel</label>
            <select className="select w-52" value={wizard.channel} onChange={(e) => setWizard({ ...wizard, channel: e.target.value })}>
              <option value="whatsapp">WhatsApp</option><option value="sms">SMS</option>
            </select>
          </div>
        )}

        {step === 3 && (
          <div>
            <label className="label">Discount Offer ({wizard.offer_pct}% Off The Service Price)</label>
            <div className="flex gap-1.5">
              {[0, 5, 10, 15, 20].map((p) => (
                <button key={p} onClick={() => setWizard({ ...wizard, offer_pct: p })}
                  className={`btn-sm border rounded px-3 py-1.5 ${wizard.offer_pct === p ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>{p}%</button>
              ))}
            </div>
            <div className="text-xs text-slate-400 mt-2">The offer raises each lead's chance to book: chance = stage base × (0.5 + score/100) × (1 + offer/200).</div>
          </div>
        )}

        {step === 4 && (
          <div>
            <label className="label">Campaign Name</label>
            <input className="input" placeholder="e.g. Hot Lead Offer - Week 47" value={wizard.name || ''} onChange={(e) => setWizard({ ...wizard, name: e.target.value })} />
            <label className="label mt-3">When</label>
            <div className="flex gap-2">
              <button onClick={() => setWizard({ ...wizard, schedule: '' })} className={`btn-sm border rounded px-3 py-1.5 ${!wizard.schedule ? 'bg-brand-600 text-white border-brand-600' : 'bg-white border-slate-200'}`}>
                <Send size={12} className="inline" /> Send Now
              </button>
              <span className="chip bg-slate-50 border-slate-200 text-slate-500"><Clock size={11} /> Best send time: {preview?.best_send_time || '11:00 AM'}</span>
            </div>
            <div className="text-xs text-amber-600 mt-2">Promotional messages are only delivered inside quiet hours 09:00-21:00 (BR-12) - outside the window they queue automatically.</div>
          </div>
        )}

        {step === 5 && (
          <div>
            {!preview ? <Spinner /> : (
              <div className="grid md:grid-cols-2 gap-4">
                <div className="space-y-3">
                  <div className="card p-3">
                    <div className="flex justify-between text-sm"><span>Audience</span><b>{preview.audience_size}</b></div>
                    <div className="flex justify-between text-xs text-slate-500"><span>Excluded (no consent)</span><span>{preview.excluded_consent}</span></div>
                    <div className="flex justify-between text-xs text-slate-500"><span>Excluded (frequency cap 3/week)</span><span>{preview.excluded_frequency_cap}</span></div>
                  </div>
                  <div className="card p-3">
                    <div className="text-xs font-bold text-slate-500 mb-1">Expected Bookings</div>
                    <div className="text-lg font-extrabold text-emerald-600">{preview.expected_bookings_range[0]} - {preview.expected_bookings_range[1]}</div>
                    <Bar value={Math.min(100, preview.expected_bookings * 8)} color="bg-emerald-500" />
                  </div>
                  <div className="card p-3">
                    <div className="text-xs font-bold text-slate-500 mb-1">Expected Revenue</div>
                    <div className="text-lg font-extrabold text-emerald-600">{fmtMoney(preview.expected_revenue_range[0])} - {fmtMoney(preview.expected_revenue_range[1])}</div>
                    <div className="text-[11px] text-slate-400">Sum of per-person chance × discounted price (12.4)</div>
                  </div>
                  <button className="btn-primary w-full justify-center" disabled={!wizard.name || !preview.audience_size || launching}
                    onClick={async () => {
                      if (launching) return
                      setLaunchErr('')
                      setLaunching(true)
                      try {
                        await api('/campaigns', {
                          method: 'POST',
                          body: { kind: wizard.kind, segment_key: wizard.segment_key, sub_key: wizard.sub_key || undefined, name: wizard.name, offer_pct: wizard.offer_pct, channel: wizard.channel, template_body: wizard.template_body, period: wizard.period, schedule_at: wizard.schedule || undefined },
                        })
                        toast.success(`Campaign "${wizard.name}" launched to ${preview.audience_size} people`)
                        setWizardOpen(false)
                        setTab('campaigns')
                        void qc.invalidateQueries()
                      } catch (e) { setLaunchErr((e as ApiError).message) } finally { setLaunching(false) }
                    }}>
                    {launching ? <><BtnSpinner /> Launching…</> : `Launch Campaign (${preview.audience_size} People)`}
                  </button>
                  {launchErr && <ErrorBox error={new Error(launchErr)} />}
                </div>
                <div>
                  <div className="text-xs font-bold text-slate-500 mb-1">WhatsApp-Style Preview (Per-Lead Personalisation)</div>
                  <div className="space-y-2 max-h-72 overflow-y-auto">
                    {preview.samples.map((s) => (
                      <div key={s.person} className="bg-emerald-50 border border-emerald-200 rounded-lg rounded-tl-none px-3 py-2 text-xs">{s.text}</div>
                    ))}
                    {!preview.samples.length && <Empty text="Empty audience for this filter" />}
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        <div className="flex justify-between mt-4">
          <button className="btn-ghost" disabled={step === 1} onClick={() => setStep(step - 1)}>Back</button>
          <button className="btn-primary" disabled={step === 5 || (step === 1 && !wizard.segment_key)} onClick={() => setStep(step + 1)}>Next →</button>
        </div>
      </Modal>
    </div>
  )
}

function CampaignRow({ c, onAction, results, pendingAct }: { c: Campaign; onAction: (act: string) => void; results?: boolean; pendingAct?: string }) {
  const [detailOpen, setDetailOpen] = useState(false)
  const [stopOpen, setStopOpen] = useState(false)
  const { data: res } = useQuery({
    queryKey: ['campaign-results', c._id],
    queryFn: () => api<{ messages: { _id: string; person_name: string; status: string; rendered_text: string; reply_text?: string }[] }>(`/campaigns/${c._id}/results`),
    enabled: detailOpen,
  })
  const statusColor: Record<string, string> = {
    live: 'bg-emerald-100 text-emerald-700', paused: 'bg-amber-100 text-amber-700',
    scheduled: 'bg-sky-100 text-sky-700', completed: 'bg-slate-200 text-slate-600', stopped: 'bg-rose-100 text-rose-700',
  }
  const conv = c.stats.sent ? Math.round((100 * c.stats.replied) / c.stats.sent) : 0
  const busy = (act: string) => pendingAct === act
  const anyBusy = !!pendingAct
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`chip ${statusColor[c.status]}`}>{c.status}</span>
        <span className="font-bold text-slate-800">{c.name}</span>
        <span className="text-xs text-slate-400">{c.kind} · {c.channel} · {c.offer_pct}% off · by {c.created_by} · {fmtDateTime(c.created_at)}</span>
        <span className="ml-auto flex gap-1">
          {c.status === 'live' && <button className="btn-ghost btn-sm" disabled={anyBusy} onClick={() => onAction('pause')}>{busy('pause') ? <BtnSpinner /> : <Pause size={11} />} Pause</button>}
          {c.status === 'paused' && <button className="btn-ghost btn-sm" disabled={anyBusy} onClick={() => onAction('resume')}>{busy('resume') ? <BtnSpinner /> : <Play size={11} />} Resume</button>}
          {c.status !== 'completed' && c.status !== 'stopped' && <button className="btn-ghost btn-sm text-rose-600" disabled={anyBusy} onClick={() => setStopOpen(true)}>{busy('stop') ? <BtnSpinner /> : <Square size={11} />} Stop</button>}
          <button className="btn-ghost btn-sm" disabled={anyBusy} onClick={() => onAction('duplicate')}>{busy('duplicate') ? <BtnSpinner /> : <Copy size={11} />} Duplicate</button>
          <button className="btn-ghost btn-sm" onClick={() => setDetailOpen(true)}>Results</button>
        </span>
      </div>
      <div className="grid grid-cols-4 md:grid-cols-8 gap-2 mt-3 text-center">
        {[['Audience', c.stats.audience], ['Sent', c.stats.sent], ['Delivered', c.stats.delivered], ['Read', c.stats.read], ['Replied', c.stats.replied], ['Booked', c.stats.booked], ['Revenue', fmtMoney(c.stats.revenue)], ['Reply Rate', `${conv}%`]].map(([k, v]) => (
          <div key={String(k)} className="bg-slate-50 rounded-lg py-1.5">
            <div className="text-sm font-bold text-slate-800">{v}</div>
            <div className="text-[10px] text-slate-400">{k}</div>
          </div>
        ))}
      </div>
      <ConfirmModal open={stopOpen} onClose={() => setStopOpen(false)} danger busy={pendingAct === 'stop'}
        title={`Stop "${c.name}"?`} confirmLabel="Stop Campaign"
        message={`Stopping halts all sending for this campaign. ${c.stats.sent} of ${c.stats.audience} messages already went out. This cannot be resumed - duplicate it instead if you plan to run it again.`}
        onConfirm={() => { setStopOpen(false); onAction('stop') }} />
      <Modal open={detailOpen} onClose={() => setDetailOpen(false)} wide title={`Results - ${c.name}`}>
        <div className="space-y-1.5 max-h-96 overflow-y-auto">
          {(res?.messages || []).map((m) => (
            <div key={m._id} className="border border-slate-100 rounded-lg p-2 text-sm">
              <div className="flex justify-between"><b>{m.person_name}</b>
                <span className={`chip ${m.status === 'replied' ? 'bg-sky-100 text-sky-700 border-sky-200' : 'bg-slate-100 border-slate-200 text-slate-500'}`}>{m.status}</span></div>
              <div className="text-xs text-slate-500 mt-1">{m.rendered_text}</div>
              {m.reply_text && <div className="text-xs text-sky-700 mt-1">↩ "{m.reply_text}"</div>}
            </div>
          ))}
          {!res?.messages.length && <Empty text="No messages dispatched yet" />}
        </div>
      </Modal>
    </Card>
  )
}

/* ---------------------------------- source quality (moved from Leads - marketing oversight) */
interface SQSource {
  source: string; label: string; leads: number; contacted: number; contact_rate: number
  booked: number; visits: number; revenue: number; revenue_display: string; conversion_pct: number
  spend: number; cost_per_lead: number | null; cost_per_visit: number | null; roas: number | null
}
interface SQResponse { sources: SQSource[]; recommendation: string | null; totals: { revenue: number; spend: number } }

function SourceQuality() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const { data, isLoading } = useQuery({
    queryKey: ['source-quality', fp.period, fp.store_id],
    queryFn: () => api<SQResponse>('/analytics/source-quality' + qs(fp)),
  })
  if (isLoading) return <Spinner />
  if (!data) return <ErrorBox error={new Error('Could not load source quality')} />
  const maxRev = Math.max(...data.sources.map((s) => s.revenue), 1)
  return (
    <div className="space-y-4">
      {data.recommendation && (
        <div className="bg-brand-50 border border-brand-200 rounded-lg px-4 py-3 text-sm text-brand-900">
          <Lightbulb size={14} className="inline mr-1 -mt-0.5" />
          <b>Marketing read (rule-based):</b> {data.recommendation}
        </div>
      )}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[900px]">
          <thead><tr className="bg-slate-50">
            <th className="th">Source</th><th className="th">Ad Spend</th><th className="th">Leads</th>
            <th className="th">Contacted</th><th className="th">Booked</th><th className="th">Completed Visits</th>
            <th className="th">Revenue</th><th className="th">Visit Conv.</th><th className="th">Cost / Lead</th>
            <th className="th">Cost / Visit</th><th className="th">ROAS</th>
          </tr></thead>
          <tbody>
            {data.sources.map((s) => (
              <tr key={s.source} className={`hover:bg-slate-50 ${s.revenue === maxRev && s.revenue > 0 ? 'bg-emerald-50/50' : ''}`}>
                <td className="td font-semibold">{s.label}</td>
                <td className="td text-sm">{s.spend ? `Rs ${s.spend.toLocaleString('en-IN')}` : '-'}</td>
                <td className="td">{s.leads}</td>
                <td className="td">{s.contact_rate}%</td>
                <td className="td">{s.booked}</td>
                <td className="td font-semibold">{s.visits}</td>
                <td className="td font-semibold">{s.revenue_display}</td>
                <td className="td">
                  <span className={`chip ${s.conversion_pct >= 15 ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                    : s.conversion_pct >= 5 ? 'bg-amber-50 text-amber-700 border-amber-200'
                      : 'bg-rose-50 text-rose-700 border-rose-200'}`}>{s.conversion_pct}%</span>
                </td>
                <td className="td text-sm">{s.cost_per_lead !== null ? `Rs ${s.cost_per_lead.toLocaleString('en-IN')}` : '-'}</td>
                <td className="td text-sm">{s.cost_per_visit !== null ? `Rs ${s.cost_per_visit.toLocaleString('en-IN')}` : '-'}</td>
                <td className="td">
                  {s.roas !== null ? (
                    <span className={`chip ${s.roas >= 1 ? 'bg-emerald-50 text-emerald-700 border-emerald-200' : 'bg-rose-50 text-rose-700 border-rose-200'}`}>
                      {s.roas}x
                    </span>
                  ) : <span className="text-slate-300">organic</span>}
                </td>
              </tr>
            ))}
            {!data.sources.length && <tr><td className="td text-slate-400" colSpan={11}>No leads in this period</td></tr>}
          </tbody>
        </table>
        <div className="px-3 py-2 border-t border-slate-100 space-y-1">
          {data.sources.map((s) => (
            <div key={s.source} className="flex items-center gap-2">
              <span className="text-[11px] text-slate-500 w-28 truncate">{s.label}</span>
              <Bar value={(s.revenue / maxRev) * 100} color="bg-emerald-500" />
              <span className="text-[11px] text-slate-400 w-24 text-right">{s.revenue_display}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}
