import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, Users, UserSquare2, Megaphone, CalendarDays, Store, Sparkles,
  Settings, LogOut, Bell, Activity, Wifi, WifiOff, Bot,
  Menu,
} from 'lucide-react'
import { api } from '../lib/api'
import { useFilter, useRt, feedText, type User } from '../lib/store'
import { timeAgo } from '../lib/format'
import { useRealtime } from '../lib/rt'
import { Modal } from './ui'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, section: 'dashboard' },
  { to: '/ai-calls', label: 'AI Calling', icon: Bot, section: 'aicalls' },
  { to: '/leads', label: 'Leads', icon: Users, section: 'leads' },
  { to: '/customers', label: 'Customers', icon: UserSquare2, section: 'customers' },
  { to: '/appointments', label: 'Appointments', icon: CalendarDays, section: 'appointments' },
  { to: '/marketing', label: 'Marketing', icon: Megaphone, section: 'marketing' },
  { to: '/team', label: 'Store & Team', icon: Store, section: 'team' },
  { to: '/services', label: 'Services', icon: Sparkles, section: 'services' },
  { to: '/settings', label: 'Settings', icon: Settings, section: 'settings' },
]

export default function Layout() {
  const nav = useNavigate()
  const user: User = JSON.parse(localStorage.getItem('sf_user') || '{}')
  const filter = useFilter()
  const { events, connected } = useRt()
  const [alertsOpen, setAlertsOpen] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
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
    // single-login deployment: only the admin signs in, so every section is admin
    const allowed: Record<string, string[]> = {
      dashboard: ['admin'],
      aicalls: ['admin'],
      leads: ['admin'],
      customers: ['admin'],
      marketing: ['admin'],
      appointments: ['admin'],
      team: ['admin'],
      services: ['admin'],
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
      {/* Mobile backdrop */}
      {mobileOpen && <div className="fixed inset-0 bg-slate-900/40 z-30 md:hidden anim-fade" onClick={() => setMobileOpen(false)} />}
      {/* Sidebar (slide-in drawer on mobile, fixed on desktop) */}
      <aside className={`flex w-56 bg-white border-r border-slate-200 flex-col fixed inset-y-0 left-0 z-40
        transition-transform duration-200 ease-out ${mobileOpen ? 'translate-x-0' : '-translate-x-full'}
        md:translate-x-0 md:transition-none`}>
        <div className="px-4 py-4 border-b border-slate-100">
          <div className="text-lg font-extrabold text-brand-700 tracking-tight">SalesFlow</div>
          <div className="text-[11px] text-slate-400">Lead → Appointment → Renewal</div>
        </div>
        <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
          {visibleNav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === '/'}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm font-medium transition
                ${isActive ? 'bg-brand-50 text-brand-700' : 'text-slate-600 hover:bg-slate-50'}`}>
              <n.icon size={17} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-slate-100">
          <div className="px-3 py-2 text-xs text-slate-400">
            {user.name}
            <div className="text-[10px] uppercase tracking-wide">{role}</div>
          </div>
          <button onClick={logout} className="w-full flex items-center gap-2 px-3 py-2 rounded-lg text-sm text-slate-500 hover:bg-rose-50 hover:text-rose-600">
            <LogOut size={16} /> Sign Out
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex-1 md:ml-56 flex flex-col min-h-screen min-w-0">
        <header className="bg-white border-b border-slate-200 sticky top-0 z-30">
          <div className="flex items-center gap-2 sm:gap-3 px-3 sm:px-5 py-2.5">
            <button className="md:hidden p-2 -ml-1 rounded-lg hover:bg-slate-100" onClick={() => setMobileOpen(true)} aria-label="Open menu">
              <Menu size={18} className="text-slate-600" />
            </button>
            <div className="text-sm font-semibold text-slate-700 truncate min-w-0">
              {visibleNav.find((n) => n.to === location.pathname)?.label || 'SalesFlow'}
            </div>
            <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
              <span className={`chip ${connected ? 'bg-emerald-50 text-emerald-600 border-emerald-200' : 'bg-slate-100 text-slate-400 border-slate-200'}`}>
                {connected ? <Wifi size={12} /> : <WifiOff size={12} />} <span className="hidden sm:inline">{connected ? 'Live' : 'Offline'}</span>
              </span>
              {/* Global filter: period + store (DASH-03) - inline on desktop, own row on mobile */}
              <div className="hidden sm:flex items-center gap-2">
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
              </div>
              <button className="relative p-2 rounded-lg hover:bg-slate-100" onClick={() => setAlertsOpen(true)} aria-label="Alerts">
                <Bell size={18} className="text-slate-600" />
                {!!alerts?.alerts.length && (
                  <span className="absolute -top-0.5 -right-0.5 bg-rose-500 text-white text-[10px] font-bold rounded-full min-w-4 h-4 px-1 flex items-center justify-center anim-pop">
                    {alerts.alerts.length}
                  </span>
                )}
              </button>
            </div>
          </div>
          {/* mobile filter row */}
          <div className="sm:hidden flex gap-2 px-3 pb-2 -mt-1">
            <select className="select flex-1 min-w-0" value={filter.period} onChange={(e) => filter.setPeriod(e.target.value as never)}>
              <option value="today">Today</option>
              <option value="7">7 Days</option>
              <option value="30">30 Days</option>
              <option value="90">90 Days</option>
              <option value="all">All Time</option>
            </select>
            <select className="select flex-1 min-w-0" value={filter.storeId} onChange={(e) => filter.setStore(e.target.value)}>
              <option value="">All Stores</option>
              {(me?.stores || []).map((s) => <option key={s._id} value={s._id}>{s.name}</option>)}
            </select>
          </div>
        </header>

        {/* Live activity strip */}
        {events.length > 0 && (
          <div className="bg-brand-50/60 border-b border-brand-100 px-3 sm:px-5 py-1.5 flex items-center gap-2 text-xs text-slate-600 overflow-hidden whitespace-nowrap">
            <Activity size={13} className="text-brand-600 shrink-0" />
            <span className="font-semibold text-brand-700 shrink-0">Live</span>
            <span className="truncate min-w-0">{feedText(events[0])}</span>
            <span className="text-slate-400 ml-1 shrink-0">{timeAgo(events[0].at)}</span>
          </div>
        )}

        <main className="flex-1 min-w-0 p-3 sm:p-5 pb-20 md:pb-5">
          {/* route transition: content fades and rises on every navigation */}
          <div key={location.pathname} className="page-enter">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Mobile bottom nav - the four everyday surfaces + everything else in the drawer */}
      <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 bg-white border-t border-slate-200
        pb-[env(safe-area-inset-bottom)] flex items-stretch" aria-label="Primary">
        {visibleNav.slice(0, 4).map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === '/'}
            className={({ isActive }) =>
              `flex-1 flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium
              ${isActive ? 'text-brand-700' : 'text-slate-400'}`}>
            {({ isActive }) => (<>
              <n.icon size={18} strokeWidth={isActive ? 2.4 : 1.8} />
              {n.label.split(' ')[0]}
            </>)}
          </NavLink>
        ))}
        <button onClick={() => setMobileOpen(true)} className="flex-1 flex flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium text-slate-400">
          <Menu size={18} strokeWidth={1.8} /> More
        </button>
      </nav>

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
    </div>
  )
}
