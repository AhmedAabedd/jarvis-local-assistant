import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export const keys = {
  profile: ['profile'] as const,
  overview: ['overview'] as const,
  builtins: ['builtins'] as const,
  models: ['models'] as const,
  servers: ['servers'] as const,
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
export function useServers() {
  return useQuery({ queryKey: keys.servers, queryFn: api.servers.list })
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
