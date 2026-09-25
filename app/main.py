"""管网漏点隔离规划台 — FastAPI 入口。

API:
  GET  /api/network                 读取当前管网输入
  PUT  /api/network                 整体保存管网输入(校验后覆盖)
  POST /api/plan                    常规求解并保存方案
  POST /api/plan-failover           单阀失效备援求解并保存方案
  POST /api/plan-siting             新增阀门选址预览(只读, 不写管网/不入库)
  GET  /api/plans                   最近方案列表
  GET  /api/plans/{id}              方案详情(含节点/管段状态)
  POST /api/plans/{id}/sequence     可行方案的现场执行顺序规划(只读计算)
  POST /api/plans/{id}/jumpers      可行方案的临时供水跨接预览(只读计算)
  POST /api/demo                    恢复内置示例管网
  POST /api/clear                   清空管网输入
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

import database
from demo import DEMO_NETWORK
from solver import (MAX_VALVES, NetworkValidationError, plan_jumpers,
                    plan_sequence, solve, solve_failover, solve_siting)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class NodeIn(BaseModel):
    id: str = Field(min_length=1)
    role: Literal["inlet", "leak", "must_keep", "normal"] = "normal"
    label: str | None = None

    @field_validator("id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("节点编号不能为空")
        return v


class SegmentIn(BaseModel):
    id: str = Field(min_length=1)
    endpoint_a: str = Field(min_length=1)
    endpoint_b: str = Field(min_length=1)

    @field_validator("id", "endpoint_a", "endpoint_b")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class ValveIn(BaseModel):
    id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    cost: int = Field(gt=0)

    @field_validator("id", "segment_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class NetworkIn(BaseModel):
    nodes: list[NodeIn] = Field(default_factory=list)
    segments: list[SegmentIn] = Field(default_factory=list)
    valves: list[ValveIn] = Field(default_factory=list, max_length=MAX_VALVES)


class SequenceIn(BaseModel):
    """执行顺序规划录入: 计划阀门操作时长 + 漏点漏水权重。

    值的正整数校验在 solver.plan_sequence 中逐条进行, 以便返回明确错误。
    """
    durations: dict[str, Any] = Field(default_factory=dict)
    leak_weights: dict[str, Any] = Field(default_factory=dict)


class CandidateIn(BaseModel):
    """选址候选新阀录入。

    各字段(唯一新阀编号/尚无阀门的管段/正整数安装费与关阀代价)在
    solver.solve_siting 中复用图校验逐条检查, 以便返回明确错误。
    """
    id: Any = None
    segment_id: Any = None
    install_cost: Any = None
    close_cost: Any = None


class SitingIn(BaseModel):
    """选址预览请求: 当前编辑中的管网(可空, 空则用已保存管网) + 候选位置。"""
    network: NetworkIn | None = None
    candidates: list[CandidateIn] = Field(default_factory=list)


class JumperIn(BaseModel):
    """临时供水跨接候选录入。

    各字段(唯一跨接编号/两个不同的现有节点/正整数铺设费)在
    solver.plan_jumpers 中逐条校验, 以便返回明确错误。
    """
    id: Any = None
    endpoint_a: Any = None
    endpoint_b: Any = None
    cost: Any = None


class JumpersIn(BaseModel):
    """临时供水跨接预览录入: 至多 6 条候选跨接。"""
    candidates: list[JumperIn] = Field(default_factory=list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    yield


app = FastAPI(title="管网漏点隔离规划台", version="1.0.0", lifespan=lifespan)


def _payload(net: NetworkIn) -> dict:
    return net.model_dump()


@app.get("/api/network")
def get_network() -> dict:
    with database.get_conn() as conn:
        return database.load_network(conn)


@app.put("/api/network")
def save_network(net: NetworkIn) -> dict:
    payload = _payload(net)
    try:
        # 提前用求解器的完整校验(端点存在、阀门绑定等), 保证保存的是有效管网
        from solver import validate_network
        validate_network(payload)
    except NetworkValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    with database.get_conn() as conn:
        database.replace_network(conn, payload)
    return {"ok": True, "counts": {
        "nodes": len(payload["nodes"]),
        "segments": len(payload["segments"]),
        "valves": len(payload["valves"]),
    }}


@app.post("/api/plan")
def create_plan(net: NetworkIn | None = None) -> dict:
    """常规求解。请求体可空: 为空时使用数据库中已保存的管网。"""
    with database.get_conn() as conn:
        payload = _payload(net) if net is not None else database.load_network(conn)
        try:
            result = solve(payload)
        except NetworkValidationError as exc:
            raise HTTPException(status_code=422,
                                detail={"errors": exc.errors})
        plan_id = database.save_plan(conn, result, payload)
    result["id"] = plan_id
    return result


@app.post("/api/plan-failover")
def create_failover_plan(net: NetworkIn | None = None) -> dict:
    """单阀失效备援求解(独立 API)。请求体可空: 为空时用已保存管网。

    要求计划关阀方案在正常执行及任意一枚计划阀门关不上(保持开启)时,
    全部漏点与进水点断开、全部必保点仍连通至少一个进水点。
    """
    with database.get_conn() as conn:
        payload = _payload(net) if net is not None else database.load_network(conn)
        try:
            result = solve_failover(payload)
        except NetworkValidationError as exc:
            raise HTTPException(status_code=422,
                                detail={"errors": exc.errors})
        plan_id = database.save_plan(conn, result, payload)
    result["id"] = plan_id
    return result


@app.post("/api/plan-siting")
def create_siting_plan(req: SitingIn) -> dict:
    """新增阀门选址预览(独立 API, 只读: 不写管网、不写入历史方案)。

    对当前编辑中的管网(请求体未带 network 时使用已保存管网), 在尚无阀门
    的管段上评估至多 6 个候选新阀, 寻找漏点全隔离且必保点全有水的增设及
    关阀方案: 只为实际关闭的候选阀计安装费, 先最小化安装费合计, 再最小化
    本次关阀代价, 仍并列按新增管段编号列表、关阀编号列表字典序决胜。
    """
    if req.network is not None:
        payload = _payload(req.network)
    else:
        with database.get_conn() as conn:
            payload = database.load_network(conn)
    try:
        return solve_siting(payload,
                            [c.model_dump() for c in req.candidates])
    except NetworkValidationError as exc:
        raise HTTPException(status_code=422,
                            detail={"errors": exc.errors})


@app.get("/api/plans")
def get_plans(limit: int = 20) -> dict:
    limit = max(1, min(limit, 100))
    with database.get_conn() as conn:
        return {"plans": database.list_plans(conn, limit)}


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int) -> dict:
    with database.get_conn() as conn:
        plan = database.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    return plan


@app.post("/api/plans/{plan_id}/sequence")
def plan_execution_sequence(plan_id: int, seq: SequenceIn) -> dict:
    """已保存可行方案的现场执行顺序规划(独立 API)。

    只读计算: 仅读取方案及其管网快照, 不修改、不新增任何数据。
    不可行方案、遗漏/多余编号、非正整数数值都会返回明确错误。
    """
    with database.get_conn() as conn:
        plan = database.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    if not plan.get("feasible"):
        reason = plan.get("reason_code")
        raise HTTPException(status_code=422, detail={"errors": [
            f"方案 #{plan_id} 为不可行方案"
            + (f"(reason_code: {reason})" if reason else "")
            + ", 没有可执行的关阀清单, 不能规划执行顺序"
        ]})
    try:
        result = plan_sequence(plan["network_snapshot"],
                               plan.get("closed_valves", []),
                               seq.durations, seq.leak_weights)
    except NetworkValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    result["plan_id"] = plan_id
    result["solver"] = plan.get("solver", "base")
    return result


@app.post("/api/plans/{plan_id}/jumpers")
def plan_jumper_preview(plan_id: int, req: JumpersIn) -> dict:
    """已保存可行方案的临时供水跨接预览(独立 API, 只读计算)。

    基于方案快照与已关阀管段, 评估至多 6 条候选跨接(新增通水管段):
    在保持全部漏点与进水点断开、全部必保点有水的前提下, 先最大化由断水
    转为有水的普通节点数, 再最小化铺设费合计, 并列时按跨接编号升序列表
    字典序选取; 无法恢复普通节点时返回空方案。不修改管网与历史方案。
    """
    with database.get_conn() as conn:
        plan = database.get_plan(conn, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="方案不存在")
    if not plan.get("feasible"):
        reason = plan.get("reason_code")
        raise HTTPException(status_code=422, detail={"errors": [
            f"方案 #{plan_id} 为不可行方案"
            + (f"(reason_code: {reason})" if reason else "")
            + ", 没有已关阀管段可作为跨接预览的基础, 不能规划临时供水跨接"
        ]})
    try:
        result = plan_jumpers(plan["network_snapshot"],
                              plan.get("closed_valves", []),
                              [c.model_dump() for c in req.candidates])
    except NetworkValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    result["plan_id"] = plan_id
    result["solver"] = plan.get("solver", "base")
    return result


@app.post("/api/demo")
def load_demo() -> dict:
    with database.get_conn() as conn:
        database.replace_network(conn, DEMO_NETWORK)
    return {"ok": True, "network": DEMO_NETWORK}


@app.post("/api/clear")
def clear_network() -> dict:
    with database.get_conn() as conn:
        database.replace_network(conn,
                                 {"nodes": [], "segments": [], "valves": []})
    return {"ok": True}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---- 静态页面 ----
STATIC_DIR = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
