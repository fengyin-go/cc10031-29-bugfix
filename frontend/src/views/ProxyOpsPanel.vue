<template>
  <section class="proxy-ops" data-testid="proxy-ops-panel">
    <h3 class="proxy-ops-title">代理防护联动 · 健康候选与切换计划</h3>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">在线节点</span>
        <strong class="stat-value">{{ stats?.online ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">健康候选</span>
        <strong class="stat-value">{{ stats?.healthy ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待探测</span>
        <strong class="stat-value">{{ stats?.probe_pending ?? '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已禁用（不可选）</span>
        <strong class="stat-value">{{ stats?.disabled ?? '—' }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reloadCandidates">
      <fieldset class="check-group">
        <legend>出口地域（可多选）</legend>
        <label v-for="region in stats?.regions ?? []" :key="region" class="check-item">
          <input v-model="selectedRegions" type="checkbox" :value="region" :data-testid="`region-${region}`" />
          <span>{{ region }}</span>
        </label>
      </fieldset>
      <fieldset class="check-group">
        <legend>协议类型（可多选）</legend>
        <label v-for="protocol in stats?.protocols ?? []" :key="protocol" class="check-item">
          <input v-model="selectedProtocols" type="checkbox" :value="protocol" :data-testid="`protocol-${protocol}`" />
          <span>{{ protocol }}</span>
        </label>
      </fieldset>
      <button class="btn" data-testid="query-candidates" type="submit">查询健康候选</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>节点名称</th>
          <th>IP地址</th>
          <th>出口地域</th>
          <th>协议类型</th>
          <th>带宽上限</th>
          <th>延迟</th>
          <th>探测状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="node in candidates" :key="node.id" :data-testid="`candidate-${node.id}`">
          <td>{{ node.name }}</td>
          <td>{{ node.ip }}</td>
          <td>{{ node.region }}</td>
          <td>{{ node.protocol }}</td>
          <td>{{ node.bandwidth }}</td>
          <td>{{ node.latency || '—' }}</td>
          <td>{{ node.probe_state }}</td>
        </tr>
        <tr v-if="!candidates.length">
          <td colspan="7" class="empty-state">当前筛选下暂无健康候选节点</td>
        </tr>
      </tbody>
    </table>

    <form class="analysis-form" @submit.prevent="submitPlan">
      <label>
        <span>处理项（流程，逗号分隔）</span>
        <input v-model="flowsText" data-testid="plan-flows" placeholder="如：采集流程A,采集流程B" />
      </label>
      <label>
        <span>计划编号（留空自动生成，重复提交只生效一次）</span>
        <input v-model="planId" data-testid="plan-id" placeholder="PLAN-自定义" />
      </label>
      <label>
        <span>防护策略ID（逗号分隔，留空取触发封锁策略）</span>
        <input v-model="strategyIdsText" data-testid="plan-strategies" placeholder="如：3" />
      </label>
      <button class="btn primary" data-testid="submit-plan" type="submit" :disabled="submitting">
        {{ submitting ? '提交中…' : '生成并提交切换计划' }}
      </button>
    </form>

    <p v-if="planMessage" class="plan-message" :class="{ 'error-text': !planOk }" data-testid="plan-message">
      {{ planMessage }}
    </p>

    <template v-if="lastPlan">
      <p class="status-legend">
        <span class="legend-item">计划号：{{ lastPlan.plan_id }}</span>
        <span class="legend-item">处理终态：{{ lastPlan.terminal_state }}</span>
        <span class="legend-item">候选节点：{{ lastPlan.candidate_count }}</span>
        <span v-if="lastPlan.unassigned.length" class="legend-item">
          未分配：{{ lastPlan.unassigned.join('、') }}
        </span>
      </p>
      <table v-if="checks.length" class="data-table">
        <thead>
          <tr><th>处理项</th><th>节点</th><th>核对结果</th></tr>
        </thead>
        <tbody>
          <tr v-for="check in checks" :key="`${check.flow}-${check.node_id}`">
            <td>{{ check.flow }}</td>
            <td>{{ check.node_id }}</td>
            <td>{{ check.passed ? '核对通过' : check.reason }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <h4 class="proxy-ops-subtitle">分配结果清单（唯一回执）</h4>
    <table class="data-table">
      <thead>
        <tr>
          <th>回执号</th>
          <th>计划号</th>
          <th>处理项</th>
          <th>分配节点</th>
          <th>防护策略</th>
          <th>策略状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="receipt in receipts" :key="receipt.receipt_id" :data-testid="`receipt-${receipt.receipt_id}`">
          <td>{{ receipt.receipt_id }}</td>
          <td>{{ receipt.plan_id }}</td>
          <td>{{ receipt.flow }}</td>
          <td>{{ receipt.node_name }}（{{ receipt.node_ip }}）</td>
          <td>{{ receipt.strategy_name }}</td>
          <td>{{ receipt.strategy_status }}</td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="6" class="empty-state">暂无分配回执，提交切换计划后在此查看</td>
        </tr>
      </tbody>
    </table>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import {
  loadProxyAllocations,
  loadProxyCandidates,
  loadProxyPoolStats,
  submitSwitchPlan,
} from '@/api/backend-service'
import type {
  AllocationReceipt,
  ProxyCandidate,
  ProxyPoolStats,
  SwitchPlan,
} from '@/data/types'

const stats = ref<ProxyPoolStats | null>(null)
const candidates = ref<ProxyCandidate[]>([])
const receipts = ref<AllocationReceipt[]>([])
const selectedRegions = ref<string[]>([])
const selectedProtocols = ref<string[]>([])
const flowsText = ref('')
const planId = ref('')
const strategyIdsText = ref('')
const submitting = ref(false)
const planMessage = ref('')
const planOk = ref(true)
const lastPlan = ref<SwitchPlan | null>(null)
const checks = ref<{ flow: string; node_id: number; passed: boolean; reason: string }[]>([])
const errorMessage = ref('')

async function reloadCandidates() {
  errorMessage.value = ''
  try {
    const payload = await loadProxyCandidates(selectedRegions.value, selectedProtocols.value)
    candidates.value = payload.candidates
    stats.value = payload.stats
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '健康候选查询失败'
  }
}

async function reloadAllocations() {
  try {
    const payload = await loadProxyAllocations()
    receipts.value = payload.receipts
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '分配结果读取失败'
  }
}

async function submitPlan() {
  const flows = flowsText.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  if (!flows.length) {
    planOk.value = false
    planMessage.value = '请先填写处理项（流程）'
    return
  }
  const strategyIds = strategyIdsText.value
    .split(/[,，]/).map((s) => s.trim()).filter(Boolean).map(Number)
    .filter((n) => Number.isFinite(n))
  submitting.value = true
  errorMessage.value = ''
  try {
    const result = await submitSwitchPlan({
      plan_id: planId.value.trim() || undefined,
      flows,
      regions: selectedRegions.value,
      protocols: selectedProtocols.value,
      strategy_ids: strategyIds.length ? strategyIds : undefined,
    })
    planOk.value = result.ok
    planMessage.value = result.message
    lastPlan.value = result.plan
    checks.value = result.apply?.checks ?? []
    await Promise.all([reloadAllocations(), reloadCandidates()])
  } catch (error) {
    planOk.value = false
    planMessage.value = error instanceof Error ? error.message : '切换计划提交失败'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  try {
    stats.value = await loadProxyPoolStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '节点池统计读取失败'
  }
  await Promise.all([reloadCandidates(), reloadAllocations()])
})
</script>

<style scoped>
.proxy-ops { margin-top: 24px; border-top: 1px solid var(--border); padding-top: 16px; }
.proxy-ops-title { margin: 0 0 12px; font-size: 15px; }
.proxy-ops-subtitle { margin: 16px 0 8px; font-size: 14px; }
.check-group { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; display: flex; gap: 10px; }
.check-group legend { font-size: 12px; color: var(--muted); padding: 0 4px; }
.check-item { display: flex; align-items: center; gap: 4px; font-size: 13px; }
.plan-message { font-size: 13px; color: #15803d; }
.analysis-form label span { font-size: 12px; color: var(--muted); }
</style>
