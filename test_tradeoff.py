"""solve_tradeoff 关阀成本与断水影响权衡预览测试。

覆盖: Pareto 非支配返回 / 双目标定义(新增断水普通节点权重和) /
同一对指标字典序去重 / 排序 / 录入校验 / 无解原因 / 随机对拍。
"""
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


# ---- 双分支管网: 三个非支配方案 (2,12) / (11,5) / (20,0) ----
NET = {
    "nodes": [
        {"id": "S", "role": "inlet"},
        {"id": "A", "role": "normal"},
        {"id": "B", "role": "normal"},
        {"id": "C", "role": "normal"},
        {"id": "L1", "role": "leak"},
        {"id": "L2", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "e0", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "e1", "endpoint_a": "A", "endpoint_b": "B"},
        {"id": "e2", "endpoint_a": "B", "endpoint_b": "L1"},
        {"id": "e3", "endpoint_a": "A", "endpoint_b": "C"},
        {"id": "e4", "endpoint_a": "C", "endpoint_b": "L2"},
        {"id": "e5", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "e1", "cost": 1},
        {"id": "V2", "segment_id": "e2", "cost": 10},
        {"id": "V3", "segment_id": "e3", "cost": 1},
        {"id": "V4", "segment_id": "e4", "cost": 10},
    ],
}
W = {"A": 100, "B": 5, "C": 7}

r = solve_tradeoff(NET, W)
check(r["feasible"] and r["solver"] == "tradeoff", "feasible/tradeoff")
check(r["subset_count"] == 16, f"subset_count {r['subset_count']}")
check(r["isolating_subset_count"] == 9, f"isolating {r['isolating_subset_count']}")
check(r["feasible_subset_count"] == 9, f"feasible {r['feasible_subset_count']}")
check(r["unique_pair_count"] == 8, f"unique {r['unique_pair_count']}")
check(r["pareto_count"] == 3, f"pareto {r['pareto_count']}")
check(r["normal_node_count"] == 3, "normal count")
check(r["initially_supplied_normal_count"] == 3, "initially supplied")
check(r["initially_disconnected_normal"] == [], "no initially off")
check(r["weights"] == W, "weights echoed")

pairs = [(o["total_cost"], o["disconnected_impact"], o["closed_valves"])
         for o in r["options"]]
check(pairs == [
    (2, 12, ["V1", "V3"]),
    (11, 5, ["V1", "V4"]),
    (20, 0, ["V2", "V4"]),
], f"front {pairs}")
check([o["rank"] for o in r["options"]] == [1, 2, 3], "ranks")

# 方案明细: 新增断水节点与权重
o1 = r["options"][0]
check(o1["newly_disconnected_nodes"] == ["B", "C"], f"opt1 lost {o1['newly_disconnected_nodes']}")
check(o1["newly_disconnected_count"] == 2, "opt1 count")
check({(w["id"], w["weight"]) for w in o1["newly_disconnected_weights"]}
      == {("B", 5), ("C", 7)}, "opt1 weights")
# A 始终由 S 供水(e0 无阀), 永不断水
for o in r["options"]:
    assert "A" not in o["newly_disconnected_nodes"]
    # 节点/管段状态完整, 漏点全部隔离、必保点全部有水
    leaks = [n for n in o["node_status"] if n["role"] == "leak"]
    check(all(n["status"] == "isolated" for n in leaks), f"opt{o['rank']} leaks isolated")
    keeps = [n for n in o["node_status"] if n["role"] == "must_keep"]
    check(all(n["supplied"] for n in keeps), f"opt{o['rank']} keeps supplied")
    check(len(o["segment_status"]) == 6, f"opt{o['rank']} segments")
    closed_segs = {s["id"] for s in o["segment_status"] if s["status"] == "closed"}
    check(closed_segs == {"e1", "e3"}, f"opt1 closed segs {closed_segs}")
    break
check("只读" in r["message"] and "不修改管网" in r["message"], "readonly note")

# ---- 同一对指标去重: 两条并联边(等价阀 V1/V2), 字典序最小者保留 ----
PARALLEL = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "L", "role": "leak"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p3", "endpoint_a": "M", "endpoint_b": "L"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 2},
        {"id": "V2", "segment_id": "p2", "cost": 2},
        {"id": "V3", "segment_id": "p3", "cost": 5},
    ],
}
r = solve_tradeoff(PARALLEL, {"M": 9})
pairs = [(o["total_cost"], o["disconnected_impact"], o["closed_valves"])
         for o in r["options"]]
# (4,9,[V1,V2]) 与 (5,0,[V3]) 非支配; (2,9,[V1])/(2,9,[V2]) 同指标去重且被 (4,9) 支配
check(pairs == [(4, 9, ["V1", "V2"]), (5, 0, ["V3"])], f"parallel front {pairs}")
check(r["feasible_subset_count"] == 5 and r["unique_pair_count"] == 4
      and r["pareto_count"] == 2, f"parallel counts {r}")

# ---- 关阀前已断水的普通节点不计入断水影响 ----
ISLAND = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "C", "role": "normal"},
        {"id": "D", "role": "normal"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "A", "endpoint_b": "L"},
        {"id": "p3", "endpoint_a": "C", "endpoint_b": "D"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 1},
    ],
}
r = solve_tradeoff(ISLAND, {"A": 5, "C": 100, "D": 100})
check(r["initially_disconnected_normal"] == ["C", "D"], "initially off C,D")
check(r["initially_supplied_normal_count"] == 1, "only A supplied")
pairs = [(o["total_cost"], o["disconnected_impact"]) for o in r["options"]]
check(pairs == [(1, 0)], f"island front {pairs}")
check(r["options"][0]["closed_valves"] == ["V2"], "island closes V2")

# ---- 没有漏点: 只有不关阀方案, 断水影响 0 ----
NOLEAK = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "A"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
r = solve_tradeoff(NOLEAK, {"A": 3})
check(r["pareto_count"] == 1 and r["options"][0]["closed_valves"] == []
      and r["options"][0]["total_cost"] == 0
      and r["options"][0]["disconnected_impact"] == 0, f"noleak {r['options']}")
check(r["isolating_subset_count"] == 2 and r["feasible_subset_count"] == 2,
      "noleak: 无漏点时所有组合都满足(A)(B)(饿死普通点不违规, 仅计入断水影响)")

# ---- 没有普通节点: impact 恒 0 ----
NONORMAL = {
    "nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
    "valves": [{"id": "V1", "segment_id": "p1", "cost": 1}],
}
r = solve_tradeoff(NONORMAL, {})
check(r["feasible"] and r["options"][0]["closed_valves"] == ["V1"]
      and r["options"][0]["disconnected_impact"] == 0
      and r["normal_node_count"] == 0 and r["weights"] == {}, "no-normal")

# ---- 无解: 无阀通路 ----
BAD = {"nodes": [{"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"}],
       "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "L"}],
       "valves": []}
r = solve_tradeoff(BAD, {})
check(r["feasible"] is False and r["solver"] == "tradeoff", "bad solver")
check(r["reason_code"] == "UNCUTTABLE_LEAK_PATH", f"bad code {r['reason_code']}")
check(r["reason_detail"]["witness_path"], "bad witness")

# ---- 无解: 无进水点 ----
r = solve_tradeoff({"nodes": [{"id": "L", "role": "leak"}],
                    "segments": [], "valves": []}, {})
check(not r["feasible"] and r["reason_code"] == "NO_INLET", "no inlet")

# ---- 无解: 必保点本来断水 ----
ALREADY = {"nodes": [{"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"}],
           "segments": [], "valves": []}
r = solve_tradeoff(ALREADY, {})
check(not r["feasible"] and r["reason_code"] == "GUARANTEED_ALREADY_DISCONNECTED",
      "already disconnected")

# ---- 示例管网: 唯一可行方案 V2+V4(代价 7, 影响 0) ----
from demo import DEMO_NETWORK  # noqa: E402
r = solve_tradeoff(DEMO_NETWORK, {"A": 11, "B": 22})
check(r["feasible"] and r["pareto_count"] == 1, f"demo pareto {r['pareto_count']}")
o = r["options"][0]
check(o["closed_valves"] == ["V2", "V4"] and o["total_cost"] == 7
      and o["disconnected_impact"] == 0
      and o["newly_disconnected_nodes"] == [], f"demo opt {o}")

# ---- 录入校验 ----
expect_errors(NET, {}, ["缺少普通节点的用水权重", "A"])
expect_errors(NET, {"A": 100, "B": 5, "C": 7, "X": 1},
              ["提供了非普通节点的用水权重(多余编号)", "X"])
# 漏点/进水点/必保点编号也是多余编号
expect_errors(NET, {"A": 100, "B": 5, "C": 7, "L1": 1}, ["多余编号", "L1"])
for bad in [0, -1, 1.5, "3", True, None]:
    expect_errors(NET, {"A": bad, "B": 5, "C": 7}, ["用水权重必须是正整数"])
expect_errors(NET, [], ["weights 必须是"])
expect_errors(NET, None, ["weights 必须是"])
# 多余 + 值非法同时报错
try:
    solve_tradeoff(NET, {"A": 0, "B": 5, "C": 7, "X": 1})
except NetworkValidationError as exc:
    joined = " | ".join(exc.errors)
    check("多余编号" in joined and "用水权重必须是正整数" in joined,
          f"both errors: {exc.errors}")

# ---- 随机对拍: 独立暴力枚举计算 Pareto 前沿 ----
def brute_front(net, weights):
    nodes, segments, valves = validate_network(net)
    idx = {nd["id"]: i for i, nd in enumerate(nodes)}
    adj = {nd["id"]: [] for nd in nodes}
    seg_valve = {}
    for v in valves:
        seg_valve[v["segment_id"]] = v["id"]
    for sg in segments:
        vid = seg_valve.get(sg["id"])
        adj[sg["endpoint_a"]].append((sg["endpoint_b"], vid))
        adj[sg["endpoint_b"]].append((sg["endpoint_a"], vid))
    inlets = [nd["id"] for nd in nodes if nd["role"] == "inlet"]
    leaks = {nd["id"] for nd in nodes if nd["role"] == "leak"}
    keeps = {nd["id"] for nd in nodes if nd["role"] == "must_keep"}
    normals = [nd["id"] for nd in nodes if nd["role"] == "normal"]
    vcost = {v["id"]: v["cost"] for v in valves}
    allv = sorted(vcost)

    def fed(closed):
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

    fed0 = fed(set())
    recs = []
    for k in range(len(allv) + 1):
        for combo in itertools.combinations(allv, k):
            f = fed(set(combo))
            if f & leaks or not keeps <= f:
                continue
            impact = sum(weights[nid] for nid in normals
                         if nid in fed0 and nid not in f)
            recs.append((sum(vcost[v] for v in combo), impact, tuple(combo)))
    # 同对指标字典序去重
    best = {}
    for c, imp, names in recs:
        if (c, imp) not in best or names < best[(c, imp)]:
            best[(c, imp)] = names
    # Pareto
    pairs = sorted(best)
    front = []
    best_imp = None
    for c, imp in pairs:
        if best_imp is None or imp < best_imp:
            front.append((c, imp, list(best[(c, imp)])))
            best_imp = imp
    front.sort(key=lambda x: (x[0], x[1], x[2]))
    return front


rng = random.Random(20260925)
trials = 0
for trial in range(200):
    n = rng.randint(3, 7)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet", "leak"] + ["normal"] * (n - 2)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
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
    for nid in node_ids:
        if role_map[nid] == "normal" and rng.random() < 0.4:
            role_map[nid] = "must_keep"
    segs = seg_list[:]
    rng.shuffle(segs)
    n_valve = rng.randint(0, min(6, len(segs)))
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 9)}
                  for i, sg in enumerate(segs[:n_valve])]
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list, "valves": valve_list}
    w = {nid: rng.randint(1, 20)
         for nid in node_ids if role_map[nid] == "normal"}
    res = solve_tradeoff(net, w)
    bf = brute_front(net, w)
    if not bf:
        check(res["feasible"] is False, f"trial {trial} both infeasible")
        continue
    check(res["feasible"] is True, f"trial {trial} feasible")
    got = [(o["total_cost"], o["disconnected_impact"], o["closed_valves"])
           for o in res["options"]]
    check(got == bf, f"trial {trial}: {got} != brute {bf}")
    # 自洽: impact = 新增断水节点权重和; 排序非降; 全部非支配
    for o in res["options"]:
        s1 = o["disconnected_impact"]
        s2 = sum(x["weight"] for x in o["newly_disconnected_weights"])
        check(s1 == s2, f"trial {trial} impact sum {s1} != {s2}")
        check(set(o["newly_disconnected_nodes"])
              == {x["id"] for x in o["newly_disconnected_weights"]},
              f"trial {trial} lost ids")
    costs = [(o["total_cost"], o["disconnected_impact"]) for o in res["options"]]
    check(costs == sorted(costs), f"trial {trial} sorted")
    trials += 1
check(trials >= 40, f"enough random trials ({trials})")

print(f"tradeoff solver tests: {passed} checks passed")
