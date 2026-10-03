"""M2-5 回归总入口：一条命令跑完 M2 全部验收脚本，出一张表 + 一个统一退出码。

用法：
    python m2_regression.py                 # 跑全部（应用需已在 :8080 运行）
    python m2_regression.py --only page     # 只跑 id/名称含 "page" 的步骤
    python m2_regression.py --list          # 只列出步骤，不执行
    python m2_regression.py --clean         # 让每个联调脚本清掉自己的测试数据
    python m2_regression.py --selftest      # 自检：证明解析器真能分出红/绿/XPASS
    python m2_regression.py --no-static     # 跳过静态检查（不需要起服务）
    python m2_regression.py --no-cleanup    # 不清库（排查残渣时用，正常别加）

为什么要有它（这不是重复造轮子）：
    4 个联调脚本各自 sys.exit(0/1)。人要跑 4 次、读 4 段几十行的输出、自己汇总。
    真实事故形态是：改完代码"只跑了 page 那个"就以为没事 —— 这个脚本专门防这个。
    判定完全交给子进程的退出码（各脚本已约定：有 FAIL 才非 0，XFAIL 不影响退出码）；
    "通过/失败" 计数只用于出表，抠不到就显示 n/a，绝不让计数失败升级成误判。
"""
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

import _env

HERE = os.path.dirname(os.path.abspath(__file__))
PY = _env.PYTHON          # 默认当前解释器，可用环境变量 PYTHON 覆盖
BASE = _env.BASE

# (id, 名称, 脚本, 是否需要服务, 超时秒)
STEPS = [
    ("S0", "openapi.json + README 一致性", "check_openapi.py", False, 120),
    ("M2-1", "员工分页查询", "m2_page_verify.py", True, 420),
    ("M2-2", "新增员工", "m2_verify.py", True, 300),
    ("M2-3", "编辑员工", "m2_update_verify.py", True, 300),
    ("M2-4", "启用/禁用员工", "m2_status_verify.py", True, 300),
]

# ---------------------------------------------------------------- 解析


def parse_counts(text):
    """从脚本输出里抠 (通过, 失败, xfail)。抠不到给 None，不猜、不默认成 0。

    各脚本汇总行两种写法：
        结果: 32 通过 / 0 失败                     (ASCII 冒号)
        结果：通过 32 / 失败 0 / 已知(XFAIL) 2      (全角冒号)
    """
    line = None
    for ln in text.splitlines():
        if "失败" in ln and ("通过" in ln or "PASS" in ln) and "自检" not in ln:
            line = ln                      # 取最后一条汇总行
    if line is None:
        # 退路：数标记（脚本崩在半路、来不及打汇总行时用得上）
        p = len(re.findall(r"^\s*\[PASS\]", text, re.M))
        f = len(re.findall(r"^\s*\[FAIL\]", text, re.M))
        x = len(re.findall(r"^\s*\[XFAIL\]", text, re.M))
        if p == 0 and f == 0:
            return (None, None, None)   # 一个标记都没有 = 压根没跑起来，不猜
        return (p, f, x)

    def grab(word):
        m = re.search(r"(\d+)\s*%s" % word, line) or re.search(r"%s\s*(\d+)" % word, line)
        return int(m.group(1)) if m else None

    xm = re.search(r"(?:XFAIL\)|已知)\D*(\d+)", line)
    return (grab("通过"), grab("失败"), int(xm.group(1)) if xm else None)


def parse_failures(text):
    """抠出『失败项：』后面的条目，用于红的时候直接指路。"""
    names, hit = [], False
    for ln in text.splitlines():
        if "失败项" in ln:
            hit = True
            continue
        if hit:
            if ln.startswith("=") or not ln.strip():
                break
            if ln.strip().startswith("-"):
                names.append(ln.strip().lstrip("- ").strip())
    return names


def parse_xpass(text):
    """XFAIL 变绿 = XPASS：缺陷被顺手修了，该转正成正向断言。"""
    return re.findall(r"^\s*\[XPASS\]\s*(.+?)\s*$", text, re.M)


# ---------------------------------------------------------------- 执行


def server_alive():
    try:
        with urllib.request.urlopen(BASE + "/doc.html", timeout=3):
            return True
    except urllib.error.HTTPError:
        return True                     # 有响应就算活着（404 也行）
    except Exception:
        return False


def run_step(sid, name, script, needs_server, timeout, extra):
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return {"id": sid, "name": name, "script": script, "rc": None,
                "out": "", "err": "[FATAL] 脚本不存在: %s" % path,
                "secs": 0.0, "missing": True}

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.time()
    try:
        proc = subprocess.run([PY, path] + extra, cwd=HERE, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        rc = proc.returncode
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace")
        err = "超时 %ds" % timeout
        rc = None
    return {"id": sid, "name": name, "script": script, "rc": rc,
            "out": out, "err": err, "secs": time.time() - t0, "missing": False}


def sweep(steps, title):
    """让每个联调脚本清掉自己的测试行。

    ⚠️ 必须**跑前也清**。老脚本只在开跑前清理、跑完不收尾，
    于是"上一轮留下的行"会毒害"下一轮"：m2_page_verify 的判据里有 total，
    表里多出几行 → 本应 32/0 变成 31/1。
    这类失败长得像回归，其实是脏状态 —— 所以回归入口必须自带双向清扫。
    """
    print("\n[%s] 清扫测试残渣" % title)
    for sid, name, script, srv, _to in steps:
        if not srv or not os.path.exists(os.path.join(HERE, script)):
            continue
        r = run_step(sid, name, script, srv, 120, ["--clean"])
        msg = None
        for key in ("已清理", "已删除", "已删"):
            for ln in r["out"].splitlines():
                if key in ln and "===" not in ln:
                    msg = ln.strip()
                    break
            if msg:
                break
        print("  [%-5s] %s" % (sid, msg or "rc=%s（没抠到计数行）" % r["rc"]))


# ---------------------------------------------------------------- 自检


def selftest():
    """变异自检：喂进三种合成输出，确认解析器分辨得出来。

    关键不是"解析对了"，而是"喂红的必须报红"——否则整张表就是自我安慰。
    """
    global _passed, _failed
    _passed, _failed = [], []

    def eq(name, got, want):
        if got == want:
            _passed.append(name)
            print("  [PASS] %s" % name)
        else:
            _failed.append(name)
            print("  [FAIL] %s  期望 %r 实得 %r" % (name, want, got))

    print("=" * 68)
    print("[SELFTEST] 喂合成输出，确认汇总/判定有判别力")
    print("=" * 68)

    green_a = "结果: 32 通过 / 0 失败\n"
    green_b = "结果：通过 32 / 失败 0 / 已知(XFAIL) 2\n"
    red = ("结果: 30 通过 / 2 失败\n失败项：\n"
           "  - BUG-001 pageSize=-1 未归一化\n  - BUG-002 status=5 未拒绝\n"
           + "=" * 10 + "\n")
    crash = "[FATAL] 连接被拒绝\n"

    eq("A. ASCII 汇总行 -> 通过=32", parse_counts(green_a)[0], 32)
    eq("A. ASCII 汇总行 -> 失败=0", parse_counts(green_a)[1], 0)
    eq("B. 全角汇总行 -> 通过=32", parse_counts(green_b)[0], 32)
    eq("B. 全角汇总行 -> xfail=2", parse_counts(green_b)[2], 2)
    eq("C. 红行 -> 失败=2", parse_counts(red)[1], 2)
    eq("C. 失败项抠出 2 条", len(parse_failures(red)), 2)
    eq("C. 失败项首条对齐", parse_failures(red)[0][:7], "BUG-001")
    eq("D. 崩掉时不许猜成 0 失败", parse_counts(crash)[1], None)
    eq("D. XPASS 可被识别",
       len(parse_xpass("  [XPASS] C7.1 现在对了")), 1)

    print("\n" + "=" * 68)
    print("自检结果: %d 通过 / %d 失败" % (len(_passed), len(_failed)))
    print("全绿 → 说明这张表不是永远绿的摆设。")
    print("=" * 68)
    sys.exit(1 if _failed else 0)


# ---------------------------------------------------------------- main


def main():
    argv = sys.argv[1:]

    if "--selftest" in argv:
        selftest()

    if "--list" in argv:
        print("步骤清单（%d 步）：" % len(STEPS))
        for sid, name, script, srv, _to in STEPS:
            print("  %-5s %-24s %-22s %s" % (sid, name, script,
                                             "需服务" if srv else "纯静态"))
        return 0

    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    extra = ["--clean"] if "--clean" in argv else []
    skip_static = "--no-static" in argv

    steps = [s for s in STEPS if not (skip_static and not s[3])]
    if only:
        steps = [s for s in steps if only.lower() in (s[0] + s[1] + s[2]).lower()]
    if not steps:
        print("[FATAL] --only %r 没匹配到任何步骤，先跑 --list 看看" % only)
        return 2

    need_srv = any(s[3] for s in steps)
    print("=" * 74)
    print("M2 回归总入口  |  %d/%d 步  |  目标 %s" % (len(steps), len(STEPS), BASE))
    if extra:
        print("附加参数: %s" % " ".join(extra))
    print("=" * 74)

    if need_srv:
        print("\n[预检] 应用是否在 %s 上跑着 ..." % BASE)
        if not server_alive():
            print("  [FATAL] 连不上 %s —— 先把 Spring Boot 起起来再跑。" % BASE)
            print("          （想只跑静态检查：python m2_regression.py --only S0）")
            return 2
        print("  ok，端口有响应")

    results = []
    do_sweep = not extra and "--no-cleanup" not in argv
    if need_srv and do_sweep:
        sweep(steps, "预处理")

    for sid, name, script, srv, timeout in steps:
        print("\n" + "-" * 74)
        print(">>> [%s] %s  (%s)" % (sid, name, script))
        print("-" * 74)
        r = run_step(sid, name, script, srv, timeout, extra)
        results.append(r)
        if r["out"]:
            sys.stdout.write(r["out"] if r["out"].endswith("\n") else r["out"] + "\n")
        if r["err"] and r["err"].strip() and r["rc"] is None:
            print("[stderr] %s" % r["err"].strip()[:400])
        print("<<< [%s] 退出码=%s  用时 %.1fs"
              % (sid, "超时" if r["rc"] is None else r["rc"], r["secs"]))

    # ---- 汇总表
    print("\n" + "=" * 74)
    print("M2 回归汇总")
    print("=" * 74)
    print("  %-6s %-22s %6s %6s %7s %8s  %s"
          % ("步骤", "名称", "通过", "失败", "XFAIL", "用时", "判定"))
    print("  " + "-" * 70)

    bad = []
    for r in results:
        p, f, x = parse_counts(r["out"])
        if r["missing"]:
            verdict = "缺失"
        elif r["rc"] is None:
            verdict = "超时"
        elif r["rc"] == 0 and f in (None, 0):
            verdict = "PASS"
        elif r["rc"] == 0:
            verdict = "PASS"          # 退出码是权威，计数只作参考
        else:
            verdict = "FAIL"
        if verdict != "PASS":
            bad.append((r, verdict))
        print("  %-6s %-22s %6s %6s %7s %7.1fs  %s"
              % (r["id"], r["name"],
                 "n/a" if p is None else p,
                 "n/a" if f is None else f,
                 "n/a" if x is None else x,
                 r["secs"], verdict))

    # ---- XFAIL / XPASS 提醒
    all_xfail, all_xpass = [], []
    for r in results:
        all_xfail += re.findall(r"^\s*\[XFAIL\]\s*(.+?)\s*$", r["out"], re.M)
        all_xpass += [(r["id"], n) for n in parse_xpass(r["out"])]
    if all_xfail:
        print("\n  已知缺陷（XFAIL，不计入红绿）：")
        for n in all_xfail:
            print("    · %s" % n[:100])
    if all_xpass:
        print("\n  ⚠ XPASS：下面这些原本登记的缺陷现在绿了，该转正成正向断言：")
        for sid, n in all_xpass:
            print("    · [%s] %s" % (sid, n[:100]))

    # ---- 红的时候指路
    if bad:
        print("\n  失败指路：")
        for r, verdict in bad:
            print("    · [%s] %s -> %s" % (r["id"], r["name"], verdict))
            for nm in parse_failures(r["out"]):
                print("        - %s" % nm[:110])
            print("        单跑复查：%s %s" % (PY, os.path.join(HERE, r["script"])))

    print("\n" + "=" * 74)
    bad_ids = set(b[0]["id"] for b in bad)
    total_pass = sum(1 for r in results if r["id"] not in bad_ids)
    print("总计: %d/%d 步通过" % (total_pass, len(results)))
    print("结论: %s" % ("M2 全绿，可以写验收记录并提交" if not bad
                      else "有 %d 步不绿，先别提交" % len(bad)))
    print("=" * 74)

    # ---- 收尾清扫（与前置清扫配对，保证跑完把库恢复原样）
    if do_sweep:
        sweep(steps, "收尾")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
