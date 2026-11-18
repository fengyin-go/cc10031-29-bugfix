"""代理防护联动接口：健康候选查询、切换计划提交、分配结果清单。

与通用资源路由分离，避免 /api/{module}/{entry_id} 动态段抢占固定路径；
健康判定与候选池统一走 proxy_router 服务的 status 口径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.schemas import SwitchPlanPayload
from app.services.anti_block import AntiBlockService
from app.services.proxy_router import ProxyRouterService
from app.services import service_for

router = APIRouter(prefix="/api/proxy-ops", tags=["代理防护联动"])


def _proxy_service() -> ProxyRouterService:
    svc = service_for("proxy_router")
    assert isinstance(svc, ProxyRouterService)
    return svc


def _anti_block_service() -> AntiBlockService:
    svc = service_for("anti_block")
    assert isinstance(svc, AntiBlockService)
    return svc


@router.get("/candidates")
def list_candidates(
    regions: list[str] = Query(default=[]),
    protocols: list[str] = Query(default=[]),
) -> dict[str, Any]:
    """健康候选池：支持批量选择地域和协议，返回探测状态。

    在线节点（含暂无探测数据的在线节点）都会保留在候选池中，
    已禁用节点任何分支都不入池。
    """
    svc = _proxy_service()
    pool = svc.get_candidate_pool(regions=regions or None, protocols=protocols or None)
    return {
        "candidates": pool,
        "total": len(pool),
        "stats": svc.get_node_pool_stats(),
    }


@router.get("/pool-stats")
def pool_stats() -> dict[str, Any]:
    """节点池统计：在线状态与健康节点数同一口径。"""
    return _proxy_service().get_node_pool_stats()


@router.post("/switch-plan")
def submit_switch_plan(payload: SwitchPlanPayload) -> dict[str, Any]:
    """生成并整组提交代理切换计划。

    同一 plan_id 并发或重复提交只生效一次；同一节点不会重复分配给多个流程。
    """
    proxy_svc = _proxy_service()
    anti_svc = _anti_block_service()
    if not payload.flows:
        return {"ok": False, "message": "缺少处理项（流程），无法生成切换计划",
                "plan": None, "apply": None}
    plan = proxy_svc.build_switch_plan(
        payload.flows,
        regions=payload.regions or None,
        protocols=payload.protocols or None,
        plan_id=payload.plan_id or None,
    )
    apply_result = anti_svc.apply_switch_plan(plan, strategy_ids=payload.strategy_ids)
    plan["terminal_state"] = apply_result.get("terminal_state", plan["terminal_state"])
    return {
        "ok": apply_result.get("ok", False),
        "message": apply_result.get("message", ""),
        "plan": plan,
        "apply": apply_result,
        "receipts": apply_result.get("receipts", []),
    }


@router.get("/allocations")
def list_allocations() -> dict[str, Any]:
    """分配结果清单：唯一回执与已生效计划。"""
    return _anti_block_service().get_allocations()
