import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export const keys = {
  profile: ['profile'] as const,
  overview: ['overview'] as const,
  builtins: ['builtins'] as const,
  models: ['models'] as const,
  modelCatalog: ['model-catalog'] as const,
  embeddingModels: ['embedding-models'] as const,
  voiceModels: ['voice-models'] as const,
  servers: ['servers'] as const,
  skills: ['skills'] as const,
  skillTargets: ['skill-targets'] as const,
  agents: ['agents'] as const,
  agentNodes: ['agent-nodes'] as const,
  workflows: ['workflows'] as const,
  workflowNodes: ['workflow-nodes'] as const,
  voice: ['voice'] as const,
  telegram: ['telegram'] as const,
  whatsapp: ['whatsapp'] as const,
  heartbeat: ['heartbeat'] as const,
}

export function useProfile() {
  return useQuery({ queryKey: keys.profile, queryFn: api.profile.get })
}
export function useOverview() {
  return useQuery({ queryKey: keys.overview, queryFn: api.overview.get })
}
export function useBuiltins() {
  return useQuery({ queryKey: keys.builtins, queryFn: api.builtins.list })
}
export function useModels() {
  return useQuery({ queryKey: keys.models, queryFn: api.models.list })
}
export function useModelCatalog() {
  return useQuery({ queryKey: keys.modelCatalog, queryFn: api.modelCatalog.list })
}
export function useEmbeddingModels() {
  return useQuery({ queryKey: keys.embeddingModels, queryFn: api.embeddingModels.list })
}
export function useVoiceModels() {
  return useQuery({ queryKey: keys.voiceModels, queryFn: api.voiceModels.list })
}
export function useServers() {
  return useQuery({ queryKey: keys.servers, queryFn: api.servers.list })
}
export function useSkills() {
  return useQuery({ queryKey: keys.skills, queryFn: api.skills.list })
}
export function useSkillTargets() {
  return useQuery({ queryKey: keys.skillTargets, queryFn: api.skills.targets })
}
export function useAgents() {
  return useQuery({ queryKey: keys.agents, queryFn: api.agents.list })
}
export function useAgentNodes(workflowId?: number) {
  return useQuery({
    queryKey: [...keys.agentNodes, workflowId ?? 'global'],
    queryFn: () => api.agentNodes.list(workflowId),
  })
}
export function useWorkflows() {
  return useQuery({ queryKey: keys.workflows, queryFn: api.workflows.list })
}
export function useWorkflowNodes(ownerWorkflowId?: number) {
  return useQuery({
    queryKey: [...keys.workflowNodes, ownerWorkflowId ?? 'global'],
    queryFn: () => api.workflowNodes.list(ownerWorkflowId),
  })
}
