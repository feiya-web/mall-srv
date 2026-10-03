"""M2-4 启用 / 禁用员工 —— 联调验收脚本。

用法（应用需已用新代码重启，端口 8080）：
    python m2_status_verify.py            # 跑全部用例并查库核验
    python m2_status_verify.py --check    # 只读：打印员工表状态现状
    python m2_status_verify.py --clean    # 仅清理 m4_test_% 测试数据后退出
    python m2_status_verify.py --selftest # 变异自检：改坏数据，确认「不变」断言会红

这条接口的两个校验都不是"参数格式"问题，而是**数据完整性**问题，所以验收重点在这两处：

  1. status 只允许 0/1 —— status 列是 tinyint，能存 -128~127，传 5 数据库照收不误。
     一旦出现 5，业务里 `status == 1` 和 `status == DISABLE` 两头都不算，
     这个员工"既不是启用也不是禁用"，按状态过滤的列表两边都查不到。必须在应用层拦。
     验收判据：接口拦下了 **且库里没被写成非法值**（只断言拦下是不够的）。

  2. 不能禁用自己 —— 禁掉当前登录人他就失去管理能力；最后会 lockout。
     验收判据：接口拦下了 **且自己的 status 没变、之后仍能登录**。

另外这脚本带一条**全表守护断言**：任何一步之后，employee 表里所有 status 必须 ∈ {0,1}。
它是防"全表更新"这类灾难的兜底 —— 一行漏传 id 把整张表刷成同一个值是真实发生过的事故。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request

from _env import BASE, ADMIN_USER, DEMO_PASSWORD, SRC, mysql_argv

ADMIN_PWD = DEMO_PASSWORD

TEST_PREFIX = "m4_test_"
VICTIM_USER = TEST_PREFIX + "victim"
VICTIM_PWD = "Victim@12345"
VICTIM_NAME = "验收-被操作用户"
VICTIM_PHONE = "13600001111"

_passed = []
_failed = []
_token = None
_admin_id = None


# ---------------------------------------------------------------- HTTP
def call(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def admin_login(user=ADMIN_USER, pwd=ADMIN_PWD):
    return call("POST", "/admin/employee/login",
                body={"username": user, "password": pwd})


# ---------------------------------------------------------------- DB
def sql(query):
    p = subprocess.run(
        mysql_argv(query),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("[FATAL] 查库失败: %s" % (p.stderr or p.stdout))
    return [ln for ln in (p.stdout or "").splitlines()
            if ln.strip() and "[Warning]" not in ln]


def status_of(username):
    rows = sql("SELECT status FROM employee WHERE username='%s';" % username)
    return rows[0] if rows else None


def status_of_id(eid):
    rows = sql("SELECT status FROM employee WHERE id=%s;" % eid)
    return rows[0] if rows else None


def illegal_status_rows():
    """状态不是 0/1 的行 —— 正常应恒为空。"""
    return sql("SELECT CONCAT(id,'|',username,'|status=',status) FROM employee "
               "WHERE status NOT IN (0,1);")


def clean_test_rows():
    rows = sql("SELECT CONCAT(id,'|',username,'|status=',status) FROM employee "
               "WHERE username LIKE '%s%%';" % TEST_PREFIX)
    if rows:
        print("  将删除以下测试行：")
        for r in rows:
            print("    - %s" % r)
    sql("DELETE FROM employee WHERE username LIKE '%s%%';" % TEST_PREFIX)
    return len(rows)


# ---------------------------------------------------------------- 断言
def check(name, cond, detail=""):
    if cond:
        _passed.append(name)
        print("  [PASS] %s" % name)
    else:
        _failed.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


def expect_code(name, js, code, msg_contains=None):
    ok = js.get("code") == code
    detail = "实际 code=%s msg=%s" % (js.get("code"), js.get("msg"))
    if ok and msg_contains is not None:
        got = js.get("msg") or ""
        ok = msg_contains in got
        if not ok:
            detail += " 期望 msg 含 '%s'" % msg_contains
    check(name, ok, detail)


def guard_no_illegal_status(tag):
    """全表守护：任何时刻都不允许出现 0/1 之外的状态值。"""
    bad = illegal_status_rows()
    check("%s 全表 status 仍然只有 0/1" % tag, not bad,
          "发现非法状态行: %s" % bad)


def set_status(eid, status, token=None):
    tk = _token if token is None else token
    return call("PUT", "/admin/employee/%s/status/%s" % (eid, status),
                token=tk or None)


# ---------------------------------------------------------------- 前置
def setup():
    global _admin_id
    rows = sql("SELECT id FROM employee WHERE username='%s';" % ADMIN_USER)
    if not rows:
        raise SystemExit("[FATAL] 找不到 admin 账号")
    _admin_id = rows[0]

    sql("DELETE FROM employee WHERE username='%s';" % VICTIM_USER)
    st, js = call("POST", "/admin/employee", token=_token, body={
        "name": VICTIM_NAME, "username": VICTIM_USER,
        "password": VICTIM_PWD, "phone": VICTIM_PHONE})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 前置新增失败（M2-2 是否已就绪？）: %s" % js)

    victim_id = sql("SELECT id FROM employee WHERE username='%s';" % VICTIM_USER)[0]
    print("  admin id=%s  status=%s" % (_admin_id, status_of(ADMIN_USER)))
    print("  被操作用户 id=%s  username=%s  status=%s"
          % (victim_id, VICTIM_USER, status_of(VICTIM_USER)))
    check("前置.1 admin 状态是 1", status_of(ADMIN_USER) == "1",
          "实际=%s" % status_of(ADMIN_USER))
    check("前置.2 被操作用户状态是 1", status_of(VICTIM_USER) == "1",
          "实际=%s" % status_of(VICTIM_USER))
    guard_no_illegal_status("前置.3")
    return victim_id


# ---------------------------------------------------------------- 用例
def case_disable_other(victim_id, victim_token):
    print("\n[E1] 禁用他人（admin 禁 victim）")
    st, js = set_status(victim_id, 0)
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("E1.1 返回 code=1", js, 1)
    check("E1.2 victim.status 变成 0", status_of(VICTIM_USER) == "0",
          "实际=%s" % status_of(VICTIM_USER))
    check("E1.3 admin.status 未受影响", status_of(ADMIN_USER) == "1",
          "实际=%s" % status_of(ADMIN_USER))
    guard_no_illegal_status("E1.4")

    print("\n[E2] 幂等：再禁一次")
    st, js = set_status(victim_id, 0)
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("E2.1 重复禁用仍 code=1", js, 1)
    check("E2.2 victim.status 仍是 0", status_of(VICTIM_USER) == "0",
          "实际=%s" % status_of(VICTIM_USER))
    print("     注：MySQL 驱动默认返回『匹配行数』而非『实际变更行数』，"
          "所以值没变时 rows 仍是 1，不会被误判成『id 不存在』。")

    print("\n[E3] 被禁用后不能登录")
    st, js = admin_login(VICTIM_USER, VICTIM_PWD)
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("E3.1 登录失败 code=0", js, 0)
    check("E3.2 提示『账号已被禁用』", "禁用" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))

    print("\n[E4] 被禁用后，之前签发的 token 还能用吗（高级边界／观察型）")
    st, js = call("GET", "/admin/employee/page?page=1&pageSize=1", token=victim_token)
    usable = js.get("code") == 1
    print("     用 victim 的旧 token 调 GET /admin/employee/page -> HTTP %s %s"
          % (st, json.dumps(js, ensure_ascii=False)))
    if usable:
        check("E4.1 观测：旧 token 仍可用（禁用未能即时吊销令牌）", True)
        print("     ↑ 这是真实缺口，不是脚本问题：JwtTokenAdminInterceptor 只验签名、"
              "不查数据库，所以已签发的 token 在 TTL(2h) 内一直有效。")
        print("       定性：属于『token 无法即时吊销』的已知取舍，归入待改清单，"
              "不要在用例里断言它必须失败——那会钉死一个尚未修复的行为。")
    else:
        check("E4.1 旧 token 已失效（说明有查库校验）", True)


def case_enable_other(victim_id):
    print("\n[E5] 启用他人")
    st, js = set_status(victim_id, 1)
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("E5.1 返回 code=1", js, 1)
    check("E5.2 victim.status 变回 1", status_of(VICTIM_USER) == "1",
          "实际=%s" % status_of(VICTIM_USER))

    print("\n[E6] 被重新启用后可以登录")
    st, js = admin_login(VICTIM_USER, VICTIM_PWD)
    expect_code("E6.1 登录成功 code=1", js, 1)
    check("E6.2 拿得到 token", bool(js.get("data", {}).get("token")))


def case_self_protection():
    print("\n[E7] 禁用自己（admin 禁 admin）—— 核心边界")
    st, js = set_status(_admin_id, 0)
    print("     PUT /admin/employee/%s/status/0 -> HTTP %s %s"
          % (_admin_id, st, json.dumps(js, ensure_ascii=False)))
    expect_code("E7.1 被拦下 code=0", js, 0)
    check("E7.2 提示含『不能禁用自己』", "不能禁用自己" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))
    check("E7.3 admin.status 仍是 1（校验没漏过）", status_of(ADMIN_USER) == "1",
          "实际=%s" % status_of(ADMIN_USER))
    st2, js2 = admin_login()
    check("E7.4 admin 仍能登录（未 lockout）", js2.get("code") == 1,
          "HTTP %s %s" % (st2, js2))
    guard_no_illegal_status("E7.5")

    print("\n[E8] 启用自己（按设计放行）")
    st, js = set_status(_admin_id, 1)
    print("     PUT /admin/employee/%s/status/1 -> HTTP %s %s"
          % (_admin_id, st, json.dumps(js, ensure_ascii=False)))
    expect_code("E8.1 放行 code=1", js, 1)
    check("E8.2 admin.status 仍是 1", status_of(ADMIN_USER) == "1",
          "实际=%s" % status_of(ADMIN_USER))


def case_illegal_status(victim_id):
    print("\n[E9] status 非法值：2 / 5 / -1 / 99 —— 核心边界")
    for bad in (2, 5, -1, 99):
        st, js = set_status(victim_id, bad)
        print("     status=%-3s -> HTTP %s %s" % (bad, st, json.dumps(js, ensure_ascii=False)))
        expect_code("E9.1 status=%s 被拦下" % bad, js, 0)
        check("E9.2 status=%s 没被写进库里" % bad,
              status_of(VICTIM_USER) not in (str(bad),),
              "库里被写成了 %s" % status_of(VICTIM_USER))
    check("E9.3 victim 状态仍停留在合法值 1",
          status_of(VICTIM_USER) == "1", "实际=%s" % status_of(VICTIM_USER))
    guard_no_illegal_status("E9.4")
    print("     注：tinyint 本身能存 -128~127，这些值如果漏过应用层校验会被数据库照单全收，"
          "所以判据必须包含『库里没被写成非法值』，只断言『接口返回 code=0』是不够的。")

    print("\n[E10] status 非数字（观察型）")
    st, js = call("PUT", "/admin/employee/%s/status/abc" % victim_id, token=_token)
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    check("E10.1 未把 500 抛给前端", js.get("code") == 0, "实际 %s" % js)
    print("     备注：@PathVariable Integer 收到 'abc' 抛 MethodArgumentTypeMismatchException，"
          "落兜底 handler → 文案是『系统繁忙』而非『参数格式错误』。与 M2-3 同一根因。")


def case_bad_id():
    print("\n[E11] id 不存在")
    mx = sql("SELECT COALESCE(MAX(id),0)+1000 FROM employee;")[0]
    st, js = set_status(mx, 0)
    print("     PUT /admin/employee/%s/status/0 -> HTTP %s %s"
          % (mx, st, json.dumps(js, ensure_ascii=False)))
    expect_code("E11.1 返回 code=0（不能假成功）", js, 0)
    check("E11.2 提示含『员工不存在』", "员工不存在" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))

    print("\n[E12] id 少一段（路径参数天然不可缺，应 404 而不是匹配到别处）")
    st, js = call("PUT", "/admin/employee/status/0", token=_token)
    print("     PUT /admin/employee/status/0 -> HTTP %s %s"
          % (st, json.dumps(js, ensure_ascii=False)))
    check("E12.1 没有匹配到 /{id} 之外的处理器（未误作用到别的行）",
          js.get("code") != 1, "竟然返回成功：%s" % js)
    guard_no_illegal_status("E12.2")


def case_auth():
    print("\n[E13] 未带 token")
    st, js = set_status(999999, 0, token="")
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    check("E13.1 返回 HTTP 401", st == 401, "实际 HTTP %s" % st)
    expect_code("E13.2 body code=0", js, 0)


# ---------------------------------------------------------------- 变异自检
def selftest():
    """数据层变异：把①非法状态值②admin 被禁用 造出来，确认守护断言真的会红。"""
    print("=" * 74)
    print("变异自检：造出非法状态 / 造出 admin 被禁用，确认断言有判别力")
    print("=" * 74)
    victim_id = setup()

    print("\n[变异 1] UPDATE victim SET status=5（模拟漏过应用层校验）")
    sql("UPDATE employee SET status=5 WHERE id=%s;" % victim_id)
    got = status_of(VICTIM_USER)
    print("        库里现在是: %s" % got)
    check("变异生效：status 确实变成 5", got == "5")
    check("变异1 → 全表守护『status 只有 0/1』应变红", bool(illegal_status_rows()))
    check("变异1 → E1.2『victim.status 是 0』应变红", got != "0")
    sql("UPDATE employee SET status=1 WHERE id=%s;" % victim_id)

    print("\n[变异 2] UPDATE admin SET status=0（模拟『不能禁用自己』校验漏过）")
    sql("UPDATE employee SET status=0 WHERE username='%s';" % ADMIN_USER)
    try:
        got = status_of(ADMIN_USER)
        check("变异生效：admin 确实被禁用了", got == "0")
        check("变异2 → E7.3『admin.status 仍是 1』应变红", got != "1")
        st, js = admin_login()
        check("变异2 → E7.4『admin 仍能登录』应变红", js.get("code") != 1,
              "竟然还能登录，说明登录没校验状态")
    finally:
        # 变异把 admin 真的禁掉了，无论上面是否抛错都必须还原 ——
        # 留在禁用态的话，后续任何脚本、乃至你自己登录后台都会被挡在外面。
        # 这就是"变异会改坏真实数据"的典型场景：还原逻辑不能放在正常路径上。
        sql("UPDATE employee SET status=1 WHERE username='%s';" % ADMIN_USER)
        print("\n[恢复] 已在 finally 分支强制把 admin.status 还原为 1")

    check("恢复.1 admin.status 回到 1", status_of(ADMIN_USER) == "1",
          "实际=%s —— 若这里是 FAIL，手工执行："
          "UPDATE employee SET status=1 WHERE username='admin';" % status_of(ADMIN_USER))
    st, js = admin_login()
    check("恢复.2 admin 恢复后能登录", js.get("code") == 1, "HTTP %s %s" % (st, js))
    guard_no_illegal_status("恢复.3")

    print("\n[前提 3] 确认两条校验都写在 Service 里（业务规则，可被其他入口复用）")
    src = (SRC / "com/mall/service/EmployeeService.java").read_text(encoding="utf-8")
    check("前提3.1 Service 中存在 status 值域校验",
          "STATUS_INVALID" in src)
    check("前提3.2 Service 中存在『不能禁用自己』校验",
          "CANNOT_DISABLE_SELF" in src and "BaseContext.getCurrentId()" in src)
    check("前提3.3 用的是 Long.equals 而不是 ==",
          "id.equals(BaseContext.getCurrentId())" in src,
          "若写成 == 会在 id>127 时静默失效")

    print("\n清理自检残留")
    clean_test_rows()


# ---------------------------------------------------------------- main
def main():
    global _token, _admin_id

    if "--check" in sys.argv:
        rows = sql("SELECT id, name, username, status FROM employee ORDER BY id;")
        print("=== employee 表状态现状（共 %d 行）===" % len(rows))
        for r in rows:
            print("  " + r)
        bad = illegal_status_rows()
        print("非法状态行: %s" % (bad or "无"))
        return

    if "--clean" in sys.argv:
        print("=== 清理测试数据 ===")
        print("  已清理 %d 行（前缀 %s*）" % (clean_test_rows(), TEST_PREFIX))
        return

    st, js = admin_login()
    if js.get("code") != 1:
        raise SystemExit("[FATAL] admin 登录失败: HTTP %s %s" % (st, js))
    _token = js["data"]["token"]
    print("  adminToken = %s..." % _token[:24])

    if "--selftest" in sys.argv:
        selftest()
        print("\n" + "=" * 74)
        print("自检结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
        print("全绿 → 说明守护断言确实有判别力。")
        print("=" * 74)
        sys.exit(1 if _failed else 0)

    print("=" * 74)
    print("M2-4 启用/禁用员工 联调验收")
    print("目标: %s  |  端点: PUT /admin/employee/{id}/status/{status}" % BASE)
    print("=" * 74)

    print("\n[准备] 清理上一轮测试数据")
    print("  已清理 %d 行（前缀 %s*）" % (clean_test_rows(), TEST_PREFIX))
    print("\n[准备] 铺测试数据")
    victim_id = setup()

    print("\n[准备] 先拿一个 victim 的旧 token（E4 要用）")
    st, js = admin_login(VICTIM_USER, VICTIM_PWD)
    if js.get("code") != 1:
        raise SystemExit("[FATAL] victim 初始登录失败: %s" % js)
    victim_token = js["data"]["token"]
    print("  victimToken = %s..." % victim_token[:24])

    case_disable_other(victim_id, victim_token)
    case_enable_other(victim_id)
    case_self_protection()
    case_illegal_status(victim_id)
    case_bad_id()
    case_auth()

    print("\n" + "=" * 74)
    print("结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
    if _failed:
        print("失败项：")
        for f in _failed:
            print("  - %s" % f)
    print("=" * 74)
    print("收尾核验：admin.status=%s  victim.status=%s"
          % (status_of(ADMIN_USER), status_of(VICTIM_USER)))
    guard_no_illegal_status("收尾")
    print("复查：python m2_status_verify.py --check")
    print("清理：python m2_status_verify.py --clean")
    print("自检：python m2_status_verify.py --selftest")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
