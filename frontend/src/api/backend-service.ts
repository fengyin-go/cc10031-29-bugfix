import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  AllocationReceipt,
  EntryRow,
  OverviewResult,
  PageResult,
  ProxyCandidate,
  ProxyPoolStats,
  SwitchPlanResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/** 跨模块数据流：获取全平台领域快照 */
export async function loadNodeStats(module?: string): Promise<Record<string, unknown>> {
  const query = module ? `?module=${module}` : ''
  const response = await request(`/api/nodestats${query}`)
  if (!response.ok) throw new Error('节点统计读取失败')
  return await response.json()
}

/** 跨模块数据流：获取指定 topic 的通道消息 */
export async function loadChannelSnapshot(topic: string): Promise<Record<string, unknown>> {
  const response = await request(`/api/channel/${topic}`)
  if (!response.ok) throw new Error('通道快照读取失败')
  return await response.json()
}

/** 跨模块数据流：查询脏数据隔离区 */
export async function loadDirtyVault(source?: string): Promise<Record<string, unknown>> {
  const query = source ? `?source=${source}` : ''
  const response = await request(`/api/dirty-vault${query}`)
  if (!response.ok) throw new Error('脏数据查询失败')
  return await response.json()
}

/** 跨模块数据流：查询采集记录 */
export async function loadCollectRecords(batchRef?: string): Promise<Record<string, unknown>> {
  const query = batchRef ? `?batch_ref=${batchRef}` : ''
  const response = await request(`/api/collect-records${query}`)
  if (!response.ok) throw new Error('采集记录查询失败')
  return await response.json()
}

/** 跨模块数据流：执行RPA全生命周期管道 */
export async function triggerPipeline(lifecycle: string): Promise<Record<string, unknown>> {
  const response = await request(`/api/pipeline/${lifecycle}`, {
    method: 'POST',
  })
  if (!response.ok) throw new Error('管道执行失败')
  return await response.json()
}

/** 跨模块数据流：获取失败恢复状态 */
export async function loadRecoveryStats(): Promise<Record<string, unknown>> {
  const response = await request('/api/recovery')
  if (!response.ok) throw new Error('恢复状态查询失败')
  return await response.json()
}

/** 代理防护联动：批量按地域/协议查询健康候选与探测状态 */
export async function loadProxyCandidates(
  regions: string[] = [],
  protocols: string[] = [],
): Promise<{ candidates: ProxyCandidate[]; total: number; stats: ProxyPoolStats }> {
  const query = new URLSearchParams()
  regions.forEach((r) => query.append('regions', r))
  protocols.forEach((p) => query.append('protocols', p))
  const response = await request(`/api/proxy-ops/candidates?${query.toString()}`)
  if (!response.ok) throw new Error('健康候选查询失败')
  return await response.json()
}

/** 代理防护联动：节点池统计（在线与健康同口径） */
export async function loadProxyPoolStats(): Promise<ProxyPoolStats> {
  const response = await request('/api/proxy-ops/pool-stats')
  if (!response.ok) throw new Error('节点池统计读取失败')
  return await response.json()
}

/** 代理防护联动：提交代理切换计划（同一 plan_id 重复提交只生效一次） */
export async function submitSwitchPlan(payload: {
  plan_id?: string
  flows: string[]
  regions?: string[]
  protocols?: string[]
  strategy_ids?: number[]
}): Promise<SwitchPlanResult> {
  const response = await request('/api/proxy-ops/switch-plan', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

/** 代理防护联动：分配结果清单（唯一回执） */
export async function loadProxyAllocations(): Promise<{
  receipts: AllocationReceipt[]
  receipt_count: number
  applied_plans: string[]
}> {
  const response = await request('/api/proxy-ops/allocations')
  if (!response.ok) throw new Error('分配结果读取失败')
  return await response.json()
}
