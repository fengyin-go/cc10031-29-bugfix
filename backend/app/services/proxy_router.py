"""代理路由领域服务 — 代理节点池管理，输出 domain_snapshot。

数据工程核心：数据聚合计算与指标产出 — 跨模块数据产物

健康判定口径（修复点）：
- 节点记录只持久化 ``status`` 字段，健康判定与候选池必须读取同一字段；
- 不再读取节点上并不存在的“可用状态”字段，避免在线节点被整批过滤为空；
- 已禁用节点任何分支都不进入候选池；在线但暂无探测数据的节点可先按
  在线状态进入候选，探测完成后再补充探测数据，两个分支都不会漏掉在线节点。
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ProxyRouterService(BaseModuleService):
    MODULE = "proxy_router"
    ENTITY = "代理节点"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["节点名称", "IP地址", "出口地域", "协议类型", "带宽上限", "延迟"]
    REQUIRED_FIELDS = ["节点名称", "IP地址", "出口地域"]
    STATUS_ORDER = ["在线", "离线", "限速中", "已禁用"]
    ACTION_RULES = {"启用节点": "离线", "禁用节点": "限速中"}
    NEGATIVE_ACTIONS = []

    # 健康判定与候选池共用的唯一数据字段：节点记录只保存 status
    HEALTH_FIELD = "status"
    HEALTHY_STATUS = "在线"
    DISABLED_STATUS = "已禁用"

    def __init__(self) -> None:
        self._plan_lock = threading.Lock()
        self._plan_seq = 0

    # ------------------------------------------------------------------
    # 健康判定（与候选池同一字段、同一口径）
    # ------------------------------------------------------------------

    def is_healthy(self, row: dict[str, Any]) -> bool:
        """节点是否健康：只看 status 是否为在线，已禁用永远不健康。"""
        return row.get(self.HEALTH_FIELD) == self.HEALTHY_STATUS

    @staticmethod
    def probe_state(row: dict[str, Any]) -> str:
        """探测状态：有延迟探测数据为已探测，否则为待探测。

        在线但暂时没有探测数据的节点返回“待探测”，仍允许进入候选池；
        探测完成后数据补齐，节点继续留在候选池，两个分支都不会让节点消失。
        """
        latency = row.get("延迟")
        return "已探测" if latency not in (None, "") else "待探测"

    def _node_view(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id"),
            "name": row.get("节点名称"),
            "ip": row.get("IP地址"),
            "region": row.get("出口地域"),
            "protocol": row.get("协议类型"),
            "bandwidth": row.get("带宽上限"),
            "latency": row.get("延迟"),
            "status": row.get("status"),
            "probe_state": self.probe_state(row),
            "healthy": self.is_healthy(row),
            "selectable": self.is_healthy(row),
        }

    def get_healthy_nodes(self) -> list[dict[str, Any]]:
        """获取健康代理节点：在线即可用，与候选池读取同一 status 字段。"""
        rows = store.rows(self.MODULE)
        return [self._node_view(r) for r in rows if self.is_healthy(r)]

    # ------------------------------------------------------------------
    # 候选池：批量地域/协议筛选，已禁用永不入池
    # ------------------------------------------------------------------

    def get_candidate_pool(
        self,
        regions: list[str] | None = None,
        protocols: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """解析可批量使用的健康节点集合。

        - 与健康判定使用同一 status 字段，在线节点不会因空查询消失；
        - 在线但待探测的节点同样保留在候选池；
        - 已禁用节点保持不可选，任何分支都不入池；
        - regions / protocols 支持批量多选，空列表表示不限。
        """
        pool = self.get_healthy_nodes()
        if regions:
            wanted = {r.strip() for r in regions if r and r.strip()}
            if wanted:
                pool = [n for n in pool if str(n.get("region") or "") in wanted]
        if protocols:
            wanted = {p.strip() for p in protocols if p and p.strip()}
            if wanted:
                pool = [n for n in pool if str(n.get("protocol") or "") in wanted]
        return pool

    # ------------------------------------------------------------------
    # 代理切换计划：同一节点不重复分配给多个流程
    # ------------------------------------------------------------------

    def build_switch_plan(
        self,
        flows: list[str],
        regions: list[str] | None = None,
        protocols: list[str] | None = None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        """按处理项（流程）生成代理切换计划。

        从候选池逐条分配唯一节点：同一节点不会重复分配给多个流程；
        候选不足时剩余流程进入 unassigned，计划终态随之改变。
        """
        candidates = self.get_candidate_pool(regions=regions, protocols=protocols)
        items: list[dict[str, Any]] = []
        unassigned: list[str] = []
        used_node_ids: set[Any] = set()
        for flow in flows:
            node = next((c for c in candidates if c["id"] not in used_node_ids), None)
            if node is None:
                unassigned.append(flow)
                continue
            used_node_ids.add(node["id"])
            items.append({
                "flow": flow,
                "node_id": node["id"],
                "node_name": node["name"],
                "node_ip": node["ip"],
                "region": node["region"],
                "protocol": node["protocol"],
                "probe_state": node["probe_state"],
            })
        if plan_id is None:
            with self._plan_lock:
                self._plan_seq += 1
                plan_id = f"PLAN-{self._plan_seq:04d}"
        terminal_state = "待提交" if not unassigned else "候选不足"
        return {
            "plan_id": plan_id,
            "flows": list(flows),
            "items": items,
            "unassigned": unassigned,
            "candidate_count": len(candidates),
            "terminal_state": terminal_state,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # 统计：在线状态与健康节点数同一口径
    # ------------------------------------------------------------------

    def get_node_pool_stats(self) -> dict[str, Any]:
        """获取节点池统计，健康节点数与候选池、在线状态口径一致。"""
        rows = store.rows(self.MODULE)
        healthy = self.get_healthy_nodes()
        return {
            "total_nodes": len(rows),
            "online": sum(1 for r in rows if r.get("status") == "在线"),
            "offline": sum(1 for r in rows if r.get("status") == "离线"),
            "rate_limited": sum(1 for r in rows if r.get("status") == "限速中"),
            "disabled": sum(1 for r in rows if r.get("status") == "已禁用"),
            "healthy": len(healthy),
            "probed": sum(1 for n in healthy if n["probe_state"] == "已探测"),
            "probe_pending": sum(1 for n in healthy if n["probe_state"] == "待探测"),
            "regions": sorted({r.get("出口地域", "") for r in rows if r.get("出口地域")}),
            "protocols": sorted({r.get("协议类型", "") for r in rows if r.get("协议类型")}),
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        stats = self.get_node_pool_stats()
        return {**base, **stats}
