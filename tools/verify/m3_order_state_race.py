"""M5-3（M3-5）订单「状态流转」并发缺陷 —— 原理证明 + 真并发复现 + 修复验收。

================================================================================
背景：一句话讲清被测的是什么
================================================================================
`OrderService` 里 6 个改状态的方法（paySuccess / cancel / accept / reject /
delivery / complete）长得一模一样，都是三步：

    ① 读   Orders o = getById(id);
    ② 算   o.setStatus(stateMachine.apply(o.getStatus(), 某事件));
    ③ 写   ordersMapper.updateById(o);   // UPDATE orders SET status=? WHERE id=?

问题在第 ③ 步：**`updateById` 生成的 SQL 里，WHERE 只有 id，没有 status。**

于是「状态机」的能力边界就露出来了：
    `apply(当前状态, 事件)` 校验的是「**①读到的那个旧状态**允不允许这个事件」，
    它**保证不了「③写的这一刻」库里的状态还是不是那个值**。
⇒ 两个并发请求都从 status=2 出发，状态机**都放行**，然后后写的把先写的覆盖掉，
   而**两个请求都返回 code=1（成功）**。

================================================================================
脚本怎么写：断言写「终态」还是写「现状」
================================================================================
⚠️ 本脚本的断言一律写**修好之后应有的正确行为**（也就是 hits == 0）。
   ⇒ 现在（未修复）跑，相关断言会 FAIL ⇒ **那个 FAIL 本身就是立项证据**。
   修完再跑一遍，同一条断言转绿 ⇒ 修复得到证明。

其中「同一订单上发货与取消同时成功」这条当前已登记为 **XFAIL**：
按照 `tools/verify/` 的既有约定，已知缺陷不打断退出码（其他人跑脚本不会红成一片），
但**修好之后会打 XPASS**，提醒你把这条从 XFAIL 名单里摘掉。

================================================================================
用法（应用需已在 :8080 运行）
================================================================================
  python tools/verify/m3_order_state_race.py --doctor
      # 先跑这个：解释器 / mysql / 连库 / 应用存活 / 双端登录，五项自检

  python tools/verify/m3_order_state_race.py --case proof
      # 【确定性】纯 SQL 演示「WHERE 没有 status」的后果 + 带上 status 就能挡住。
      # 不需要并发、不靠运气，100% 复现，用来解释「病灶到底是什么」。

  python tools/verify/m3_order_state_race.py --case serial
      # 【基线】串行下两个互斥动作只能成功一个 —— 先定义清楚「什么叫正确」。

  python tools/verify/m3_order_state_race.py --case race  -r 40 -k 4
      # 【真并发】商家「发货」 vs 用户「取消」，Barrier 卡齐后同发。
      # r = 轮数，k = 每种动作各发几个线程。

  python tools/verify/m3_order_state_race.py --case admin -r 40 -k 4
      # 【真并发】同一个后台里的「发货」vs「取消订单」（只要一个 token，更好复现）。

  python tools/verify/m3_order_state_race.py --selftest
      # 判别力自检：证明 code=0 和 code=1 **都可能出现**，断言不是恒真/恒假的摆设。

  python tools/verify/m3_order_state_race.py --check   # 只读：打印本脚本造的订单
  python tools/verify/m3_order_state_race.py --clean   # 只清理本脚本造的订单

⚠️ 本机两条前提（缺一条就跑不起来，别猜，跑 --doctor）：
    1. shell 里的 `python` 必须是真解释器。PowerShell / IDEA 终端里的 `python`
       是 Microsoft Store 占位别名 —— 跑完零输出、不报错，比报错更坑。
    2. `mysql` 不在 PATH 里，必须用 MYSQL_BIN 指定 mysql.exe 的完整路径。
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

from _env import (ADMIN_USER, BASE, DB, DEMO_PASSWORD, MYSQL_BIN, MYSQL_HOST,
                  MYSQL_PORT, mysql_argv)

# ============================================================================
# 被测对象：订单状态常量（抄自 `com.mall.pojo.entity.Orders`，改那边这里要同步）
# ============================================================================
ST_PENDING_PAYMENT = 1   # 待付款
ST_TO_BE_ACCEPTED = 2    # 待发货   ← 竞态就从这里出发
ST_ACCEPTED = 3          # 待收货
ST_DELIVERING = 4        # 配送中
ST_COMPLETED = 5         # 已完成
ST_CANCELLED = 6         # 已取消

LABEL = {
    ST_PENDING_PAYMENT: "待付款", ST_TO_BE_ACCEPTED: "待发货", ST_ACCEPTED: "待收货",
    ST_DELIVERING: "配送中", ST_COMPLETED: "已完成", ST_CANCELLED: "已取消",
}

# 本脚本造的订单统一用这个订单号前缀，清理时只删它，绝不碰真实数据
ORDER_NO_PREFIX = "M35RACE-"

USER = os.environ.get("DEMO_USER", "zhangsan")   # C 端用户（取消动作由他发起）

# ---------------------------------------------------------------- 断言登记
_passed = []
_failed = []
_xfailed = []
_xpassed = []

# 已知缺陷名单：**修复前**这些断言会红，属于预期内失败。
# ⚠️ 修复通过后必须把对应条目从这里摘掉 —— 脚本会打 XPASS 提醒你。
KNOWN_XFAIL = {
    # 2026-10-03：R1 / A1 已修复（OrdersMapper.casStatus 状态条件更新），
    # 40 轮真并发下 0 次命中，已从本名单摘除，转为正经断言。
    # 这里留空是为了以后发现新缺陷时有个登记处，也提醒读代码的人：
    # 状态流转当年就是靠"两条都成功"暴露的，别再退回 updateById 绝对值写。
}


# ============================================================================
# 底层工具
# ============================================================================
def sql(query):
    """执行一条 SQL，返回 [[列, 列, ...], ...]（制表符分隔、无表头）。"""
    out = subprocess.run(mysql_argv(query), capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("[FATAL] mysql 执行失败：\n%s\n"
                         "       检查 MYSQL_BIN 是否指向真正的 mysql 客户端（本机不在 PATH 里）"
                         % (out.stderr or out.stdout or "").strip())
    return [line.split("\t") for line in out.stdout.strip().splitlines() if line]


def sql_no_exit(query):
    """同上，但**失败不退出**，返回 (是否成功, 文本)。造轮子给"预期会失败"的动作用。"""
    out = subprocess.run(mysql_argv(query), capture_output=True, text=True, encoding="utf-8")
    return out.returncode == 0, (out.stderr or out.stdout or "").strip()


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
    except urllib.error.URLError as exc:
        return 0, {"raw": "URLError: %s" % exc.reason}


def check(name, ok, detail=""):
    """断言登记。名字在 KNOWN_XFAIL 里时按既有约定走 XFAIL / XPASS 两态。"""
    if name in KNOWN_XFAIL:
        if ok:
            _xpassed.append(name)
            print("  [XPASS] %s%s\n"
                  "         ⚠️ 这条原本登记为已知缺陷，现在通过了 ⇒ 该把这个坑填上了，\n"
                  "            请把「%s」从 KNOWN_XFAIL 里摘掉再重跑，让它变成正经的 PASS。"
                  % (name, ("  → " + detail) if detail else "", name))
        else:
            _xfailed.append(name)
            print("  [XFAIL] %s%s" % (name, ("  → " + detail) if detail else ""))
        return
    (_passed if ok else _failed).append(name)
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  → " + detail) if detail else ""))


# ============================================================================
# 数据操作
# ============================================================================
def uid_of():
    rows = sql("SELECT id FROM user WHERE username='%s'" % USER)
    if not rows:
        raise SystemExit("[FATAL] 种子用户 %s 不存在，先导入 sql/mall_order.sql" % USER)
    return int(rows[0][0])


def mk_order(uid, seq, status=ST_TO_BE_ACCEPTED):
    """直接造一条订单用于测试；返回新订单 id。

    为什么不走「下单 + 支付」接口一步步来：那条链路依赖 Redis（幂等锁），
    Redis 一挂就复现不了状态问题了。**被测对象必须隔离**，别让无关依赖进来搅局。
    """
    rows = sql(
        "INSERT INTO orders (number, user_id, consignee, phone, address, amount,"
        " pay_status, status, order_time)"
        " VALUES ('%s%06d', %d, '压测收货人', '13800000000', '压测地址', 10.00, 1, %d, NOW());"
        " SELECT LAST_INSERT_ID();"
        % (ORDER_NO_PREFIX, seq, uid, status))
    return int(rows[0][0])


def status_of(oid):
    rows = sql("SELECT status FROM orders WHERE id=%d" % oid)
    return int(rows[0][0]) if rows else -1


def clean():
    sql("DELETE FROM orders WHERE number LIKE '%s%%'" % ORDER_NO_PREFIX)


def order_rows():
    return sql("SELECT id, number, status FROM orders WHERE number LIKE '%s%%' "
               "ORDER BY id" % ORDER_NO_PREFIX)


# ============================================================================
# 登录
# ============================================================================
def login_admin():
    st, js = call("POST", "/admin/employee/login",
                  body={"username": ADMIN_USER, "password": DEMO_PASSWORD})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 管理端登录失败: HTTP %s %s" % (st, js))
    return js["data"]["token"]


def login_user():
    st, js = call("POST", "/user/login", body={"username": USER, "password": DEMO_PASSWORD})
    if js.get("code") != 1:
        raise SystemExit("[FATAL] 用户登录失败: HTTP %s %s" % (st, js))
    return js["data"]["token"]


# ============================================================================
# 案例 1 —— proof：确定性原理证明（不用并发、不靠运气）
# ============================================================================
def case_proof(uid):
    print("\n" + "=" * 68)
    print("案例 P：原理证明 —— 「WHERE 里没有 status」到底会怎样")
    print("=" * 68)
    print("   这一段**完全用 SQL 说话**：把 `updateById` 生成的语句原样抄出来执行，")
    print("   所以结论不依赖并发运气，100% 可重现。")

    oid = mk_order(uid, 900001)
    print("\n   [准备] 造一条订单 id=%d，status=%d（%s）"
          % (oid, ST_TO_BE_ACCEPTED, LABEL[ST_TO_BE_ACCEPTED]))

    # ---- ① 两个请求各自读（此刻读到的都是 2）
    read_a = status_of(oid)
    read_b = status_of(oid)
    print("\n   [① 读] 请求A(发货) 读到 status=%d ；请求B(取消) 读到 status=%d" % (read_a, read_b))

    # ---- ② 状态机各自放行
    print("   [② 校验] A：2→3 合法   B：2→6 合法   ⇒ 状态机两边都放行")

    # ---- ③ A 先写（这就是 MyBatis-Plus updateById 生成的样子）
    rows_a = sql("UPDATE orders SET status=%d WHERE id=%d; SELECT ROW_COUNT();"
                 % (ST_ACCEPTED, oid))
    print("\n   [③ 写-A] UPDATE orders SET status=3 WHERE id=%d  → 影响 %s 行"
          % (oid, rows_a[0][0]))

    # ---- ④ B 后写（病灶：它并不知道自己手里的 2 已经过期了）
    rows_b = sql("UPDATE orders SET status=%d WHERE id=%d; SELECT ROW_COUNT();"
                 % (ST_CANCELLED, oid))
    print("   [④ 写-B] UPDATE orders SET status=6 WHERE id=%d  → 影响 %s 行   ← 病灶"
          % (oid, rows_b[0][0]))

    final = status_of(oid)
    print("\n   [结果] 订单终态 status=%d（%s）" % (final, LABEL.get(final, str(final))))
    print("          A 说自己「发货成功」，B 说自己「取消成功」，两边都 code=1，")
    print("          但 A 的结果被 B 悄悄抹掉了 —— 这就是「状态流转缺陷」。")

    check("P1 两次写都被数据库接受（说明 UPDATE 没有带条件保护）",
          rows_a[0][0] == "1" and rows_b[0][0] == "1",
          "A 影响 %s 行，B 影响 %s 行" % (rows_a[0][0], rows_b[0][0]))
    check("P2 B 的旧值写把 A 的「待收货」覆盖成了「已取消」",
          final == ST_CANCELLED, "终态 %d（%s）" % (final, LABEL.get(final, str(final))))

    # ---- ⑤ 反证：把「读到的旧状态」写进 WHERE 就挡得住
    print("\n   [对照] 换一种写法：把「我读到的是 2」这个前提写进 WHERE ——")
    # 注意：ROW_COUNT() 是**会话级**变量，必须和那条 UPDATE 在同一次 mysql 调用里查。
    # 分两次调用（换一个连接）读到的永远是 -1 —— 那是"这个连接没执行过语句"，不是"影响了 -1 行"。
    rows_c = sql("UPDATE orders SET status=%d WHERE id=%d AND status=%d; SELECT ROW_COUNT();"
                 % (ST_ACCEPTED, oid, ST_TO_BE_ACCEPTED))
    print("          UPDATE orders SET status=3 WHERE id=%d AND status=2  → 影响 %s 行"
          % (oid, rows_c[0][0]))
    print("          库里现在已经不是 2 了，所以这一句**改不动任何一行**。")
    print("          ⇒ 只要拿到「影响行数=0」，就知道有人抢先改过 ⇒ 可以明确报错，而不是假装成功。")

    check("P3 带上原状态做条件的 UPDATE 影响 0 行（这才是防御写法）",
          rows_c[0][0] == "0", "实际影响 %s 行" % rows_c[0][0])
    check("P4 订单状态没有被 P3 的失败写篡改", status_of(oid) == ST_CANCELLED)


# ============================================================================
# 案例 2 —— serial：先把「什么叫正确」定义清楚
# ============================================================================
def case_serial(admin_token, user_token, uid):
    print("\n" + "=" * 68)
    print("案例 S：串行基线 —— 没有并发时，两个互斥动作只能成功一个")
    print("=" * 68)
    print("   跑这一步是为了**先把「正确」定义出来**，后面的并发才有对照标准。")

    # --- S-A：先发货，再取消
    oid = mk_order(uid, 900011)
    print("\n   [顺序 1] 先「发货」，再「取消」")
    _, j1 = call("PUT", "/admin/order/accept/%d" % oid, token=admin_token)
    s1 = status_of(oid)
    _, j2 = call("PUT", "/user/order/cancel/%d" % oid, token=user_token)
    s2 = status_of(oid)
    print("      发货 → code=%s msg=%s  (status=%d %s)"
          % (j1.get("code"), j1.get("msg"), s1, LABEL.get(s1, "")))
    print("      取消 → code=%s msg=%s  (status=%d %s)"
          % (j2.get("code"), j2.get("msg"), s2, LABEL.get(s2, "")))
    check("S1 发货成功，状态推进到待收货(3)",
          j1.get("code") == 1 and s1 == ST_ACCEPTED, "status=%d" % s1)
    check("S2 待收货的订单再取消，必须被拒绝（code=0）",
          j2.get("code") == 0 and s2 == ST_ACCEPTED, "status=%d" % s2)

    # --- S-B：先取消，再发货
    oid2 = mk_order(uid, 900012)
    print("\n   [顺序 2] 先「取消」，再「发货」")
    _, k1 = call("PUT", "/user/order/cancel/%d" % oid2, token=user_token)
    t1 = status_of(oid2)
    _, k2 = call("PUT", "/admin/order/accept/%d" % oid2, token=admin_token)
    t2 = status_of(oid2)
    print("      取消 → code=%s msg=%s  (status=%d %s)"
          % (k1.get("code"), k1.get("msg"), t1, LABEL.get(t1, "")))
    print("      发货 → code=%s msg=%s  (status=%d %s)"
          % (k2.get("code"), k2.get("msg"), t2, LABEL.get(t2, "")))
    check("S3 取消成功，状态推进到已取消(6)",
          k1.get("code") == 1 and t1 == ST_CANCELLED, "status=%d" % t1)
    check("S4 已取消的订单再发货，必须被拒绝（code=0）",
          k2.get("code") == 0 and t2 == ST_CANCELLED, "status=%d" % t2)

    print("\n   ⇒ 结论：**同一条订单，这两条互斥路径只能成功一条。**")
    print("     这个结论就是下面并发案例的判据 —— 要是并发下一次跑出两条都成功，那就是缺陷。")


# ============================================================================
# 案例 3/4 —— 真并发
# ============================================================================
def _fire(label, method, path, token, oid, barrier, sink, lock, errs):
    def run():
        try:
            barrier.wait(timeout=20)
        except threading.BrokenBarrierError:
            return
        try:
            _, js = call(method, path, token=token)
            with lock:
                sink[label].append(js.get("code"))
        except Exception as exc:      # 网络抖动不该让整轮报废，记下来即可
            with lock:
                errs.append("%s: %s" % (label, exc))
    return run


def _race_round(label_a, mk_a, label_b, mk_b, admin_token, user_token, uid,
                rounds, k, tag, fail_name):
    """跑 rounds 轮「两个互斥动作同时打同一条订单」，统计双双成功的轮数。

    判据为什么这么定：
        串行下（案例 S 已证明）这两条路径只能成功一条。
        ⇒ 只要某一轮里 **A 至少有 1 个成功、B 也至少有 1 个成功**，就一定说明
          这两个请求是在**各自都读到旧状态**的情况下放行的 —— 竞态实锤。
        ⇒ 反过来，如果 0 轮命中，只能说"这次没撞上"，不能说"没问题"（窗口太窄时常见）。
    """
    print("\n" + "=" * 68)
    print("案例 %s：真并发 —— %s vs %s（%d 轮 × 每边 %d 个线程）"
          % (tag, label_a, label_b, rounds, k))
    print("=" * 68)
    hits = []
    detail_lines = []

    for r in range(1, rounds + 1):
        # 号段按案例分开：两个案例都用 100000+r 会让后一个案例撞上
        # 前一个案例留下的订单（orders.uk_number 唯一 → 直接 Duplicate entry 崩掉），
        # 那样报错掩盖了真正的断言结果。tag 是 "C" / "D"。
        oid = mk_order(uid, (ord(tag) - ord("C") + 1) * 100000 + r)
        barrier = threading.Barrier(2 * k)
        lock = threading.Lock()
        sink = {label_a: [], label_b: []}
        errs = []

        ths = []
        for _ in range(k):
            # 用 {oid} 占位而不是 %d：路径里可能带 URL 编码的 %xx（如 reason=%E6%B5%8B），
            # 那种百分号会被 str % 误当成占位符，直接 TypeError 崩掉整个用例。
            ths.append(threading.Thread(target=_fire(
                label_a, mk_a[0], mk_a[1].replace("{oid}", str(oid)), mk_a[2], oid, barrier, sink, lock, errs)))
            ths.append(threading.Thread(target=_fire(
                label_b, mk_b[0], mk_b[1].replace("{oid}", str(oid)), mk_b[2], oid, barrier, sink, lock, errs)))
        for t in ths:
            t.start()
        for t in ths:
            t.join(timeout=30)

        ok_a = sum(1 for c in sink[label_a] if c == 1)
        ok_b = sum(1 for c in sink[label_b] if c == 1)
        final = status_of(oid)

        if ok_a >= 1 and ok_b >= 1:
            hits.append((r, oid, ok_a, ok_b, final))
            detail_lines.append(
                "      轮%02d order=%d  %s成功%d个 / %s成功%d个  → 终态status=%d(%s)"
                % (r, oid, label_a, ok_a, label_b, ok_b, final, LABEL.get(final, str(final))))
        if r % 10 == 0 or r == rounds:
            print("      已跑 %3d/%d 轮，累计命中 %d 轮" % (r, rounds, len(hits)))

    print("")
    if detail_lines:
        print("   命中的轮次（两条互斥路径都成功了 ⇒ 状态机校验被绕过）：")
        for line in detail_lines[:10]:
            print(line)
        if len(detail_lines) > 10:
            print("      …… 另有 %d 轮同类命中" % (len(detail_lines) - 10))
    else:
        print("   本轮 0 命中。⚠️ 这不等于没问题 —— 只说明这次没撞上窗口，")
        print("   请加大 -r/-k 重试；要一个确定性的答案请看 `--case proof`。")

    check(fail_name, len(hits) == 0,
          "%d/%d 轮出现「%s 与 %s 双双成功」" % (len(hits), rounds, label_a, label_b))
    return len(hits)


def case_race(admin_token, user_token, uid, rounds, k):
    """商家「发货」 vs 用户「取消」—— 最贴近真实业务的那一组。"""
    return _race_round(
        "发货", ("PUT", "/admin/order/accept/{oid}", admin_token),
        "取消", ("PUT", "/user/order/cancel/{oid}", user_token),
        admin_token, user_token, uid, rounds, k, "C",
        "R1 同一订单上「发货」与「取消」不得双双成功")


def case_admin_race(admin_token, user_token, uid, rounds, k):
    """同一个后台里「发货」vs「取消订单」—— 两边都用 admin token，更好复现。"""
    return _race_round(
        "发货", ("PUT", "/admin/order/accept/{oid}", admin_token),
        "取消订单", ("PUT", "/admin/order/reject/{oid}?reason=%E6%B5%8B%E8%AF%95", admin_token),
        admin_token, user_token, uid, rounds, k, "D",
        "A1 同一订单上「发货」与「取消订单」不得双双成功")


# ============================================================================
# 判别力自检 —— 证明这些断言不是摆设
# ============================================================================
def case_selftest(admin_token, user_token, uid):
    print("\n" + "=" * 68)
    print("自检：证明「成功 / 失败」两种结果**都真能出现**，断言不是恒真或恒假")
    print("=" * 68)
    print("   为什么必须有这一步：一条永远 PASS 的断言等于没有断言。")
    print("   这里故意把订单摆成不同状态，逼出 code=1 和 code=0 两种结果。")

    # S-1：待发货 + 取消 ⇒ 应当成功
    oid = mk_order(uid, 900021, ST_TO_BE_ACCEPTED)
    _, j1 = call("PUT", "/user/order/cancel/%d" % oid, token=user_token)
    print("\n   [变异1] 订单停在「待发货」时取消 → code=%s" % j1.get("code"))
    check("T1 待发货状态下取消应当成功（证明 code=1 这条路是通的）",
          j1.get("code") == 1 and status_of(oid) == ST_CANCELLED, "code=%s" % j1.get("code"))

    # S-2：已完成 + 取消 ⇒ 应当失败
    oid2 = mk_order(uid, 900022, ST_COMPLETED)
    _, j2 = call("PUT", "/user/order/cancel/%d" % oid2, token=user_token)
    print("   [变异2] 订单已「已完成」时取消 → code=%s msg=%s"
          % (j2.get("code"), j2.get("msg")))
    check("T2 已完成状态下取消必须失败（证明 code=0 这条路也是通的）",
          j2.get("code") == 0 and status_of(oid2) == ST_COMPLETED, "code=%s" % j2.get("code"))

    # S-3：已完成 + 发货 ⇒ 应当失败
    oid3 = mk_order(uid, 900023, ST_COMPLETED)
    _, j3 = call("PUT", "/admin/order/accept/%d" % oid3, token=admin_token)
    print("   [变异3] 订单已「已完成」时发货 → code=%s msg=%s"
          % (j3.get("code"), j3.get("msg")))
    check("T3 已完成状态下发货必须失败（状态机确实在拦非法流转）",
          j3.get("code") == 0 and status_of(oid3) == ST_COMPLETED, "code=%s" % j3.get("code"))

    print("\n   ⇒ 三条都成立，说明上面的判据是有判别力的：")
    print("     合法流转会给 code=1，非法流转会给 code=0，两者都能被观测到。")


# ============================================================================
# --doctor / --check / --clean
# ============================================================================
def doctor():
    print("== 环境自检（五件事，缺一件都会让脚本「假通过」）==")
    ok = True

    print("  1) Python 解释器：%s" % sys.executable)
    print("     %s" % sys.version.split()[0])
    try:                       # 占位别名会抛 OSError / 无输出，这条是最后一道识别
        sec = subprocess.run([sys.executable, "-c", "import sys;print('ok')"],
                             capture_output=True, text=True, timeout=15)
        probe = sec.stdout.strip()
    except Exception:
        probe = ""
    print("     探针：%s"
          % ("[OK] 真解释器" if probe == "ok"
             else "[异常] 跑不出东西 —— 多半是 Microsoft Store 的 python 占位别名（零输出 + 退出码 9009）"))

    p = MYSQL_BIN
    found = shutil.which(p) is not None or bool(p) and __import__("pathlib").Path(p).exists()
    print("  2) mysql 客户端：%r  %s" % (p, "[OK]" if found else "[缺失]"))
    if not found:
        print("     → 传 MYSQL_BIN 环境变量，例如 export MYSQL_BIN=\"/c/Program Files/MySQL/.../bin/mysql.exe\"")
        ok = False

    try:
        ver = sql("SELECT VERSION()")[0][0]
        print("  3) 连库 %s@%s：%s  [OK]" % (_env_db(), _env_host(), ver))
    except SystemExit as exc:
        print("  3) 连库失败 [FATAL]\n%s" % exc)
        return False

    _, js = call("GET", "/admin/order/statistics")
    alive = js.get("code") is not None
    print("  4) 应用 %s：%s" % (BASE, "[OK]" if alive else "[不通] %s" % js))
    if not alive:
        print("     → 先在 IDEA 里跑 MallOrderApplication，等出现 Started ... 再回来")
        ok = False

    try:
        login_admin()
        print("  5) 双端登录：admin [OK]", end="  ")
    except SystemExit as exc:
        print("  5) admin 登录失败：%s" % exc)
        return False
    try:
        login_user()
        print("user(%s) [OK]" % USER)
    except SystemExit as exc:
        print("\n     user 登录失败：%s" % exc)
        return False

    print("\n  ⇒ %s" % ("环境齐了，可以开跑。" if ok else "环境还差东西，先看上面的提示。"))
    return ok


def _env_db():
    from _env import DB
    return DB


def _env_host():
    from _env import MYSQL_HOST, MYSQL_PORT
    return "%s:%s" % (MYSQL_HOST, MYSQL_PORT)


def do_check():
    rows = order_rows()
    if not rows:
        print("库里没有本脚本造的订单（订单号前缀 %s），很干净。" % ORDER_NO_PREFIX)
        return
    print("本脚本造的订单共 %d 条：" % len(rows))
    for oid, num, st in rows:
        print("  id=%-8s %s  status=%s (%s)" % (oid, num, st, LABEL.get(int(st), "?")))
    print("\n清理命令： python tools/verify/m3_order_state_race.py --clean")


# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="M3-5 订单状态流转并发 复现/验收")
    ap.add_argument("--case", default="all",
                    choices=["all", "proof", "serial", "race", "admin"],
                    help="要跑的案例，默认全跑")
    ap.add_argument("-r", "--rounds", type=int, default=40, help="并发轮数（默认 40）")
    ap.add_argument("-k", "--threads", type=int, default=4, help="每种动作的线程数（默认 4）")
    ap.add_argument("--doctor", action="store_true", help="只做环境自检")
    ap.add_argument("--selftest", action="store_true", help="只跑判别力自检")
    ap.add_argument("--check", action="store_true", help="只读：列出本脚本造的订单")
    ap.add_argument("--clean", action="store_true", help="只清理本脚本造的订单")
    args = ap.parse_args()

    if args.check:
        do_check()
        return
    if args.clean:
        clean()
        print("已清理订单号前缀为 %s 的订单。" % ORDER_NO_PREFIX)
        return
    if args.doctor:
        sys.exit(0 if doctor() else 1)

    # 跑真实案例前：先清场（幂等，跑两遍不影响结论）+ 拿环境
    clean()
    uid = uid_of()
    if args.selftest:
        case_selftest(login_admin(), login_user(), uid)
    else:
        admin_token = login_admin()
        user_token = login_user()
        case = args.case
        if case in ("all", "proof"):
            case_proof(uid)
        if case in ("all", "serial"):
            case_serial(admin_token, user_token, uid)
        if case in ("all", "race"):
            case_race(admin_token, user_token, uid, args.rounds, args.threads)
        if case in ("all", "admin"):
            case_admin_race(admin_token, user_token, uid, args.rounds, args.threads)
        if case == "all":
            case_selftest(admin_token, user_token, uid)

    total = len(_passed) + len(_failed) + len(_xfailed) + len(_xpassed)
    print("\n" + "=" * 68)
    print("汇总：%d 条断言 —— PASS %d / FAIL %d / XFAIL %d / XPASS %d"
          % (total, len(_passed), len(_failed), len(_xfailed), len(_xpassed)))
    if _failed:
        print("  失败明细：")
        for n in _failed:
            print("    - %s" % n)
    if _xfailed:
        print("  ⚠️ 预期内失败（已登记的已知缺陷，不打断退出码）：")
        for n in _xfailed:
            print("    - %s" % n)
        print("     这些就是**尚未修复**的证据：每一条 XFAIL 都对应一处能真实复现的问题。")
    if _xpassed:
        print("  ✅ XPASS（原本登记为缺陷、现已通过 —— 请把它移出 KNOWN_XFAIL）：")
        for n in _xpassed:
            print("    - %s" % n)
    print("=" * 68)
    print("\n订单号前缀 %s 的测试数据留着复盘用；要清理："
          " python tools/verify/m3_order_state_race.py --clean" % ORDER_NO_PREFIX)
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
