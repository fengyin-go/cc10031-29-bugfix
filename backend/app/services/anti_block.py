"""反封锁领域服务 — 防护策略管理，输出 domain_snapshot 供跨模块消费。

数据工程核心：数据清洗与转换链路处理 — 跨模块数据产物

代理切换计划处理口径：
- 反封锁模块负责解析处理项形成的代理切换计划，按候选节点逐条核对、整组提交；
- 处理列表（防护策略行）与结果清单（分配回执）在同一事务内同时更新；
- 并发重复提交同一计划只生效一次（按 plan_id 幂等去重）；
- 状态流转沿用既有规则（STATUS_ORDER / pending 口径），不另起一套；
- 批量切换中同一节点不会重复分配给多个流程，冲突会改变联动处理终态。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store

# 联动处理终态：整组提交成功 / 核对冲突（含重复分配、节点不可用等）
TERMINAL_APPLIED = "整组已提交"
TERMINAL_CONFLICT = "核对冲突未提交"


class AntiBlockService(BaseModuleService):
    MODULE = "anti_block"
    ENTITY = "防护策略"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["策略名称", "目标站点", "请求间隔", "IP代理池", "UA轮换", "验证码处理", "Cookie策略"]
    REQUIRED_FIELDS = ["策略名称", "目标站点", "请求间隔"]
    STATUS_ORDER = ["草稿", "已启用", "触发封锁", "已停用"]
    ACTION_RULES = {"启用策略": "草稿", "停用策略": "已启用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    # 节点健康判定与代理路由候选池共用同一数据字段
    NODE_HEALTH_FIELD = "status"
    NODE_HEALTHY_STATUS = "在线"
    NODE_DISABLED_STATUS = "已禁用"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._applied_plans: dict[str, dict[str, Any]] = {}
        self._receipts: list[dict[str, Any]] = []
        self._receipt_seq = 0

    # ------------------------------------------------------------------
    # 站点防护查询
    # ------------------------------------------------------------------

    def get_site_protection(self, site: str) -> dict[str, Any]:
        """查询指定站点的反封锁策略覆盖情况。"""
        rows = store.rows(self.MODULE)
        active = [
            {
                "id": r["id"], "name": r.get("策略名称"),
                "interval": r.get("请求间隔"), "ip_pool": r.get("IP代理池"),
                "ua_rotation": r.get("UA轮换"), "captcha": r.get("验证码处理"),
            }
            for r in rows
            if r.get("status") == "已启用" and (site in r.get("目标站点", "") or site == r.get("目标站点"))
        ]
        return {
            "site": site,
            "active_protections": len(active),
            "strategies": active,
            "blocked": sum(1 for r in rows if r.get("status") == "触发封锁"),
        }

    # ------------------------------------------------------------------
    # 代理切换计划：逐条核对、整组提交、唯一回执、幂等去重
    # ------------------------------------------------------------------

    def _target_strategies(self, count: int,
                           strategy_ids: list[int] | None) -> list[dict[str, Any] | None]:
        """按处理项数量取待更新策略：显式指定优先，否则取触发封锁中的策略。"""
        if strategy_ids:
            found: list[dict[str, Any] | None] = []
            for sid in strategy_ids:
                found.append(store.find(self.MODULE, int(sid)))
            return found
        blocked = [
            r for r in store.rows(self.MODULE)
            if r.get("status") == "触发封锁"
        ]
        return blocked[:count]

    def _verify_item(self, item: dict[str, Any],
                     strategy: dict[str, Any] | None,
                     seen_node_ids: set[Any]) -> str | None:
        """逐条核对计划项，返回失败原因；None 表示核对通过。"""
        node_id = item.get("node_id")
        node = store.find("proxy_router", int(node_id)) if node_id is not None else None
        if node is None:
            return f"节点 {node_id} 不存在"
        if node.get(self.NODE_HEALTH_FIELD) == self.NODE_DISABLED_STATUS:
            return f"节点 {node_id} 已禁用，不可分配"
        if node.get(self.NODE_HEALTH_FIELD) != self.NODE_HEALTHY_STATUS:
            return f"节点 {node_id} 不在线，当前状态 {node.get(self.NODE_HEALTH_FIELD)}"
        if node_id in seen_node_ids:
            return f"节点 {node_id} 被重复分配给多个流程"
        if strategy is None:
            return f"处理项 {item.get('flow')} 没有待更新的防护策略"
        return None

    def apply_switch_plan(
        self,
        plan: dict[str, Any],
        strategy_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        """解析处理项形成的代理切换计划并整组应用。

        - 逐条核对：节点存在、在线、未禁用、不重复分配、策略齐备；
        - 整组提交：任一处理项核对失败则整组不提交，联动终态变为核对冲突；
        - 唯一回执：每条成功分配生成全局唯一回执号；
        - 幂等：同一 plan_id 并发或重复提交只生效一次，后续返回首次结果；
        - 状态沿用既有规则：触发封锁 → 已启用，pending 按 STATUS_ORDER 终态口径。
        """
        plan_id = str(plan.get("plan_id") or "").strip()
        if not plan_id:
            return {"ok": False, "message": "切换计划缺少 plan_id，无法幂等登记", "receipts": []}

        with self._lock:
            if plan_id in self._applied_plans:
                cached = self._applied_plans[plan_id]
                return {**cached, "deduplicated": True,
                        "message": f"计划 {plan_id} 已提交过，重复提交仅首次生效"}

            items = list(plan.get("items", []))
            if not items:
                return {"ok": False, "plan_id": plan_id,
                        "terminal_state": TERMINAL_CONFLICT,
                        "checks": [], "receipts": [], "deduplicated": False,
                        "message": "计划内无可提交的处理项，整组未提交"}
            strategies = self._target_strategies(len(items), strategy_ids)

            # 逐条核对
            checks: list[dict[str, Any]] = []
            seen_node_ids: set[Any] = set()
            for idx, item in enumerate(items):
                strategy = strategies[idx] if idx < len(strategies) else None
                error = self._verify_item(item, strategy, seen_node_ids)
                seen_node_ids.add(item.get("node_id"))
                checks.append({
                    "flow": item.get("flow"),
                    "node_id": item.get("node_id"),
                    "strategy_id": strategy.get("id") if strategy else None,
                    "passed": error is None,
                    "reason": error or "核对通过",
                })

            failures = [c for c in checks if not c["passed"]]
            if failures:
                # 整组不提交：处理列表与结果清单保持原状，仅改变联动终态
                result = {
                    "ok": False,
                    "plan_id": plan_id,
                    "terminal_state": TERMINAL_CONFLICT,
                    "checks": checks,
                    "receipts": [],
                    "deduplicated": False,
                    "message": f"{len(failures)} 条处理项核对失败，整组未提交",
                }
                return result

            # 整组提交：处理列表（策略行）与结果清单（回执）同时更新
            receipts: list[dict[str, Any]] = []
            now = datetime.now(timezone.utc).isoformat()
            for idx, item in enumerate(items):
                strategy = strategies[idx]
                assert strategy is not None  # 核对阶段已保证策略齐备
                node = store.find("proxy_router", int(item["node_id"]))
                assert node is not None  # 核对阶段已保证节点存在
                strategy["IP代理池"] = f"{node.get('节点名称')}({node.get('IP地址')})"
                if strategy.get("status") == "触发封锁":
                    strategy["status"] = "已启用"
                    strategy["abnormal"] = False
                # 沿用既有状态规则：终态（STATUS_ORDER 末位）之外保持待办
                strategy["pending"] = strategy.get("status") != self.STATUS_ORDER[-1]

                self._receipt_seq += 1
                receipts.append({
                    "receipt_id": f"RCPT-{self._receipt_seq:04d}",
                    "plan_id": plan_id,
                    "flow": item.get("flow"),
                    "node_id": node.get("id"),
                    "node_name": node.get("节点名称"),
                    "node_ip": node.get("IP地址"),
                    "strategy_id": strategy.get("id"),
                    "strategy_name": strategy.get("策略名称"),
                    "strategy_status": strategy.get("status"),
                    "allocated_at": now,
                })
            self._receipts.extend(receipts)

            result = {
                "ok": True,
                "plan_id": plan_id,
                "terminal_state": TERMINAL_APPLIED,
                "checks": checks,
                "receipts": receipts,
                "deduplicated": False,
                "message": f"计划 {plan_id} 已整组提交，{len(receipts)} 条分配生效",
            }
            self._applied_plans[plan_id] = {
                k: v for k, v in result.items() if k != "deduplicated"
            }
            return result

    def get_allocations(self) -> dict[str, Any]:
        """分配结果清单：已生效回执与已登记计划。"""
        with self._lock:
            return {
                "receipts": list(self._receipts),
                "receipt_count": len(self._receipts),
                "applied_plans": sorted(self._applied_plans),
            }

    # ------------------------------------------------------------------
    # 领域快照
    # ------------------------------------------------------------------

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        with self._lock:
            receipt_count = len(self._receipts)
            applied = len(self._applied_plans)
        return {
            **base,
            "active_strategies": sum(1 for r in rows if r.get("status") == "已启用"),
            "blocked_count": sum(1 for r in rows if r.get("status") == "触发封锁"),
            "switch_plans_applied": applied,
            "allocation_receipts": receipt_count,
        }
