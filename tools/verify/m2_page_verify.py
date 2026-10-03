"""M2-1 分页查询 —— 联调验收脚本（BUG-20260923-001 的回归脚本）。

用法（应用需已用新代码重启，端口 8080）：
    python m2_page_verify.py            # 跑全部用例（自动造数据 + 收尾清理）
    python m2_page_verify.py --check    # 只读：打印 employee 表现状
    python m2_page_verify.py --clean    # 仅清理 m2_test_% 测试数据后退出
    python m2_page_verify.py --selftest # 变异自检：故意改坏数据，确认断言真的会变红

为什么要写这个脚本：
    分页的缺陷特征是"编译通过、接口返回 code=1、看起来一切正常"。
    ?pageSize=-1 返回全表、total=0 这两件事在**数据量少的时候根本看不出来**
    （本项目 employee 表当时只有 1 行 admin，所以手工探针时很容易漏判）。
    所以这个脚本先造 25 行数据再验，并且**断言分三层**：
      第 1 层  返回值（records 条数 / total）
      第 2 层  查库交叉核验（total 是否等于 DB 真实行数）
      第 3 层  SQL 日志证据（LIMIT 有没有拼上、COUNT 有没有发出）
    只做第 1 层 = 自欺欺人。

第三条设计：**已知缺陷登记为 XFAIL，不混进 FAIL。**
    C7 那两条（page=abc 被吞成 200"系统繁忙"）的根因在全局异常处理器，
    归 M4 的账。如果和真故障放同一个桶里，CI 就永远红着，
    真正的回归失败会被这堆已知噪音淹没 —— 这正是缺陷①本身造成的后果。
    XFAIL 会打印、会计数，但**不影响退出码**；哪天它变绿了会打印 XPASS 提醒你转正。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from _env import BASE, ADMIN_USER, DEMO_PASSWORD, LOG, mysql_argv

ADMIN_PWD = DEMO_PASSWORD

# 测试数据前缀：--clean 只删这一前缀，绝不碰真实数据。
# 注意这里用的是 m2_testpg_ 而不是 m2_test_ —— 后者是 m2_verify.py 的前缀，
# 两边共用会导致 A 脚本的 --clean 把 B 脚本的数据删掉（本来就不会有人发现，
# 直到某次 CI 里两个脚本并行跑，total 断言无缘无故变少）。
# 每个脚本必须有自己的前缀，这是并行安全的前提。
TEST_PREFIX = "m2_testpg_"
N_ROWS = 25          # 造 25 行：> 默认页大小 10，才能让"夹紧"和"全表"区分开
DEFAULT_SIZE = 10    # 与 EmployeeService.DEFAULT_PAGE_SIZE 对应
MAX_SIZE = 100       # 与 EmployeeService.MAX_PAGE_SIZE 对应

_passed = []
_failed = []
_known = []          # 已知缺陷（XFAIL）：登记在案，不计入退出码，但必须打印出来


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
        raw = exc.read().decode("utf-8", "replace")
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


def page(token, **params):
    """GET /admin/employee/page?<params>"""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    status, js = call("GET", "/admin/employee/page" + ("?" + qs if qs else ""),
                      token=token)
    return status, js


def page_raw(token, qs):
    """原样拼查询串（用于 abc 这种非法字面量，urlencode 会帮倒忙）"""
    status, js = call("GET", "/admin/employee/page?" + qs, token=token)
    return status, js


# ---------------------------------------------------------------- DB
def sql(query):
    p = subprocess.run(
        mysql_argv(query),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise SystemExit("[FATAL] 查库失败: %s" % (p.stderr or p.stdout))
    return [ln for ln in (p.stdout or "").splitlines()
            if ln.strip() and "[Warning]" not in ln]


def db_count(where=""):
    rows = sql("SELECT COUNT(*) FROM employee %s;" % where)
    return int(rows[0]) if rows else 0


def setup_rows(n=N_ROWS):
    """造 n 行测试数据，status 奇偶交替（1 启用 / 0 禁用），便于验条件筛选。"""
    sql("DELETE FROM employee WHERE username LIKE '%s%%';" % TEST_PREFIX)
    values = []
    for i in range(1, n + 1):
        values.append("('%s%02d','%s%02d',SHA2('123456',256),'1390000%04d',%d)"
                      % ("分页测试", i, TEST_PREFIX, i, i, 1 if i % 2 else 0))
    sql("INSERT INTO employee (name, username, password, phone, status) VALUES "
        + ",".join(values) + ";")


def clean_rows():
    rows = sql("SELECT COUNT(*) FROM employee WHERE username LIKE '%s%%';" % TEST_PREFIX)
    sql("DELETE FROM employee WHERE username LIKE '%s%%';" % TEST_PREFIX)
    return int(rows[0]) if rows else 0


# ---------------------------------------------------------------- SQL 日志证据
def log_delta(fn):
    """执行 fn()，返回本次新增的日志文本（第 3 层证据）。"""
    before = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    result = fn()
    time.sleep(0.35)
    with open(LOG, "rb") as f:
        f.seek(before)
        return result, f.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- 断言
def check(name, cond, detail=""):
    if cond:
        _passed.append(name)
        print("  [PASS] %s" % name)
    else:
        _failed.append(name)
        print("  [FAIL] %s  %s" % (name, detail))
    return bool(cond)


def records_of(js):
    d = js.get("data") or {}
    return d.get("records") if isinstance(d.get("records"), list) else []


def xfail(name, cond, detail="", reason=""):
    """已知缺陷登记：断言今天会红，但那是别的模块的账，不该让本脚本的退出码失真。

    与 check() 的区别只在于**是否计入退出码**：
      - 仍然是红的 -> [XFAIL]，打印出来 + 计数，绝不静默忽略
      - 竟然绿了   -> [XPASS]，说明上游把缺陷修好了，该把这条**转成正式断言**
    设计理由：把"已知缺陷"和"真故障"混在同一个 FAIL 桶里，真故障就会像
    缺陷① 描述的那样被噪音淹没。CI 只该对"意外"报警，不该对"已知"报警。
    """
    if cond:
        _passed.append(name)
        print("  [XPASS] %s  <- 已知缺陷已修！请把这条转成正式断言" % name)
    else:
        _known.append(name)
        print("  [XFAIL] %s  (%s)" % (name, reason or detail))
    return bool(cond)


def ids_of(js):
    return [r.get("id") for r in records_of(js)]


# ---------------------------------------------------------------- 用例
def case_basic(token):
    print("\n[C1] 基本分页 + 脱敏 + SQL 证据")
    total_rows = db_count()
    (status, js), delta = log_delta(lambda: page(token, page=1, pageSize=10))
    print("     HTTP %s  total=%s  records=%d" % (
        status, (js.get("data") or {}).get("total"), len(records_of(js))))

    check("C1.1 code=1", js.get("code") == 1, "实际 %s" % js.get("code"))
    check("C1.2 records 恰好 10 条", len(records_of(js)) == 10,
          "实际 %d 条" % len(records_of(js)))
    check("C1.3 total == DB 真实行数(%d)" % total_rows,
          (js.get("data") or {}).get("total") == total_rows,
          "接口 total=%s" % (js.get("data") or {}).get("total"))

    recs = records_of(js)
    check("C1.4 列表已脱敏（password 全为 null）",
          all(r.get("password") is None for r in recs),
          "有 %d 条带 password" % sum(1 for r in recs if r.get("password") is not None))
    # 反向半边：库里 password 必须非空，否则 C1.4 是空断言（本来就没值可脱）
    db_pwd_nonempty = sql("SELECT COUNT(*) FROM employee WHERE username LIKE '%s%%' "
                          "AND password <> '';" % TEST_PREFIX)
    check("C1.5 库里 password 非空（证明 C1.4 不是空断言）",
          int(db_pwd_nonempty[0]) == N_ROWS,
          "非空 %s / 期望 %d" % (db_pwd_nonempty[0], N_ROWS))
    check("C1.6 按 id 倒序",
          ids_of(js) == sorted(ids_of(js), reverse=True), "%s" % ids_of(js))

    check("C1.7 SQL 里有 LIMIT（第 3 层证据）", "LIMIT" in delta,
          "本次日志未见 LIMIT")
    check("C1.8 SQL 里有 COUNT(*)（第 3 层证据）", "COUNT(*)" in delta,
          "本次日志未见 COUNT(*)")


def case_normalize(token):
    print("\n[C2] 分页参数归一（BUG-20260923-001 的核心）")
    total_rows = db_count()

    status, js = page(token, page=1, pageSize=-1)
    n = len(records_of(js))
    t = (js.get("data") or {}).get("total")
    print("     pageSize=-1 -> records=%d total=%s" % (n, t))
    check("C2.1 pageSize=-1 被夹成默认 10 条", n == DEFAULT_SIZE,
          "实际 %d 条" % n)
    check("C2.2 pageSize=-1 时 total > 0（COUNT 没被跳过）",
          isinstance(t, int) and t > 0, "actual total=%s" % t)
    check("C2.3 pageSize=-1 不等于全表 %d 行" % total_rows, n != total_rows,
          "实际返回了全表 %d 行" % n)

    # 第 3 层：夹紧之后 LIMIT 必须还在 SQL 里
    (status, js2), delta = log_delta(lambda: page(token, page=1, pageSize=-1))
    check("C2.4 pageSize=-1 的 SQL 里仍有 LIMIT", "LIMIT" in delta,
          "LIMIT 消失了")
    check("C2.5 pageSize=-1 的 SQL 里仍有 COUNT(*)", "COUNT(*)" in delta,
          "COUNT(*) 被跳过了")

    _, js3 = page(token, page=1, pageSize=0)
    check("C2.6 pageSize=0 -> 默认 10 条", len(records_of(js3)) == DEFAULT_SIZE,
          "实际 %d 条" % len(records_of(js3)))

    _, js4 = page(token, page=1, pageSize=1000000)
    size_field = (js4.get("data") or {}).get("size")
    check("C2.7 pageSize=1000000 被夹到上限 %d（看 size 字段）" % MAX_SIZE,
          size_field == MAX_SIZE, "size=%s" % size_field)
    check("C2.8 pageSize=1000000 实际返回条数 <= %d" % MAX_SIZE,
          len(records_of(js4)) <= MAX_SIZE, "实际 %d 条" % len(records_of(js4)))

    _, p1 = page(token, page=1, pageSize=10)
    _, p0 = page(token, page=0, pageSize=10)
    check("C2.9 page=0 等价于 page=1", ids_of(p0) == ids_of(p1),
          "page=0 -> %s" % ids_of(p0))
    _, pn = page(token, page=-1, pageSize=10)
    check("C2.10 page=-1 等价于 page=1", ids_of(pn) == ids_of(p1),
          "page=-1 -> %s" % ids_of(pn))


def case_paging(token):
    print("\n[C3] total 正确性 + 翻页不重不漏")
    total_rows = db_count()
    _, pg1 = page(token, page=1, pageSize=10)
    _, pg2 = page(token, page=2, pageSize=10)
    _, pg3 = page(token, page=3, pageSize=10)
    _, pg99 = page(token, page=99, pageSize=10)

    check("C3.1 末页(page=3) 恰好 %d 条" % (total_rows - 20),
          len(records_of(pg3)) == total_rows - 20,
          "实际 %d 条" % len(records_of(pg3)))
    check("C3.2 第 1 页与第 2 页 id 不重叠",
          not (set(ids_of(pg1)) & set(ids_of(pg2))),
          "交集 %s" % (set(ids_of(pg1)) & set(ids_of(pg2))))
    check("C3.3 超出末页 page=99 -> 0 条但 total 仍正确",
          len(records_of(pg99)) == 0
          and (pg99.get("data") or {}).get("total") == total_rows,
          "records=%d total=%s" % (len(records_of(pg99)),
                                   (pg99.get("data") or {}).get("total")))


def case_filter(token):
    print("\n[C4] 条件筛选")
    n_enable = db_count("WHERE status=1")
    n_disable = db_count("WHERE status=0")

    _, e = page(token, status=1, pageSize=100)
    check("C4.1 status=1 -> total == DB(%d)" % n_enable,
          (e.get("data") or {}).get("total") == n_enable,
          "接口 total=%s" % (e.get("data") or {}).get("total"))
    check("C4.2 status=1 -> 返回行全部 status=1",
          all(r.get("status") == 1 for r in records_of(e)),
          "%s" % [r.get("status") for r in records_of(e)])

    _, d = page(token, status=0, pageSize=100)
    check("C4.3 status=0 -> total == DB(%d)" % n_disable,
          (d.get("data") or {}).get("total") == n_disable,
          "接口 total=%s" % (d.get("data") or {}).get("total"))

    _, n1 = page(token, name="分页测试0", pageSize=100)
    got = records_of(n1)
    check("C4.4 name 模糊命中且都含该串",
          len(got) > 0 and all("分页测试0" in (r.get("name") or "") for r in got),
          "命中 %d 条" % len(got))

    _, n2 = page(token, name="", pageSize=100)
    check("C4.5 name 空串不参与过滤（total 不变）",
          (n2.get("data") or {}).get("total") == db_count(),
          "接口 total=%s" % (n2.get("data") or {}).get("total"))


def case_status_whitelist(token):
    print("\n[C5] status 值域白名单（读接口与写接口口径必须一致）")
    total_rows = db_count()
    _, js = page(token, status=5, pageSize=10)
    print("     status=5 -> HTTP? code=%s msg=%s total=%s"
          % (js.get("code"), js.get("msg"), (js.get("data") or {}).get("total")))
    check("C5.1 status=5 被拒绝（code=0 且 msg 指明状态不合法）",
          js.get("code") == 0 and "状态值不合法" in (js.get("msg") or ""),
          "实际 code=%s msg=%s" % (js.get("code"), js.get("msg")))
    check("C5.2 status=5 不得静默返回数据（records 为空）",
          len(records_of(js)) == 0, "返回了 %d 条" % len(records_of(js)))
    check("C5.3 status=5 不得静默返回 total=0 的'假成功'",
          not (js.get("code") == 1 and (js.get("data") or {}).get("total") == 0),
          "被当成正常空结果了（total=0, code=1）—— 静默失效")

    _, js2 = page(token, status=-1, pageSize=10)
    check("C5.4 status=-1 同样被拒绝",
          js2.get("code") == 0 and "状态值不合法" in (js2.get("msg") or ""),
          "实际 code=%s msg=%s" % (js2.get("code"), js2.get("msg")))


def case_auth():
    print("\n[C6] 鉴权")
    s1, j1 = page(None, page=1, pageSize=10)
    check("C6.1 无 token -> 401", s1 == 401, "HTTP %s" % s1)
    s2, j2 = page("bogus.token.value", page=1, pageSize=10)
    check("C6.2 伪造 token -> 401", s2 == 401, "HTTP %s" % s2)


def case_bad_input(token):
    print("\n[C7] 边界输入（观察型：目的不是通过，是把缺陷暴露出来）")
    print("     注：下面两条的根因是全局异常处理器（缺陷①），归 M4 的账，本脚本登记为 XFAIL")
    s1, j1 = page_raw(token, "page=abc&pageSize=10")
    print("     page=abc -> HTTP %s code=%s msg=%s" % (s1, j1.get("code"), j1.get("msg")))
    xfail("C7.1 page=abc 应被当作参数错误（而不是'系统繁忙'）",
          j1.get("code") == 0 and "系统繁忙" not in (j1.get("msg") or ""),
          detail="msg=%s" % j1.get("msg"),
          reason="缺陷① 全局异常处理器把 MethodArgumentTypeMismatchException 吞成 200 —— 归 M4")

    s2, j2 = page_raw(token, "pageSize=abc")
    print("     pageSize=abc -> HTTP %s code=%s msg=%s" % (s2, j2.get("code"), j2.get("msg")))
    xfail("C7.2 pageSize=abc 应被当作参数错误",
          j2.get("code") == 0 and "系统繁忙" not in (j2.get("msg") or ""),
          detail="msg=%s" % j2.get("msg"),
          reason="缺陷① 全局异常处理器 —— 归 M4")


# ---------------------------------------------------------------- 变异自检
def selftest(token):
    print("\n" + "=" * 68)
    print("[SELFTEST] 变异自检：改坏数据，确认主断言真的会变红")
    print("  原则：断言不红 = 断言没有判别力 = 测试是装饰品")
    print("=" * 68)

    # --- M1：把测试行降到 3 行（总行数 4 < 默认页大小）---
    # 目的：证明 C2.1「pageSize=-1 -> 10 条」不是"数据太少所以碰巧成立"的空断言。
    print("\n[M1] 测试行降到 3 行（总行数 4 < 默认页大小 %d）" % DEFAULT_SIZE)
    setup_rows(3)
    _, js = page(token, page=1, pageSize=-1)
    n = len(records_of(js))
    check("M1.1 变异后只返回 %d 条 < %d -> 主断言 C2.1 会因此变红"
          % (n, DEFAULT_SIZE), n < DEFAULT_SIZE, "实际 %d 条" % n)
    print("       ^ 说明 C2.1 成立的条件是「测试数据 > 页大小」；")
    print("         数据不足时它会 vacuous pass —— 所以本脚本坚持造 %d 行。" % N_ROWS)

    # --- M2：把 password 置空串 ---
    # 目的：证明 C1.5「库里 password 非空」这半条断言有效，C1.4 不是空断言。
    print("\n[M2] 把测试行 password 改成空串后，C1.5 应变红")
    setup_rows(N_ROWS)
    sql("UPDATE employee SET password='' WHERE username LIKE '%s%%';"
        % TEST_PREFIX)
    left = int(sql("SELECT COUNT(*) FROM employee WHERE username LIKE '%s%%' "
                   "AND password <> '';" % TEST_PREFIX)[0])
    check("M2.1 变异生效：库里 password 已全为空串", left == 0,
          "仍非空 %d 行" % left)
    check("M2.2 C1.5 的判据变成 %d != %d -> 会变红（判别力成立）"
          % (left, N_ROWS), left != N_ROWS, "断言没红 —— 没有判别力")

    # --- M3：删掉一行，total 必须跟着变 ---
    # 目的：证明 C1.3「total == DB 行数」不是拿接口的数去比接口自己。
    print("\n[M3] 删掉 1 行后，total 必须随之减小（证明 total 真的来自 DB）")
    setup_rows(N_ROWS)
    _, a = page(token, page=1, pageSize=10)
    ta = (a.get("data") or {}).get("total")
    sql("DELETE FROM employee WHERE id=(SELECT * FROM (SELECT id FROM employee "
        "WHERE username LIKE '%s%%' ORDER BY id LIMIT 1) t);" % TEST_PREFIX)
    _, b = page(token, page=1, pageSize=10)
    tb = (b.get("data") or {}).get("total")
    check("M3.1 删 1 行后 total 减 1（%s -> %s）" % (ta, tb),
          isinstance(ta, int) and isinstance(tb, int) and tb == ta - 1,
          "total 没跟着变 —— 可能不是从 DB 取的")

    # --- M4：断言框架本身 ---
    print("\n[M4] 断言框架自检：故意给一个必然为假的断言，应当变红")
    before = len(_failed)
    check("M4.1 这条断言必然为假（若显示 PASS 则框架失效）", 1 == 2)
    ok = len(_failed) == before + 1
    _failed.pop()   # 撤掉这条故意为假的断言，不污染最终计分
    check("M4.2 框架正确把它记为失败（框架可用）", ok, "框架没登记失败")

    # 收尾：自检也必须"来去无痕"。留 25 行残渣会污染后续以 total 为判据的用例，
    # 而且下次 --selftest 的 M3 期望值会跟着飘 —— 这是我自己踩过的坑。
    n = clean_rows()
    print("\n[收尾] 已清理 %d 行变异测试数据" % n)


# ---------------------------------------------------------------- main
def main():
    if "--check" in sys.argv:
        print("employee 表现状：")
        rows = sql("SELECT id, username, name, status, LEFT(password,12) FROM employee "
                   "ORDER BY id;")
        for r in rows:
            print("  " + r)
        print("  共 %d 行；测试数据 %d 行（前缀 %s）"
              % (len(rows), db_count("WHERE username LIKE '%s%%'" % TEST_PREFIX),
                 TEST_PREFIX))
        return 0

    if "--clean" in sys.argv:
        n = clean_rows()
        print("已清理 %d 行测试数据（前缀 %s）" % (n, TEST_PREFIX))
        return 0

    token = admin_login()

    if "--selftest" in sys.argv:
        selftest(token)
        print("\n" + "=" * 68)
        print("[SELFTEST] 通过 %d / 失败 %d" % (len(_passed), len(_failed)))
        return 1 if _failed else 0

    print("=" * 68)
    print("M2-1 分页查询验收  |  测试数据前缀 %s  |  造 %d 行" % (TEST_PREFIX, N_ROWS))
    print("=" * 68)
    # 前置：确认跑的是新代码（/page 存在且带鉴权）
    s0, j0 = page(token, page=1, pageSize=1)
    if j0.get("code") != 1:
        raise SystemExit("[FATAL] /admin/employee/page 不可用：HTTP %s %s\n"
                         "        确认应用已用新代码重启" % (s0, j0))

    setup_rows(N_ROWS)
    print("已造 %d 行测试数据（DB 当前共 %d 行）" % (N_ROWS, db_count()))

    case_basic(token)
    case_normalize(token)
    case_paging(token)
    case_filter(token)
    case_status_whitelist(token)
    case_auth()
    case_bad_input(token)

    n = clean_rows()
    print("\n已清理 %d 行测试数据" % n)

    print("\n" + "=" * 68)
    print("结果：通过 %d / 失败 %d / 已知(XFAIL) %d"
          % (len(_passed), len(_failed), len(_known)))
    if _failed:
        print("失败用例（真故障，需要修）：")
        for f in _failed:
            print("  - %s" % f)
    if _known:
        print("已知缺陷（不在本模块范围内，已登记不阻塞）：")
        for k in _known:
            print("  - %s" % k)
    print("=" * 68)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
