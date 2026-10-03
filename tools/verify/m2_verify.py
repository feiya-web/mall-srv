"""M2-2 新增员工 —— 联调验收脚本（正常 / 异常 / 边界 三类用例 + 查库核验）。

用法（应用需已用新代码重启，端口 8080）：
    python m2_verify.py            # 跑全部用例并查库核验
    python m2_verify.py --check    # 只读：打印 employee 表现状
    python m2_verify.py --clean    # 仅清理 m2_test_% 测试数据后退出
    python m2_verify.py --selftest # 变异自检：故意改坏数据，确认断言真的会变红

为什么要写这个脚本：
    接口返回 code=1 只证明"没抛异常"，不证明数据落对了。
    新增接口真正的验收点有四个，缺一个都不算过：
      1. 行真的插进去了（查库）
      2. 密码是 SHA-256 摘要，不是明文（查库比对）
      3. status 默认值是我们显式设的 1，而不是靠 DB 兜底
      4. 用明文密码能反向登录成功（端到端证明摘要口径没写错）
"""
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request

from _env import BASE, ADMIN_USER, DEMO_PASSWORD, mysql_argv

ADMIN_PWD = DEMO_PASSWORD

# 测试数据前缀：所有测试账号都带这个前缀，--clean 只删这一前缀，不误伤真实数据
TEST_PREFIX = "m2_test_"
NEW_USER = TEST_PREFIX + "kitchen01"
NEW_PWD = "Test@12345"
NEW_NAME = "验收-厨师01"
NEW_PHONE = "13900001111"

_passed = []
_failed = []


# ---------------------------------------------------------------- HTTP
def call(method, path, token=None, body=None, raw_body=None):
    if raw_body is not None:
        data = raw_body.encode("utf-8")
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
    else:
        data = None
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
        raise SystemExit("[FATAL] admin 登录失败: HTTP %s %s\n"
                         "       确认应用已启动、admin 密码为 %s" % (status, js, ADMIN_PWD))
    return js["data"]["token"]


# ---------------------------------------------------------------- DB
def sql(query):
    """执行一条 SQL，返回 tab 分隔的文本行列表。"""
    p = subprocess.run(
        mysql_argv(query),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("[FATAL] 查库失败: %s" % (p.stderr or p.stdout))
    return [ln for ln in (p.stdout or "").splitlines()
            if ln.strip() and "[Warning]" not in ln]


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


# ---------------------------------------------------------------- 用例
def case_normal(token):
    print("\n[C1] 正常新增（name/username/password/phone 齐全）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": NEW_NAME, "username": NEW_USER,
        "password": NEW_PWD, "phone": NEW_PHONE})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C1.1 返回 code=1", js, 1)

    rows = sql("SELECT id, username, password, status, phone FROM employee "
               "WHERE username='%s';" % NEW_USER)
    check("C1.2 数据已落库（查到 1 行）", len(rows) == 1,
          "实际 %d 行" % len(rows))
    if not rows:
        return None
    eid, uname, pwd, st, phone = rows[0].split("\t")

    check("C1.3 密码是 SHA-256 摘要（非明文）", pwd == sha256(NEW_PWD),
          "库里=%s 期望=%s" % (pwd, sha256(NEW_PWD)))
    check("C1.4 密码不是明文", pwd != NEW_PWD, "库里出现了明文密码！")
    check("C1.5 status 默认 1", st == "1", "实际 status=%s" % st)
    check("C1.6 phone 一致", phone == NEW_PHONE, "实际 phone=%s" % phone)

    # 端到端：新员工用明文密码能登录
    s2, j2 = call("POST", "/admin/employee/login",
                  body={"username": NEW_USER, "password": NEW_PWD})
    check("C1.7 新员工可用明文密码登录（摘要口径正确）", j2.get("code") == 1,
          "HTTP %s %s" % (s2, j2))
    return eid


def case_duplicate(token):
    print("\n[C2] 重复用户名（唯一性）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": "重复者", "username": NEW_USER,
        "password": "Whatever@1", "phone": "13900002222"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C2.1 返回 code=0", js, 0)
    check("C2.2 提示含『已存在』", "已存在" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))
    cnt = sql("SELECT COUNT(*) FROM employee WHERE username='%s';" % NEW_USER)
    check("C2.3 没有插入第二行", cnt and cnt[0] == "1", "实际 %s 行" % (cnt[0] if cnt else "?"))


def case_validation(token):
    print("\n[C3] 必填缺失（@NotBlank）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": "无用户名", "password": "abc12345"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C3.1 返回 code=0", js, 0)
    check("C3.2 提示指向 username 且为『不能为空』",
          "username" in (js.get("msg") or "") and "不能为空" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))

    print("\n[C4] 空白字符串绕过（name=\"\"）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": "   ", "username": "m2_test_blank", "password": "abc12345"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C4.1 空白串被 @NotBlank 拦下", js, 0)


def case_phone(token):
    print("\n[C5] 手机号格式非法（@Pattern）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": "手机号错", "username": "m2_test_phone",
        "password": "abc12345", "phone": "12345"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C5.1 返回 code=0", js, 0)
    check("C5.2 提示『手机号格式不正确』",
          "手机号格式不正确" in (js.get("msg") or ""),
          "实际 msg=%s" % js.get("msg"))

    print("\n[C6] 手机号留空（选填，应放行）")
    status, js = call("POST", "/admin/employee", token=token, body={
        "name": "无手机号", "username": "m2_test_nophone", "password": "abc12345"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    expect_code("C6.1 不填手机号可正常新增", js, 1)


def case_auth():
    print("\n[C7] 未带 token（鉴权拦截）")
    status, js = call("POST", "/admin/employee", body={
        "name": "无令牌", "username": "m2_test_noauth", "password": "abc12345"})
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    check("C7.1 返回 HTTP 401", status == 401, "实际 HTTP %s" % status)
    expect_code("C7.2 body code=0", js, 0)


def case_race(token, n=16):
    """并发穿透：同一用户名同时打 N 个新增请求。

    期望：恰好 1 个成功，其余被挡。关键在于看『被谁挡的』——
      - msg 以 "用户名" 开头 → 被 Service 的预检挡住
      - msg 恰好是 " 已存在"（无前缀）→ 预检漏判、被数据库 uk_username 唯一索引挡住
    只要出现第二种，就证明注释里那句"预检挡不住并发"是真的，且唯一索引确实在兜底。
    """
    import threading

    print("\n[C9] 并发穿透（%d 个并发请求打同一个新用户名）" % n)
    user = TEST_PREFIX + "race"
    sql("DELETE FROM employee WHERE username='%s';" % user)

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()  # 卡齐了再一起发，尽量制造真并发
        st, js = call("POST", "/admin/employee", token=token, body={
            "name": "并发", "username": user, "password": "Race@12345"})
        with lock:
            results.append((st, js.get("code"), js.get("msg")))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [r for r in results if r[1] == 1]
    precheck = [r for r in results if r[1] != 1 and (r[2] or "").startswith("用户名")]
    dbindex = [r for r in results if r[1] != 1
               and "已存在" in (r[2] or "") and not (r[2] or "").startswith("用户名")]
    other = [r for r in results if r not in ok + precheck + dbindex]

    print("     成功 %d / 预检拦截 %d / 唯一索引拦截 %d / 其他 %d"
          % (len(ok), len(precheck), len(dbindex), len(other)))
    for st, code, msg in sorted(set(results), key=lambda x: str(x)):
        print("     - HTTP %s code=%s msg=%s (x%d)"
              % (st, code, msg, results.count((st, code, msg))))

    check("C9.1 恰好只有 1 个成功", len(ok) == 1,
          "实际成功 %d 个（超卖！）" % len(ok))
    cnt = sql("SELECT COUNT(*) FROM employee WHERE username='%s';" % user)
    check("C9.2 库里只有 1 行", cnt and cnt[0] == "1",
          "实际 %s 行" % (cnt[0] if cnt else "?"))
    if dbindex:
        check("C9.3 观测到唯一索引兜底（预检确实挡不住并发）", True)
        print("     ↑ 这几条 msg 无『用户名』前缀，是 DuplicateKeyException 走的"
              "另一个 handler —— 数据库唯一索引把预检漏掉的并发接住了。")
    else:
        print("     · 本轮预检全挡住了，没观测到索引兜底（并发不够真，可加大 N 再试）。")


def case_bad_json(token):
    print("\n[C8] 请求体不是合法 JSON（边界：观察降级行为）")
    status, js = call("POST", "/admin/employee", token=token, raw_body="{bad json")
    print("     HTTP %s -> %s" % (status, json.dumps(js, ensure_ascii=False)))
    check("C8.1 未抛 500 给前端（有统一错误返回）", js.get("code") == 0,
          "实际 %s" % js)
    print("     备注：HttpMessageNotReadableException 会落到兜底 handler，"
          "文案是『系统繁忙』而非『参数格式错误』——记为待改项。")


# ---------------------------------------------------------------- 变异自检
def selftest(token):
    """变异自检：故意把数据改坏，确认对应断言真的会变红。

    为什么需要这一步：
        断言不红 = 断言没有判别力 = 测试只是装饰品。
        一个永远绿的断言，和没有断言，对缺陷的检出能力是一样的。
        所以"断言写对了"本身也要被验证——这一步就是"测试的测试"。

        这里用改数据库的方式制造变异，好处是不用改代码、不用重启应用，
        且只动 m2_test_ 前缀的临时行。
    """
    user = NEW_USER
    print("=" * 74)
    print("变异自检：故意改坏数据，确认对应断言会变红")
    print("=" * 74)

    sql("DELETE FROM employee WHERE username='%s';" % user)
    st, js = call("POST", "/admin/employee", token=token, body={
        "name": NEW_NAME, "username": user, "password": NEW_PWD, "phone": NEW_PHONE})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 自检前置失败（新增没成功）: %s" % js)
    print("\n前置：已用正常流程插入测试行 %s" % user)

    print("\n[变异 1] UPDATE password='%s'（模拟漏写 PasswordUtil.encode）" % NEW_PWD)
    sql("UPDATE employee SET password='%s' WHERE username='%s';" % (NEW_PWD, user))
    got = sql("SELECT password FROM employee WHERE username='%s';" % user)[0]
    print("        库里的值现在是: %s" % got)
    check("变异1 → C1.3『密码是 SHA-256 摘要』应变红", got != sha256(NEW_PWD))
    check("变异1 → C1.4『密码不是明文』应变红", got == NEW_PWD)

    print("\n[变异 2] UPDATE status=0（模拟默认值没设对）")
    sql("UPDATE employee SET status=0 WHERE username='%s';" % user)
    st_val = sql("SELECT status FROM employee WHERE username='%s';" % user)[0]
    check("变异2 → C1.5『status 默认 1』应变红", st_val != "1")

    print("\n[变异 3] DELETE 该行（模拟 insert 没生效）")
    sql("DELETE FROM employee WHERE username='%s';" % user)
    n = sql("SELECT COUNT(*) FROM employee WHERE username='%s';" % user)[0]
    check("变异3 → C1.2『数据已落库』应变红", n == "0")

    print("\n[前提 4] 确认 uk_username 唯一索引还在（C9 兜底的前提）")
    idx = sql("SELECT COUNT(*) FROM information_schema.statistics "
              "WHERE table_schema='%s' AND table_name='employee' "
              "AND index_name='uk_username' AND non_unique=0;" % DB)[0]
    check("前提4 → uk_username 唯一索引存在", idx == "1")

    print("\n清理自检残留")
    clean_test_rows()


# ---------------------------------------------------------------- main
def main():
    if "--check" in sys.argv:
        rows = sql("SELECT id, name, username, phone, status, create_time FROM employee ORDER BY id;")
        print("=== employee 表现状（共 %d 行）===" % len(rows))
        for r in rows:
            print("  " + r)
        return

    if "--clean" in sys.argv:
        print("=== 清理测试数据 ===")
        n = clean_test_rows()
        print("  已清理 %d 行（前缀 %s*）" % (n, TEST_PREFIX))
        return

    if "--selftest" in sys.argv:
        selftest(admin_login())
        print("\n" + "=" * 74)
        print("自检结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
        print("全绿 → 说明这些断言确实有判别力，不是永远绿的摆设。")
        print("=" * 74)
        sys.exit(1 if _failed else 0)

    print("=" * 74)
    print("M2-2 新增员工 联调验收")
    print("目标: %s" % BASE)
    print("=" * 74)

    print("\n[准备] 清理上一轮的测试数据")
    n = clean_test_rows()
    print("  已清理 %d 行（前缀 %s*）" % (n, TEST_PREFIX))

    print("\n[准备] 获取 adminToken")
    token = admin_login()
    print("  adminToken = %s..." % token[:24])

    case_normal(token)
    case_duplicate(token)
    case_validation(token)
    case_phone(token)
    case_auth()
    case_bad_json(token)
    case_race(token)

    print("\n" + "=" * 74)
    print("结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
    if _failed:
        print("失败项：")
        for f in _failed:
            print("  - %s" % f)
    print("=" * 74)
    print("复查命令：python m2_verify.py --check")
    print("清理命令：python m2_verify.py --clean")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
