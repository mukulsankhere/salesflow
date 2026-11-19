import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  PhoneCall, Ear, MessageSquareWarning, Bot, Wallet, Zap, Target,
  Pause, Play, Settings2, MessageCircle, Smartphone, Send, ChevronRight, Timer,
  MapPin, RefreshCw, BadgeCheck,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import { toast } from '../lib/toast'
import { useCountUp } from '../lib/useCountUp'
import { Card, SectionTitle, Spinner, ErrorBox, BtnSpinner, Modal, Empty, Tabs, Bar } from '../components/ui'

interface TranscriptLine { who: 'ai' | 'lead' | 'admin'; text: string; tag: string; at: string }
interface StoreOffer {
  store_id: string; store_name: string; area?: string; km: number
  pct: number; price_before: number; price_after: number
}
interface AiCall {
  _id: string; person_name: string; phone?: string; service?: string; store?: string
  location?: string; tier: string; score: number; outcome: string; stage_tag: string; sentiment: string
  intents: string[]; transcript: TranscriptLine[]; cost: number
  store_load_pct?: number; overload?: boolean; offers?: StoreOffer[]; offer_accepted?: boolean
  rerouted?: boolean; booked_store?: string; discount_pct?: number; data_synced?: boolean
  slot_pref?: string; slot_day?: string; slot_hour?: number
  kind?: 'lead' | 'renewal'; customer_id?: string; expired_days?: number; plan_end?: string
  started_at: string; ended_at?: string | null; booked_slot?: string
}
interface AiMessage {
  _id: string; person_name: string; channel: 'whatsapp' | 'sms'; kind: string
  text: string; status: string; at: string; when_note?: string; reply_text?: string
}
interface QueueLead {
  lead_id: string; name: string; tier: 'hot' | 'warm' | 'low'; score: number
  stage: string; service: string; store: string; location?: string
  store_load_pct?: number; overload_expected?: boolean
  attempts_today: number; reason: string; renewal?: boolean
}
interface SyncStatus {
  ok?: boolean; issue_count?: number; repaired?: number; at?: string | null; reason?: string
}
interface AiState {
  shift: { enabled: boolean; voice: string; language: string; window_start: number; window_end: number; daily_budget: number; daily_lead_cap: number; speed: string; script: string; max_attempts: number }
  window_ok: boolean; ist_time: string
  kpis: { attempted: number; cap: number; connect_rate: number; connects: number; booked: number; renewed: number; renewal_calls: number; hot_total: number; hot_reached: number; hot_pct: number; cost: number; budget: number; cost_per_connect: number; handoffs: number; avg_duration: number; rerouted: number }
  funnel: { dialed: number; connected: number; interested: number; offered: number; booked: number }
  breakdown: { voicemail: number; no_answer: number; dnc: number; not_interested: number; handoffs: number; other: number }
  live: AiCall | null; queue: QueueLead[]; recent_calls: AiCall[]; recent_messages: AiMessage[]
  msg_stats: { whatsapp_today: number; sms_today: number; replies_today: number }
  retry_waiting: number; sync?: SyncStatus
}

const OUTCOME_META: Record<string, { label: string; chip: string }> = {
  in_progress: { label: 'On Call', chip: 'bg-brand-50 text-brand-700 border-brand-100' },
  booked: { label: 'Booked', chip: 'bg-emerald-100 text-emerald-700 border-emerald-200' },
  renewed: { label: 'Renewed', chip: 'bg-teal-100 text-teal-700 border-teal-200' },
  callback: { label: 'Callback', chip: 'bg-sky-100 text-sky-700 border-sky-200' },
  interested: { label: 'Interested', chip: 'bg-violet-100 text-violet-700 border-violet-200' },
  not_interested: { label: 'Not Interested', chip: 'bg-slate-100 text-slate-500 border-slate-200' },
  voicemail: { label: 'Voicemail', chip: 'bg-slate-100 text-slate-500 border-slate-200' },
  no_answer: { label: 'No Answer', chip: 'bg-rose-100 text-rose-700 border-rose-200' },
  dnc: { label: 'DNC', chip: 'bg-rose-100 text-rose-700 border-rose-200' },
  handed_over: { label: 'Handed To You', chip: 'bg-amber-100 text-amber-700 border-amber-200' },
  ended_by_admin: { label: 'Ended', chip: 'bg-slate-100 text-slate-500 border-slate-200' },
  failed: { label: 'Failed', chip: 'bg-slate-100 text-slate-500 border-slate-200' },
}
const TIER_META: Record<string, string> = {
  hot: 'bg-rose-100 text-rose-700 border-rose-200',
  warm: 'bg-amber-100 text-amber-700 border-amber-200',
  low: 'bg-slate-100 text-slate-500 border-slate-200',
}
const MSG_STATUS: Record<string, string> = {
  sent: 'bg-sky-100 text-sky-700 border-sky-200', delivered: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  read: 'bg-emerald-100 text-emerald-700 border-emerald-200', replied: 'bg-violet-100 text-violet-700 border-violet-200',
  blocked: 'bg-rose-100 text-rose-700 border-rose-200', failed: 'bg-rose-100 text-rose-700 border-rose-200',
}
const KIND_LABEL: Record<string, string> = {
  booking_confirmation: 'Booking Confirmation', callback: 'Callback Scheduled', nudge: 'Missed-Call Nudge',
  reminder: 'Appointment Reminder', opt_out: 'Opt-Out Confirmation', test: 'Test Message',
}

function fmtClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export default function AICalls() {
  const qc = useQueryClient()
  const [msgTab, setMsgTab] = useState('all')
  const [detail, setDetail] = useState<AiCall | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const { data, isLoading, error } = useQuery({
    queryKey: ['ai-state'],
    queryFn: () => api<AiState>('/ai-calls/state'),
    refetchInterval: 4000,
  })

  const invalidate = () => { void qc.invalidateQueries({ queryKey: ['ai-state'] }) }
  const shift = useMutation({
    mutationFn: (enabled: boolean) => api<{ message: string }>('/ai-calls/shift', { method: 'POST', body: { enabled } }),
    onSuccess: (r) => { invalidate(); toast.success(r.message) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const queueAct = useMutation({
    mutationFn: (v: { id: string; action: string }) =>
      api<{ message: string }>(`/ai-calls/queue/${v.id}/action`, { method: 'POST', body: { action: v.action } }),
    onSuccess: (r) => { invalidate(); toast.success(r.message) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const testMsg = useMutation({
    mutationFn: (channel: string) =>
      api<{ text: string }>('/ai-calls/messages/test', { method: 'POST', body: { channel } }),
    onSuccess: (r) => { invalidate(); toast.success(r.text) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const syncRun = useMutation({
    mutationFn: () => api<{ message: string }>('/ai-calls/sync/run', { method: 'POST' }),
    onSuccess: (r) => { invalidate(); toast.success(r.message) },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const saveSettings = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api<{ message: string }>('/ai-calls/settings', { method: 'POST', body }),
    onSuccess: (r) => { invalidate(); setSettingsOpen(false); toast.success(r.message) },
    onError: (e) => toast.error((e as ApiError).message),
  })

  if (isLoading) return <Spinner />
  if (error || !data) return <ErrorBox error={error || 'No data'} />

  const { shift: cfg, kpis, funnel } = data
  const messages = (data.recent_messages || []).filter((m) => msgTab === 'all' || m.channel === msgTab)
  const budgetPct = Math.min(100, Math.round(100 * kpis.cost / Math.max(1, kpis.budget)))

  return (
    <div className="max-w-6xl space-y-5">
      {/* header */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-extrabold text-slate-800 flex items-center gap-2">
            <Bot size={20} className="text-brand-600" /> AI Calling
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Aarav works your lead queue score-first, SLA-aware - and follows every call up on WhatsApp/SMS automatically.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            className={`chip border ${data.sync?.ok === false ? 'bg-rose-50 text-rose-700 border-rose-200' : 'bg-emerald-50 text-emerald-700 border-emerald-200'}`}
            disabled={syncRun.isPending}
            onClick={() => syncRun.mutate()}
            title={`Last verified ${data.sync?.at ? new Date(data.sync.at).toLocaleString('en-IN') : 'yet to run'} · after ${data.sync?.reason || 'the first booking'}`}>
            {syncRun.isPending ? <RefreshCw size={12} className="animate-spin" /> : <BadgeCheck size={12} />}
            Data Sync {data.sync?.ok === false ? 'Issues' : 'Verified'}
          </button>
          <button className="btn-ghost btn-sm" onClick={() => setSettingsOpen(true)}>
            <Settings2 size={13} /> Guardrails
          </button>
          <button className={`btn-sm ${cfg.enabled ? 'btn-danger' : 'btn-primary'}`}
            disabled={shift.isPending}
            onClick={() => shift.mutate(!cfg.enabled)}>
            {shift.isPending ? <BtnSpinner /> : cfg.enabled ? <Pause size={13} /> : <Play size={13} />}
            {cfg.enabled ? 'Pause Shift' : 'Start Shift'}
          </button>
        </div>
      </div>

      {/* shift control */}
      <Card className="p-4 anim-fade-up">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
          <div className="flex items-center gap-3">
            <button aria-label="Toggle auto-calling" onClick={() => shift.mutate(!cfg.enabled)}
              className={`relative w-12 h-7 rounded-full transition-colors shrink-0 ${cfg.enabled ? 'bg-brand-600' : 'bg-slate-300'}`}>
              <span className={`absolute top-1 left-1 w-5 h-5 bg-white rounded-full shadow transition-transform ${cfg.enabled ? 'translate-x-5' : ''}`} />
            </button>
            <div>
              <div className="font-bold text-sm text-slate-800">Auto-Calling Shift</div>
              <div className="text-[11px] text-slate-500">
                {!cfg.enabled ? 'Paused - the queue is frozen, retries reschedule automatically'
                  : !data.window_ok ? `Outside the calling window (opens at ${cfg.window_start}:00 IST, now ${data.ist_time})`
                    : `Running - calling window ${cfg.window_start}:00-${cfg.window_end}:00 IST (TRAI-safe)`}
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-x-8 gap-y-3 text-sm flex-1 justify-end">
            <div><div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Voice Agent</div>
              <div className="font-semibold text-slate-700">{cfg.voice} · {cfg.language}</div></div>
            <div><div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Attempted Today</div>
              <div className="font-semibold text-slate-700 tabular-nums">{kpis.attempted} / {kpis.cap} leads</div></div>
            <div className="min-w-40"><div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Budget</div>
              <div className="font-semibold text-slate-700 tabular-nums">₹{kpis.cost} <span className="text-slate-400 text-xs font-normal">of ₹{kpis.budget}</span></div>
              <div className="mt-1"><Bar value={budgetPct} color={budgetPct > 85 ? 'bg-amber-500' : 'bg-brand-500'} /></div></div>
            <div><div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Retry Queue</div>
              <div className="font-semibold text-slate-700 tabular-nums">{data.retry_waiting} waiting</div></div>
            <div><div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide">Handed To Team</div>
              <div className="font-semibold text-slate-700 tabular-nums">{kpis.handoffs} today</div></div>
          </div>
        </div>
      </Card>

      {/* KPI strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5 gap-3">
        {[
          { icon: PhoneCall, color: '#2563eb', label: 'Calls Attempted Today', node: `${kpis.attempted}`, sub: `${data.retry_waiting} in retry queue` },
          { icon: Zap, color: '#7c3aed', label: 'Connect Rate', node: `${kpis.connect_rate}%`, sub: `${kpis.connects} of ${kpis.attempted} answered` },
          { icon: Target, color: '#059669', label: 'Booked By AI', node: `${kpis.booked}`, sub: `${kpis.rerouted} rerouted with % off · ${kpis.renewed || 0} plan renewals · data auto-synced` },
          { icon: Timer, color: '#d97706', label: 'Hot Leads Reached First', node: `${kpis.hot_pct}%`, sub: `${kpis.hot_reached} of ${kpis.hot_total} hot leads` },
          { icon: Wallet, color: '#0f766e', label: 'Cost Per Connect', node: `₹${kpis.cost_per_connect}`, sub: `avg call ${fmtClock(kpis.avg_duration)}` },
        ].map((k, i) => <KpiCard key={k.label} k={k} i={i} />)}
      </div>

      {/* queue next-up */}
      <Card className="p-0 anim-fade-up overflow-hidden" style={{ '--d': '160ms' } as CSSProperties}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
          <div>
            <div className="text-base font-bold text-slate-800">Call Queue - Next Up</div>
            <div className="text-xs text-slate-500">Reuses your lead score + SLA priority - the AI never jumps the line you defined</div>
          </div>
          <span className="chip bg-brand-50 text-brand-700 border-brand-100">{data.queue.length} ready</span>
        </div>
        {data.queue.length === 0 ? (
          <Empty text="No dialable leads right now - if no lead is left the AI falls back to renewal follow-ups for expired-plan customers" />
        ) : (
          <div>
            {data.queue.map((q, i) => (
              <div key={q.lead_id} className="flex flex-wrap items-center gap-2 sm:gap-3 px-4 py-2.5 border-t border-slate-50 first:border-t-0 anim-fade-up"
                style={{ '--d': `${i * 40}ms` } as CSSProperties}>
                <span className="w-6 h-6 rounded-lg bg-slate-100 text-slate-500 text-[11px] font-bold flex items-center justify-center shrink-0">{i + 1}</span>
                <div className="flex-1 min-w-48">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-semibold text-sm text-slate-800">{q.name}</span>
                    <span className={`chip border ${TIER_META[q.tier]}`}>{q.tier === 'hot' ? 'Hot' : q.tier === 'warm' ? 'Warm' : 'Low'} {q.score}</span>
                    {q.renewal && <span className="chip bg-teal-50 text-teal-700 border-teal-200">Renewal follow-up</span>}
                    {q.attempts_today > 0 && <span className="chip bg-slate-50 text-slate-400 border-slate-200">attempt {q.attempts_today + 1}</span>}
                    {q.overload_expected && <span className="chip bg-amber-50 text-amber-700 border-amber-200"><MapPin size={11} className="inline" /> {q.store_load_pct}% loaded → % off ready</span>}
                  </div>
                  <div className="text-[11px] text-slate-400 mt-0.5">{q.service} · {q.store} ({q.location}) · {q.reason}</div>
                </div>
                <div className="flex gap-1.5">
                  {!q.renewal && <button className="btn-ghost btn-sm" disabled={queueAct.isPending}
                    onClick={() => queueAct.mutate({ id: q.lead_id, action: 'defer' })}>Defer</button>}
                  <button className="btn-ghost btn-sm" disabled={queueAct.isPending}
                    onClick={() => queueAct.mutate({ id: q.lead_id, action: 'skip' })}>Skip</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* funnel + recent calls */}
      <div className="grid lg:grid-cols-2 gap-4">
        <Card className="p-4 anim-fade-up" style={{ '--d': '200ms' } as CSSProperties}>
          <SectionTitle title="Today's Call Outcomes" />
          {([
            ['Dialed', funnel.dialed, 100, '#93b0f7'],
            ['Connected', funnel.connected, funnel.dialed ? 100 * funnel.connected / funnel.dialed : 0, '#8b5cf6'],
            ['Interested', funnel.interested, funnel.dialed ? 100 * funnel.interested / funnel.dialed : 0, '#3d6cf2'],
            ['Offered', funnel.offered, funnel.dialed ? 100 * funnel.offered / funnel.dialed : 0, '#f59e0b'],
            ['Booked', funnel.booked, funnel.dialed ? 100 * funnel.booked / funnel.dialed : 0, '#059669'],
          ] as [string, number, number, string][]).map(([label, n, w, color]) => (
            <div key={label} className="mb-2.5">
              <div className="flex justify-between text-xs mb-1">
                <span className="text-slate-600">{label}</span>
                <span className="font-bold tabular-nums">{n}</span>
              </div>
              <div className="h-2.5 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full rounded-full transition-[width] duration-700 ease-out anim-fade-up"
                  style={{ width: `${Math.min(100, w)}%`, background: color }} />
              </div>
            </div>
          ))}
          <div className="text-[11px] text-slate-400 mt-2">
            Voicemail {data.breakdown.voicemail} · No answer {data.breakdown.no_answer} · Not interested {data.breakdown.not_interested} · DNC {data.breakdown.dnc} · Handed to you {data.breakdown.handoffs} · Other {data.breakdown.other}. Every outcome writes back to the same lead pipeline.
          </div>
        </Card>

        <Card className="p-0 anim-fade-up overflow-hidden" style={{ '--d': '240ms' } as CSSProperties}>
          <div className="px-4 py-3 border-b border-slate-100">
            <div className="text-base font-bold text-slate-800">Recent AI Calls</div>
            <div className="text-xs text-slate-500">Tap a call for its full transcript</div>
          </div>
          {data.recent_calls.length === 0 ? (
            <Empty text="No calls completed yet in this shift" />
          ) : (
            <div className="max-h-80 overflow-y-auto">
              {data.recent_calls.map((c, i) => {
                const meta = OUTCOME_META[c.outcome] || OUTCOME_META.failed
                return (
                  <button key={c._id} onClick={() => setDetail(c)}
                    className="w-full text-left flex flex-wrap items-center gap-2 px-4 py-2.5 border-t border-slate-50 first:border-t-0 hover:bg-slate-50 anim-fade-up"
                    style={{ '--d': `${i * 40}ms` } as CSSProperties}>
                    <span className="text-xs text-slate-400 tabular-nums w-10 shrink-0">
                      {new Date(c.started_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                    </span>
                    <div className="flex-1 min-w-40">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold text-sm text-slate-800">{c.person_name}</span>
                        <span className={`chip border ${TIER_META[c.tier]}`}>{c.tier === 'hot' ? 'Hot' : c.tier === 'warm' ? 'Warm' : 'Low'} {c.score}</span>
                        {c.kind === 'renewal' && <span className="chip bg-teal-50 text-teal-700 border-teal-200">Renewal · expired {c.expired_days}d</span>}
                        {c.rerouted && <span className="chip bg-emerald-50 text-emerald-700 border-emerald-200">Rerouted · {c.discount_pct}% off</span>}
                      </div>
                      <div className="text-[11px] text-slate-400">{c.service} · {c.booked_store || c.store}{c.booked_slot ? ` · ${c.booked_slot}` : ''}</div>
                    </div>
                    <span className={`chip border ${meta.chip}`}>{meta.label}</span>
                    <ChevronRight size={14} className="text-slate-300" />
                  </button>
                )
              })}
            </div>
          )}
        </Card>
      </div>

      {/* automatic messaging */}
      <Card className="p-0 anim-fade-up overflow-hidden" style={{ '--d': '280ms' } as CSSProperties}>
        <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 border-b border-slate-100">
          <div>
            <div className="text-base font-bold text-slate-800">Automatic WhatsApp &amp; SMS</div>
            <div className="text-xs text-slate-500">Every call outcome triggers the right follow-up message - consent-first, quiet hours honoured</div>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="chip border-emerald-200 bg-emerald-50 text-emerald-700"><MessageCircle size={12} /> {data.msg_stats.whatsapp_today} WhatsApp today</span>
            <span className="chip border-sky-200 bg-sky-50 text-sky-700"><Smartphone size={12} /> {data.msg_stats.sms_today} SMS today</span>
            <span className="chip border-violet-200 bg-violet-50 text-violet-700">{data.msg_stats.replies_today} replies</span>
            <button className="btn-ghost btn-sm" disabled={testMsg.isPending} onClick={() => testMsg.mutate('whatsapp')}>
              {testMsg.isPending ? <BtnSpinner /> : <Send size={12} />} Test WhatsApp
            </button>
            <button className="btn-ghost btn-sm" disabled={testMsg.isPending} onClick={() => testMsg.mutate('sms')}>
              {testMsg.isPending ? <BtnSpinner /> : <Send size={12} />} Test SMS
            </button>
          </div>
        </div>
        <div className="px-4 pt-2">
          <Tabs tabs={[{ key: 'all', label: 'All' }, { key: 'whatsapp', label: 'WhatsApp' }, { key: 'sms', label: 'SMS' }]}
            active={msgTab} onChange={setMsgTab} />
        </div>
        {messages.length === 0 ? (
          <Empty icon={<MessageCircle size={28} className="text-slate-300" />} text="No messages yet - they fire automatically after calls and bookings" />
        ) : (
          <div className="max-h-96 overflow-y-auto pb-2">
            {messages.map((m, i) => (
              <div key={m._id} className="flex flex-wrap items-start gap-2 px-4 py-2.5 border-t border-slate-50 anim-fade-up"
                style={{ '--d': `${Math.min(i, 10) * 30}ms` } as CSSProperties}>
                <span className={`w-7 h-7 rounded-lg flex items-center justify-center shrink-0 ${m.channel === 'whatsapp' ? 'bg-emerald-100 text-emerald-600' : 'bg-sky-100 text-sky-600'}`}>
                  {m.channel === 'whatsapp' ? <MessageCircle size={14} /> : <Smartphone size={14} />}
                </span>
                <div className="flex-1 min-w-52">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-semibold text-sm text-slate-800">{m.person_name}</span>
                    <span className="chip bg-slate-50 text-slate-500 border-slate-200">{KIND_LABEL[m.kind] || m.kind}</span>
                    <span className={`chip border ${MSG_STATUS[m.status] || 'bg-slate-100 text-slate-500 border-slate-200'}`}>{m.status}</span>
                  </div>
                  <div className="text-xs text-slate-600 mt-1">{m.text}</div>
                  {m.reply_text && <div className="text-xs text-violet-700 bg-violet-50 border border-violet-100 rounded-lg px-2 py-1 mt-1 inline-block">Reply: “{m.reply_text}”</div>}
                </div>
                <span className="text-[11px] text-slate-400 tabular-nums">
                  {new Date(m.at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* guardrails summary */}
      <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { icon: Bot, title: 'Voice & Language', lines: [`${cfg.voice} · ${cfg.language}`, `Speed ${cfg.speed} · interruptions allowed`] },
          { icon: MessageSquareWarning, title: 'Script Flow', lines: [`${cfg.script} - greet → intent → price → slot`, 'Manager / complaint → instant handoff'] },
          { icon: Timer, title: 'Retry Policy', lines: [`${cfg.max_attempts} attempts · ${Math.round(90)} min gap`, '2 misses → WhatsApp nudge instead'] },
          { icon: Ear, title: 'Compliance', lines: [`${cfg.window_start}:00-${cfg.window_end}:00 IST window · DND honoured`, 'STOP keyword → instant DNC'] },
        ].map((g, i) => (
          <Card key={g.title} className="p-4 anim-fade-up" style={{ '--d': `${300 + i * 50}ms` } as CSSProperties}>
            <div className="flex items-center gap-2 font-bold text-sm text-slate-800"><g.icon size={15} className="text-brand-600" /> {g.title}</div>
            <ul className="mt-2 space-y-1">
              {g.lines.map((l) => <li key={l} className="text-xs text-slate-500 flex gap-1.5"><span className="w-1 h-1 rounded-full bg-brand-400 mt-1.5 shrink-0" />{l}</li>)}
            </ul>
          </Card>
        ))}
      </div>

      {/* transcript detail modal */}
      <Modal open={!!detail} onClose={() => setDetail(null)} wide
        title={`Call transcript - ${detail?.person_name || ''}`}>
        {detail && <>
          <div className="flex flex-wrap gap-1.5 mb-3">
            <span className={`chip border ${(OUTCOME_META[detail.outcome] || OUTCOME_META.failed).chip}`}>{(OUTCOME_META[detail.outcome] || OUTCOME_META.failed).label}</span>
            <span className="chip bg-slate-50 text-slate-500 border-slate-200">{detail.service} · {detail.booked_store || detail.store}</span>
            <span className="chip bg-slate-50 text-slate-500 border-slate-200">sentiment: {detail.sentiment}</span>
            {detail.booked_slot && <span className="chip bg-emerald-50 text-emerald-700 border-emerald-200">{detail.booked_slot}</span>}
            {detail.rerouted && <span className="chip bg-emerald-50 text-emerald-700 border-emerald-200">Rerouted · {detail.discount_pct}% off</span>}
            {detail.data_synced && <span className="chip bg-sky-50 text-sky-700 border-sky-200"><BadgeCheck size={11} className="inline" /> Pipeline Synced</span>}
          </div>
          <TranscriptView lines={detail.transcript} height="h-96" />
        </>}
      </Modal>

      {/* guardrails editor */}
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)}
        cfg={cfg} busy={saveSettings.isPending} onSave={(b) => saveSettings.mutate(b)} />
    </div>
  )
}

function KpiCard({ k, i }: { k: { icon: typeof PhoneCall; color: string; label: string; node: string; sub: string }, i: number }) {
  const numeric = parseFloat(k.node.replace(/[^0-9.]/g, ''))
  const hasNumber = !isNaN(numeric) && k.node !== ''
  const v = useCountUp(hasNumber ? numeric : 0)
  const shown = hasNumber ? k.node.replace(String(numeric), String(v)) : k.node
  return (
    <Card className="p-4 anim-fade-up" style={{ '--d': `${i * 60}ms` } as CSSProperties}>
      <div className="flex items-start justify-between">
        <div className="text-xs font-medium text-slate-500 pr-6">{k.label}</div>
        <k.icon size={16} style={{ color: k.color }} className="shrink-0" />
      </div>
      <div className="text-xl font-extrabold mt-1 tabular-nums" style={{ color: k.color }}>{shown}</div>
      <div className="text-[11px] text-slate-400 mt-0.5">{k.sub}</div>
    </Card>
  )
}

function TranscriptView({ lines, height }: { lines: TranscriptLine[]; height: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [lines.length])
  if (!lines.length) {
    return <div className={`${height} flex items-center justify-center`}>
      <Empty icon={<PhoneCall size={26} className="text-slate-200" />} text="The transcript appears here while Aarav speaks" />
    </div>
  }
  return (
    <div ref={ref} className={`${height} overflow-y-auto px-4 py-3 space-y-2`}>
      {lines.map((l, i) => (
        <div key={i} className={`max-w-[88%] px-3 py-2 rounded-xl text-[13px] anim-fade-up ${l.who === 'ai' ? 'bg-brand-50 border border-brand-100' : 'bg-slate-50 border border-slate-200 ml-auto'}`}>
          <div className={`text-[10px] font-bold uppercase tracking-wide mb-0.5 ${l.who === 'ai' ? 'text-brand-700' : 'text-slate-400'}`}>
            {l.who === 'ai' ? 'Aarav · AI' : l.who === 'admin' ? 'You' : 'Lead'}
            <span className="font-normal normal-case tracking-normal text-slate-400 ml-1.5">
              {new Date(l.at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          </div>
          {l.text}
        </div>
      ))}
    </div>
  )
}

function SettingsModal({ open, onClose, cfg, busy, onSave }: {
  open: boolean; onClose: () => void; cfg: AiState['shift']; busy: boolean
  onSave: (body: Record<string, unknown>) => void
}) {
  const [ws, setWs] = useState(cfg.window_start)
  const [we, setWe] = useState(cfg.window_end)
  const [budget, setBudget] = useState(cfg.daily_budget)
  const [cap, setCap] = useState(cfg.daily_lead_cap)
  const [attempts, setAttempts] = useState(cfg.max_attempts)
  useEffect(() => {
    if (open) { setWs(cfg.window_start); setWe(cfg.window_end); setBudget(cfg.daily_budget); setCap(cfg.daily_lead_cap); setAttempts(cfg.max_attempts) }
  }, [open, cfg])
  return (
    <Modal open={open} onClose={onClose} title="AI Calling Guardrails">
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div><label className="label" htmlFor="ai-ws">Window Start (IST)</label>
            <select id="ai-ws" className="select" value={ws} onChange={(e) => setWs(+e.target.value)}>
              {Array.from({ length: 13 }, (_, i) => i + 6).map((h) => <option key={h} value={h}>{h}:00</option>)}
            </select></div>
          <div><label className="label" htmlFor="ai-we">Window End (IST, TRAI cap 21)</label>
            <select id="ai-we" className="select" value={we} onChange={(e) => setWe(+e.target.value)}>
              {Array.from({ length: 14 }, (_, i) => i + 8).map((h) => <option key={h} value={h}>{h}:00</option>)}
            </select></div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><label className="label" htmlFor="ai-budget">Daily Budget (₹)</label>
            <input id="ai-budget" type="number" className="input" value={budget} min={50} max={10000}
              onChange={(e) => setBudget(+e.target.value)} /></div>
          <div><label className="label" htmlFor="ai-cap">Daily Lead Cap</label>
            <input id="ai-cap" type="number" className="input" value={cap} min={5} max={500}
              onChange={(e) => setCap(+e.target.value)} /></div>
        </div>
        <div><label className="label" htmlFor="ai-att">Max Attempts Per Lead</label>
          <select id="ai-att" className="select" value={attempts} onChange={(e) => setAttempts(+e.target.value)}>
            {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
          </select></div>
        <div className="text-xs text-slate-500 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
          Voice <b>{cfg.voice}</b> ({cfg.language}), speed {cfg.speed}, script <b>{cfg.script}</b>. DND lists are honoured, max 2 calls/lead/day, “STOP” opts a lead out instantly, and consent is recorded on connect.
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy}
          onClick={() => onSave({ window_start: ws, window_end: we, daily_budget: budget, daily_lead_cap: cap, max_attempts: attempts })}>
          {busy ? 'Saving…' : 'Save Guardrails'}
        </button>
      </div>
    </Modal>
  )
}
