import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { Loading } from '../components/ui/Loading'

const ChatPage = lazy(() =>
  import('../features/chat/ChatPage').then((module) => ({ default: module.ChatPage })),
)
const StudioLayout = lazy(() =>
  import('../features/studio/StudioLayout').then((module) => ({ default: module.StudioLayout })),
)
const OverviewPage = lazy(() =>
  import('../features/overview/OverviewPage').then((module) => ({ default: module.OverviewPage })),
)
const ResourcesPage = lazy(() =>
  import('../features/resources/ResourcesPage').then((module) => ({
    default: module.ResourcesPage,
  })),
)
const McpServersPage = lazy(() =>
  import('../features/resources/McpServersPage').then((module) => ({
    default: module.McpServersPage,
  })),
)
const WorkflowsPage = lazy(() =>
  import('../features/workflows/WorkflowsPage').then((module) => ({
    default: module.WorkflowsPage,
  })),
)
const SkillsPage = lazy(() =>
  import('../features/skills/SkillsPage').then((module) => ({ default: module.SkillsPage })),
)
const VoicePage = lazy(() =>
  import('../features/settings/VoicePage').then((module) => ({ default: module.VoicePage })),
)
const ProvidersPage = lazy(() =>
  import('../features/settings/ProvidersPage').then((module) => ({
    default: module.ProvidersPage,
  })),
)
const TelegramPage = lazy(() =>
  import('../features/settings/TelegramPage').then((module) => ({ default: module.TelegramPage })),
)
const WhatsAppPage = lazy(() =>
  import('../features/settings/WhatsAppPage').then((module) => ({ default: module.WhatsAppPage })),
)
const HeartbeatPage = lazy(() =>
  import('../features/settings/HeartbeatPage').then((module) => ({
    default: module.HeartbeatPage,
  })),
)
const ProfilePage = lazy(() =>
  import('../features/settings/ProfilePage').then((module) => ({ default: module.ProfilePage })),
)

export function App() {
  return (
    <Suspense fallback={<Loading label="Opening Mounir…" />}>
      <Routes>
        <Route path="/" element={<ChatPage />} />
        <Route path="/admin" element={<StudioLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="models" element={<ResourcesPage key="models" kind="models" />} />
          <Route path="servers" element={<McpServersPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="agents" element={<ResourcesPage key="agents" kind="agents" />} />
          <Route path="workflows" element={<WorkflowsPage />} />
          <Route path="voice" element={<VoicePage />} />
          <Route path="providers" element={<ProvidersPage />} />
          <Route path="telegram" element={<TelegramPage />} />
          <Route path="whatsapp" element={<WhatsAppPage />} />
          <Route path="heartbeat" element={<HeartbeatPage />} />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="*" element={<Navigate to="/admin" replace />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
