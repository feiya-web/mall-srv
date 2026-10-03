"""M2-3 编辑员工 —— 联调验收脚本。

用法（应用需已用新代码重启，端口 8080）：
    python m2_update_verify.py            # 跑全部用例并查库核验
    python m2_update_verify.py --check    # 只读：打印 m3_test_ 目标行的现状
    python m2_update_verify.py --clean    # 仅清理 m3_test_% 测试数据后退出
    python m2_update_verify.py --selftest # 变异自检：故意改坏数据，确认断言真的会变红

这个接口的验收重点和新增完全不同。
新增看的是"该写进去的写进去了"，编辑看的是**"不该动的字段一个都没动"**。
更新接口把字段改没（密码被清空、账号被改名、状态被覆盖）是最经典的缺陷形态，
而且它不报错——接口返回 code=1，只有查库才看得出来。

所以本脚本的核心是四条「不变」断言：
    编辑之后 password / username / status / 未被提交的 phone 必须原封不动。
其中 password 那条同时是安全断言：编辑资料不该有改密码的能力。

依赖：admin/123456 可登录；employee 表存在 uk_username 唯一索引。
"""
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request

from _env import BASE, ADMIN_USER, DEMO_PASSWORD, SRC, mysql_argv

ADMIN_PWD = DEMO_PASSWORD

TEST_PREFIX = "m3_test_"
TARGET_USER = TEST_PREFIX + "target"
TARGET_PWD = "Edit@12345"
TARGET_NAME = "编辑前-姓名"
TARGET_PHONE = "13700001111"

NEW_NAME = "编辑后-姓名"
NEW_PHONE = "13700002222"

_passed = []
_failed = []
_token = None


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


def admin_login():
    status, js = call("POST", "/admin/employee/login",
                      body={"username": ADMIN_USER, "password": ADMIN_PWD})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] admin 登录失败: HTTP %s %s" % (status, js))
    return js["data"]["token"]


# ---------------------------------------------------------------- DB
def sql(query):
    p = subprocess.run(
        mysql_argv(query),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("[FATAL] 查库失败: %s" % (p.stderr or p.stdout))
    return [ln for ln in (p.stdout or "").splitlines()
            if ln.strip() and "[Warning]" not in ln]


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_by_id(eid):
    """按 id 读目标行。mysql -N -B 下 NULL 会输出字面量 NULL。"""
    rows = sql("SELECT id, name, username, password, phone, status "
               "FROM employee WHERE id=%s;" % eid)
    if not rows:
        return None
    f = rows[0].split("\t")
    while len(f) < 6:
        f.append("")
    return {"id": f[0], "name": f[1], "username": f[2],
            "password": f[3], "phone": f[4], "status": f[5]}


def clean_test_rows():
    rows = sql("SELECT CONCAT(id,'|',username,'|',name) FROM employee "
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


def assert_unchanged(tag, before, after, fields):
    """一批『不变』断言，是编辑接口最重要的一组。"""
    for f in fields:
        check("%s『%s』未被改动" % (tag, f), before[f] == after[f],
              "编辑前=%s 编辑后=%s" % (before[f], after[f]))


def put(eid, body, token=None):
    """token 传 None 时用全局 adminToken；要特意不带 token 请传空字符串。"""
    tk = _token if token is None else token
    return call("PUT", "/admin/employee/%s" % eid, token=tk or None, body=body)


# ---------------------------------------------------------------- 前置
def setup():
    """铺一条干净的编辑目标，返回 (id, 基线快照)。"""
    sql("DELETE FROM employee WHERE username='%s';" % TARGET_USER)
    status, js = call("POST", "/admin/employee", token=_token, body={
        "name": TARGET_NAME, "username": TARGET_USER,
        "password": TARGET_PWD, "phone": TARGET_PHONE})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 前置新增失败（M2-2 是否已就绪？）: %s" % js)

    rows = sql("SELECT id FROM employee WHERE username='%s';" % TARGET_USER)
    eid = rows[0]
    base = read_by_id(eid)
    print("  目标 id=%s  基线: name=%s phone=%s status=%s password=%s...%s"
          % (eid, base["name"], base["phone"], base["status"],
             base["password"][:12], base["password"][-6:]))
    check("前置.1 基线密码是 SHA-256 摘要", base["password"] == sha256(TARGET_PWD),
          "库里=%s" % base["password"])
    check("前置.2 基线 status=1", base["status"] == "1", "实际=%s" % base["status"])
    return eid, base


# ---------------------------------------------------------------- 用例
def case_normal(eid, base):
    print("\n[D1] 正常编辑（改 name + phone）")
    st, js = put(eid, {"name": NEW_NAME, "phone": NEW_PHONE})
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("D1.1 返回 code=1", js, 1)

    after = read_by_id(eid)
    check("D1.2 name 已变为新值", after["name"] == NEW_NAME, "实际=%s" % after["name"])
    check("D1.3 phone 已变为新值", after["phone"] == NEW_PHONE, "实际=%s" % after["phone"])
    assert_unchanged("D1.4", base, after, ["password", "username", "status"])
    return after


def case_only_name(eid, base):
    print("\n[D2] 只传 name（phone 不提交，验证 NOT_NULL 策略不会把它清空）")
    st, js = put(eid, {"name": "只改名字"})
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("D2.1 返回 code=1", js, 1)

    after = read_by_id(eid)
    check("D2.2 name 已变", after["name"] == "只改名字", "实际=%s" % after["name"])
    check("D2.3 phone 保持不变（没被 null 清空）", after["phone"] == base["phone"],
          "编辑前=%s 编辑后=%s" % (base["phone"], after["phone"]))
    assert_unchanged("D2.4", base, after, ["password", "username", "status"])
    return after


def case_smuggle(eid, base):
    print("\n[D3] 请求体里夹带 password / username / status（不信任前端）")
    st, js = put(eid, {
        "name": "夹带测试",
        "password": "Hacked@999",
        "username": "m3_test_hacked",
        "status": 0,
    })
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    print("     注：这三个字段不在 EmployeeUpdateDTO 里，被 Jackson 静默忽略属预期行为。")
    expect_code("D3.1 返回 code=1", js, 1)

    after = read_by_id(eid)
    check("D3.2 name 正常生效", after["name"] == "夹带测试", "实际=%s" % after["name"])
    assert_unchanged("D3.3", base, after, ["password", "username", "status"])
    check("D3.4 密码未被改成 Hacked@999 的摘要",
          after["password"] != sha256("Hacked@999"),
          "库里=%s" % after["password"])
    return after


def case_validation(eid, base):
    print("\n[D4] name 为空 / 全空白（@NotBlank）")
    for label, body in [("缺失", {"phone": "13700003333"}),
                        ("空串", {"name": "", "phone": "13700003333"}),
                        ("空白串", {"name": "   ", "phone": "13700003333"})]:
        st, js = put(eid, body)
        print("     [%s] HTTP %s -> %s" % (label, st, json.dumps(js, ensure_ascii=False)))
        expect_code("D4.1 name %s 被拦下" % label, js, 0)

    print("\n[D5] phone 格式非法（@Pattern）")
    st, js = put(eid, {"name": "手机号错", "phone": "12345"})
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    expect_code("D5.1 返回 code=0", js, 0)
    check("D5.2 提示『手机号格式不正确』", "手机号格式不正确" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))

    print("\n[D6] 校验失败的请求不得产生任何写入")
    after = read_by_id(eid)
    check("D6.1 name 未被改成『手机号错』", after["name"] != "手机号错",
          "实际=%s" % after["name"])
    check("D6.2 phone 未被改成非法值", after["phone"] != "12345",
          "实际=%s" % after["phone"])
    return after


def case_not_found(base):
    print("\n[D7] id 不存在")
    mx = sql("SELECT COALESCE(MAX(id),0)+1000 FROM employee;")[0]
    st, js = put(mx, {"name": "不存在的人"})
    print("     PUT /admin/employee/%s -> HTTP %s %s"
          % (mx, st, json.dumps(js, ensure_ascii=False)))
    expect_code("D7.1 返回 code=0（不能假成功）", js, 0)
    check("D7.2 提示含『员工不存在』", "员工不存在" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))


def case_bad_path():
    print("\n[D8] id 非数字（观察型：看这条路径长什么样）")
    st, js = call("PUT", "/admin/employee/abc", token=_token, body={"name": "x"})
    print("     PUT /admin/employee/abc -> HTTP %s %s"
          % (st, json.dumps(js, ensure_ascii=False)))
    check("D8.1 未把 500 抛给前端（有统一错误返回）", js.get("code") == 0,
          "实际 %s" % js)
    print("     备注：@PathVariable Long 收到 'abc' 会抛 NumberFormatException，"
          "落兜底 handler → 文案是『系统繁忙』而不是『参数格式错误』。记为待改项。")


def case_auth():
    print("\n[D9] 未带 token")
    st, js = put(1, {"name": "无令牌"}, token="")
    print("     HTTP %s -> %s" % (st, json.dumps(js, ensure_ascii=False)))
    check("D9.1 返回 HTTP 401", st == 401, "实际 HTTP %s" % st)
    expect_code("D9.2 body code=0", js, 0)


def case_login_still_works():
    print("\n[D10] 端到端：编辑折腾了这么多轮，原密码还能登录吗")
    st, js = call("POST", "/admin/employee/login",
                  body={"username": TARGET_USER, "password": TARGET_PWD})
    check("D10.1 原密码仍可登录（密码确实没被动过）", js.get("code") == 1,
          "HTTP %s %s" % (st, js))
    st2, js2 = call("POST", "/admin/employee/login",
                    body={"username": TARGET_USER, "password": "Hacked@999"})
    check("D10.2 夹带进来的密码不能登录", js2.get("code") != 1,
          "竟然登录成功了！HTTP %s %s" % (st2, js2))


# ---------------------------------------------------------------- 变异自检
def selftest():
    """数据层变异：把『不该动的字段』逐个改坏，确认那四条不变断言真的会红。"""
    print("=" * 74)
    print("变异自检：把『不该动的字段』改坏，确认『不变』断言有判别力")
    print("=" * 74)
    eid, base = setup()

    print("\n[变异 1] UPDATE password='明文'（模拟更新时把密码覆盖成明文）")
    sql("UPDATE employee SET password='%s' WHERE id=%s;" % (TARGET_PWD, eid))
    got = read_by_id(eid)["password"]
    print("        库里现在是: %s" % got)
    check("变异生效：值确实变成了明文", got == TARGET_PWD)
    check("变异1 → D1.4『password 未被改动』应变红", got != base["password"])

    print("\n[变异 2] UPDATE username='m3_test_hacked'（模拟更新时改了登录账号）")
    sql("UPDATE employee SET username='%s' WHERE id=%s;" % (TEST_PREFIX + "hacked", eid))
    got = read_by_id(eid)["username"]
    check("变异生效：username 确实变了", got == TEST_PREFIX + "hacked")
    check("变异2 → D1.4『username 未被改动』应变红", got != base["username"])

    print("\n[变异 3] UPDATE status=0（模拟编辑顺带把人禁用了）")
    sql("UPDATE employee SET status=0 WHERE id=%s;" % eid)
    got = read_by_id(eid)["status"]
    check("变异生效：status 确实变成 0", got == "0")
    check("变异3 → D1.4『status 未被改动』应变红", got != base["status"])

    print("\n[变异 4] UPDATE phone=NULL（模拟更新把它清空了）")
    sql("UPDATE employee SET phone=NULL WHERE id=%s;" % eid)
    got = read_by_id(eid)["phone"]
    check("变异生效：phone 确实变成 NULL", got == "NULL")
    check("变异4 → D2.3『phone 保持不变』应变红", got != base["phone"])

    print("\n[前提 5] 确认 EmployeeUpdateDTO 里没有 password/username/status 字段")
    src = (SRC / "com/mall/pojo/dto/EmployeeUpdateDTO.java").read_text(encoding="utf-8")
    for f in ("password", "username", "status"):
        check("前提5 → DTO 中不存在字段 %s" % f, ("private String %s;" % f) not in src
              and ("private Integer %s;" % f) not in src)

    print("\n清理自检残留")
    clean_test_rows()


# ---------------------------------------------------------------- main
def main():
    global _token

    if "--check" in sys.argv:
        rows = sql("SELECT id, name, username, phone, status FROM employee "
                   "WHERE username LIKE '%s%%';" % TEST_PREFIX)
        print("=== m3_test_ 目标行现状（%d 行）===" % len(rows))
        for r in rows:
            print("  " + r)
        if not rows:
            print("  (无) 用默认模式跑一次即可创建")
        return

    if "--clean" in sys.argv:
        print("=== 清理测试数据 ===")
        print("  已清理 %d 行（前缀 %s*）" % (clean_test_rows(), TEST_PREFIX))
        return

    _token = admin_login()
    print("  adminToken = %s..." % _token[:24])

    if "--selftest" in sys.argv:
        selftest()
        print("\n" + "=" * 74)
        print("自检结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
        print("全绿 → 说明这四条『不变』断言确实有判别力。")
        print("=" * 74)
        sys.exit(1 if _failed else 0)

    print("=" * 74)
    print("M2-3 编辑员工 联调验收")
    print("目标: %s  |  端点: PUT /admin/employee/{id}" % BASE)
    print("=" * 74)

    print("\n[准备] 清理上一轮测试数据")
    print("  已清理 %d 行（前缀 %s*）" % (clean_test_rows(), TEST_PREFIX))
    print("\n[准备] 铺编辑目标")
    eid, base = setup()

    after1 = case_normal(eid, base)
    after2 = case_only_name(eid, after1)
    after3 = case_smuggle(eid, after2)
    case_validation(eid, after3)
    case_not_found(base)
    case_bad_path()
    case_auth()
    case_login_still_works()

    print("\n" + "=" * 74)
    print("结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
    if _failed:
        print("失败项：")
        for f in _failed:
            print("  - %s" % f)
    print("=" * 74)
    print("最终目标行：%s" % read_by_id(eid))
    print("复查：python m2_update_verify.py --check")
    print("清理：python m2_update_verify.py --clean")
    print("自检：python m2_update_verify.py --selftest")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
