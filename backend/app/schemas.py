"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class SwitchPlanPayload(BaseModel):
    """代理切换计划提交：处理项（流程）批量分配健康候选节点。"""
    plan_id: str | None = None
    flows: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    protocols: list[str] = Field(default_factory=list)
    strategy_ids: list[int] | None = None
