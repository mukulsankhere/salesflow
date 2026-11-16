import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, Users, UserSquare2, Megaphone, CalendarDays, Store, Package,
  Sparkles, CalendarCheck, Settings, LogOut, Bell, ListChecks, Activity, Wifi, WifiOff,
} from 'lucide-react'
import { api } from '../lib/api'
import { useFilter, useRt, feedText, type User } from '../lib/store'
import { timeAgo } from '../lib/format'
import { useRealtime } from '../lib/rt'
import { Modal } from './ui'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, section: 'dashboard' },
  { to: '/leads', label: 'Leads', icon: Users, section: 'leads' },
  { to: '/customers', label: 'Customers', icon: UserSquare2, section: 'customers' },
  { to: '/appointments', label: 'Appointments', icon: CalendarDays, section: 'appointments' },
  { to: '/marketing', label: 'Marketing', icon: Megaphone, section: 'marketing' },
  { to: '/team', label: 'Store & Team', icon: Store, section: 'team' },
  { to: '/products', label: 'Products', icon: Package, section: 'products' },
  { to: '/insights', label: 'AI Insights', icon: Sparkles, section: 'insights' },
  { to: '/my-schedule', label: 'My Schedule', icon: CalendarCheck, section: 'myschedule' },
  { to: '/settings', label: 'Settings', icon: Settings, section: 'settings' },
]

const WORKFLOW_STEPS = [
  { n: 1, label: 'Capture Leads', desc: 'Add, import or auto-ingest from portals', to: '/leads', tab: 'all' },
  { n: 2, label: 'Call The Right Lead', desc: 'Hot leads first, inside the SLA', to: '/leads', tab: 'hot' },
  { n: 3, label: 'Offer Appointments', desc: 'Move interested leads to Appt. Offered', to: '/leads', tab: 'pipeline' },
  { n: 4, label: 'Book And Confirm', desc: 'Booking picker, price, WhatsApp confirmation', to: '/appointments' },
  { n: 5, label: 'Run Campaigns', desc: 'Lead-specific and customer campaigns', to: '/marketing' },
  { n: 6, label: 'Renew On Day 25', desc: 'Renewal reminders and held slots', to: '/customers' },
  { n: 7, label: 'Balance The Stores', desc: 'Reroute, waitlist and staff moves', to: '/team' },
  { n: 8, label: 'Review AI Insights', desc: 'Apply what raises bookings and revenue', to: '/insights' },
]

export default function Layout() {
  const nav = useNavigate()
  const user: User = JSON.parse(localStorage.getItem('sf_user') || '{}')
  const filter = useFilter()
  const { events, connected } = useRt()
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [workflowOpen, setWorkflowOpen] = useState(false)
  useRealtime()

  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ user: User; stores: { _id: string; name: string }[] }>('/auth/me'),
    staleTime: 300_000,
  })
  const { data: alerts } = useQuery({
    queryKey: ['alerts', filter.period, filter.storeId],
    queryFn: () => api<{ alerts: { kind: string; severity: string; text: string; section: string }[] }>(
      `/alerts?period=${filter.period}${filter.storeId ? `&store_id=${filter.storeId}` : ''}`),
    refetchInterval: 60_000,
  })

  useEffect(() => {
    document.title = 'SalesFlow'
  }, [])

  const role = user.roles?.[0] || 'staff'
  const visibleNav = NAV.filter((n) => {
    const allowed: Record<string, string[]> = {
      dashboard: ['admin', 'manager', 'sales', 'marketing', 'readonly'],
      leads: ['admin', 'manager', 'sales', 'marketing'],
      customers: ['admin', 'manager', 'sales', 'marketing'],
      marketing: ['admin', 'marketing'],
      appointments: ['admin', 'manager', 'sales'],
      team: ['admin', 'manager'],
      products: ['admin', 'manager', 'sales', 'marketing'],
      insights: ['admin', 'manager', 'sales', 'marketing'],
      myschedule: ['staff', 'admin', 'manager'],
      settings: ['admin'],
    }
    return allowed[n.section]?.includes(role)
  })

  function logout() {
    localStorage.removeItem('sf_token')
    localStorage.removeItem('sf_user')
    nav('/login')
  }

  return (
    <div className="min-h-screen flex">
      {/* Sidebar */}
      <aside className="w-56 bg-white border-r border-slate-200 flex flex-col fixed inset-y-0 z-40">
        <div className="px-4 py-4 border-b border-slate-100">
          <div className="text-lg font-extrabold text-brand-700 tracking-tight">SalesFlow</div>
          <div className="text-[11px] text-slate-400">Lead → Appointment → Renewal</div>
        </div>
        <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
          {visibleNav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition
                ${isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-50'}`}>
              <n.icon size={17} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-slate-100">
          <button onClick={() => setWorkflowOpen(true)}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-50">
            <ListChecks size={17} /> Workflow
          </button>
          <div className="px-3 py-2 text-xs text-slate-400 border-t border-slate-50 mt-2">
            {user.name}
            <div className="text-[10px] uppercase tracking-wide">{role}</div>
          </div>
          <button onClick={logout} className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-500 hover:bg-rose-50 hover:text-rose-600">
            <LogOut size={16} /> Sign Out
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 ml-56 flex flex-col min-h-screen">
        <header className="bg-white border-b border-slate-200 sticky top-0 z-30">
          <div className="flex items-center gap-3 px-5 py-2.5">
            <div className="text-sm font-semibold text-slate-700">
              {visibleNav.find((n) => n.to === location.pathname)?.label || 'SalesFlow'}
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span className={`chip ${connected ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                {connected ? <Wifi size={12} /> : <WifiOff size={12} />} {connected ? 'Live' : 'Offline'}
              </span>
              {/* Global filter: period + store (DASH-03) */}
              <select className="select w-32" value={filter.period} onChange={(e) => filter.setPeriod(e.target.value as never)}>
                <option value="today">Today</option>
                <option value="7">7 Days</option>
                <option value="30">30 Days</option>
                <option value="90">90 Days</option>
                <option value="all">All Time</option>
              </select>
              <select className="select w-44" value={filter.storeId} onChange={(e) => filter.setStore(e.target.value)}>
                <option value="">All Stores</option>
                {(me?.stores || []).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
              </select>
              <button className="relative p-2 rounded-lg hover:bg-slate-100" onClick={() => setAlertsOpen(true)}>
                <Bell size={18} className="text-slate-600" />
                {!!alerts?.alerts.length && (
                  <span className="absolute -top-0.5 -right-0.5 bg-rose-500 text-white text-[10px] font-bold rounded-full min-w-4 h-4 px-1 flex items-center justify-center">
                    {alerts.alerts.length}
                  </span>
                )}
              </button>
            </div>
          </div>
        </header>

        {/* Live activity strip */}
        {events.length > 0 && (
          <div className="bg-brand-50/60 border-b border-brand-100 px-5 py-1.5 flex items-center gap-2 text-xs text-slate-600 overflow-hidden whitespace-nowrap">
            <Activity size={13} className="text-brand-600 shrink-0" />
            <span className="font-semibold text-brand-700">Live</span>
            <span className="truncate">{feedText(events[0])}</span>
            <span className="text-slate-400 ml-1 shrink-0">{timeAgo(events[0].at)}</span>
          </div>
        )}

        <main className="flex-1 p-5">
          <Outlet />
        </main>
      </div>

      {/* Alerts centre (RT-03) */}
      <Modal open={alertsOpen} onClose={() => setAlertsOpen(false)} title="Alert Centre">
        <div className="space-y-2">
          {(alerts?.alerts || []).map((a, i) => (
            <button key={i} onClick={() => { setAlertsOpen(false); nav(a.section === 'team' ? '/team' : `/${a.section}`) }}
              className={`w-full text-left px-3 py-2.5 rounded-lg border text-sm flex items-center justify-between
                ${a.severity === 'high' ? 'border-rose-200 bg-rose-50 text-rose-700'
                  : a.severity === 'medium' ? 'border-amber-200 bg-amber-50 text-amber-700'
                    : 'border-slate-200 bg-slate-50 text-slate-600'}`}>
              <span>{a.text}</span>
              <span className="text-xs opacity-60">Open →</span>
            </button>
          ))}
          {!alerts?.alerts.length && <div className="text-sm text-slate-400 py-6 text-center">No alerts - everything is inside target.</div>}
        </div>
      </Modal>

      {/* Workflow side menu */}
      <Modal open={workflowOpen} onClose={() => setWorkflowOpen(false)} title="Workflow - 8 Guided Steps">
        <div className="space-y-2">
          {WORKFLOW_STEPS.map((s) => (
            <button key={s.n} onClick={() => { setWorkflowOpen(false); nav(s.to) }}
              className="w-full flex items-start gap-3 px-3 py-2.5 rounded-lg border border-slate-200 hover:border-brand-300 hover:bg-brand-50/40 text-left">
              <span className="w-6 h-6 rounded-full bg-brand-600 text-white text-xs font-bold flex items-center justify-center shrink-0">{s.n}</span>
              <span>
                <span className="block text-sm font-semibold text-slate-800">{s.label}</span>
                <span className="block text-xs text-slate-500">{s.desc}</span>
              </span>
            </button>
          ))}
        </div>
      </Modal>
    </div>
  )
}
