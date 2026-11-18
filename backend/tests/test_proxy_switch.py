"""代理健康查询修复与代理切换计划联动测试。

覆盖：
1. 健康节点查询不再读取不存在的“可用状态”字段，在线节点不再被过滤为空
2. 健康判定与候选池使用同一 status 字段，口径一致
3. 已禁用节点任何分支都不入候选池；在线待探测节点保留在候选池
4. 批量地域/协议筛选
5. 切换计划唯一分配：同一节点不重复分配给多个流程
6. 反封锁整组提交：处理列表与结果清单同时更新，沿用既有状态规则
7. 并发重复提交只生效一次（plan_id 幂等）
8. 冲突（重复分配/节点不可用）整组不提交并改变联动处理终态
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app.services.anti_block import TERMINAL_APPLIED, TERMINAL_CONFLICT, AntiBlockService
from app.services.proxy_router import ProxyRouterService
from app.store import store


@pytest.fixture()
def proxy_svc() -> ProxyRouterService:
    return ProxyRouterService()


@pytest.fixture()
def anti_svc() -> AntiBlockService:
    return AntiBlockService()


def _plan_id() -> str:
    return f"TEST-{uuid.uuid4().hex[:12]}"


def _restore_row(module: str, entry_id: int, snapshot: dict) -> None:
    row = store.find(module, entry_id)
    if row is not None:
        row.clear()
        row.update(snapshot)


# ── 1. 健康字段判定修复 ──

class TestHealthyNodesFixed:
    def test_online_nodes_not_filtered_out(self, proxy_svc) -> None:
        """在线节点不再因读取不存在的“可用状态”字段而被过滤为空。"""
        healthy = proxy_svc.get_healthy_nodes()
        online = [r for r in store.rows("proxy_router") if r.get("status") == "在线"]
        assert len(healthy) == len(online) > 0
        assert {n["id"] for n in healthy} == {r["id"] for r in online}

    def test_health_and_candidate_share_same_field(self, proxy_svc) -> None:
        """健康判定与候选池使用同一 status 字段：改状态两边同步变化。"""
        row = next(r for r in store.rows("proxy_router") if r.get("status") == "在线")
        snapshot = dict(row)
        try:
            row["status"] = "离线"
            assert all(n["id"] != row["id"] for n in proxy_svc.get_healthy_nodes())
            assert all(n["id"] != row["id"] for n in proxy_svc.get_candidate_pool())
        finally:
            _restore_row("proxy_router", row["id"], snapshot)
        assert any(n["id"] == row["id"] for n in proxy_svc.get_healthy_nodes())
        assert any(n["id"] == row["id"] for n in proxy_svc.get_candidate_pool())

    def test_stats_consistent_with_online(self, proxy_svc) -> None:
        """在线状态统计与健康节点数口径一致。"""
        stats = proxy_svc.get_node_pool_stats()
        assert stats["healthy"] == stats["online"]
        assert stats["healthy"] == len(proxy_svc.get_candidate_pool())


# ── 2. 候选池分支规则 ──

class TestCandidatePool:
    def test_disabled_never_in_pool(self, proxy_svc) -> None:
        """已禁用节点保持不可选：即使补上探测数据也不入池。"""
        disabled = [r for r in store.rows("proxy_router") if r.get("status") == "已禁用"]
        assert disabled, "种子数据应包含已禁用节点"
        row = disabled[0]
        snapshot = dict(row)
        try:
            row["延迟"] = "10ms"  # 已探测分支也不放行
            pool_ids = {n["id"] for n in proxy_svc.get_candidate_pool()}
            assert row["id"] not in pool_ids
        finally:
            _restore_row("proxy_router", row["id"], snapshot)

    def test_online_without_probe_stays_in_pool(self, proxy_svc) -> None:
        """在线但暂无探测数据的节点可先按在线状态进入候选。"""
        row = next(r for r in store.rows("proxy_router") if r.get("status") == "在线")
        snapshot = dict(row)
        try:
            row["延迟"] = ""
            pool = proxy_svc.get_candidate_pool()
            match = [n for n in pool if n["id"] == row["id"]]
            assert match and match[0]["probe_state"] == "待探测"
            # 探测完成后节点仍留在候选池
            row["延迟"] = "31ms"
            match = [n for n in proxy_svc.get_candidate_pool() if n["id"] == row["id"]]
            assert match and match[0]["probe_state"] == "已探测"
        finally:
            _restore_row("proxy_router", row["id"], snapshot)

    def test_batch_region_protocol_filter(self, proxy_svc) -> None:
        """批量选择地域和协议查看健康候选。"""
        stats = proxy_svc.get_node_pool_stats()
        regions = stats["regions"]
        assert regions
        picked = regions[:1]
        pool = proxy_svc.get_candidate_pool(regions=picked)
        assert pool and all(n["region"] in picked for n in pool)

        full = proxy_svc.get_candidate_pool()
        protocols = sorted({n["protocol"] for n in full})
        if protocols:
            pool = proxy_svc.get_candidate_pool(protocols=protocols[:1])
            assert all(n["protocol"] == protocols[0] for n in pool)

        # 空查询不过滤：在线节点不会因空查询消失
        assert len(proxy_svc.get_candidate_pool(regions=[], protocols=[])) == len(full)


# ── 3. 切换计划：唯一分配 ──

class TestSwitchPlan:
    def test_unique_allocation(self, proxy_svc) -> None:
        """批量切换不把同一节点重复分配给多个流程。"""
        flows = ["流程A", "流程B", "流程C"]
        plan = proxy_svc.build_switch_plan(flows, plan_id=_plan_id())
        node_ids = [item["node_id"] for item in plan["items"]]
        assert len(node_ids) == len(set(node_ids))
        assert len(plan["items"]) + len(plan["unassigned"]) == len(flows)

    def test_candidate_shortage_marked_unassigned(self, proxy_svc) -> None:
        """候选不足时剩余处理项进入 unassigned，终态随之改变。"""
        candidate_count = len(proxy_svc.get_candidate_pool())
        flows = [f"流程{i}" for i in range(candidate_count + 2)]
        plan = proxy_svc.build_switch_plan(flows, plan_id=_plan_id())
        assert len(plan["unassigned"]) == 2
        assert plan["terminal_state"] == "候选不足"


# ── 4. 反封锁整组提交 ──

class TestApplySwitchPlan:
    def _blocked_strategy(self) -> dict:
        row = next(
            (r for r in store.rows("anti_block") if r.get("status") == "触发封锁"), None)
        if row is None:  # 前面用例已恢复过则直接造一条
            row = store.find("anti_block", 3)
            row["status"] = "触发封锁"
            row["pending"] = True
        return row

    def test_group_commit_updates_list_and_receipts(self, proxy_svc, anti_svc) -> None:
        """整组提交：处理列表（策略行）与结果清单（回执）同时更新。"""
        strategy = self._blocked_strategy()
        snapshot = dict(strategy)
        plan = proxy_svc.build_switch_plan(["流程X"], plan_id=_plan_id())
        assert plan["items"], "至少应有一条可分配处理项"
        before = len(anti_svc.get_allocations()["receipts"])
        try:
            result = anti_svc.apply_switch_plan(plan, strategy_ids=[strategy["id"]])
            assert result["ok"] is True
            assert result["terminal_state"] == TERMINAL_APPLIED
            # 处理列表更新：策略恢复已启用、代理池写入节点、沿用既有 pending 规则
            assert strategy["status"] == "已启用"
            assert strategy["pending"] is True  # 已启用不是终态，保持待办
            node = store.find("proxy_router", plan["items"][0]["node_id"])
            assert node["节点名称"] in strategy["IP代理池"]
            # 结果清单同步更新：新增唯一回执
            after = anti_svc.get_allocations()["receipts"]
            assert len(after) == before + 1
            receipt = after[-1]
            assert receipt["receipt_id"].startswith("RCPT-")
            assert receipt["plan_id"] == plan["plan_id"]
            assert receipt["strategy_id"] == strategy["id"]
        finally:
            _restore_row("anti_block", strategy["id"], snapshot)

    def test_duplicate_submission_applies_once(self, proxy_svc, anti_svc) -> None:
        """并发重复提交同一 plan_id 只生效一次。"""
        strategy = self._blocked_strategy()
        snapshot = dict(strategy)
        plan = proxy_svc.build_switch_plan(["流程Y"], plan_id=_plan_id())
        try:
            first = anti_svc.apply_switch_plan(plan, strategy_ids=[strategy["id"]])
            assert first["ok"] is True
            receipts_after_first = len(anti_svc.get_allocations()["receipts"])
            # 重复提交：去重返回首次结果，不再新增回执
            second = anti_svc.apply_switch_plan(plan, strategy_ids=[strategy["id"]])
            assert second["deduplicated"] is True
            assert second["receipts"] == first["receipts"]
            assert len(anti_svc.get_allocations()["receipts"]) == receipts_after_first
        finally:
            _restore_row("anti_block", strategy["id"], snapshot)

    def test_conflict_rejects_whole_group(self, proxy_svc, anti_svc) -> None:
        """同一节点重复分配给多个流程 → 整组不提交，联动终态改变。"""
        strategy = self._blocked_strategy()
        snapshot = dict(strategy)
        pool = proxy_svc.get_candidate_pool()
        node_id = pool[0]["id"]
        plan = {
            "plan_id": _plan_id(),
            "items": [
                {"flow": "流程1", "node_id": node_id},
                {"flow": "流程2", "node_id": node_id},  # 重复分配 → 冲突
            ],
        }
        try:
            result = anti_svc.apply_switch_plan(plan, strategy_ids=[strategy["id"]])
            assert result["ok"] is False
            assert result["terminal_state"] == TERMINAL_CONFLICT
            assert result["receipts"] == []
            # 整组未提交：策略行保持原状
            assert strategy["IP代理池"] == snapshot["IP代理池"]
            assert strategy["status"] == snapshot["status"]
        finally:
            _restore_row("anti_block", strategy["id"], snapshot)

    def test_disabled_node_rejected(self, proxy_svc, anti_svc) -> None:
        """已禁用节点即使被手工塞进计划也会被核对拦下。"""
        strategy = self._blocked_strategy()
        snapshot = dict(strategy)
        disabled = next(r for r in store.rows("proxy_router") if r.get("status") == "已禁用")
        plan = {
            "plan_id": _plan_id(),
            "items": [{"flow": "流程Z", "node_id": disabled["id"]}],
        }
        try:
            result = anti_svc.apply_switch_plan(plan, strategy_ids=[strategy["id"]])
            assert result["ok"] is False
            assert "已禁用" in result["checks"][0]["reason"]
        finally:
            _restore_row("anti_block", strategy["id"], snapshot)
