"""管网漏点隔离求解器。

模型:
  - 节点 = 无向管网上的点; 每个节点有角色: inlet(进水点)/leak(漏点)/
    must_keep(必保供水点)/normal(普通点)。
  - 管段 = 无向边; 每条管段最多绑定 1 个阀门。关闭阀门即删除对应管段。
  - 目标: 选择代价之和最小的阀门组合, 使
      (A) 每个漏点与所有进水点都不连通(漏点被隔离);
      (B) 每个必保点仍与至少一个进水点连通。
    同代价时, 取关闭阀门编号升序表的字典序最小者。

两种求解:
  - solve(payload): 常规规划, 只要求方案本身满足 (A)(B)。
  - solve_failover(payload): 单阀失效备援规划。计划关闭的每一枚阀门都可能
    "关不上" 而保持开启; 要求方案正常执行时, 以及任意一枚计划阀门失效(其
    绑定管段保持通水)时, (A)(B) 仍然成立。位掩码 M 的方案备援可行, 当且
    仅当 M 本身以及每个 M\\{j}(j∈M) 都是常规可行方案。

另提供 plan_sequence(snapshot, closed_valves, durations, leak_weights):
对已保存的可行方案做现场执行顺序规划(只读计算)。每枚计划阀门有正整数
操作时长, 每个漏点有正整数漏水权重; 操作某阀的时段内该阀仍开启, 该时段
暴露量 = 时长 × 仍通水漏点权重之和; 用子集 DP 求累计暴露量最小的执行
顺序, 同暴露量取阀门编号序列字典序最小者。

以及 solve_siting(payload, candidates): 新增阀门选址预览(只读计算)。
在尚无阀门的管段上给出至多 6 个候选新阀(唯一新阀编号 + 正整数安装费 +
正整数关阀代价, 原阀与候选合计仍不超过 14 个), 枚举"原阀 + 全部候选"
的关阀子集, 寻找漏点全隔离且必保点全有水的增设及关阀方案。只为实际关闭
的候选阀计安装费; 优化次序: 安装费合计最小 -> 本次关阀代价最小 ->
新增管段编号升序列表字典序 -> 关阀编号升序列表字典序。无解时说明候选
范围内不可修复。

以及 plan_jumpers(snapshot, closed_valves, raw_candidates): 已保存可行
方案的临时供水跨接预览(只读计算)。基于方案快照与已关阀管段, 录入至多
6 条候选跨接(唯一编号 + 两个不同的现有节点 + 正整数铺设费), 跨接视为
新增通水管段。枚举全部候选组合, 始终保持全部漏点与进水点断开、全部必保
点有水; 先最大化由断水转为有水的普通节点数, 再最小化铺设费合计, 并列时
按跨接编号升序列表字典序选取; 无法恢复普通节点时返回空方案。

阀门最多 14 个, 枚举全部 2^V 个子集(V<=14 => 16384), 对每个子集做
BFS 连通性检查即可, 简单且确定。
"""

from __future__ import annotations

from collections import deque
from typing import Any

MAX_VALVES = 14
MAX_CANDIDATES = 6


class NetworkValidationError(ValueError):
    """输入管网数据不合法, errors 字段携带逐条原因。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _norm_id(raw: Any) -> str:
    return str(raw if raw is not None else "").strip()


def _strict_pos_int(value: Any) -> int | None:
    """严格正整数: 接受 int 或其十进制字符串; 拒绝 bool/浮点/其他, 非法返回 None。"""
    try:
        iv = int(value)
        if iv <= 0 or str(iv) != str(value).strip():
            return None
        return iv
    except (TypeError, ValueError):
        return None


def validate_network(payload: dict[str, Any]) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    """校验并规整 {nodes, segments, valves}。

    返回 (nodes, segments, valves), 均为带规整字段的 dict 列表。
    """
    errors: list[str] = []

    raw_nodes = payload.get("nodes") or []
    raw_segments = payload.get("segments") or []
    raw_valves = payload.get("valves") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_segments, list) \
            or not isinstance(raw_valves, list):
        raise NetworkValidationError(["nodes/segments/valves 必须是数组"])

    # ---- 节点 ----
    node_role = {"inlet", "leak", "must_keep", "normal"}
    nodes: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    for i, nd in enumerate(raw_nodes):
        if not isinstance(nd, dict):
            errors.append(f"节点 #{i + 1}: 必须是对象")
            continue
        nid = _norm_id(nd.get("id"))
        if not nid:
            errors.append(f"节点 #{i + 1}: 缺少编号")
        elif nid in node_ids:
            errors.append(f"节点编号重复: {nid}")
        role = nd.get("role", "normal")
        if role not in node_role:
            errors.append(f"节点 {nid or i + 1}: 角色非法({role}), "
                          f"应为 inlet/leak/must_keep/normal")
        label = _norm_id(nd.get("label")) or nid
        node_ids.add(nid)
        nodes.append({"id": nid, "role": role, "label": label})

    # ---- 阀门 ----
    valves: list[dict[str, Any]] = []
    valve_ids: set[str] = set()
    for i, vl in enumerate(raw_valves):
        if not isinstance(vl, dict):
            errors.append(f"阀门 #{i + 1}: 必须是对象")
            continue
        vid = _norm_id(vl.get("id"))
        if not vid:
            errors.append(f"阀门 #{i + 1}: 缺少编号")
        elif vid in valve_ids:
            errors.append(f"阀门编号重复: {vid}")
        cost = vl.get("cost")
        cost_int = _strict_pos_int(cost)
        if cost_int is None:
            errors.append(f"阀门 {vid or i + 1}: 关闭代价必须是正整数")
        seg_id = _norm_id(vl.get("segment_id"))
        if not seg_id:
            errors.append(f"阀门 {vid or i + 1}: 必须绑定一条管段")
        valve_ids.add(vid)
        valves.append({"id": vid, "cost": cost_int, "segment_id": seg_id})

    if len(valves) > MAX_VALVES:
        errors.append(f"阀门数量为 {len(valves)}, 超过上限 {MAX_VALVES} 个")

    # ---- 管段 ----
    segments: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    seg_valves: dict[str, str] = {}
    for vl in valves:
        if vl["segment_id"]:
            if vl["segment_id"] in seg_valves:
                errors.append(
                    f"管段 {vl['segment_id']} 同时绑定了阀门 "
                    f"{seg_valves[vl['segment_id']]} 和 {vl['id']}"
                    f"(每条管段最多 1 个阀门)"
                )
            else:
                seg_valves[vl["segment_id"]] = vl["id"]

    for i, sg in enumerate(raw_segments):
        if not isinstance(sg, dict):
            errors.append(f"管段 #{i + 1}: 必须是对象")
            continue
        sid = _norm_id(sg.get("id"))
        if not sid:
            errors.append(f"管段 #{i + 1}: 缺少编号")
        elif sid in segment_ids:
            errors.append(f"管段编号重复: {sid}")
        a, b = _norm_id(sg.get("endpoint_a")), _norm_id(sg.get("endpoint_b"))
        if a not in node_ids:
            errors.append(f"管段 {sid or i + 1}: 端点 {a or '(空)'} 不存在")
        if b not in node_ids:
            errors.append(f"管段 {sid or i + 1}: 端点 {b or '(空)'} 不存在")
        if a and a == b:
            errors.append(f"管段 {sid or i + 1}: 两个端点不能相同({a})")
        segment_ids.add(sid)
        segments.append({
            "id": sid,
            "endpoint_a": a,
            "endpoint_b": b,
            "valve_id": seg_valves.get(sid),
        })

    for vl in valves:
        if vl["segment_id"] and vl["segment_id"] not in segment_ids:
            errors.append(f"阀门 {vl['id']}: 绑定的管段 {vl['segment_id']} 不存在")

    if errors:
        raise NetworkValidationError(errors)
    return nodes, segments, valves


class _Graph:
    """校验后管网的图表示: 邻接表 + 关阀位掩码 + BFS 连通性。"""

    def __init__(self, nodes: list[dict[str, Any]],
                 segments: list[dict[str, Any]],
                 valves: list[dict[str, Any]]):
        self.nodes = nodes
        self.segments = segments
        self.ids = [nd["id"] for nd in nodes]
        self.idx = {nid: i for i, nid in enumerate(self.ids)}
        n = len(self.ids)
        self.n = n

        self.inlet_set = {i for i, nd in enumerate(nodes)
                          if nd["role"] == "inlet"}
        self.leak_set = {i for i, nd in enumerate(nodes)
                         if nd["role"] == "leak"}
        self.keep_set = {i for i, nd in enumerate(nodes)
                         if nd["role"] == "must_keep"}
        self.leak_bits = sum(1 << i for i in self.leak_set)
        self.keep_bits = sum(1 << i for i in self.keep_set)

        # 阀门按编号升序: 保证位序确定, 同代价时用编号列表直接做字典序比较
        self.valves_sorted = sorted(valves, key=lambda vl: vl["id"])
        self.nv = len(self.valves_sorted)
        self.total_masks = 1 << self.nv
        v_idx = {vl["id"]: i for i, vl in enumerate(self.valves_sorted)}
        self.costs = [vl["cost"] for vl in self.valves_sorted]

        # 邻接表: adj[u] = [(v, edge_index)]; edge_closed_bit[e] = 该边阀门的位
        adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
        self.edge_closed_bit: list[int] = []
        for sg in segments:
            u, v = self.idx[sg["endpoint_a"]], self.idx[sg["endpoint_b"]]
            e = len(self.edge_closed_bit)
            bit = ((1 << v_idx[sg["valve_id"]])
                   if sg["valve_id"] is not None else 0)
            self.edge_closed_bit.append(bit)
            adj[u].append((v, e))
            adj[v].append((u, e))
        self.adj = adj

    def reachable(self, mask: int, sources: set[int]) -> int:
        """从 sources 出发、避开 mask 关阀管段后可达节点的位掩码。"""
        seen = 0
        dq: deque[int] = deque()
        for s in sources:
            if not (seen >> s) & 1:
                seen |= 1 << s
                dq.append(s)
        while dq:
            u = dq.popleft()
            for w, e in self.adj[u]:
                bit = self.edge_closed_bit[e]
                if bit and (mask & bit):
                    continue
                if not (seen >> w) & 1:
                    seen |= 1 << w
                    dq.append(w)
        return seen

    def subset_costs(self) -> list[int]:
        """所有阀门子集的代价; 按 lowbit DP 递推。"""
        costs = [0] * self.total_masks
        for m in range(1, self.total_masks):
            low = m & -m
            costs[m] = costs[m ^ low] + self.costs[low.bit_length() - 1]
        return costs

    def closed_names(self, mask: int) -> list[str]:
        """mask 中关闭的阀门编号(编号升序)。"""
        return [self.valves_sorted[b]["id"]
                for b in range(self.nv) if (mask >> b) & 1]


def solve(payload: dict[str, Any]) -> dict[str, Any]:
    """求解最低代价关阀组合, 返回规划结果(见 README 的返回示例)。"""
    nodes, segments, valves = validate_network(payload)
    g = _Graph(nodes, segments, valves)

    # ---- 前置检查(不关任何阀门时) ----
    if not g.inlet_set:
        return _infeasible("NO_INLET",
                           "管网中没有进水点, 任何方案都无法供水。")
    base_reach = g.reachable(0, g.inlet_set)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [g.nodes[i]["id"] for i in range(g.n)
                 if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
        )

    isolating_count = 0
    if not g.leak_set:
        # 没有漏点: 无需关阀即可满足全部要求
        best_mask = 0
        isolating_count = 1
    else:
        subset_cost = g.subset_costs()
        best_mask = -1
        best_cost = None
        best_list: tuple[str, ...] = ()

        for m in range(g.total_masks):
            fed = g.reachable(m, g.inlet_set)
            if (fed & g.leak_bits) == 0:
                isolating_count += 1
            else:
                continue
            if (fed & g.keep_bits) != g.keep_bits:
                continue
            closed = tuple(g.closed_names(m))
            c = subset_cost[m]
            if (best_cost is None or c < best_cost
                    or (c == best_cost and closed < best_list)):
                best_cost, best_mask, best_list = c, m, closed

        if best_mask < 0:
            return _explain_infeasible(g, isolating_count)

    result = _build_statuses(g, best_mask, isolating_count)
    result["solver"] = "base"
    return result


def solve_failover(payload: dict[str, Any]) -> dict[str, Any]:
    """求解单阀失效备援关阀组合。

    方案 mask 备援可行 <=> mask 常规可行, 且对其中每个已关阀 j,
    mask\\{j}(j 关不上、保持开启)也常规可行。无解时用 infeasible_kind
    区分: no_base_plan(本来就没有常规可行方案) / no_failover_plan
    (常规方案存在, 但没有备援方案)。
    """
    nodes, segments, valves = validate_network(payload)
    g = _Graph(nodes, segments, valves)

    # ---- 与常规规划一致的前置检查; 此类情况下"原本就没有常规可行方案" ----
    if not g.inlet_set:
        return _infeasible(
            "NO_INLET", "管网中没有进水点, 任何方案都无法供水。",
            solver="failover", kind="no_base_plan")
    base_reach = g.reachable(0, g.inlet_set)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [g.nodes[i]["id"] for i in range(g.n)
                 if (already_off >> i) & 1]
        return _infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names},
            solver="failover", kind="no_base_plan")

    # ---- 预计算每个阀门子集的常规可行性 ----
    # fed[m]: 关闭 m 时所有进水点可达节点; feasible[m]: 是否满足 (A)(B)
    isolating_count = 0
    feasible = [False] * g.total_masks
    for m in range(g.total_masks):
        fed = g.reachable(m, g.inlet_set)
        isolated = (fed & g.leak_bits) == 0
        if isolated:
            isolating_count += 1
        feasible[m] = isolated and (fed & g.keep_bits) == g.keep_bits

    base_feasible = [m for m in range(g.total_masks) if feasible[m]]
    if not base_feasible:
        # 常规规划本身无解: 复用常规无解原因分析
        result = _explain_infeasible(g, isolating_count)
        result["solver"] = "failover"
        result["infeasible_kind"] = "no_base_plan"
        return result

    # ---- 在常规可行方案中枚举备援可行方案 ----
    subset_cost = g.subset_costs()
    best_mask = -1
    best_cost = None
    best_list: tuple[str, ...] = ()
    failover_count = 0
    for m in base_feasible:
        # 每一枚计划阀门关不上时, 少关这枚阀的方案仍须常规可行
        robust = True
        bits = m
        while bits:
            low = bits & -bits
            if not feasible[m ^ low]:
                robust = False
                break
            bits ^= low
        if not robust:
            continue
        failover_count += 1
        closed = tuple(g.closed_names(m))
        c = subset_cost[m]
        if (best_cost is None or c < best_cost
                or (c == best_cost and closed < best_list)):
            best_cost, best_mask, best_list = c, m, closed

    if best_mask < 0:
        return _explain_no_failover(g, base_feasible, subset_cost)

    # ---- 构造正常执行 + 逐阀失效校验结果 ----
    result = _build_statuses(g, best_mask, isolating_count)
    result["solver"] = "failover"
    result["base_feasible_subset_count"] = len(base_feasible)
    result["failover_candidate_count"] = failover_count
    result["normal_check"] = _scenario_check(g, best_mask, None)
    result["failure_checks"] = [
        _scenario_check(g, best_mask ^ (1 << b), b)
        for b in range(g.nv) if (best_mask >> b) & 1
    ]
    n_closed = len(result["closed_valves"])
    result["message"] = (
        f"已找到单阀失效备援方案: 关闭 {n_closed} 个阀门, 总代价 "
        f"{result['total_cost']}。正常执行及任意一枚计划阀门关不上"
        f"(保持开启)时, 全部漏点均与进水点断开、全部必保点仍有供水。"
    )
    return result


def solve_siting(payload: dict[str, Any],
                 raw_candidates: Any) -> dict[str, Any]:
    """新增阀门选址预览求解(只读计算, 不写管网、不入库方案)。

    在已校验管网基础上, 允许在尚无阀门的管段上增设至多 MAX_CANDIDATES 个
    候选新阀(唯一新阀编号 + 正整数安装费 + 正整数关阀代价, 原阀与候选
    合计仍不超过 MAX_VALVES)。枚举"原阀 + 全部候选"的所有关阀子集(安装
    但不关闭的候选阀不影响连通性也不计费, 最优解不会包含它, 故关阀子集
    即方案), 要求全部漏点与进水点断开且全部必保点仍有水。优化次序:
      1) 安装费合计最小(只为实际关闭的候选阀计安装费);
      2) 本次关阀代价最小(被关闭的原阀关闭代价 + 新阀关阀代价之和);
      3) 新增管段编号升序列表字典序最小;
      4) 关阀编号升序列表字典序最小。
    无解时复用常规无解原因分析, 并说明候选范围内不可修复。
    """
    # ---- 1) 原管网校验(复用现有图校验) ----
    nodes, segments, valves = validate_network(payload)

    # ---- 2) 候选录入校验: 数量 / 新阀编号 / 安装管段 / 安装费 ----
    if raw_candidates is None:
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raise NetworkValidationError(["candidates 必须是数组"])
    errors: list[str] = []
    if len(raw_candidates) > MAX_CANDIDATES:
        errors.append(f"候选位置数量为 {len(raw_candidates)}, "
                      f"超过上限 {MAX_CANDIDATES} 个")
    candidates: list[dict[str, Any]] = []
    for i, cd in enumerate(raw_candidates):
        if not isinstance(cd, dict):
            errors.append(f"候选 #{i + 1}: 必须是对象")
            continue
        vid = _norm_id(cd.get("id"))
        seg_id = _norm_id(cd.get("segment_id"))
        if not vid:
            errors.append(f"候选 #{i + 1}: 缺少新阀编号")
        if not seg_id:
            errors.append(f"候选阀门 {vid or f'#{i + 1}'}: 必须选择安装管段")
        install = _strict_pos_int(cd.get("install_cost"))
        if install is None:
            errors.append(f"候选阀门 {vid or f'#{i + 1}'}: 安装费必须是正整数, "
                          f"收到: {cd.get('install_cost')!r}")
        candidates.append({"id": vid, "segment_id": seg_id,
                           "install_cost": install,
                           "close_cost": cd.get("close_cost")})

    # ---- 3) 扩展管网校验(复用现有图校验): 新阀编号唯一、管段存在且尚无
    #        阀门、关阀代价为正整数、原阀 + 候选合计 <= MAX_VALVES ----
    ext_payload = {
        "nodes": nodes,
        "segments": segments,
        "valves": ([{"id": v["id"], "segment_id": v["segment_id"],
                     "cost": v["cost"]} for v in valves]
                   + [{"id": c["id"], "segment_id": c["segment_id"],
                       "cost": c["close_cost"]} for c in candidates]),
    }
    try:
        nodes2, segments2, valves2 = validate_network(ext_payload)
    except NetworkValidationError as exc:
        raise NetworkValidationError(errors + exc.errors) from exc
    if errors:
        raise NetworkValidationError(errors)

    # ---- 4) 在扩展管网上枚举全部关阀子集(复用连通性判定) ----
    g = _Graph(nodes2, segments2, valves2)
    cand_by_id = {c["id"]: c for c in candidates}
    norm_valve = {v["id"]: v for v in valves2}
    # 每个阀门位上的安装费: 原阀为 0, 候选阀为其安装费
    installs = [0] * g.nv
    for b, vl in enumerate(g.valves_sorted):
        c = cand_by_id.get(vl["id"])
        if c is not None:
            installs[b] = c["install_cost"]

    def _unfixable(result: dict[str, Any]) -> dict[str, Any]:
        result["solver"] = "siting"
        result["message"] = ("候选范围内不可修复(已计入全部候选新阀): "
                             + result["message"])
        return result

    # 前置检查(与常规规划一致)
    if not g.inlet_set:
        return _unfixable(_infeasible(
            "NO_INLET", "管网中没有进水点, 任何方案都无法供水。"))
    base_reach = g.reachable(0, g.inlet_set)
    already_off = g.keep_bits & ~base_reach
    if already_off:
        names = [g.nodes[i]["id"] for i in range(g.n)
                 if (already_off >> i) & 1]
        return _unfixable(_infeasible(
            "GUARANTEED_ALREADY_DISCONNECTED",
            "即使不关任何阀门, 以下必保点也无法连通任何进水点(关阀只会进一步"
            "减少连通性, 故无解): " + ", ".join(names),
            {"starved_guaranteed": names}))

    # 全部子集的本次关阀代价 / 安装费(只计实际关闭的候选阀), lowbit DP
    close_sum = g.subset_costs()
    install_sum = [0] * g.total_masks
    for m in range(1, g.total_masks):
        low = m & -m
        install_sum[m] = install_sum[m ^ low] + installs[low.bit_length() - 1]

    best_key: tuple[int, int, tuple[str, ...], tuple[str, ...]] | None = None
    best_mask = -1
    isolating_count = 0
    for m in range(g.total_masks):
        fed = g.reachable(m, g.inlet_set)
        if (fed & g.leak_bits) == 0:
            isolating_count += 1
        else:
            continue
        if (fed & g.keep_bits) != g.keep_bits:
            continue
        new_segs = tuple(sorted(
            g.valves_sorted[b]["segment_id"]
            for b in range(g.nv)
            if (m >> b) & 1 and g.valves_sorted[b]["id"] in cand_by_id))
        key = (install_sum[m], close_sum[m], new_segs,
               tuple(g.closed_names(m)))
        if best_key is None or key < best_key:
            best_key, best_mask = key, m

    if best_mask < 0:
        return _unfixable(_explain_infeasible(g, isolating_count))

    # ---- 5) 构造选址预览结果(复用节点/管段状态构造) ----
    result = _build_statuses(g, best_mask, isolating_count)
    closed = result["closed_valves"]
    # 管段状态反映方案执行后的管网: 未被增设(未关闭)的候选阀不落在管段上
    for st in result["segment_status"]:
        vid = st["valve_id"]
        if vid is not None and vid in cand_by_id and vid not in closed:
            st["valve_id"] = None
    new_valves = [{
        "id": vid,
        "segment_id": norm_valve[vid]["segment_id"],
        "install_cost": cand_by_id[vid]["install_cost"],
        "close_cost": norm_valve[vid]["cost"],
    } for vid in closed if vid in cand_by_id]
    details = []
    for vid in closed:
        v = norm_valve[vid]
        d: dict[str, Any] = {"id": vid, "segment_id": v["segment_id"],
                             "close_cost": v["cost"],
                             "is_new": vid in cand_by_id}
        if vid in cand_by_id:
            d["install_cost"] = cand_by_id[vid]["install_cost"]
        details.append(d)

    install_total = install_sum[best_mask]
    close_total = result.pop("total_cost")  # _build_statuses 的总代价即本次关阀代价
    new_segment_ids = [v["segment_id"] for v in
                       sorted(new_valves, key=lambda x: x["segment_id"])]
    if new_valves:
        add_part = (f"在管段 {', '.join(new_segment_ids)} 增设 "
                    f"{len(new_valves)} 个新阀(安装费合计 {install_total})")
    else:
        add_part = "无需增设新阀(安装费 0)"
    result["solver"] = "siting"
    result["new_valves"] = new_valves
    result["new_segment_ids"] = new_segment_ids
    result["install_cost_total"] = install_total
    result["close_cost_total"] = close_total
    result["closed_valve_details"] = details
    result["candidate_count"] = len(candidates)
    result["message"] = (
        f"选址预览(只读, 不写入管网与历史方案): {add_part}; 本次共关闭 "
        f"{len(closed)} 个阀门, 关阀代价合计 {close_total}, "
        f"全部漏点已隔离、全部必保点仍有供水。")
    return result


def _scenario_check(g: _Graph, mask: int, failed_bit: int | None) -> dict[str, Any]:
    """一种执行情形下的 (A)(B) 校验; failed_bit=None 表示正常执行。"""
    fed = g.reachable(mask, g.inlet_set)
    leaks_reachable = [g.nodes[i]["id"] for i in range(g.n)
                       if ((g.leak_bits >> i) & 1 and (fed >> i) & 1)]
    keeps_starved = [g.nodes[i]["id"] for i in range(g.n)
                     if ((g.keep_bits >> i) & 1 and not (fed >> i) & 1)]
    check: dict[str, Any] = {
        "scenario": "normal" if failed_bit is None else "valve_failure",
        "failed_valve": (None if failed_bit is None
                         else g.valves_sorted[failed_bit]["id"]),
        "effective_closed_valves": g.closed_names(mask),
        "leaks_isolated": not leaks_reachable,
        "leaks_reachable": leaks_reachable,
        "keeps_supplied": not keeps_starved,
        "keeps_starved": keeps_starved,
        "ok": not leaks_reachable and not keeps_starved,
    }
    if failed_bit is not None:
        vl = g.valves_sorted[failed_bit]
        check["segment_id"] = vl["segment_id"]
        check["cost"] = vl["cost"]
    return check


def _explain_no_failover(g: _Graph, base_feasible: list[int],
                         subset_cost: list[int]) -> dict[str, Any]:
    """常规可行方案存在, 但没有任何备援可行方案。"""
    # 取最低代价(同代价字典序最小)的常规方案, 逐阀展示其失效后果
    base_best = -1
    base_best_cost = None
    base_best_list: tuple[str, ...] = ()
    for m in base_feasible:
        closed = tuple(g.closed_names(m))
        c = subset_cost[m]
        if (base_best_cost is None or c < base_best_cost
                or (c == base_best_cost and closed < base_best_list)):
            base_best_cost, base_best, base_best_list = c, m, closed

    checks = [
        _scenario_check(g, base_best ^ (1 << b), b)
        for b in range(g.nv) if (base_best >> b) & 1
    ]
    failing = [chk for chk in checks if not chk["ok"]]
    fail_desc = "; ".join(_describe_failure(chk) for chk in failing)
    message = (
        f"存在 {len(base_feasible)} 个常规可行方案, 但没有任何方案能承受单阀"
        f"失效。最低代价常规方案为关闭 "
        f"{', '.join(base_best_list) or '(无)'}(代价 {base_best_cost}); "
    )
    if not checks:
        message += "该方案不关任何阀门, 无需备援。"
    else:
        message += f"当其计划阀门关不上时: {fail_desc}。"

    return _infeasible(
        "NO_FAILOVER_PLAN", message,
        {
            "base_feasible_subset_count": len(base_feasible),
            "best_base_plan": {
                "closed_valves": list(base_best_list),
                "total_cost": base_best_cost,
                "failure_checks": checks,
            },
        },
        solver="failover", kind="no_failover_plan",
    )


def _describe_failure(chk: dict[str, Any]) -> str:
    reasons = []
    if chk["leaks_reachable"]:
        reasons.append("漏点 " + ", ".join(chk["leaks_reachable"]) + " 仍通进水点")
    if chk["keeps_starved"]:
        reasons.append("必保点 " + ", ".join(chk["keeps_starved"]) + " 断水")
    return f"阀门 {chk['failed_valve']} 关不上 -> {'; '.join(reasons)}"


def _build_statuses(g: _Graph, best_mask: int,
                    isolating_count: int) -> dict[str, Any]:
    """构造某方案下的节点/管段状态及汇总字段(方案正常执行视角)。"""
    n = g.n
    fed = g.reachable(best_mask, g.inlet_set)
    # 每个节点在该方案下可连通的进水点(逐进水点 BFS)
    inlet_of: list[list[str]] = [[] for _ in range(n)]
    for j in sorted(g.inlet_set):
        r = g.reachable(best_mask, {j})
        for i in range(n):
            if (r >> i) & 1:
                inlet_of[i].append(g.nodes[j]["id"])

    node_status: list[dict[str, Any]] = []
    for i, nd in enumerate(g.nodes):
        is_fed = bool((fed >> i) & 1)
        if nd["role"] == "leak":
            status = "isolated" if not is_fed else "leak_not_isolated"
        elif not is_fed:
            status = "disconnected"
        elif nd["role"] == "inlet":
            status = "inlet_supply"
        else:
            status = "supplied"
        node_status.append({
            "id": nd["id"], "label": nd["label"], "role": nd["role"],
            "supplied": is_fed, "status": status,
            "connected_inlets": inlet_of[i],
        })

    closed_valve_ids = set(g.closed_names(best_mask))
    segment_status: list[dict[str, Any]] = []
    for sg in g.segments:
        u, v = g.idx[sg["endpoint_a"]], g.idx[sg["endpoint_b"]]
        if sg["valve_id"] in closed_valve_ids:
            status, flowing = "closed", False
        elif (fed >> u) & 1 and (fed >> v) & 1:
            status, flowing = "flowing", True
        else:
            status, flowing = "dead", False
        segment_status.append({
            "id": sg["id"], "endpoint_a": sg["endpoint_a"],
            "endpoint_b": sg["endpoint_b"], "valve_id": sg["valve_id"],
            "status": status, "flowing": flowing,
        })

    closed = g.closed_names(best_mask)
    total_cost = sum(g.costs[b] for b in range(g.nv)
                     if (best_mask >> b) & 1)
    return {
        "feasible": True,
        "total_cost": total_cost,
        "closed_valves": closed,
        "closed_count": len(closed),
        "subset_count": g.total_masks,
        "isolating_subset_count": isolating_count,
        "node_status": node_status,
        "segment_status": segment_status,
        "message": (f"已找到最优关阀方案: 关闭 {len(closed)} 个阀门, "
                    f"总代价 {total_cost}。"),
    }


def _explain_infeasible(g: _Graph, isolating_count: int) -> dict[str, Any]:
    """常规无解原因分析(已有方案都不满足必保约束)。"""
    n, nv = g.n, g.nv

    if isolating_count == 0:
        # 全关阀后仍有漏点可达进水点: 存在不含阀门的通路
        all_closed = (1 << nv) - 1
        fed_all_closed = g.reachable(all_closed, g.inlet_set)
        reachable_leaks = [g.nodes[i]["id"] for i in range(n)
                           if ((g.leak_bits >> i) & 1
                               and (fed_all_closed >> i) & 1)]
        witness = _witness_path(g, all_closed, reachable_leaks)
        detail = "关闭全部阀门后, 漏点仍可经不含阀门的管段到达进水点, 无法切断。"
        if witness:
            detail += f" 无阀通路示例: {witness}。"
        return _infeasible(
            "UNCUTTABLE_LEAK_PATH", detail,
            {"reachable_leaks_when_all_closed": reachable_leaks,
             "witness_path": witness},
        )

    # 枚举所有"能隔离漏点"的方案, 求在每一方案中都断水的必保点交集
    always_starved: set[int] = set(g.keep_set)
    witness_valves: list[str] = []
    for m in range(1 << nv):
        fed = g.reachable(m, g.inlet_set)
        if (fed & g.leak_bits) != 0:
            continue
        starved = {i for i in g.keep_set if not (fed >> i) & 1}
        always_starved &= starved
        if not witness_valves and starved:
            witness_valves = g.closed_names(m)

    if always_starved:
        names = [g.nodes[i]["id"] for i in sorted(always_starved)]
        return _infeasible(
            "GUARANTEED_CONFLICT",
            "任何能隔离全部漏点的关阀组合, 都会使以下必保点与所有进水点断开, "
            "故不存在可行方案: " + ", ".join(names) + "。",
            {"starved_in_every_isolation": names,
             "example_isolation_valves": witness_valves},
        )
    return _infeasible(
        "GUARANTEED_CONFLICT",
        "每个能隔离漏点的关阀组合都会使至少一个必保点断水(不同组合影响的"
        "必保点不同), 无法同时满足隔离与必保供水要求。",
        {"example_isolation_valves": witness_valves},
    )


def _witness_path(g: _Graph, mask: int, target_node_ids: list[str]) -> str:
    """在关闭 mask 后仍走得通的图里, BFS 重建一条 进水点->目标漏点 的路径。"""
    if not target_node_ids:
        return ""
    targets = {g.idx[t] for t in target_node_ids}
    prev_node = [-1] * g.n
    prev_edge = [-1] * g.n
    dq: deque[int] = deque()
    for s in g.inlet_set:
        if prev_node[s] == -1:
            prev_node[s] = s
            dq.append(s)
    found = -1
    while dq:
        u = dq.popleft()
        if u in targets:
            found = u
            break
        for w, e in g.adj[u]:
            bit = g.edge_closed_bit[e]
            if bit and (mask & bit):
                continue
            if prev_node[w] == -1:
                prev_node[w] = u
                prev_edge[w] = e
                dq.append(w)
    if found < 0:
        return ""
    path_nodes: list[str] = []
    path_segs: list[str] = []
    cur = found
    while prev_node[cur] != cur:
        path_nodes.append(g.nodes[cur]["id"])
        path_segs.append(g.segments[prev_edge[cur]]["id"])
        cur = prev_node[cur]
    path_nodes.append(g.nodes[cur]["id"])
    path_nodes.reverse()
    path_segs.reverse()
    text = path_nodes[0]
    for sid, nid in zip(path_segs, path_nodes[1:]):
        text += f" --管段 {sid}--> {nid}"
    return text


def _infeasible(code: str, message: str, extra: dict | None = None,
                *, solver: str = "base", kind: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "feasible": False,
        "solver": solver,
        "reason_code": code,
        "message": message,
        "closed_valves": [],
        "total_cost": None,
        "node_status": [],
        "segment_status": [],
    }
    if kind is not None:
        out["infeasible_kind"] = kind
    if extra:
        out["reason_detail"] = extra
    return out


# ---------------------------------------------------------------------------
# 现场执行顺序规划(只读计算, 针对已保存的可行方案)
# ---------------------------------------------------------------------------

class SequenceInputError(NetworkValidationError):
    """执行顺序规划的录入数据不合法, errors 字段携带逐条原因。"""


def _positive_int(value: Any) -> int | None:
    """严格正整数: 拒绝 bool/浮点/字符串/0/负数, 合法时返回 int。"""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def plan_sequence(snapshot: dict[str, Any],
                  closed_valves: list[str],
                  durations: dict[str, Any],
                  leak_weights: dict[str, Any]) -> dict[str, Any]:
    """为可行关阀方案规划现场执行顺序(只读, 不修改任何已存数据)。

    模型:
      - 计划阀门逐枚关闭; 操作阀门 v 的时段内 v 仍开启(未关上), 该时段的
        暴露量 = v 的操作时长 × 此时段仍与进水点连通的漏点权重之和。
      - 每步完成后按多进水点连通性(BFS)更新仍通水漏点。
      - 目标: 排列全部计划阀门, 使各时段暴露量之和(累计暴露量)最小;
        同暴露量时取阀门编号序列字典序最小的排列, 保证顺序唯一。

    解法: 仍通水漏点集合只取决于"已关闭阀门集合 S"而与顺序无关, 故用
    子集 DP: suffix[S] = 从已关闭集合 S 做到全部关完的最小累计暴露量,
    suffix[S] = min_{v∉S}(d_v × W(S) + suffix[S∪{v}])。k≤14 => 2^k×k
    次转移。之后从空集出发, 每步在保持最优的候选阀门中取编号最小者,
    即得字典序最小的最优排列。
    """
    try:
        nodes, segments, valves = validate_network(snapshot)
    except NetworkValidationError as exc:
        raise SequenceInputError(
            ["方案快照数据不合法: " + e for e in exc.errors])
    g = _Graph(nodes, segments, valves)

    errors: list[str] = []

    # ---- 计划阀门必须存在于快照中 ----
    valve_by_id = {vl["id"]: vl for vl in valves}
    plan_valves = sorted(dict.fromkeys(closed_valves))
    plan_set = set(plan_valves)
    for vid in plan_valves:
        if vid not in valve_by_id:
            errors.append(f"计划阀门 {vid} 不存在于方案快照的管网中")

    # ---- 操作时长: 编号集合必须恰好等于计划阀门集合, 值为正整数 ----
    raw_dur = durations if isinstance(durations, dict) else {}
    dur: dict[str, int] = {}
    missing = [v for v in plan_valves if v not in raw_dur]
    extra = sorted(k for k in raw_dur if k not in plan_set)
    if missing:
        errors.append("缺少计划阀门的操作时长: " + ", ".join(missing))
    if extra:
        errors.append("提供了非计划阀门的操作时长(多余编号): "
                      + ", ".join(extra))
    for v in plan_valves:
        if v in raw_dur:
            val = _positive_int(raw_dur[v])
            if val is None:
                errors.append(
                    f"阀门 {v} 的操作时长必须是正整数(分钟), "
                    f"收到: {raw_dur[v]!r}")
            else:
                dur[v] = val

    # ---- 漏水权重: 编号集合必须恰好等于快照漏点集合, 值为正整数 ----
    leak_ids = sorted(nd["id"] for nd in nodes if nd["role"] == "leak")
    leak_set = set(leak_ids)
    raw_w = leak_weights if isinstance(leak_weights, dict) else {}
    weight: dict[str, int] = {}
    missing_l = [lid for lid in leak_ids if lid not in raw_w]
    extra_l = sorted(k for k in raw_w if k not in leak_set)
    if missing_l:
        errors.append("缺少漏点的漏水权重: " + ", ".join(missing_l))
    if extra_l:
        errors.append("提供了非漏点的漏水权重(多余编号): "
                      + ", ".join(extra_l))
    for lid in leak_ids:
        if lid in raw_w:
            val = _positive_int(raw_w[lid])
            if val is None:
                errors.append(
                    f"漏点 {lid} 的漏水权重必须是正整数, "
                    f"收到: {raw_w[lid]!r}")
            else:
                weight[lid] = val

    if not g.inlet_set and (plan_valves or leak_ids):
        errors.append("方案快照中没有进水点, 无法评估漏点连通性")

    if errors:
        raise SequenceInputError(errors)

    k = len(plan_valves)
    full = (1 << k) - 1
    v_idx = {vl["id"]: i for i, vl in enumerate(g.valves_sorted)}
    plan_bits = [1 << v_idx[v] for v in plan_valves]
    leak_index = {lid: i for i, lid in enumerate(leak_ids)}

    # ---- 每个计划阀门子集 S: 实际关阀掩码 / 仍通水漏点位掩码 / 权重和 ----
    size = 1 << k
    g_mask = [0] * size       # 子集 s 对应的 _Graph 关阀位掩码
    flowing = [0] * size      # 子集 s 下仍通水漏点的位掩码
    wsum = [0] * size         # 子集 s 下仍通水漏点权重之和
    for s in range(size):
        if s:
            low = s & -s
            g_mask[s] = g_mask[s ^ low] | plan_bits[low.bit_length() - 1]
        fed = g.reachable(g_mask[s], g.inlet_set)
        fm = 0
        for lid, li in leak_index.items():
            if (fed >> g.idx[lid]) & 1:
                fm |= 1 << li
        flowing[s] = fm
        ws = 0
        m = fm
        while m:
            low = m & -m
            ws += weight[leak_ids[low.bit_length() - 1]]
            m ^= low
        wsum[s] = ws

    # ---- 子集 DP: suffix[s] = 从已关闭集合 s 做到全部关完的最小暴露量 ----
    suffix: list[int | None] = [None] * size
    suffix[full] = 0
    for s in range(full - 1, -1, -1):  # ns = s∪{j} > s, 倒序递推
        best: int | None = None
        ws = wsum[s]
        for j in range(k):
            if (s >> j) & 1:
                continue
            val = dur[plan_valves[j]] * ws + suffix[s | (1 << j)]
            if best is None or val < best:
                best = val
        suffix[s] = best

    # ---- 从空集出发, 每步取能保持全局最优的最小编号阀门 => 字典序最小 ----
    order: list[int] = []
    s = 0
    while s != full:
        for j in range(k):  # plan_valves 已按编号升序
            if (s >> j) & 1:
                continue
            ns = s | (1 << j)
            if dur[plan_valves[j]] * wsum[s] + suffix[ns] == suffix[s]:
                order.append(j)
                s = ns
                break

    def leak_names(fm: int) -> list[str]:
        return [lid for lid in leak_ids if (fm >> leak_index[lid]) & 1]

    # ---- 逐步生成展示数据(起止时间/刚隔离/仍通水/累计暴露量) ----
    steps: list[dict[str, Any]] = []
    t = 0
    cum = 0
    s = 0
    for pos, j in enumerate(order):
        vid = plan_valves[j]
        d = dur[vid]
        exp = d * wsum[s]
        ns = s | (1 << j)
        newly = flowing[s] & ~flowing[ns]
        cum += exp
        steps.append({
            "order": pos + 1,
            "valve_id": vid,
            "segment_id": valve_by_id[vid]["segment_id"],
            "duration": d,
            "start": t,
            "end": t + d,
            "flowing_leaks_during": leak_names(flowing[s]),
            "flowing_weight_during": wsum[s],
            "exposure": exp,
            "newly_isolated_leaks": leak_names(newly),
            "flowing_leaks_after": leak_names(flowing[ns]),
            "flowing_weight_after": wsum[ns],
            "cumulative_exposure": cum,
        })
        t += d
        s = ns

    seq_names = [plan_valves[j] for j in order]
    if seq_names:
        message = (f"最优执行顺序: {' → '.join(seq_names)}; 总时长 {t} 分钟, "
                   f"累计暴露量 {cum}。")
    else:
        message = "该方案无需关闭任何阀门, 无操作时段, 累计暴露量 0。"
    return {
        "ok": True,
        "closed_valves": plan_valves,
        "durations": dur,
        "leak_weights": weight,
        "initial_flowing_leaks": leak_names(flowing[0]),
        "initial_flowing_weight": wsum[0],
        "final_flowing_leaks": leak_names(flowing[full]),
        "sequence": seq_names,
        "steps": steps,
        "total_duration": t,
        "total_exposure": cum,
        "message": message,
    }


# ---------------------------------------------------------------------------
# 临时供水跨接预览(只读计算, 针对已保存的可行方案)
# ---------------------------------------------------------------------------

def plan_jumpers(snapshot: dict[str, Any],
                 closed_valves: list[str],
                 raw_candidates: Any) -> dict[str, Any]:
    """已保存可行方案的临时供水跨接预览(只读计算, 不改方案与管网)。

    基于方案快照与已关阀管段(即方案执行后的管网), 评估至多 MAX_CANDIDATES
    条候选跨接: 每条含唯一编号、两个不同的现有节点与正整数铺设费, 跨接视为
    新增通水管段(无阀门, 铺设后始终通水)。枚举全部候选组合, 要求加入跨接后
    仍保持全部漏点与进水点断开、全部必保点有水; 在此前提下依次:
      1) 最大化由断水转为有水的普通节点数;
      2) 最小化铺设费合计;
      3) 跨接编号升序列表字典序最小。
    无法恢复任何普通节点时返回空方案(不铺设跨接, 费用 0)。
    """
    try:
        nodes, segments, valves = validate_network(snapshot)
    except NetworkValidationError as exc:
        raise NetworkValidationError(
            ["方案快照数据不合法: " + e for e in exc.errors]) from exc

    errors: list[str] = []

    # ---- 方案已关阀管段(计划阀门必须存在于快照管网中) ----
    valve_by_id = {vl["id"]: vl for vl in valves}
    closed = sorted(dict.fromkeys(_norm_id(v) for v in (closed_valves or [])))
    for vid in closed:
        if vid not in valve_by_id:
            errors.append(f"方案关闭的阀门 {vid} 不存在于方案快照的管网中")
    closed_segments = {valve_by_id[v]["segment_id"] for v in closed
                       if v in valve_by_id}

    # ---- 候选跨接录入校验: 数量 / 唯一编号 / 两个不同现有节点 / 正整数铺设费 ----
    if raw_candidates is None:
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raise NetworkValidationError(["candidates 必须是数组"])
    if len(raw_candidates) > MAX_CANDIDATES:
        errors.append(f"候选跨接数量为 {len(raw_candidates)}, "
                      f"超过上限 {MAX_CANDIDATES} 条")
    node_ids = {nd["id"] for nd in nodes}
    segment_ids = {sg["id"] for sg in segments}
    cands: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for i, cd in enumerate(raw_candidates):
        if not isinstance(cd, dict):
            errors.append(f"候选跨接 #{i + 1}: 必须是对象")
            continue
        jid = _norm_id(cd.get("id"))
        a, b = _norm_id(cd.get("endpoint_a")), _norm_id(cd.get("endpoint_b"))
        if not jid:
            errors.append(f"候选跨接 #{i + 1}: 缺少跨接编号")
        elif jid in seen_ids:
            errors.append(f"跨接编号重复: {jid}")
        elif jid in segment_ids:
            errors.append(f"跨接编号 {jid} 与现有管段编号重复")
        seen_ids.add(jid)
        label = jid or f"#{i + 1}"
        if not a or not b:
            errors.append(f"候选跨接 {label}: 必须选择两个不同的现有节点作为端点")
        else:
            if a not in node_ids:
                errors.append(
                    f"候选跨接 {label}: 端点 {a} 不存在于方案快照的节点中")
            if b not in node_ids:
                errors.append(
                    f"候选跨接 {label}: 端点 {b} 不存在于方案快照的节点中")
            if a == b and a in node_ids:
                errors.append(f"候选跨接 {label}: 两个端点不能相同({a})")
        cost = _positive_int(cd.get("cost"))
        if cost is None:
            errors.append(f"候选跨接 {label}: 铺设费必须是正整数, "
                          f"收到: {cd.get('cost')!r}")
        cands.append({"id": jid, "endpoint_a": a, "endpoint_b": b,
                      "cost": cost})
    if errors:
        raise NetworkValidationError(errors)

    # ---- 图: 方案执行后的管网(已关阀管段移除) + 候选跨接边 ----
    n = len(nodes)
    idx = {nd["id"]: i for i, nd in enumerate(nodes)}
    base_adj: list[list[int]] = [[] for _ in range(n)]
    for sg in segments:
        if sg["id"] in closed_segments:
            continue
        u, v = idx[sg["endpoint_a"]], idx[sg["endpoint_b"]]
        base_adj[u].append(v)
        base_adj[v].append(u)
    jumper_adj: list[list[tuple[int, int]]] = [[] for _ in range(n)]
    for j, cd in enumerate(cands):
        u, v = idx[cd["endpoint_a"]], idx[cd["endpoint_b"]]
        jumper_adj[u].append((v, 1 << j))
        jumper_adj[v].append((u, 1 << j))

    inlet_set = {i for i, nd in enumerate(nodes) if nd["role"] == "inlet"}
    leak_bits = sum(1 << i for i, nd in enumerate(nodes)
                    if nd["role"] == "leak")
    keep_bits = sum(1 << i for i, nd in enumerate(nodes)
                    if nd["role"] == "must_keep")
    normal_idx = [i for i, nd in enumerate(nodes) if nd["role"] == "normal"]

    def reachable(active: int) -> int:
        """从全部进水点出发, 经未关阀管段与已铺设跨接(active 位掩码)可达节点。"""
        seen = 0
        dq: deque[int] = deque()
        for s in inlet_set:
            if not (seen >> s) & 1:
                seen |= 1 << s
                dq.append(s)
        while dq:
            u = dq.popleft()
            for w in base_adj[u]:
                if not (seen >> w) & 1:
                    seen |= 1 << w
                    dq.append(w)
            for w, bit in jumper_adj[u]:
                if (active & bit) and not (seen >> w) & 1:
                    seen |= 1 << w
                    dq.append(w)
        return seen

    fed_base = reachable(0)
    disconnected_before = [nodes[i]["id"] for i in normal_idx
                           if not (fed_base >> i) & 1]

    # ---- 枚举候选组合: 始终保持 (A)(B), 优化 (恢复数, 铺设费, 编号列表) ----
    m = len(cands)
    best_key: tuple[int, int, tuple[str, ...]] | None = None
    best_active = 0
    best_fed = fed_base
    feasible_combinations = 0
    for active in range(1 << m):
        fed = reachable(active)
        if (fed & leak_bits) != 0 or (fed & keep_bits) != keep_bits:
            continue
        feasible_combinations += 1
        restored = sum(1 for i in normal_idx
                       if (fed >> i) & 1 and not (fed_base >> i) & 1)
        cost = sum(cands[j]["cost"] for j in range(m) if (active >> j) & 1)
        ids = tuple(sorted(cands[j]["id"] for j in range(m)
                           if (active >> j) & 1))
        key = (-restored, cost, ids)
        if best_key is None or key < best_key:
            best_key, best_active, best_fed = key, active, fed
    if best_key is None:
        # 方案本身已不满足 (A)(B)(数据不一致时兜底): 返回空方案
        best_active, best_fed = 0, fed_base

    selected = [cands[j] for j in range(m) if (best_active >> j) & 1]
    selected.sort(key=lambda c: c["id"])
    selected_ids = [c["id"] for c in selected]
    lay_total = sum(c["cost"] for c in selected)
    restored_nodes = [nodes[i]["id"] for i in normal_idx
                      if (best_fed >> i) & 1 and not (fed_base >> i) & 1]
    still_disconnected = [nodes[i]["id"] for i in normal_idx
                          if not (best_fed >> i) & 1]

    # ---- 铺设跨接后的节点/管段状态(复用状态构造, 跨接为新增通水管段) ----
    final_segments = segments + [
        {"id": c["id"], "endpoint_a": c["endpoint_a"],
         "endpoint_b": c["endpoint_b"], "valve_id": None}
        for c in selected
    ]
    g = _Graph(nodes, final_segments, valves)
    v_idx = {vl["id"]: b for b, vl in enumerate(g.valves_sorted)}
    mask = 0
    for vid in closed:
        mask |= 1 << v_idx[vid]
    statuses = _build_statuses(g, mask, feasible_combinations)
    jumper_set = set(selected_ids)
    for st in statuses["segment_status"]:
        if st["id"] in jumper_set:
            st["is_jumper"] = True

    if restored_nodes:
        message = (
            f"临时供水跨接预览(只读, 不修改管网与历史方案): 铺设 "
            f"{len(selected)} 条跨接({', '.join(selected_ids)}), 铺设费合计 "
            f"{lay_total}, 恢复供水普通节点 {len(restored_nodes)} 个"
            f"({', '.join(restored_nodes)}); 全部漏点仍与进水点断开、"
            f"全部必保点仍有供水。")
    elif not disconnected_before:
        message = ("临时供水跨接预览(只读, 不修改管网与历史方案): 方案执行后"
                   "没有断水的普通节点, 无需铺设跨接, 返回空方案(费用 0)。")
    else:
        message = (
            f"临时供水跨接预览(只读, 不修改管网与历史方案): 断水普通节点 "
            f"{', '.join(disconnected_before)} 无法经候选跨接恢复供水"
            f"(跨接不得使漏点复通进水点), 返回空方案(不铺设跨接, 费用 0)。")

    return {
        "ok": True,
        "closed_valves": statuses["closed_valves"],
        "candidate_count": m,
        "combination_count": 1 << m,
        "feasible_combination_count": feasible_combinations,
        "selected_jumpers": selected,
        "selected_ids": selected_ids,
        "lay_cost_total": lay_total,
        "restored_nodes": restored_nodes,
        "restored_count": len(restored_nodes),
        "disconnected_normal_before": disconnected_before,
        "still_disconnected_normal": still_disconnected,
        "node_status": statuses["node_status"],
        "segment_status": statuses["segment_status"],
        "message": message,
    }
