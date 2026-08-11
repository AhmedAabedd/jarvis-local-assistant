import {
  Activity,
  Bot,
  Boxes,
  ChevronLeft,
  Database,
  LayoutDashboard,
  MessageCircle,
  Mic2,
  Radio,
  Server,
  UserRound,
} from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useProfile } from '../../hooks/useStudioData'

const primary = [
  { to: '/admin', end: true, label: 'Overview', icon: LayoutDashboard },
  { to: '/admin/models', label: 'Models', icon: Database, resetResourceList: true },
  { to: '/admin/servers', label: 'MCP Servers', icon: Server, resetResourceList: true },
  { to: '/admin/agents', label: 'Subagents', icon: Boxes, resetResourceList: true },
]
const settings = [
  { to: '/admin/voice', label: 'Voice', icon: Mic2 },
  { to: '/admin/telegram', label: 'Telegram', icon: Radio },
  { to: '/admin/whatsapp', label: 'WhatsApp', icon: MessageCircle },
  { to: '/admin/heartbeat', label: 'Heartbeat', icon: Activity },
  { to: '/admin/profile', label: 'Profile', icon: UserRound },
]

export function StudioLayout() {
  const profile = useProfile().data
  return (
    <div className="studio-shell">
      <aside className="studio-sidebar">
        <div className="brand">
          <span className="brand__mark">
            <Bot size={21} />
          </span>
          <span>
            <strong>MOUNIR</strong>
            <small>Agent Studio</small>
          </span>
        </div>
        <nav className="studio-nav" aria-label="Agent Studio">
          <span className="nav-label">Workspace</span>
          {primary.map(({ icon: Icon, resetResourceList, ...item }) => (
            <NavLink
              key={item.to}
              {...item}
              state={resetResourceList ? { resetResourceList: true } : undefined}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
          <span className="nav-label">Connections & settings</span>
          {settings.map(({ icon: Icon, ...item }) => (
            <NavLink key={item.to} {...item}>
              <Icon size={18} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <a className="back-to-chat" href="/">
          <ChevronLeft size={18} />
          <span>
            <strong>Return to chat</strong>
            <small>Open {profile?.assistant_name || 'Mounir'}</small>
          </span>
        </a>
      </aside>
      <main className="studio-main">
        <Outlet />
      </main>
    </div>
  )
}
