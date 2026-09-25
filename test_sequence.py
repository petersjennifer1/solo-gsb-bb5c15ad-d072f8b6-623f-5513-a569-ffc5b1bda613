"""plan_sequence 算法测试 + API 端到端测试。"""
import itertools
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    NetworkValidationError, SequenceInputError, plan_sequence, solve,
    solve_failover, _Graph, validate_network,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def exposure_bruteforce(g, plan_names, dur, weights):
    """穷举全部排列, 返回 (最小暴露量, 字典序最小的最优排列)。"""
    leak_ids = [nd["id"] for nd in g.nodes if nd["role"] == "leak"]
    v_idx = {vl["id"]: i for i, vl in enumerate(g.valves_sorted)}

    def wsum_for(closed_names):
        mask = 0
        for name in closed_names:
            mask |= 1 << v_idx[name]
        fed = g.reachable(mask, g.inlet_set)
        return sum(weights[lid] for lid in leak_ids
                   if (fed >> g.idx[lid]) & 1)

    best = None
    best_order = None
    for perm in itertools.permutations(plan_names):
        total = 0
        closed = []
        for name in perm:
            total += dur[name] * wsum_for(closed)
            closed = closed + [name]
        if best is None or total < best or (total == best and list(perm) < best_order):
            best = total
            best_order = list(perm)
    return best, best_order


# ---- 1. 示例管网: 单漏点, 两种顺序暴露量相同 -> 编号字典序 ----
from demo import DEMO_NETWORK  # noqa: E402

res = plan_sequence(DEMO_NETWORK, ["V2", "V4"],
                    {"V2": 10, "V4": 20}, {"L": 5})
check(res["sequence"] == ["V2", "V4"], f"demo seq {res['sequence']}")
check(res["total_duration"] == 30, "demo duration")
check(res["total_exposure"] == 150, f"demo exposure {res['total_exposure']}")
s1, s2 = res["steps"]
check((s1["start"], s1["end"]) == (0, 10), "step1 time")
check(s1["flowing_leaks_during"] == ["L"] and s1["exposure"] == 50, "step1")
check(s1["newly_isolated_leaks"] == [], "step1 newly")
check(s1["flowing_leaks_after"] == ["L"], "step1 after")
check(s1["cumulative_exposure"] == 50, "step1 cum")
check((s2["start"], s2["end"]) == (10, 30), "step2 time")
check(s2["newly_isolated_leaks"] == ["L"], "step2 newly isolated")
check(s2["flowing_leaks_after"] == [], "step2 after all isolated")
check(s2["cumulative_exposure"] == 150, "step2 cum")
check(res["final_flowing_leaks"] == [], "final flowing")

# 调换时长, 单漏点且单阀均切不断时暴露量仍与顺序无关 -> 仍是 V2 在前
res2 = plan_sequence(DEMO_NETWORK, ["V2", "V4"],
                     {"V2": 100, "V4": 1}, {"L": 5})
check(res2["sequence"] == ["V2", "V4"], f"demo tie {res2['sequence']}")
check(res2["total_exposure"] == 505, f"demo tie exposure {res2['total_exposure']}")

# ---- 2. 两个独立漏点, 高权重漏点应先隔离 ----
NET2 = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "X", "role": "normal"},
        {"id": "L1", "role": "leak"}, {"id": "L2", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "X"},
        {"id": "p2", "endpoint_a": "X", "endpoint_b": "L1"},
        {"id": "p3", "endpoint_a": "X", "endpoint_b": "L2"},
        {"id": "p4", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p2", "cost": 1},
        {"id": "V2", "segment_id": "p3", "cost": 1},
    ],
}
r2 = plan_sequence(NET2, ["V1", "V2"], {"V1": 5, "V2": 5},
                   {"L1": 10, "L2": 1})
check(r2["sequence"] == ["V1", "V2"], f"net2 seq {r2['sequence']}")
check(r2["total_exposure"] == 60, f"net2 exposure {r2['total_exposure']}")
check(r2["steps"][0]["newly_isolated_leaks"] == ["L1"], "net2 first isolates L1")
check(r2["steps"][0]["flowing_leaks_after"] == ["L2"], "net2 after1")
check(r2["steps"][1]["newly_isolated_leaks"] == ["L2"], "net2 after2")

# 时长不同: 先做短阀也能影响排序; 直接与穷举对拍
r2b = plan_sequence(NET2, ["V1", "V2"], {"V1": 8, "V2": 2},
                    {"L1": 3, "L2": 9})
nodes, segments, valves = validate_network(NET2)
g2 = _Graph(nodes, segments, valves)
best, best_order = exposure_bruteforce(g2, ["V1", "V2"],
                                       {"V1": 8, "V2": 2},
                                       {"L1": 3, "L2": 9})
check(r2b["total_exposure"] == best, f"net2b dp {r2b['total_exposure']} vs {best}")
check(r2b["sequence"] == best_order, f"net2b order {r2b['sequence']} vs {best_order}")

# ---- 3. 字典序: V10 < V2 (字符串序), 暴露量持平时 ----
# 3a: 两枚阀分别位于 L 的两条并联通路上, 任一枚单独都切不断 L =>
#     任一顺序暴露量相同 = w*(d1+d2), 取字符串字典序 V10 在 V2 前
NET3 = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
        {"id": "M1", "role": "normal"}, {"id": "M2", "role": "normal"},
        {"id": "L", "role": "leak"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p0", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "e1", "endpoint_a": "A", "endpoint_b": "M1"},
        {"id": "e2", "endpoint_a": "M1", "endpoint_b": "L"},
        {"id": "e3", "endpoint_a": "A", "endpoint_b": "M2"},
        {"id": "e4", "endpoint_a": "M2", "endpoint_b": "L"},
        {"id": "h", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V10", "segment_id": "e1", "cost": 1},
        {"id": "V2", "segment_id": "e3", "cost": 1},
    ],
}
r3 = plan_sequence(NET3, ["V2", "V10"], {"V2": 3, "V10": 7}, {"L": 4})
check(r3["sequence"] == ["V10", "V2"], f"lex seq {r3['sequence']}")
check(r3["total_exposure"] == 40, f"lex exp {r3['total_exposure']}")
check(r3["steps"][0]["newly_isolated_leaks"] == [], "lex no isolate at step1")
check(r3["steps"][1]["newly_isolated_leaks"] == ["L"], "lex isolate at step2")

# 3b: 两阀不等价 —— V2 单独即可隔离 L, V10 单独不行 => 必选 V2 先做
NET3B = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
        {"id": "B", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "A"},
        {"id": "p2", "endpoint_a": "A", "endpoint_b": "L"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
        {"id": "p5", "endpoint_a": "S", "endpoint_b": "B"},
        {"id": "p6", "endpoint_a": "B", "endpoint_b": "A"},
    ],
    "valves": [
        {"id": "V2", "segment_id": "p2", "cost": 1},
        {"id": "V10", "segment_id": "p1", "cost": 1},
    ],
}
# V2 先: 3*4 + 7*0 = 12; V10 先: 7*4 + 3*4 = 40
r3b = plan_sequence(NET3B, ["V2", "V10"], {"V2": 3, "V10": 7}, {"L": 4})
check(r3b["sequence"] == ["V2", "V10"], f"net3b seq {r3b['sequence']}")
check(r3b["total_exposure"] == 12, f"net3b exp {r3b['total_exposure']}")
check(r3b["steps"][0]["newly_isolated_leaks"] == ["L"], "net3b isolate early")

# ---- 4. 多进水点: 漏点必须与所有进水点断开才算隔离 ----
NET4 = {
    "nodes": [
        {"id": "S1", "role": "inlet"}, {"id": "S2", "role": "inlet"},
        {"id": "X", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "a", "endpoint_a": "S1", "endpoint_b": "X"},
        {"id": "b", "endpoint_a": "S2", "endpoint_b": "X"},
        {"id": "c", "endpoint_a": "X", "endpoint_b": "L"},
        {"id": "d", "endpoint_a": "S2", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "a", "cost": 1},
        {"id": "V2", "segment_id": "b", "cost": 1},
        {"id": "V3", "segment_id": "c", "cost": 1},
    ],
}
# 可行最优: 仅关 V3 即可让 L 与 S1,S2 全断
sol4 = solve(NET4)
check(sol4["feasible"] and sol4["closed_valves"] == ["V3"], f"net4 solve {sol4.get('closed_valves')}")
r4 = plan_sequence(NET4, ["V3"], {"V3": 12}, {"L": 2})
check(r4["steps"][0]["flowing_leaks_during"] == ["L"], "net4 during")
check(r4["steps"][0]["newly_isolated_leaks"] == ["L"], "net4 isolated from all inlets")
check(r4["total_exposure"] == 24, "net4 exposure")

# ---- 5. 错误: 遗漏 / 多余编号 / 非法数值 ----
def expect_errors(snapshot, valves, dur, wt, keys):
    try:
        plan_sequence(snapshot, valves, dur, wt)
    except SequenceInputError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))

expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 10}, {"L": 1},
              ["缺少", "V4"])
expect_errors(DEMO_NETWORK, ["V2", "V4"],
              {"V2": 10, "V4": 5, "V1": 9}, {"L": 1},
              ["多余", "V1"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 0, "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": -3, "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 1.5, "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": "10", "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": True, "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": None, "V4": 5}, {"L": 1},
              ["正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 10, "V4": 5}, {"L": 0},
              ["漏点 L", "正整数"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 10, "V4": 5}, {},
              ["缺少漏点", "L"])
expect_errors(DEMO_NETWORK, ["V2", "V4"], {"V2": 10, "V4": 5},
              {"L": 1, "X": 2}, ["多余", "X"])
expect_errors(DEMO_NETWORK, ["V9"], {"V9": 1}, {},
              ["计划阀门 V9 不存在"])

# ---- 6. 空方案(无漏点网络) ----
NET_EMPTY = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "H", "role": "must_keep"},
    ],
    "segments": [{"id": "p", "endpoint_a": "S", "endpoint_b": "H"}],
    "valves": [],
}
sol_e = solve(NET_EMPTY)
check(sol_e["feasible"] and sol_e["closed_valves"] == [], "empty solve")
r6 = plan_sequence(NET_EMPTY, [], {}, {})
check(r6["steps"] == [] and r6["total_exposure"] == 0, "empty sequence")
# 空方案下提供多余编号也报错
expect_errors(NET_EMPTY, [], {"V1": 1}, {}, ["多余"])
expect_errors(NET_EMPTY, [], {}, {"L": 1}, ["多余"])

# ---- 7. 随机对拍: 枚举全部排列验证 DP 最优性 + 字典序唯一性 ----
rng = random.Random(20260925)
for trial in range(60):
    n = rng.randint(3, 8)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet"]
    n_leak = rng.randint(1, 2)
    for i in range(n_leak):
        roles.append("leak")
    roles += ["normal"] * (n - 1 - n_leak)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
    # 保证至少一个进水点
    if "inlet" not in roles:
        role_map[node_ids[0]] = "inlet"
    seg_list = []
    # 先造一棵生成树保证连通, 再随机加边
    order = node_ids[:]
    rng.shuffle(order)
    used = [order[0]]
    si = 0
    for u in order[1:]:
        v = rng.choice(used)
        seg_list.append({"id": f"e{si}", "endpoint_a": u, "endpoint_b": v})
        used.append(u)
        si += 1
    for _ in range(rng.randint(0, 4)):
        a, b = rng.sample(node_ids, 2)
        seg_list.append({"id": f"e{si}", "endpoint_a": a, "endpoint_b": b})
        si += 1
    k = rng.randint(0, min(6, len(seg_list)))
    chosen_segs = rng.sample(seg_list, k) if k else []
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 5)}
                  for i, sg in enumerate(chosen_segs)]
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list, "valves": valve_list}
    sol = solve(net)
    if not sol["feasible"]:
        continue
    plan = sol["closed_valves"]
    dur = {v: rng.randint(1, 9) for v in plan}
    leak_ids = [nid for nid in node_ids if role_map[nid] == "leak"]
    weights = {lid: rng.randint(1, 9) for lid in leak_ids}
    res = plan_sequence(net, plan, dur, weights)
    if not plan:
        check(res["total_exposure"] == 0 and res["sequence"] == [], "empty")
        continue
    nodes, segments, valves = validate_network(net)
    g = _Graph(nodes, segments, valves)
    best, best_order = exposure_bruteforce(g, plan, dur, weights)
    check(res["total_exposure"] == best,
          f"trial {trial}: dp {res['total_exposure']} != brute {best}")
    check(res["sequence"] == best_order,
          f"trial {trial}: order {res['sequence']} != {best_order}")
    # 累计暴露量单调性 + 时间连续性
    t = 0
    cum = 0
    for st in res["steps"]:
        check(st["start"] == t and st["end"] == t + st["duration"], "time")
        cum += st["exposure"]
        check(st["cumulative_exposure"] == cum, "cum")
        check(st["exposure"] == st["duration"] * st["flowing_weight_during"],
              "exposure formula")
        t = st["end"]
    check(cum == res["total_exposure"], "total")
    # 漏点划分: newly + after = before
    all_leaks = set(leak_ids)
    before = set(res["initial_flowing_leaks"])
    check(before <= all_leaks, "leak domain")
    for st in res["steps"]:
        check(set(st["newly_isolated_leaks"]) | set(st["flowing_leaks_after"])
              == before, f"trial {trial} partition")
        check(not (set(st["newly_isolated_leaks"])
                   & set(st["flowing_leaks_after"])), "disjoint")
        check(set(st["flowing_leaks_during"]) == before,
              "during == before closure (valve still open)")
        before = set(st["flowing_leaks_after"])
    # 全部计划阀门执行完后, 可行方案的漏点应全部隔离
    check(set(res["final_flowing_leaks"]) == set(),
          f"trial {trial} final leaks {res['final_flowing_leaks']}")

print(f"solver tests: {passed} checks passed")
