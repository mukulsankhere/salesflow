import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, MoveRight, UserMinus, CalendarOff, Sparkles, GraduationCap, AlertTriangle, ExternalLink } from 'lucide-react'
import { api, ApiError, qs } from '../lib/api'
import { toast } from '../lib/toast'
import { useFilter, filterParams } from '../lib/store'
import { fmtDate, fmtMoney, todayISO } from '../lib/format'
import { Card, Modal, Tabs, Empty, Spinner, Bar, SectionTitle, Row, ErrorBox, BtnSpinner, ConfirmModal } from '../components/ui'

interface Emp {
  _id: string; name: string; role: string; store_id: string; store_name?: string
  service_ids: string[]; service_names?: (string | undefined)[]; booked_today: number
  hours_today: number; utilization: number; shift_today: string; shift_pattern?: Record<string, string>
}
interface UtilStore {
  store_id: string; name: string; area: string; utilization: number; target: number
  available_employees: number; booked_emp_hours: number; total_emp_hours: number
  appointments_today: number; revenue: number
}

const SHIFT_LABELS: Record<string, string> = { off: 'Off', morning: 'Morning (10-4)', evening: 'Evening (4-10)', full: 'Full Day' }
const WD = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

export default function Team() {
  const qc = useQueryClient()
  const filter = useFilter()
  const fp = filterParams(filter)
  const [tab, setTab] = useState('utilization')
  const [addOpen, setAddOpen] = useState(false)
  const [moveEmp, setMoveEmp] = useState<Emp | null>(null)
  const [leaveEmp, setLeaveEmp] = useState<Emp | null>(null)
  const [removeEmp, setRemoveEmp] = useState<Emp | null>(null)

  const { data: utils } = useQuery({
    queryKey: ['team-util', fp.store_id],
    queryFn: () => api<{ stores: UtilStore[]; ai_insights: { key: string; title: string; text: string; expected_bookings: number; expected_revenue: number }[] }>('/team/utilization' + qs({ store_id: fp.store_id || undefined })),
  })
  const { data: emps, isLoading } = useQuery({
    queryKey: ['employees', fp.store_id],
    queryFn: () => api<{ employees: Emp[] }>('/employees' + qs({ store_id: fp.store_id || undefined })),
  })
  const { data: roster } = useQuery({
    queryKey: ['roster', fp.store_id],
    queryFn: () => api<{ date: string; roster: { employee_id: string; name: string; store_name: string; pattern: Record<string, string>; effective: string; on_leave: boolean }[]; labels: Record<string, string> }>('/team/roster' + qs({ store_id: fp.store_id || undefined })),
    enabled: tab === 'roster',
  })

  const cycle = useMutation({
    mutationFn: ({ id, date, type }: { id: string; date: string; type: string }) =>
      api(`/employees/${id}/shift`, { method: 'PUT', body: { date, type } }),
    onSuccess: (_r, v) => {
      void qc.invalidateQueries()
      toast.success(`Shift set to ${v.type} - capacity recalculated`)
    },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const move = useMutation({
    mutationFn: ({ id, store_id }: { id: string; store_id: string }) =>
      api(`/employees/${id}/move`, { method: 'POST', body: { store_id } }),
    onSuccess: () => { void qc.invalidateQueries(); setMoveEmp(null); toast.success('Employee moved - service lists and capacity recomputed') },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const leave = useMutation({
    mutationFn: ({ id, from, to }: { id: string; from: string; to: string }) =>
      api(`/employees/${id}/leave`, { method: 'POST', body: { from_date: from, to_date: to } }),
    onSuccess: () => { void qc.invalidateQueries(); setLeaveEmp(null); toast.success('Leave approved - capacity impact applied') },
    onError: (e) => toast.error((e as ApiError).message),
  })
  const remove = useMutation({
    mutationFn: (id: string) => api(`/employees/${id}`, { method: 'DELETE' }),
    onSuccess: () => { void qc.invalidateQueries(); setRemoveEmp(null); toast.success('Employee removed') },
    onError: (e) => { setRemoveEmp(null); toast.error((e as ApiError).message) },
  })

  return (
    <div>
      <Tabs active={tab} onChange={setTab} tabs={[
        { key: 'utilization', label: 'Utilization' },
        { key: 'workload', label: 'Employee Workload' },
        { key: 'roster', label: 'Shift Roster' },
        { key: 'coaching', label: 'Manager Coaching' },
      ]} />

      {tab === 'utilization' && (
        <div className="space-y-4">
          {(utils?.stores || []).map((s) => {
            const gap = s.utilization - s.target
            return (
              <Card key={s.store_id} className="p-4">
                <div className="flex flex-wrap items-center gap-4">
                  <div className="w-40">
                    <div className="font-bold">{s.name}</div>
                    <div className="text-xs text-slate-400">{s.area} · {s.available_employees} staff</div>
                  </div>
                  <div className="flex-1 min-w-48">
                    <Bar value={s.utilization} target={s.target} color={s.utilization >= 90 ? 'bg-rose-500' : s.utilization >= s.target ? 'bg-emerald-500' : 'bg-amber-500'} />
                    <div className="text-xs text-slate-400 mt-1">{s.booked_emp_hours} of {s.total_emp_hours} employee-hours booked</div>
                  </div>
                  <div className="text-center"><div className={`text-xl font-extrabold ${gap >= 0 ? 'text-emerald-600' : 'text-amber-600'}`}>{s.utilization}%</div><div className="text-[10px] text-slate-400">target {s.target}%</div></div>
                  <div className="text-center"><div className="text-xl font-extrabold">{s.appointments_today}</div><div className="text-[10px] text-slate-400">appointments today</div></div>
                  <div className="text-center"><div className="text-xl font-extrabold text-teal-700">{fmtMoney(s.revenue)}</div><div className="text-[10px] text-slate-400">revenue (paid)</div></div>
                  {s.utilization >= 90 && <span className="chip bg-rose-100 text-rose-700 border-rose-200">Reroute Threshold Reached</span>}
                </div>
              </Card>
            )
          })}
          {!!utils?.ai_insights?.length && (
            <Card className="p-4">
              <SectionTitle title="AI Utilization Insights" right={<Sparkles size={15} className="text-brand-600" />} />
              <div className="space-y-2">
                {utils.ai_insights.map((i) => (
                  <div key={i.key} className="border border-slate-200 rounded-lg p-3">
                    <div className="text-sm font-semibold">{i.title}</div>
                    <div className="text-xs text-slate-500 mt-0.5">{i.text}</div>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}

      {tab === 'workload' && (isLoading ? <Spinner /> : (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead><tr className="bg-slate-50">
              <th className="th">Employee</th><th className="th">Role</th><th className="th">Store</th>
              <th className="th">Services</th><th className="th">Booked Today</th><th className="th">Utilization</th><th className="th">Shift</th><th className="th text-right">Manage</th>
            </tr></thead>
            <tbody>
              {(emps?.employees || []).map((e) => (
                <tr key={e._id} className="hover:bg-slate-50">
                  <td className="td font-semibold">{e.name}</td>
                  <td className="td text-sm">{e.role}</td>
                  <td className="td text-sm">{e.store_name}</td>
                  <td className="td text-xs text-slate-500">{e.service_names?.join(', ')}</td>
                  <td className="td text-sm">{e.booked_today} ({e.hours_today}h)</td>
                  <td className="td w-32"><Bar value={e.utilization} color={e.utilization >= 85 ? 'bg-emerald-500' : e.utilization >= 50 ? 'bg-brand-500' : 'bg-slate-300'} /><div className="text-[10px] text-slate-400">{e.utilization}%</div></td>
                  <td className="td text-xs">{SHIFT_LABELS[e.shift_today] || e.shift_today}</td>
                  <td className="td text-right">
                    <div className="flex justify-end gap-1">
                      <button className="btn-ghost btn-sm" onClick={() => setMoveEmp(e)}><MoveRight size={11} /> Move</button>
                      <button className="btn-ghost btn-sm" onClick={() => setLeaveEmp(e)}><CalendarOff size={11} /> Leave</button>
                      <button className="btn-ghost btn-sm text-rose-600" title={`Remove ${e.name}`} onClick={() => setRemoveEmp(e)}><UserMinus size={11} /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-2 border-t border-slate-100">
            <button className="btn-primary btn-sm" onClick={() => setAddOpen(true)}><Plus size={12} /> Add Employee</button>
          </div>
        </Card>
      ))}

      {tab === 'roster' && !roster ? <Spinner /> : tab === 'roster' && (
        <Card className="overflow-x-auto">
          <table className="w-full min-w-[700px]">
            <thead><tr className="bg-slate-50">
              <th className="th">Employee</th><th className="th">Store</th>
              {WD.map((d) => <th key={d} className="th text-center capitalize">{d}</th>)}
              <th className="th">Effective Today</th>
            </tr></thead>
            <tbody>
              {(roster?.roster || []).map((r) => (
                <tr key={r.employee_id} className="hover:bg-slate-50">
                  <td className="td font-semibold">{r.name}{r.on_leave && <span className="chip bg-rose-100 text-rose-600 border-rose-200 ml-1">Leave</span>}</td>
                  <td className="td text-xs text-slate-500">{r.store_name}</td>
                  {WD.map((d) => (
                    <td key={d} className="td text-center">
                      <select className="text-[11px] border border-slate-200 rounded px-1 py-0.5 bg-white w-20 disabled:opacity-50"
                        value={r.pattern[d] || 'full'}
                        disabled={cycle.isPending && cycle.variables?.id === r.employee_id}
                        onChange={(e) => cycle.mutate({ id: r.employee_id, date: todayISO(), type: e.target.value })}>
                        <option value="off">Off</option><option value="morning">10-4</option>
                        <option value="evening">4-10</option><option value="full">Full</option>
                      </select>
                    </td>
                  ))}
                  <td className="td text-xs font-medium">{SHIFT_LABELS[r.effective]}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="text-xs text-slate-400 px-3 py-2 border-t border-slate-100">Click a shift to change it for the effective date - capacity updates immediately. Employees with booked appointments cannot be set Off (BR-23).</div>
        </Card>
      )}

      {tab === 'coaching' && <Coaching />}

      <AddEmployeeModal open={addOpen} onClose={() => setAddOpen(false)} />
      {moveEmp && <MoveModal emp={moveEmp} busy={move.isPending} onClose={() => setMoveEmp(null)} onMove={(sid) => move.mutate({ id: moveEmp._id, store_id: sid })} />}
      {leaveEmp && <LeaveModal emp={leaveEmp} busy={leave.isPending} onClose={() => setLeaveEmp(null)} onGrant={(f, t) => leave.mutate({ id: leaveEmp._id, from: f, to: t })} />}
      {removeEmp && (
        <ConfirmModal open onClose={() => setRemoveEmp(null)} danger busy={remove.isPending}
          title={`Remove ${removeEmp.name}?`} confirmLabel="Remove Employee"
          message="The employee is detached from the roster and stops counting towards store capacity and service coverage. Past appointments and revenue stay in history."
          onConfirm={() => remove.mutate(removeEmp._id)} />
      )}
    </div>
  )
}

function AddEmployeeModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ name: '', role: 'Therapist', store_id: '', service_ids: [] as string[] })
  const [error, setError] = useState('')
  const { data: stores } = useQuery({ queryKey: ['stores'], queryFn: () => api<{ stores: { _id: string; name: string }[] }>('/stores'), staleTime: 300_000, enabled: open })
  const { data: services } = useQuery({ queryKey: ['services'], queryFn: () => api<{ services: { _id: string; name: string; role_required: string }[] }>('/services'), staleTime: 300_000, enabled: open })
  const add = useMutation({
    mutationFn: () => api('/employees', { method: 'POST', body: { ...form, shift_pattern: { mon: 'full', tue: 'full', wed: 'full', thu: 'full', fri: 'full', sat: 'morning', sun: 'off' } } }),
    onSuccess: () => { void qc.invalidateQueries(); toast.success(`${form.name} added to the roster`); onClose() },
    onError: (e) => setError((e as ApiError).message),
  })
  return (
    <Modal open={open} onClose={onClose} title="Add Employee">
      <div className="space-y-3">
        <div><label className="label">Name</label><input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        <div><label className="label">Role</label>
          <select className="select" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            {['Therapist', 'Stylist', 'Trainer', 'Dentist'].map((r) => <option key={r}>{r}</option>)}
          </select></div>
        <div><label className="label">Store</label>
          <select className="select" value={form.store_id} onChange={(e) => setForm({ ...form, store_id: e.target.value })}>
            <option value="">- Select -</option>
            {stores?.stores.map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div><label className="label">Trained Services (a store offers a service only with enough trained staff - BR-05)</label>
          <div className="flex flex-wrap gap-2">
            {services?.services.map((s) => (
              <label key={s._id} className="flex items-center gap-1.5 text-sm border border-slate-200 rounded-lg px-2.5 py-1.5">
                <input type="checkbox" checked={form.service_ids.includes(s._id)}
                  onChange={(e) => setForm({ ...form, service_ids: e.target.checked ? [...form.service_ids, s._id] : form.service_ids.filter((x) => x !== s._id) })} />
                {s.name}
              </label>
            ))}
          </div></div>
      </div>
      {error && <div className="mt-3"><ErrorBox error={new Error(error)} /></div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={!form.name || !form.store_id || !form.service_ids.length || add.isPending}
          onClick={() => { setError(''); add.mutate() }}>{add.isPending ? 'Adding…' : 'Add'}</button>
      </div>
    </Modal>
  )
}

function MoveModal({ emp, busy, onClose, onMove }: { emp: Emp; busy?: boolean; onClose: () => void; onMove: (storeId: string) => void }) {
  const { data: stores } = useQuery({ queryKey: ['stores'], queryFn: () => api<{ stores: { _id: string; name: string }[] }>('/stores'), staleTime: 300_000 })
  const [target, setTarget] = useState('')
  return (
    <Modal open onClose={onClose} title={`Move ${emp.name}`}>
      <div className="space-y-3">
        <Row label="Current Store">{emp.store_name}</Row>
        <div><label className="label">Move To</label>
          <select className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">- Select -</option>
            {stores?.stores.filter((s) => s._id !== emp.store_id).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
          </select></div>
        <div className="text-xs text-slate-400">Blocked while the employee has booked appointments (BR-23). Store service lists recompute from trained staff.</div>
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-primary" disabled={!target || busy} onClick={() => onMove(target)}>{busy ? 'Moving…' : 'Move'}</button>
        </div>
      </div>
    </Modal>
  )
}

function LeaveModal({ emp, busy, onClose, onGrant }: { emp: Emp; busy?: boolean; onClose: () => void; onGrant: (from: string, to: string) => void }) {
  const [from, setFrom] = useState(todayISO())
  const [to, setTo] = useState(todayISO())
  const { data: preview } = useQuery({
    queryKey: ['leave-preview', emp._id, from, to],
    queryFn: () => api<{ impact: { date: string; employees_before: number; employees_after: number }[] }>(`/employees/${emp._id}/leave-preview?from_date=${from}&to_date=${to}`),
    enabled: !!from && !!to,
  })
  return (
    <Modal open onClose={onClose} title={`Leave - ${emp.name}`}>
      <div className="grid grid-cols-2 gap-3">
        <div><label className="label">From</label><input type="date" className="input" value={from} onChange={(e) => setFrom(e.target.value)} /></div>
        <div><label className="label">To</label><input type="date" className="input" value={to} onChange={(e) => setTo(e.target.value)} /></div>
      </div>
      <div className="mt-3">
        <div className="label">Capacity Impact Preview</div>
        <div className="space-y-1 max-h-36 overflow-y-auto">
          {(preview?.impact || []).map((d) => (
            <div key={d.date} className="flex justify-between text-xs py-1 border-b border-slate-50">
              <span>{fmtDate(d.date)}</span>
              <span className={d.employees_after < d.employees_before ? 'text-amber-600 font-semibold' : ''}>
                {d.employees_before} → {d.employees_after} staff
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-4">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={busy} onClick={() => onGrant(from, to)}>{busy ? 'Approving…' : 'Approve Leave'}</button>
      </div>
    </Modal>
  )
}

/* ---------------------------------- manager coaching view (COACH-01) */
interface Coach {
  owner_id: string; name: string; role: string
  assigned: number; contacted: number; booked: number; lost: number
  overdue: number; contact_pct: number; conversion_pct: number; avg_response_min: number | null
}
interface AttentionItem {
  type: 'overdue_followup' | 'untouched_lead'; ref_id: string; name: string
  owner: string; store_name: string; detail: string; at: string
}
interface CoachingData {
  coaches: Coach[]
  attention: AttentionItem[]
  summary: { owners: number; attention_count: number }
}

function Coaching() {
  const filter = useFilter()
  const fp = filterParams(filter)
  const { data, isLoading } = useQuery({
    queryKey: ['coaching', fp.period, fp.store_id],
    queryFn: () => api<CoachingData>('/team/coaching' + qs(fp)),
  })
  if (isLoading) return <Spinner />
  const coaches = data?.coaches || []
  const maxAssigned = Math.max(...coaches.map((c) => c.assigned), 1)
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <SectionTitle title="Team Follow-Up Completion & Conversion (COACH-01)"
          subtitle="Who is converting, who needs coaching - drill into records that need attention" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px]">
            <thead><tr className="bg-slate-50">
              <th className="th">Lead Owner</th><th className="th">Role</th><th className="th">Assigned</th>
              <th className="th">Contacted</th><th className="th">Booked</th><th className="th">Conversion</th>
              <th className="th">Overdue Now</th><th className="th">Avg Response</th>
            </tr></thead>
            <tbody>
              {coaches.map((c) => (
                <tr key={c.owner_id} className="hover:bg-slate-50">
                  <td className="td font-semibold">{c.name}</td>
                  <td className="td"><span className="chip bg-slate-100 border-slate-200 text-slate-500">{c.role}</span></td>
                  <td className="td">
                    <Bar value={(c.assigned / maxAssigned) * 100} color="bg-brand-500" />
                    <div className="text-[10px] text-slate-400">{c.assigned} leads</div>
                  </td>
                  <td className="td">{c.contact_pct}%</td>
                  <td className="td">{c.booked}</td>
                  <td className="td">
                    <span className={`chip ${c.conversion_pct >= 20 ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                      : c.conversion_pct >= 10 ? 'bg-amber-50 text-amber-700 border-amber-200'
                        : 'bg-rose-50 text-rose-700 border-rose-200'}`}>{c.conversion_pct}%</span>
                  </td>
                  <td className="td">
                    {c.overdue ? <span className="text-rose-600 font-bold">{c.overdue}</span>
                      : <span className="text-emerald-600">0</span>}
                  </td>
                  <td className="td text-sm">{c.avg_response_min !== null ? `${c.avg_response_min} min` : '-'}</td>
                </tr>
              ))}
              {!coaches.length && <tr><td className="td text-slate-400" colSpan={8}>No assigned leads in this scope</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="text-xs text-slate-400 mt-2">Contacted = lead progressed past New. Conversion = booked ÷ assigned. Response = first outbound contact after capture.</div>
      </Card>

      <Card className="p-4">
        <div className="text-sm font-bold mb-1 flex items-center gap-2">
          <AlertTriangle size={15} className="text-amber-500" /> Records That Need Attention ({data?.summary.attention_count ?? 0})
        </div>
        <div className="text-[11px] text-slate-400 mb-3">Overdue follow-ups and new leads untouched for a day - open each one in Leads to act.</div>
        <div className="space-y-2">
          {(data?.attention || []).map((a) => (
            <div key={a.type + a.ref_id} className="border border-slate-100 rounded-lg p-2.5 flex flex-wrap items-center gap-2">
              <span className={`chip ${a.type === 'overdue_followup' ? 'bg-rose-100 text-rose-700 border-rose-200' : 'bg-amber-100 text-amber-700 border-amber-200'}`}>
                {a.type === 'overdue_followup' ? 'Overdue Follow-Up' : 'Untouched Lead'}
              </span>
              <div className="flex-1 min-w-52">
                <span className="font-semibold text-slate-800 text-sm">{a.name}</span>
                <span className="text-xs text-slate-400"> · {a.owner} · {a.store_name}</span>
                <div className="text-[11px] text-slate-500">{a.detail}</div>
              </div>
              <a className="btn-ghost btn-sm" href={`/leads?lead=${a.ref_id}`}>
                <ExternalLink size={11} /> Open in Leads
              </a>
            </div>
          ))}
          {!(data?.attention || []).length && <Empty icon={<GraduationCap size={26} className="text-emerald-500" />}
            text="Nothing needs attention - the team is on top of every follow-up" />}
        </div>
      </Card>
    </div>
  )
}
