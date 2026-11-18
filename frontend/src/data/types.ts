/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 代理健康候选节点：健康判定与候选池统一使用 status 口径 */
export type ProxyCandidate = {
  id: number
  name: string
  ip: string
  region: string
  protocol: string
  bandwidth: string
  latency: string
  status: string
  probe_state: '已探测' | '待探测'
  healthy: boolean
  selectable: boolean
}

export type ProxyPoolStats = {
  total_nodes: number
  online: number
  offline: number
  rate_limited: number
  disabled: number
  healthy: number
  probed: number
  probe_pending: number
  regions: string[]
  protocols: string[]
}

export type SwitchPlanItem = {
  flow: string
  node_id: number
  node_name: string
  node_ip: string
  region: string
  protocol: string
  probe_state: string
}

export type SwitchPlan = {
  plan_id: string
  flows: string[]
  items: SwitchPlanItem[]
  unassigned: string[]
  candidate_count: number
  terminal_state: string
  created_at: string
}

export type AllocationReceipt = {
  receipt_id: string
  plan_id: string
  flow: string
  node_id: number
  node_name: string
  node_ip: string
  strategy_id: number
  strategy_name: string
  strategy_status: string
  allocated_at: string
}

export type SwitchPlanResult = {
  ok: boolean
  message: string
  plan: SwitchPlan | null
  apply: {
    ok: boolean
    terminal_state: string
    deduplicated: boolean
    checks: { flow: string; node_id: number; passed: boolean; reason: string }[]
  } | null
  receipts: AllocationReceipt[]
}
