"""solve_tradeoff 权衡预览求解测试: 双目标非支配 / 同指标字典序 / 录入校验 / 无解。"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, solve_tradeoff, validate_network,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(net, weights, keys):
    try:
        solve_tradeoff(net, weights)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


def front_of(r):
    return [(p["total_cost"], p["impact"], p["closed_valves"])
            for p in r["plans"]]


# ---- 双链路权衡网: 每条漏点通路可选「便宜但断水」或「贵但不断水」的阀门 ----
# S--p1(V1,c2)--A(w3)--p2(V2,c5)--L1 ; S--p3(V3,c2)--B(w3)--p4(V4,c5)--L2
NET_T = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "A", "role": "normal"},
        {"id": "B", "role": "normal"},
        {"id": "L1", "role": "leak"},
        {"id": "L2", "role": "leak"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "A", "endpoint_b": "L1"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "B"},
        {"id": "p4", "endpoint_a": "B", "endpoint_b": "L2"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 2},
        {"id": "V2", "segment_id": "p2", "cost": 5},
        {"id": "V3", "segment_id": "p3", "cost": 2},
        {"id": "V4", "segment_id": "p4", "cost": 5},
    ],
}
W_T = {"A": 3, "B": 3}

# 1. 三点权衡前沿 + 同指标字典序: (7,3) 有 {V1,V4} 与 {V2,V3}, 只留前者
r = solve_tradeoff(NET_T, W_T)
check(r["feasible"] and r["solver"] == "tradeoff", "NET_T feasible")
check(front_of(r) == [
    (4, 6, ["V1", "V3"]),
    (7, 3, ["V1", "V4"]),
    (10, 0, ["V2", "V4"]),
], f"NET_T front {front_of(r)}")
check(r["plan_count"] == 3, "NET_T plan count")
check(r["subset_count"] == 16, "NET_T subset count")
check(r["isolating_subset_count"] == 9, f"NET_T isolating {r['isolating_subset_count']}")
check(r["feasible_subset_count"] == 9, f"NET_T feasible {r['feasible_subset_count']}")
check(r["weights"] == {"A": 3, "B": 3}, "NET_T weights echo")
check(r["normal_nodes"] == ["A", "B"], "NET_T normal nodes")
check(r["baseline_fed_normal"] == ["A", "B"], "NET_T baseline fed")
check(r["baseline_disconnected_normal"] == [], "NET_T baseline disconnected")
check("不写入" in r["message"], "NET_T readonly note")
# 新增断水节点及明细
p0, p1, p2 = r["plans"]
check(p0["newly_disconnected"] == ["A", "B"], "NET_T p0 newly")
check(p0["newly_disconnected_details"] == [
    {"id": "A", "label": "A", "weight": 3},
    {"id": "B", "label": "B", "weight": 3},
], f"NET_T p0 details {p0['newly_disconnected_details']}")
check(p0["closed_count"] == 2, "NET_T p0 closed count")
check(p1["newly_disconnected"] == ["A"], "NET_T p1 newly")
check(p2["newly_disconnected"] == [] and p2["newly_disconnected_details"] == [],
      "NET_T p2 newly empty")

# 2. 权重改变前沿形状: B 权重调低后 {V2,V3}(断 B)比 {V1,V4}(断 A)影响更小
r = solve_tradeoff(NET_T, {"A": 3, "B": 1})
check(front_of(r) == [
    (4, 4, ["V1", "V3"]),
    (7, 1, ["V2", "V3"]),
    (10, 0, ["V2", "V4"]),
], f"NET_T reweighted front {front_of(r)}")

# 3. 示例管网: 唯一可行方案 V2+V4(代价7, 断水影响0)
from demo import DEMO_NETWORK  # noqa: E402

r = solve_tradeoff(DEMO_NETWORK, {"A": 2, "B": 5})
check(r["feasible"], "demo feasible")
check(front_of(r) == [(7, 0, ["V2", "V4"])], f"demo front {front_of(r)}")
check(r["feasible_subset_count"] == 1, f"demo feasible {r['feasible_subset_count']}")
check(r["plans"][0]["newly_disconnected"] == [], "demo no newly disconnected")

# 4. 原本就断水的普通节点不计入断水影响
NET_ISO = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "L", "role": "leak"},
        {"id": "X", "role": "normal"},
    ],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 4}],
}
r = solve_tradeoff(NET_ISO, {"X": 7})
check(r["feasible"], "iso feasible")
check(r["baseline_fed_normal"] == [], "iso baseline fed empty")
check(r["baseline_disconnected_normal"] == ["X"], "iso baseline disconnected")
check(front_of(r) == [(4, 0, ["V1"])], f"iso front {front_of(r)}")
check(r["plans"][0]["newly_disconnected"] == [], "iso newly empty")

# 5. 无普通节点: 权重必须为空, 断水影响恒 0
NET_NO_NORMAL = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 3}],
}
r = solve_tradeoff(NET_NO_NORMAL, {})
check(r["feasible"] and r["plan_count"] == 1, "no-normal feasible")
check(front_of(r) == [(3, 0, ["V1"])], "no-normal front")
expect_errors(NET_NO_NORMAL, {"A": 1}, ["非普通节点", "A"])

# 6. 无漏点: 空方案(0,0)支配一切, 前沿只含「不关阀」
NET_NO_LEAK = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "A"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 3}],
}
r = solve_tradeoff(NET_NO_LEAK, {"A": 5})
check(r["feasible"] and r["plan_count"] == 1, "no-leak single plan")
check(front_of(r) == [(0, 0, [])], f"no-leak front {front_of(r)}")

# 7. 漏点原本就不通水: 空方案即可行
NET_DRY_LEAK = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "A", "role": "normal"},
    ],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "A"}],
    "valves": [],
}
r = solve_tradeoff(NET_DRY_LEAK, {"A": 5})
check(r["feasible"] and front_of(r) == [(0, 0, [])], "dry-leak empty plan")

# ---- 8. 无解情形(复用常规原因分析, solver=tradeoff) ----
r = solve_tradeoff({"nodes": [{"id": "L", "role": "leak"},
                              {"id": "A", "role": "normal"}],
                    "segments": [], "valves": []}, {"A": 1})
check(r["feasible"] is False and r["reason_code"] == "NO_INLET", "no inlet")
check(r["solver"] == "tradeoff", "no inlet solver")

NET_GAD = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"},
              {"id": "A", "role": "normal"}],
    "segments": [],
    "valves": [],
}
r = solve_tradeoff(NET_GAD, {"A": 1})
check(r["feasible"] is False
      and r["reason_code"] == "GUARANTEED_ALREADY_DISCONNECTED", "GAD")
check(r["solver"] == "tradeoff", "GAD solver")

NET_UNCUT = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
              {"id": "A", "role": "normal"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [],
}
r = solve_tradeoff(NET_UNCUT, {"A": 1})
check(r["feasible"] is False and r["reason_code"] == "UNCUTTABLE_LEAK_PATH",
      "uncuttable")
check(r["solver"] == "tradeoff", "uncuttable solver")

NET_CONF = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
              {"id": "H", "role": "must_keep"}, {"id": "A", "role": "normal"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
                 {"id": "p2", "endpoint_a": "L", "endpoint_b": "H"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
r = solve_tradeoff(NET_CONF, {"A": 1})
check(r["feasible"] is False and r["reason_code"] == "GUARANTEED_CONFLICT",
      "conflict")
check(r["solver"] == "tradeoff", "conflict solver")

# ---- 9. 用水权重录入校验 ----
expect_errors(NET_T, {"A": 3}, ["缺少普通节点的用水权重", "B"])
expect_errors(NET_T, {}, ["缺少普通节点的用水权重", "A", "B"])
expect_errors(NET_T, {"A": 3, "B": 3, "S": 1}, ["非普通节点", "S"])
expect_errors(NET_T, {"A": 3, "B": 3, "ZZ": 1}, ["非普通节点", "ZZ"])
for bad in [0, -2, 1.5, "3", "x", True, None]:
    expect_errors(NET_T, {"A": bad, "B": 3}, ["用水权重必须是正整数"])
    expect_errors(NET_T, {"A": 3, "B": bad}, ["用水权重必须是正整数"])
expect_errors(NET_T, "not-a-dict", ["缺少普通节点的用水权重"])
expect_errors(NET_T, None, ["缺少普通节点的用水权重"])
# 管网本身非法: 校验错误优先于权重检查
expect_errors({"nodes": [], "segments": [
    {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}], "valves": []},
    {}, ["端点"])


# ---- 10. 随机对拍: 与独立暴力枚举比较非支配前沿 ----
def bruteforce_tradeoff(net, weights):
    """独立实现: 显式组合枚举 + 集合 BFS, 返回 (前沿, 可行组合数) 或 None。"""
    nodes, segments, valves = validate_network(net)
    adj = {nd["id"]: [] for nd in nodes}
    seg_valve = {v["segment_id"]: v["id"] for v in valves}
    for sg in segments:
        vid = seg_valve.get(sg["id"])
        adj[sg["endpoint_a"]].append((sg["endpoint_b"], vid))
        adj[sg["endpoint_b"]].append((sg["endpoint_a"], vid))
    inlets = [nd["id"] for nd in nodes if nd["role"] == "inlet"]
    leaks = {nd["id"] for nd in nodes if nd["role"] == "leak"}
    keeps = {nd["id"] for nd in nodes if nd["role"] == "must_keep"}
    normals = [nd["id"] for nd in nodes if nd["role"] == "normal"]
    vcost = {v["id"]: v["cost"] for v in valves}
    vids = sorted(vcost)

    def fed_after(closed):
        seen = set(inlets)
        stack = list(inlets)
        while stack:
            u = stack.pop()
            for w, vid in adj[u]:
                if vid in closed or w in seen:
                    continue
                seen.add(w)
                stack.append(w)
        return seen

    if not inlets:
        return None
    base = fed_after(set())
    if not keeps <= base:
        return None
    base_fed_normal = [n for n in normals if n in base]

    best = {}
    feasible_count = 0
    for k in range(len(vids) + 1):
        for combo in itertools.combinations(vids, k):
            closed = set(combo)
            fed = fed_after(closed)
            if fed & leaks or not keeps <= fed:
                continue
            feasible_count += 1
            cost = sum(vcost[v] for v in combo)
            impact = sum(weights[n] for n in base_fed_normal if n not in fed)
            key = (cost, impact)
            if key not in best or combo < best[key]:
                best[key] = combo
    if not best:
        return None
    front = []
    min_imp = None
    for key in sorted(best):
        if min_imp is None or key[1] < min_imp:
            front.append((key[0], key[1], list(best[key])))
            min_imp = key[1]
    return front, feasible_count, base_fed_normal, fed_after


rng = random.Random(20260925)
trials = 0
for trial in range(400):
    n = rng.randint(3, 8)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet", "leak"] + ["normal"] * (n - 2)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
    # 随机连通图
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
    # 随机必保点(连通图 + 有进水点 => 不关阀时必可达, 避免前置无解干扰对拍)
    for nid in node_ids:
        if role_map[nid] == "normal" and rng.random() < 0.3:
            role_map[nid] = "must_keep"
    # 随机第二个漏点
    if rng.random() < 0.3:
        cands = [nid for nid in node_ids if role_map[nid] == "normal"]
        if cands:
            role_map[rng.choice(cands)] = "leak"
    segs = seg_list[:]
    rng.shuffle(segs)
    n_valve = rng.randint(0, min(6, len(segs)))
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 9)}
                  for i, sg in enumerate(segs[:n_valve])]
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list, "valves": valve_list}
    weights = {nid: rng.randint(1, 9) for nid in node_ids
               if role_map[nid] == "normal"}

    res = solve_tradeoff(net, weights)
    brute = bruteforce_tradeoff(net, weights)
    if brute is None:
        check(res["feasible"] is False, f"trial {trial} both infeasible")
        check(res["solver"] == "tradeoff", f"trial {trial} solver")
        continue
    front, feasible_count, base_fed_normal, fed_after = brute
    check(res["feasible"] is True, f"trial {trial} feasible")
    got = front_of(res)
    check(got == front, f"trial {trial}: {got} != brute {front}")
    check(res["feasible_subset_count"] == feasible_count,
          f"trial {trial} feasible count")
    check(res["plan_count"] == len(front), f"trial {trial} plan count")
    check(res["baseline_fed_normal"] == sorted(base_fed_normal),
          f"trial {trial} baseline fed")
    # 每个方案自洽: 新增断水节点与断水影响、关阀代价一致
    for p in res["plans"]:
        fed = fed_after(set(p["closed_valves"]))
        newly = [nid for nid in sorted(base_fed_normal) if nid not in fed]
        check(p["newly_disconnected"] == newly,
              f"trial {trial} newly {p['closed_valves']}")
        check(p["impact"] == sum(weights[x] for x in newly),
              f"trial {trial} impact {p['closed_valves']}")
        vcost = {v["id"]: v["cost"] for v in valve_list}
        check(p["total_cost"] == sum(vcost[v] for v in p["closed_valves"]),
              f"trial {trial} cost {p['closed_valves']}")
        check(p["closed_valves"] == sorted(p["closed_valves"]),
              f"trial {trial} closed sorted")
        check([d["id"] for d in p["newly_disconnected_details"]] == newly,
              f"trial {trial} details")
        check(all(d["weight"] == weights[d["id"]]
                  for d in p["newly_disconnected_details"]),
              f"trial {trial} detail weights")
    # 前沿性质: 代价严格升、断水影响严格降
    for a, b in zip(got, got[1:]):
        check(a[0] < b[0] and a[1] > b[1], f"trial {trial} front monotone")
    trials += 1
check(trials >= 80, f"enough random trials ({trials})")

print(f"tradeoff solver tests: {passed} checks passed")
