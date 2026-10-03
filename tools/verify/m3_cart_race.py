"""M3-1 购物车「先查后写」缺陷 —— 复现 + 验收脚本。

用法（应用需已在 :8080 运行）：
    python tools/verify/m3_cart_race.py --doctor   # 先跑这个：四跳自检（解释器/mysql/库/应用/登录）
    python tools/verify/m3_cart_race.py              # 串行案例：同一商品连点 N 次（默认 20）
    python tools/verify/m3_cart_race.py --case race  # 并发案例：Barrier 卡齐后同时发 N 次
    python tools/verify/m3_cart_race.py --case blown # 唯一约束守门：同 key 插第二条必须被 DB 拒绝
    python tools/verify/m3_cart_race.py --case sub   # 「−」按钮：add 之后立刻 sub（用独立商品，不碰上面那些数据）
    python tools/verify/m3_cart_race.py --case setmeal -n 20  # 组合套装维度：组合套装行 dish_id 是 NULL，专门抓"只修商品"的假修复
    python tools/verify/m3_cart_race.py --case ac9   # AC#9 变异：拆掉 DDL 的 dish_flavor 默认值，证明"归一是代码做的"
    python tools/verify/m3_cart_race.py --check      # 只读：打印购物车现状 + 全表重复 key 扫描
    python tools/verify/m3_cart_race.py --clean      # 只清理本脚本的测试数据（指定用户 + 商品）
    python tools/verify/m3_cart_race.py --selftest   # 变异自检：证明「只占 1 行」这条断言真有判别力

⚠️ 本机前提（两条，缺一个都跑不起来）：
    1. shell 里的 `python` 必须是真解释器。PowerShell / IDEA 终端里 `python` 是 Microsoft Store
       占位别名 —— **跑完零输出、不报错**，比报错更坑。改用托管解释器的绝对路径调用
       （路径见 `_env.py` 注释；`--doctor` 会把它打出来）。
    2. `mysql` 不在 PATH 里，必须用 MYSQL_BIN 指定 mysql.exe 的完整路径。
       （`--doctor` 会把这两条都验一遍，别自己猜。）

判据设计（三层证据，缺一不可）：
    (1) 返回值：N 次 add 是不是都 code=1
    (2) 查库  ：(user_id, dish_id, dish_flavor) 只应有 1 行，且 number 合计 = N
    (3) 读接口：GET /user/shoppingCart/list 里该商品的条目数
    —— 只看接口返回 code=1 是看不出来的（这正是一直没被发现的原因）。
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request

from _env import (BASE, DB, DEMO_PASSWORD, MYSQL_BIN, MYSQL_HOST, MYSQL_PORT,
                  MYSQL_USER, ROOT, mysql_argv)

# 测试对象：demo 用户 + 一个"没有配置喜好"的商品。
# 种子数据里 dish_flavor 只配了 dish_id 3/4/6/7/11，所以 dish_id=1 走的是 dishFlavor=null 的分支。
USER = "zhangsan"
DISH_ID = 1
# 案例 D 的探针商品：用**另一个 key**，这样测 sub 时不会碰坏案例 A 留下的脏数据。
DISH_ID_SUB = 2
# 案例 E 的探针组合套装：组合套装行的 dish_id 是 NULL —— 唯一约束最容易漏掉的那一半。
SETMEAL_ID = 1

# 唯一约束的名字与定义（变异自检要临时摘掉它再装回来，所以只写一份，避免两处不一致）。
UK_NAME = "uk_cart_user_item"
UK_DDL = ("ALTER TABLE shopping_cart ADD UNIQUE KEY %s "
          "(user_id, dish_key, setmeal_key, dish_flavor)" % UK_NAME)

# AC#9 变异用：把 dish_flavor 的「NOT NULL DEFAULT ''」安全网拆掉 / 装回。
# 目的不是改功能，而是**区分两种实现**（见 case_ac9 的说明）。
FLAVOR_COL_MUTATE = "ALTER TABLE shopping_cart MODIFY dish_flavor VARCHAR(200) NULL DEFAULT NULL"
FLAVOR_COL_RESTORE = "ALTER TABLE shopping_cart MODIFY dish_flavor VARCHAR(200) NOT NULL DEFAULT ''"

_passed = []
_failed = []


# ---------------------------------------------------------------- 底层工具
def sql(query):
    """执行一条 SQL，返回 [[列, 列, ...], ...]（制表符分隔、无表头）。"""
    out = subprocess.run(mysql_argv(query), capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("[FATAL] mysql 执行失败：\n%s\n"
                         "       检查 MYSQL_BIN 是否指向真正的 mysql 客户端（本机不在 PATH 里）"
                         % (out.stderr or "").strip())
    return [line.split("\t") for line in out.stdout.strip().splitlines() if line]


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


def check(name, ok, detail=""):
    (_passed if ok else _failed).append(name)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  → " + detail) if detail else ""))


def restore_or_die(statement, what):
    """执行"还原保护机制"的 DDL；失败就响亮退出。

    存在理由（实测事故）：`--selftest` 早先直接在 `finally` 里 `ADD UNIQUE KEY`，
    被 1062 拒掉后**索引没装回去**，而脚本继续往下跑，只表现为"S3 失败" ——
    库被静默留在无保护状态，后续几轮结果全不可信。
    → **凡"摘掉安全设施"的测试，还原失败都必须升级成 FATAL 并给出可手工执行的补救命令。**
    """
    try:
        sql(statement)
    except SystemExit as exc:
        raise SystemExit(
            "[FATAL] 还原失败：%s 仍处于被改动状态！\n"
            "        请手工执行：%s\n"
            "        原始错误：%s" % (what, statement, exc))


def hotfix_sql_escape(s):
    return s.replace("'", "''")


# ---------------------------------------------------------------- 数据操作
def uid_of():
    rows = sql("SELECT id FROM user WHERE username='%s'" % USER)
    if not rows:
        raise SystemExit("[FATAL] 种子用户 %s 不存在，先导入 sql/mall_order.sql" % USER)
    return int(rows[0][0])


def cart_state(uid, dish=DISH_ID):
    """返回 (行数, number 合计)。"""
    rows = sql("SELECT COUNT(*), IFNULL(SUM(number),0) FROM shopping_cart "
               "WHERE user_id=%d AND dish_id=%d" % (uid, dish))
    return int(rows[0][0]), int(rows[0][1])


def clean(uid, dish=DISH_ID):
    sql("DELETE FROM shopping_cart WHERE user_id=%d AND dish_id=%d" % (uid, dish))


def setmeal_state(uid):
    """组合套装维度的 (行数, number 合计)。"""
    rows = sql("SELECT COUNT(*), IFNULL(SUM(number),0) FROM shopping_cart "
               "WHERE user_id=%d AND setmeal_id=%d" % (uid, SETMEAL_ID))
    return int(rows[0][0]), int(rows[0][1])


def clean_setmeal(uid):
    sql("DELETE FROM shopping_cart WHERE user_id=%d AND setmeal_id=%d" % (uid, SETMEAL_ID))


def insert_probe(uid, label, flavor_sql):
    """直接往购物车插一行探针数据；flavor_sql 传 NULL 或 ''。"""
    sql("INSERT INTO shopping_cart (user_id, dish_id, name, amount, number, dish_flavor) "
        "VALUES (%d, %d, '%s', 1.00, 1, %s)" % (uid, DISH_ID, hotfix_sql_escape(label), flavor_sql))


def try_insert_probe(uid, label, flavor_sql):
    """同上，但**不因失败而退出**，返回 (是否成功, 错误信息)。

    存在理由：加了唯一约束之后，"插第二条同 key"必须失败 —— 而失败本来就是断言对象，
    不能再让 `sql()` 抛 SystemExit 把脚本整个带崩（这正是 2026-09-24 踩到的：
    `--case blown` 加约束后直接 FATAL 退出，看起来像"脚本坏了"，其实是"约束生效了"）。
    """
    q = ("INSERT INTO shopping_cart (user_id, dish_id, name, amount, number, dish_flavor) "
         "VALUES (%d, %d, '%s', 1.00, 1, %s)" % (uid, DISH_ID, hotfix_sql_escape(label), flavor_sql))
    out = subprocess.run(mysql_argv(q), capture_output=True, text=True, encoding="utf-8")
    return out.returncode == 0, (out.stderr or out.stdout or "").strip()


def login():
    st, js = call("POST", "/user/login", body={"username": USER, "password": DEMO_PASSWORD})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 用户登录失败: HTTP %s %s\n"
                         "       确认应用已启动、%s 的密码是 %s" % (st, js, USER, DEMO_PASSWORD))
    return js["data"]["token"]


def list_entry_count(js):
    """从 list 返回体里数出「本商品」的条目数。"""
    if js.get("code") != 1:
        return -1
    items = js.get("data") or []
    return sum(1 for it in items
               if it.get("dishId") == DISH_ID and it.get("setmealId") in (None, 0))


# ---------------------------------------------------------------- 案例
def case_seq(token, uid, n):
    print("\n== 案例 A：串行连点 %d 次同一点品（不带喜好）==" % n)
    print("   等价于用户手贱点了 %d 下「+」按钮。" % n)
    codes = []
    for _ in range(n):
        st, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": DISH_ID})
        codes.append(js.get("code"))
    rows, total = cart_state(uid)
    st, js = call("GET", "/user/shoppingCart/list", token=token)
    cnt = list_entry_count(js)
    print("   接口返回 code 序列：%s" % codes)
    print("   查库：行数=%d，number 合计=%d   |   列表接口返回该商品 %d 条" % (rows, total, cnt))
    check("A1 每一次调用都成功（code=1）", all(c == 1 for c in codes), "codes=%s" % codes)
    check("A2 同一商品只应占 1 行", rows == 1, "实际 %d 行" % rows)
    check("A3 number 合计应等于点击次数", total == n, "实际 %d，期望 %d" % (total, n))
    check("A4 列表接口只应返回 1 条", cnt == 1, "实际 %d 条" % cnt)


def case_race(token, uid, n):
    print("\n== 案例 B：Barrier 卡齐后并发发 %d 次 ==" % n)
    barrier = threading.Barrier(n)
    lock = threading.Lock()
    results = []

    def worker():
        try:
            barrier.wait(timeout=10)
        except threading.BrokenBarrierError:
            return
        st, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": DISH_ID})
        with lock:
            results.append((st, js.get("code"), js.get("msg")))

    ts = [threading.Thread(target=worker) for _ in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows, total = cart_state(uid)
    ok_cnt = sum(1 for _, c, _ in results if c == 1)
    print("   成功 %d / %d 次" % (ok_cnt, n))
    for st, c, msg in results:
        if c != 1:
            print("     失败样例：HTTP %s code=%s msg=%s" % (st, c, msg))
            break
    print("   查库：行数=%d，number 合计=%d" % (rows, total))
    check("B1 同一商品只应占 1 行", rows == 1, "实际 %d 行" % rows)
    check("B2 number 合计应等于并发次数", total == n, "实际 %d，期望 %d" % (total, n))


def case_blown(token, uid):
    """案例 C：唯一约束守门 —— 「同 key 只能有一行」这条不变量必须由 DB 兜着。

    语义变更说明（2026-09-24）：本案例原本是「手工造两条同 key 行，看接口会不会瘫」，
    用来暴露缺陷①（TooManyResults 被吞成"系统繁忙"）的后果。
    加了唯一约束之后，**第二条根本造不出来了** —— 于是断言必须跟着**不变量**走，
    从"暴露后果"改成"确认守门人还在"：
      · 万一有人误删唯一索引 → C2 立刻变红（这才是它现在的回归价值）
      · 而不是因为"实现变了、这条测不了了"就把案例删掉
    **教训：实现变了要改断言，不能删断言。**
    """
    print("\n== 案例 C：唯一约束守门（同 key 插第二条，必须被 DB 拒绝）==")
    clean(uid)
    insert_probe(uid, "探针A", "''")
    rows1, _ = cart_state(uid)
    print("   插入第 1 条同 key：行数=%d" % rows1)
    check("C1 第一条同 key 应插入成功", rows1 == 1, "实际 %d 行" % rows1)

    ok, err = try_insert_probe(uid, "探针B", "''")
    rows2, _ = cart_state(uid)
    print("   插入第 2 条同 key：%s" % ("被拒绝 ✓" if not ok else "竟然插进去了 ✗"))
    if err:
        print("      DB 原文：%s" % err.splitlines()[-1][:130])
    check("C2 第二条同 key 必须被唯一约束拒绝", not ok, "err=%s" % (err or "（居然没报错）"))
    check("C3 被拒绝后行数仍为 1（数据没被插坏）", rows2 == 1, "实际 %d 行" % rows2)

    st, js = call("GET", "/user/shoppingCart/list", token=token)
    check("C4 单行状态下 list 可用", js.get("code") == 1, "code=%s" % js.get("code"))
    st, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": DISH_ID})
    rows3, total3 = cart_state(uid)
    print("   POST /add  -> HTTP %s code=%s；行数=%d，number 合计=%d"
          % (st, js.get("code"), rows3, total3))
    check("C5 单行状态下 add 应走累加（行数仍 1，number +1）",
          rows3 == 1 and total3 == 2, "行数=%d number=%d" % (rows3, total3))
    clean(uid)


def case_sub(token, uid):
    """案例 D：「+」之后立刻「−」，能不能减回去。

    设计理由（2026-09-24 实测发现）：`sub()` 用的是同一个 `findOne()`，
    所以它吃到同一个 NULL 语义。这里用**独立商品**做实验，避免碰坏案例 A 留下的脏数据 ——
    否则一旦案例 A 跑过，D 会因为 TooManyResults 而"假失败"，掩盖真问题。
    """
    print("\n== 案例 D：add 之后立刻 sub（「+」再「−」）==")
    print("   独立商品 id=%d，不碰 id=%d 的数据。" % (DISH_ID_SUB, DISH_ID))
    clean(uid, DISH_ID_SUB)
    st, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": DISH_ID_SUB})
    print("   POST /add  -> HTTP %s code=%s msg=%s" % (st, js.get("code"), js.get("msg")))
    rows, total = cart_state(uid, DISH_ID_SUB)
    print("   查库：行数=%d，number 合计=%d" % (rows, total))
    check("D1 第一次 add 应落 1 行", rows == 1, "实际 %d 行" % rows)

    st, js = call("POST", "/user/shoppingCart/sub", token=token, body={"dishId": DISH_ID_SUB})
    print("   POST /sub  -> HTTP %s code=%s msg=%s" % (st, js.get("code"), js.get("msg")))
    rows2, total2 = cart_state(uid, DISH_ID_SUB)
    print("   查库：行数=%d，number 合计=%d" % (rows2, total2))
    check("D2 刚 add 进去的条目，sub 必须能找到", js.get("code") == 1,
          "code=%s msg=%s" % (js.get("code"), js.get("msg")))
    check("D3 数量减到 0 应删除该条目", rows2 == 0, "实际 %d 行" % rows2)
    clean(uid, DISH_ID_SUB)


def case_setmeal(token, uid, n):
    """案例 E：组合套装连点 N 次 + 「−」按钮。

    设计理由（2026-09-24 加）：一张购物车行要么是商品（`dish_id` 有值、`setmeal_id` 为 NULL），
    要么是组合套装（反过来）。如果唯一约束建在 `(user_id, dish_id, dish_flavor)` 上，
    **组合套装行的 `dish_id` 是 NULL → 进不了唯一性判定 → 重复点组合套装照样多行**。
    案例 A~D 全用商品测，抓不到这个洞，所以必须单独立一条 —— 否则"只修商品"也能全绿。
    """
    print("\n== 案例 E：组合套装连点 %d 次（组合套装行的 dish_id 是 NULL）==" % n)
    clean_setmeal(uid)
    codes = []
    for _ in range(n):
        st, js = call("POST", "/user/shoppingCart/add", token=token, body={"setmealId": SETMEAL_ID})
        codes.append(js.get("code"))
    rows, total = setmeal_state(uid)
    print("   接口返回 code 序列：%s" % codes)
    print("   查库：行数=%d，number 合计=%d" % (rows, total))
    check("E1 每一次调用都成功（code=1）", all(c == 1 for c in codes), "codes=%s" % codes)
    check("E2 同一组合套装只应占 1 行", rows == 1, "实际 %d 行" % rows)
    check("E3 number 合计应等于点击次数", total == n, "实际 %d，期望 %d" % (total, n))

    st, js = call("POST", "/user/shoppingCart/sub", token=token, body={"setmealId": SETMEAL_ID})
    rows2, total2 = setmeal_state(uid)
    print("   POST /sub  -> HTTP %s code=%s msg=%s" % (st, js.get("code"), js.get("msg")))
    print("   查库：行数=%d，number 合计=%d" % (rows2, total2))
    check("E4 组合套装的「−」也应该能用", js.get("code") == 1,
          "code=%s msg=%s" % (js.get("code"), js.get("msg")))
    check("E5 「−」一次应让 number 减 1", total2 == total - 1, "实际 %d，期望 %d" % (total2, total - 1))
    clean_setmeal(uid)


def flavor_col_state():
    """读 dish_flavor 的 (IS_NULLABLE, 默认值展示串)。

    用 CONCAT 把默认值包进方括号：空串默认值在 `-N -B` 输出里是**空字段**，
    再被 `strip()` 抹掉尾部的制表符后就彻底看不见了 —— 包一层才能和 NULL 区分开。
    """
    rows = sql("SELECT IS_NULLABLE, "
               "IF(COLUMN_DEFAULT IS NULL, '<NULL>', CONCAT('[', COLUMN_DEFAULT, ']')) "
               "FROM information_schema.COLUMNS "
               "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='shopping_cart' "
               "AND COLUMN_NAME='dish_flavor'")
    return rows[0][0], rows[0][1]


def case_ac9(token, uid, n):
    """AC#9 变异验证：证明「喜好归一是代码做的」，不是「DDL 默认值兜的」。

    手段：把 dish_flavor 改成可空 + 默认 NULL —— 也就是**抽掉 DDL 那层安全网**，
    然后在同一个库里重跑「连点 N 次」。两种实现会给出**不同**结果：

      · 依赖 DDL 兜底的实现：写入 null（列默认值补 ''）→ 库里的键与自增语句
        的 WHERE (`dish_flavor = ''`) 对不上 ⇒ 永远匹配不上 ⇒ 每点一次插一行 ⇒ N 行，红。
      · 自己归一的实现（本实现）：空喜好被显式写成 '' ⇒ 仍然 1 行 × number=N，绿。

    两条路结果不同，所以这条断言有判别力；而这也正是工单 AC#9 的字面要求。
    收尾必须把列改回去，且**独立查 information_schema 核验**（不能只信 finally 没抛异常）。
    """
    print("\n== AC#9 变异：拆掉 DDL 的 dish_flavor 安全网，看代码是否仍然正确 ==")
    clean(uid)
    b1, b2 = flavor_col_state()
    print("   变异前：IS_NULLABLE=%s  DEFAULT=%s" % (b1, b2))

    # ⚠️ ALTER 必须放在 try **里面**：只要变异执行了，finally 就一定会去还原。
    #    写在 try 外面的话，ALTER 成功、但紧接着 flavor_col_state() 抛异常，
    #    列就永远停在"可空"状态 —— 正是本文件警告过的那类事故。
    try:
        sql(FLAVOR_COL_MUTATE)
        m1, m2 = flavor_col_state()
        print("   变异后：IS_NULLABLE=%s  DEFAULT=%s  （DDL 安全网已拆）" % (m1, m2))
        check("T1 变异确实生效（列已改为可空 + 默认 NULL）",
              m1 == "YES" and m2 == "<NULL>", "IS_NULLABLE=%s DEFAULT=%s" % (m1, m2))

        codes = []
        for _ in range(n):
            st, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": DISH_ID})
            codes.append(js.get("code"))
        rows, total = cart_state(uid)
        print("   无安全网下连点 %d 次 -> 行数=%d，number 合计=%d" % (n, rows, total))
        check("T2 拆掉 DDL 安全网后仍只占 1 行（证明归一是代码做的）",
              rows == 1, "实际 %d 行" % rows)
        check("T3 number 合计仍等于点击次数（顺带复核没丢更新）",
              total == n, "实际 %d，期望 %d" % (total, n))
    finally:
        # 还原前必须先清掉可能存在的 NULL 行，否则 MODIFY NOT NULL 会被拒（1138）。
        # 规律同 selftest：**装回约束之前，先把库恢复成满足约束的状态。**
        print("   还原 DDL：dish_flavor -> NOT NULL DEFAULT ''")
        clean(uid)
        restore_or_die(FLAVOR_COL_RESTORE, "shopping_cart.dish_flavor 的 NOT NULL DEFAULT ''")

    a1, a2 = flavor_col_state()
    check("T4 DDL 已独立核验还原（IS_NULLABLE=NO / DEFAULT=[]）",
          a1 == "NO" and a2 == "[]", "IS_NULLABLE=%s DEFAULT=%s" % (a1, a2))
    clean(uid)


def do_check(uid):
    print("== shopping_cart 现状（只读）==")
    print("   本脚本的测试对象：用户 %s(id=%d) + 商品 %d" % (USER, uid, DISH_ID))
    rows = sql("SELECT id, user_id, dish_id, IFNULL(dish_flavor,'<NULL>'), number "
               "FROM shopping_cart WHERE user_id=%d AND dish_id=%d ORDER BY id" % (uid, DISH_ID))
    if not rows:
        print("   (无数据)")
    for r in rows:
        print("   id=%-6s user_id=%-4s dish_id=%-4s dish_flavor=%-10s number=%s"
              % tuple(r))
    print("   行数=%d，number 合计=%d" % cart_state(uid))
    print("\n== 全表「同 key 多行」扫描（user_id + dish_id + dish_flavor）==")
    dup = sql("SELECT user_id, dish_id, IFNULL(dish_flavor,'<NULL>'), COUNT(*) c "
              "FROM shopping_cart GROUP BY user_id, dish_id, dish_flavor "
              "HAVING c > 1 ORDER BY c DESC")
    if not dup:
        print("   (未发现重复 key)")
    for d in dup:
        print("   user_id=%s dish_id=%s dish_flavor=%s -> %s 行" % tuple(d))
    return len(dup)


def selftest(uid):
    """变异自检：证明「同一商品只应占 1 行」这条断言真有判别力。

    本项目的变异手段是**数据层变异**（改 DB，不改源码、不重启）。
    但加了唯一约束之后，"插 2 条同 key 行"这条路**被 DB 自己堵死了** ——
    于是变异点必须上移一层：**临时摘掉唯一索引 → 造 2 行 → 断言应变红 → 装回索引**。

    这同时证明两件事：
      1. 那条断言不是空断言（否则摘了索引也照样绿）；
      2. ⭐ **唯一索引是承重的** —— 摘掉它，重复行立刻回来。
    摘/装放在 try/finally 里：中途崩了也必须装回去，不能把库留在"无保护"状态。
    """
    print("\n== 变异自检：证明「同一商品只应占 1 行」这条断言有判别力 ==")
    clean(uid)
    print("   步骤1：往库里插 1 行（干净状态）")
    insert_probe(uid, "变异前", "''")
    rows, _ = cart_state(uid)
    check("S1 前置：库里确实有 1 行", rows == 1, "实际 %d" % rows)

    print("   步骤2：**临时摘掉唯一索引** %s" % UK_NAME)
    sql("ALTER TABLE shopping_cart DROP INDEX %s" % UK_NAME)
    try:
        print("          再插 1 行同 key —— 若 A2 断言有效，此时它必然变红")
        insert_probe(uid, "变异后", "''")
        rows2, _ = cart_state(uid)
        check("S2 摘掉索引后行数变 2（证明 A2 非空断言 + 索引承重）",
              rows2 == 2, "实际 %d" % rows2)
    finally:
        # ⚠️ 装回索引之前**必须**先把刚造出来的重复行清掉。
        #    库里有重复数据时 `ADD UNIQUE KEY` 会被 1062 直接拒掉 —— 实测踩过：
        #    finally 里直接 ADD，索引没装回去，库被留在"无保护"状态。
        #    规律：**拆保护和装保护之间，必须先把库恢复成"满足约束"的状态。**
        print("   步骤3：先清掉刚造的重复行，再装回唯一索引")
        clean(uid)
        restore_or_die(UK_DDL, "唯一索引 %s" % UK_NAME)

    # S3 的前提是"库里已经有 1 行同 key" —— 但 finally 里的 clean() 把行删光了，
    # 所以这里必须**先补一条**，否则第二次插入根本不构成冲突，断言会假失败（实测踩过）。
    insert_probe(uid, "恢复后A", "''")
    ok, err = try_insert_probe(uid, "恢复后B", "''")
    check("S3 索引装回后，再插同 key 必须被拒", not ok, "err=%s" % (err or "（居然没报错）"))

    print("   步骤4：清理回零")
    clean(uid)
    rows3, _ = cart_state(uid)
    check("S4 清理后归零", rows3 == 0, "实际 %d" % rows3)


def doctor():
    """四跳自检：解释器 / mysql 客户端 / 数据库 / 应用 / 登录。

    设计理由（2026-09-24 踩坑）：这个脚本在本机失败过三次，每次都是**不同**的原因
    （python 是 Store 占位别名、mysql 不在 PATH、8080 没起），而现象全是"没输出"或"报错"。
    与其来回猜，不如让它自己把四跳结论打出来 —— 以后遇到环境问题先跑 --doctor。
    """
    print("== 环境自检（失败时把这段原样贴回给我）==")
    print("  python 解释器 : %s" % sys.executable)
    print("  Python 版本   : %s" % sys.version.split()[0])
    print("  当前工作目录  : %s" % os.getcwd())
    print("  项目根(自动)  : %s" % ROOT)
    print("  接口基址      : %s" % BASE)
    ok = True

    print("\n  [1/4] mysql 客户端")
    print("        MYSQL_BIN = %r" % MYSQL_BIN)
    if shutil.which(MYSQL_BIN) or pathlib.Path(MYSQL_BIN).exists():
        print("        → 找到 ✓")
    else:
        print("        → 找不到 ✗  mysql 不在本机 PATH 里，必须用 MYSQL_BIN 指定完整路径")
        ok = False

    print("\n  [2/4] 连库")
    if not ok:
        print("        → 跳过（上一步已失败）")
    else:
        try:
            rows = sql("SELECT VERSION()")
            print("        → 成功 ✓ MySQL %s / 库 %s / 用户 %s" % (rows[0][0], DB, MYSQL_USER))
        except SystemExit as exc:
            print("        → 失败 ✗ %s" % exc)
            ok = False

    print("\n  [3/4] 应用端口")
    try:
        urllib.request.urlopen(BASE + "/user/shoppingCart/list", timeout=5)
        print("        → 可达 ✓（HTTP 200）")
    except urllib.error.HTTPError as exc:
        print("        → 可达 ✓（HTTP %s，被拦截器挡了，说明服务在跑）" % exc.code)
    except Exception as exc:
        print("        → 不可达 ✗ %s" % exc)
        print("          先在 IDEA 里启动 MallOrderApplication，等看到 Started ... 再跑")
        ok = False

    print("\n  [4/4] 用户登录")
    if not ok:
        print("        → 跳过（上一步已失败）")
    else:
        try:
            tk = login()
            print("        → 成功 ✓ 用户 %s，token 前 12 位 %s..." % (USER, tk[:12]))
        except SystemExit as exc:
            print("        → 失败 ✗ %s" % exc)
            ok = False

    print("\n" + "=" * 60)
    print("自检结论：%s" % ("四跳全通，可以直接跑 --case seq ✓" if ok else "有环节不通过，按上面 ✗ 的提示修"))
    print("=" * 60)
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--count", type=int, default=20)
    ap.add_argument("--case", choices=["seq", "race", "blown", "sub", "setmeal", "ac9"], default="seq")
    ap.add_argument("--doctor", action="store_true", help="四跳环境自检，不碰数据")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.doctor:
        return doctor()

    uid = uid_of()

    if args.check:
        do_check(uid)
        return 0
    if args.clean:
        before, _ = cart_state(uid)
        clean(uid)
        after, _ = cart_state(uid)
        print("已清理 user_id=%d + dish_id=%d：%d 行 -> %d 行" % (uid, DISH_ID, before, after))
        return 0
    if args.selftest:
        selftest(uid)
    else:
        token = login()
        print("已登录：%s" % USER)
        if args.case == "sub":
            target = DISH_ID_SUB
        else:
            target = DISH_ID
        print("起始状态（跑前清场，保证幂等）：")
        before, _ = cart_state(uid, target)
        clean(uid, target)
        print("   清掉商品 %d 的 %d 行旧数据" % (target, before))

        if args.case == "seq":
            case_seq(token, uid, args.count)
        elif args.case == "race":
            case_race(token, uid, args.count)
        elif args.case == "blown":
            case_blown(token, uid)
        elif args.case == "sub":
            case_sub(token, uid)
        elif args.case == "setmeal":
            case_setmeal(token, uid, args.count)
        elif args.case == "ac9":
            case_ac9(token, uid, args.count)

        if args.case in ("seq", "race"):
            rows, total = cart_state(uid)
            print("\n[留证] 测试数据**故意保留**，方便你亲眼看脏数据：")
            print("       user_id=%d + dish_id=%d 现有 %d 行（number 合计 %d）"
                  % (uid, DISH_ID, rows, total))
            print("       看原始行  → --check")
            print("       清掉它    → --clean")
            print("       （本脚本每次开跑前会自动清场，所以重复跑仍然是幂等的）")

    print("\n" + "=" * 60)
    print("结果：通过 %d / 失败 %d" % (len(_passed), len(_failed)))
    if _failed:
        print("失败项：" + "、".join(_failed))
    print("=" * 60)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
