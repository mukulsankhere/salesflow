import { Navigate, Route, Routes } from 'react-router-dom'
import { getToken } from './lib/api'
import { useFilter } from './lib/store'
import Login from './pages/Login'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Leads from './pages/Leads'
import Customers from './pages/Customers'
import Marketing from './pages/Marketing'
import Appointments from './pages/Appointments'
import Team from './pages/Team'
import Products from './pages/Products'
import Insights from './pages/Insights'
import MySchedule from './pages/MySchedule'
import Settings from './pages/Settings'

function RequireAuth({ children, section }: { children: JSX.Element; section?: string }) {
  const userStr = localStorage.getItem('sf_user')
  if (!getToken() || !userStr) return <Navigate to="/login" replace />
  const user = JSON.parse(userStr)
  if (section) {
    const allowed: Record<string, string[]> = {
      dashboard: ['admin', 'manager', 'sales', 'marketing', 'readonly'],
      leads: ['admin', 'manager', 'sales', 'marketing'],
      customers: ['admin', 'manager', 'sales', 'marketing'],
      marketing: ['admin', 'marketing'],
      appointments: ['admin', 'manager', 'sales'],
      team: ['admin', 'manager'],
      products: ['admin', 'manager', 'sales', 'marketing'],
      insights: ['admin', 'manager', 'sales', 'marketing'],
      settings: ['admin'],
      myschedule: ['staff', 'admin', 'manager'],
    }
    const role = user.roles?.[0]
    if (section !== 'dashboard' && allowed[section] && !allowed[section].includes(role)) {
      return (
        <div className="p-10 text-center">
          <div className="card max-w-md mx-auto p-8">
            <div className="text-3xl mb-2">🔒</div>
            <h2 className="text-lg font-bold">Section Not Available</h2>
            <p className="text-sm text-slate-500 mt-1">
              Your role ({role}) does not have access to {section}. Ask an Admin for more permissions.
            </p>
          </div>
        </div>
      )
    }
  }
  return children
}

export default function App() {
  useFilter((s) => s.period) // keep filter store hydrated
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<RequireAuth><Layout /></RequireAuth>}>
        <Route index element={<RequireAuth section="dashboard"><Dashboard /></RequireAuth>} />
        <Route path="leads" element={<RequireAuth section="leads"><Leads /></RequireAuth>} />
        <Route path="customers" element={<RequireAuth section="customers"><Customers /></RequireAuth>} />
        <Route path="marketing" element={<RequireAuth section="marketing"><Marketing /></RequireAuth>} />
        <Route path="appointments" element={<RequireAuth section="appointments"><Appointments /></RequireAuth>} />
        <Route path="team" element={<RequireAuth section="team"><Team /></RequireAuth>} />
        <Route path="products" element={<RequireAuth section="products"><Products /></RequireAuth>} />
        <Route path="insights" element={<RequireAuth section="insights"><Insights /></RequireAuth>} />
        <Route path="my-schedule" element={<RequireAuth section="myschedule"><MySchedule /></RequireAuth>} />
        <Route path="settings" element={<RequireAuth section="settings"><Settings /></RequireAuth>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
