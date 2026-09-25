"""plan_jumpers 临时供水跨接预览测试: 目标次序 / 字典序决胜 / 录入校验 / 空方案 / 只读。"""
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, plan_jumpers, solve, solve_failover,
    validate_network,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(snapshot, closed, cands, keys):
    try:
        plan_jumpers(snapshot, closed, cands)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


# ---- 备援方案管网: 冗余关阀 V1+V2 使普通节点 M,X 断水且与漏点 L 分离 ----
NET_FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "X", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p4", "endpoint_a": "M", "endpoint_b": "X"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
FO = solve_failover(NET_FO)
check(FO["feasible"] and FO["closed_valves"] == ["V1", "V2"],
      f"failover plan {FO.get('closed_valves')}")

# 1. 基本恢复 + 铺设费最小: J1/J2 都恢复 {M,X}, 选便宜的 J2; J3 会复通漏点被排除
r = plan_jumpers(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": 6},
    {"id": "J2", "endpoint_a": "H", "endpoint_b": "X", "cost": 4},
    {"id": "J3", "endpoint_a": "S", "endpoint_b": "L", "cost": 1},
])
check(r["ok"] and r["selected_ids"] == ["J2"], f"net1 selected {r['selected_ids']}")
check(r["lay_cost_total"] == 4, "net1 cost")
check(r["restored_nodes"] == ["M", "X"], f"net1 restored {r['restored_nodes']}")
check(r["restored_count"] == 2, "net1 restored count")
check(r["disconnected_normal_before"] == ["M", "X"], "net1 before")
check(r["still_disconnected_normal"] == [], "net1 still")
check(r["closed_valves"] == ["V1", "V2"], "net1 closed valves")
check(r["candidate_count"] == 3 and r["combination_count"] == 8, "net1 comb")
check(r["feasible_combination_count"] == 4,
      f"net1 feasible comb {r['feasible_combination_count']}")
check(r["selected_jumpers"] == [
    {"id": "J2", "endpoint_a": "H", "endpoint_b": "X", "cost": 4}],
    f"net1 selected detail {r['selected_jumpers']}")
st_m = [n for n in r["node_status"] if n["id"] == "M"][0]
st_l = [n for n in r["node_status"] if n["id"] == "L"][0]
check(st_m["status"] == "supplied" and st_m["connected_inlets"] == ["S"],
      "net1 M supplied")
check(st_l["status"] == "isolated", "net1 leak still isolated")
seg_j2 = [s for s in r["segment_status"] if s["id"] == "J2"][0]
check(seg_j2["is_jumper"] and seg_j2["status"] == "flowing", "net1 J2 flowing")
check(not any(s["id"] in ("J1", "J3") for s in r["segment_status"]),
      "net1 unselected jumpers not in segments")
check("不修改" in r["message"], "net1 readonly note")

# 2. 恢复数优先于铺设费: 组合恢复 3 个节点(费用 51)优于单条便宜跨接恢复 1 个
NET_FO2 = {
    "nodes": NET_FO["nodes"] + [{"id": "Y", "role": "normal"}],
    "segments": NET_FO["segments"],
    "valves": NET_FO["valves"],
}
r = plan_jumpers(NET_FO2, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": 50},
    {"id": "J2", "endpoint_a": "S", "endpoint_b": "Y", "cost": 1},
])
check(r["selected_ids"] == ["J1", "J2"],
      f"net2 count-first {r['selected_ids']}")
check(r["restored_nodes"] == ["M", "X", "Y"], f"net2 restored {r['restored_nodes']}")
check(r["lay_cost_total"] == 51, "net2 cost")

# 3a. 字典序决胜: 同恢复同费用 -> 编号升序列表最小(J1 < J2)
r = plan_jumpers(NET_FO, FO["closed_valves"], [
    {"id": "J2", "endpoint_a": "S", "endpoint_b": "M", "cost": 5},
    {"id": "J1", "endpoint_a": "H", "endpoint_b": "M", "cost": 5},
])
check(r["selected_ids"] == ["J1"], f"net3a lex {r['selected_ids']}")
# 3b. 字符串字典序: J10 < J2
r = plan_jumpers(NET_FO, FO["closed_valves"], [
    {"id": "J2", "endpoint_a": "S", "endpoint_b": "M", "cost": 5},
    {"id": "J10", "endpoint_a": "H", "endpoint_b": "M", "cost": 5},
])
check(r["selected_ids"] == ["J10"], f"net3b lex {r['selected_ids']}")

# 4. 跨接不得复通漏点: 唯一候选会复通漏点 -> 空方案
r = plan_jumpers(NET_FO, FO["closed_valves"], [
    {"id": "J3", "endpoint_a": "S", "endpoint_b": "L", "cost": 1},
])
check(r["selected_ids"] == [] and r["lay_cost_total"] == 0, "net4 empty")
check(r["restored_nodes"] == [] and r["restored_count"] == 0, "net4 no restore")
check(r["disconnected_normal_before"] == ["M", "X"], "net4 before")
check(r["still_disconnected_normal"] == ["M", "X"], "net4 still")
check(r["feasible_combination_count"] == 1, "net4 only empty feasible")
check("空方案" in r["message"] and "无法" in r["message"], "net4 message")

# 5. 孤岛普通节点(常规方案): 两组孤岛, 两条跨接都选中
NET_ISLAND = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
        {"id": "X", "role": "normal"}, {"id": "Y", "role": "normal"},
        {"id": "P", "role": "normal"}, {"id": "Q", "role": "normal"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
        {"id": "p3", "endpoint_a": "X", "endpoint_b": "Y"},
        {"id": "p4", "endpoint_a": "P", "endpoint_b": "Q"},
    ],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
SOL_I = solve(NET_ISLAND)
check(SOL_I["feasible"] and SOL_I["closed_valves"] == ["V1"], "island plan")
r = plan_jumpers(NET_ISLAND, SOL_I["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "X", "cost": 5},
    {"id": "J2", "endpoint_a": "H", "endpoint_b": "P", "cost": 3},
])
check(r["selected_ids"] == ["J1", "J2"], f"island selected {r['selected_ids']}")
check(r["lay_cost_total"] == 8, "island cost")
check(r["restored_nodes"] == ["X", "Y", "P", "Q"],
      f"island restored {r['restored_nodes']}")
check(r["still_disconnected_normal"] == [], "island still")

# 6a. 空方案: 方案执行后没有断水的普通节点(示例管网)
from demo import DEMO_NETWORK  # noqa: E402

SOL_D = solve(DEMO_NETWORK)
r = plan_jumpers(DEMO_NETWORK, SOL_D["closed_valves"], [
    {"id": "J1", "endpoint_a": "A", "endpoint_b": "B", "cost": 3},
])
check(r["selected_ids"] == [] and r["restored_count"] == 0, "demo empty")
check(r["disconnected_normal_before"] == [], "demo no disconnected")
check("没有断水的普通节点" in r["message"], "demo empty message")

# 6b. 空方案: 无候选
r = plan_jumpers(NET_FO, FO["closed_valves"], [])
check(r["selected_ids"] == [] and r["combination_count"] == 1, "no candidates")
check(r["feasible_combination_count"] == 1, "no candidates feasible")
check(r["still_disconnected_normal"] == ["M", "X"], "no candidates still")

# 6c. 空方案: 断水普通节点存在但候选够不到(候选在有水分量内部)
r = plan_jumpers(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "H", "cost": 2},
])
check(r["selected_ids"] == [] and r["restored_count"] == 0, "net6c empty")
check("空方案" in r["message"], "net6c message")

# ---- 7. 录入校验 ----
CAND_OK = [{"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": 1}]
# 候选超过 6 条
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": f"J{i}", "endpoint_a": "S", "endpoint_b": "M", "cost": 1}
    for i in range(7)], ["候选跨接数量", "超过上限 6"])
# 编号重复 / 与现有管段编号重复 / 缺少编号
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": 1},
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "H", "cost": 1},
], ["跨接编号重复", "J1"])
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "M", "cost": 1},
], ["与现有管段编号重复", "p1"])
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "", "endpoint_a": "S", "endpoint_b": "M", "cost": 1},
], ["缺少跨接编号"])
# 端点: 缺失 / 不存在 / 两端点相同
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "", "endpoint_b": "M", "cost": 1},
], ["必须选择两个不同的现有节点"])
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "ZZ", "cost": 1},
], ["端点 ZZ 不存在"])
expect_errors(NET_FO, FO["closed_valves"], [
    {"id": "J1", "endpoint_a": "S", "endpoint_b": "S", "cost": 1},
], ["两个端点不能相同"])
# 铺设费非法: 0 / 负 / 小数 / 字符串 / bool / null
for bad in [0, -3, 1.5, "5", "x", True, None]:
    expect_errors(NET_FO, FO["closed_valves"], [
        {"id": "J1", "endpoint_a": "S", "endpoint_b": "M", "cost": bad},
    ], ["铺设费必须是正整数"])
# candidates 不是数组
expect_errors(NET_FO, FO["closed_valves"], {"id": "J1"},
              ["candidates 必须是数组"])
# 方案关闭的阀门不在快照中
expect_errors(NET_FO, ["V9"], [], ["阀门 V9 不存在于方案快照"])

# ---- 8. 随机对拍: 与独立暴力枚举比较目标次序 ----
def bruteforce_jumpers(snapshot, closed, cands):
    """独立实现: 显式组合枚举 + 集合 BFS, 返回最优 (-恢复数, 费用, 编号列表)。"""
    nodes, segments, valves = validate_network(snapshot)
    closed_segs = {v["segment_id"] for v in valves if v["id"] in set(closed)}
    edges = []
    for sg in segments:
        if sg["id"] in closed_segs:
            continue
        edges.append((sg["endpoint_a"], sg["endpoint_b"], 0))
    for j, c in enumerate(cands):
        edges.append((c["endpoint_a"], c["endpoint_b"], 1 << j))
    inlets = [nd["id"] for nd in nodes if nd["role"] == "inlet"]
    leaks = {nd["id"] for nd in nodes if nd["role"] == "leak"}
    keeps = {nd["id"] for nd in nodes if nd["role"] == "must_keep"}
    normals = [nd["id"] for nd in nodes if nd["role"] == "normal"]

    def fed(active):
        seen = set(inlets)
        stack = list(inlets)
        while stack:
            u = stack.pop()
            for a, b, bit in edges:
                if bit and not (active & bit):
                    continue
                if a == u and b not in seen:
                    seen.add(b)
                    stack.append(b)
                elif b == u and a not in seen:
                    seen.add(a)
                    stack.append(a)
        return seen

    fed0 = fed(0)
    best = None
    for active in range(1 << len(cands)):
        f = fed(active)
        if f & leaks or not keeps <= f:
            continue
        restored = sum(1 for x in normals if x in f and x not in fed0)
        cost = sum(c["cost"] for j, c in enumerate(cands) if active >> j & 1)
        ids = tuple(sorted(c["id"] for j, c in enumerate(cands)
                           if active >> j & 1))
        key = (-restored, cost, ids)
        if best is None or key < best:
            best = key
    return best


rng = random.Random(20260925)
trials = 0
for trial in range(400):
    n = rng.randint(3, 8)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet", "leak"] + ["normal"] * (n - 2)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
    # 随机连通图(生成树 + 随机加边)
    order = node_ids[:]
    rng.shuffle(order)
    seg_list = []
    used = [order[0]]
    si = 0
    for u in order[1:]:
        v = rng.choice(used)
        seg_list.append({"id": f"e{si}", "endpoint_a": u, "endpoint_b": v})
        used.append(u)
        si += 1
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(node_ids, 2)
        seg_list.append({"id": f"e{si}", "endpoint_a": a, "endpoint_b": b})
        si += 1
    # 随机必保点(连通图上不关阀时必可达)
    for nid in node_ids:
        if role_map[nid] == "normal" and rng.random() < 0.3:
            role_map[nid] = "must_keep"
    # 偶发孤岛普通节点对(方案执行后断水的另一来源)
    extra = []
    if rng.random() < 0.4:
        g1, g2 = f"g{trial}a", f"g{trial}b"
        role_map[g1] = role_map[g2] = "normal"
        extra = [{"id": f"e{si}", "endpoint_a": g1, "endpoint_b": g2}]
        node_ids += [g1, g2]
    k = rng.randint(0, min(5, len(seg_list)))
    chosen = rng.sample(seg_list, k) if k else []
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 9)}
                  for i, sg in enumerate(chosen)]
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list + extra, "valves": valve_list}
    solver_fn = solve_failover if rng.random() < 0.4 else solve
    sol = solver_fn(net)
    if not sol["feasible"]:
        continue
    plan = sol["closed_valves"]
    # 随机候选跨接 0..4 条
    m = rng.randint(0, 4)
    cand_list = []
    for j in range(m):
        a, b = rng.sample(node_ids, 2)
        cand_list.append({"id": f"J{j+1}", "endpoint_a": a, "endpoint_b": b,
                          "cost": rng.randint(1, 9)})
    res = plan_jumpers(net, plan, cand_list)
    best = bruteforce_jumpers(net, plan, cand_list)
    got = (-res["restored_count"], res["lay_cost_total"],
           tuple(res["selected_ids"]))
    check(got == best, f"trial {trial}: {got} != brute {best}")
    # 结果自洽: 选中跨接 = 编号集合; 费用合计一致; 恢复节点确实由断水转有水
    check(sorted(c["id"] for c in res["selected_jumpers"])
          == res["selected_ids"], f"trial {trial} selected consistent")
    check(sum(c["cost"] for c in res["selected_jumpers"])
          == res["lay_cost_total"], f"trial {trial} cost sum")
    st = {ns["id"]: ns for ns in res["node_status"]}
    for nid in res["restored_nodes"]:
        check(st[nid]["role"] == "normal" and st[nid]["supplied"],
              f"trial {trial} restored supplied")
    check(set(res["disconnected_normal_before"])
          == set(res["restored_nodes"]) | set(res["still_disconnected_normal"]),
          f"trial {trial} partition")
    # 最终状态仍满足: 漏点全隔离、必保点全有水
    for ns in res["node_status"]:
        if ns["role"] == "leak":
            check(ns["status"] == "isolated", f"trial {trial} leak isolated")
        if ns["role"] == "must_keep":
            check(ns["supplied"], f"trial {trial} keep supplied")
    # 选中跨接出现在管段状态且标记 is_jumper
    seg_ids = {s["id"] for s in res["segment_status"] if s.get("is_jumper")}
    check(seg_ids == set(res["selected_ids"]), f"trial {trial} jumper segs")
    trials += 1
check(trials >= 40, f"enough random trials ({trials})")

print(f"jumpers solver tests: {passed} checks passed")
