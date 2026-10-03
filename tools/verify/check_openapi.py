"""openapi.json 同步自检 —— 导完接口文档后跑这个，别靠肉眼。

用法：
    python check_openapi.py                        # 全量自检（默认查 docs/apifox/openapi.json）
    python check_openapi.py <path/to/openapi.json> # 指定文件
    python check_openapi.py --selftest             # 变异自检：验证这些断言真的有判别力

为什么需要它：
    openapi.json 是生成物，除了问题它自己不会叫。
    最典型的两种"安静的错误"：
      1. 【悬空引用】工具只把类型加进了 REF_TYPES，忘了往 components.schemas 补定义
         → 生成 $ref: "#/components/schemas/Xxx"，但 Xxx 根本不存在。
         JSON 语法完全合法，json.load 也不报错，只有导入 Apifox 时才炸。
      2. 【空壳 schema】requestBody 退化成 {"type":"object"}
         → 文档里这个接口什么字段都没有，等于没写。
    这两件事肉眼都很难发现（JSON 是压缩成一坨的），所以必须让脚本判。
"""
import json
import pathlib
import re
import sys

from _env import OPENAPI as DEFAULT

# 同步后必须存在的接口（本轮 M2 新增的 4 个）
EXPECTED_PATHS = [
    ("get", "/admin/employee/page"),
    ("post", "/admin/employee"),
    ("put", "/admin/employee/{id}"),
    ("put", "/admin/employee/{id}/status/{status}"),
]

# 带 @RequestBody 的接口 -> 期望引用到的组件名
EXPECTED_BODY_REF = {
    ("post", "/admin/employee"): "EmployeeDTO",
    ("put", "/admin/employee/{id}"): "EmployeeUpdateDTO",
}

TAG_DESC_MUST_CHANGE = "员工管理"

_passed, _failed, _known = [], [], []


def check(name, cond, detail=""):
    if cond:
        _passed.append(name)
        print("  [PASS] %s" % name)
    else:
        _failed.append(name)
        print("  [FAIL] %s  %s" % (name, detail))


def body_schema(doc, method, path):
    op = doc.get("paths", {}).get(path, {}).get(method) or {}
    return (((op.get("requestBody") or {}).get("content") or {})
            .get("application/json", {}).get("schema"))


def all_refs(doc):
    """递归抓出所有 $ref 指向的组件名"""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    m = re.search(r"#/components/schemas/([A-Za-z0-9_]+)", v)
                    if m:
                        found.append(m.group(1))
                else:
                    walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(doc)
    return found


# ---------------------------------------------------------------- 自检项
def s1_loadable(path):
    print("\n[S1] 文件可解析 + 编码正确")
    raw = path.read_bytes()
    print("     前 4 字节: %r   大小: %d 字节" % (raw[:4], len(raw)))
    check("S1.1 无 UTF-8 BOM（PowerShell 的 > 会写成 UTF-16，必须警惕）",
          raw[:3] != b"\xef\xbb\xbf", "文件带了 UTF-8 BOM")
    check("S1.2 不含 UTF-16 BOM",
          raw[:2] not in (b"\xff\xfe", b"\xfe\xff"),
          "文件是 UTF-16 —— 大概率是在 PowerShell 里用了 > 重定向")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        check("S1.3 UTF-8 解码 + JSON 解析", False, "%s" % exc)
        return None
    check("S1.3 UTF-8 解码 + JSON 解析", True)
    check("S1.4 openapi 版本是 3.0.x", str(doc.get("openapi", "")).startswith("3.0"),
          "openapi=%s" % doc.get("openapi"))
    return doc


def s2_paths(doc):
    print("\n[S2] 路径完整性")
    paths = doc.get("paths", {})
    ops = sum(len(v) for v in paths.values())
    # 注意区分两个数：paths 是"路径条数"，ops 是"方法×路径"的操作数。
    # 本轮 4 个新操作只带来 3 条新路径（PUT /{id} 是挂在已有路径上的），所以两者增量不同。
    print("     路径 %d 条 / 操作 %d 个" % (len(paths), ops))
    check("S2.1 操作数 >= 44（M2 之前是 40）", ops >= 44, "实际 %d 个" % ops)
    for method, p in EXPECTED_PATHS:
        check("S2.2 存在 %s %s" % (method.upper(), p),
              method in (paths.get(p) or {}), "该接口没进文档")


def s3_body_refs(doc):
    print("\n[S3] requestBody 不是空壳（关键：{\"type\":\"object\"} 等于没文档）")
    for (method, p), want in EXPECTED_BODY_REF.items():
        sch = body_schema(doc, method, p)
        got = (sch or {}).get("$ref", "").split("/")[-1]
        check("S3 %s %s -> $ref %s" % (method.upper(), p, want),
              got == want,
              "实际 schema=%s\n          多半是 %s 没加进工具的 REF_TYPES" % (sch, want))


def s4_dangling(doc):
    print("\n[S4] 悬空引用扫描（本轮最容易踩的坑）")
    refs, have = set(all_refs(doc)), set(doc.get("components", {}).get("schemas", {}))
    missing = sorted(refs - have)
    print("     被引用的组件: %s" % sorted(refs))
    print("     已定义的组件: %s" % sorted(have))
    check("S4.1 所有 $ref 都能在 components.schemas 找到", not missing,
          "悬空: %s  <- REF_TYPES 加了但 schemas 没补" % missing)
    orphan = sorted(have - refs)
    check("S4.2 没有定义了却没人引用的组件", not orphan, "孤立: %s" % orphan)


def s5_schema_body(doc):
    print("\n[S5] 新组件本身有字段（不是空对象）")
    schemas = doc.get("components", {}).get("schemas", {})
    for name in ("EmployeeDTO", "EmployeeUpdateDTO"):
        s = schemas.get(name) or {}
        props = s.get("properties") or {}
        check("S5.1 %s 定义了 properties（%d 个字段）" % (name, len(props)), bool(props),
              "是空对象 = 文档里看不到字段")
    # 反向：白名单要能看出来。EmployeeUpdateDTO 刻意不收 username/password/status
    upd = (schemas.get("EmployeeUpdateDTO") or {}).get("properties") or {}
    for forbidden in ("username", "password", "status", "id"):
        check("S5.2 EmployeeUpdateDTO 不含 %s（白名单边界）" % forbidden,
              forbidden not in upd, "文档里出现了 %s，说明 DTO 被写宽了" % forbidden)


# ---------------------------------------------------------------- main
def count_ops(doc):
    """返回 (操作数, 路径数)。只数真正的 HTTP 方法，不把 parameters 之类算进去。"""
    methods = ("get", "post", "put", "delete", "patch")
    ops = sum(1 for p in doc.get("paths", {}).values()
              for m in p if m in methods)
    return ops, len(doc.get("paths", {}))


def s6_readme(doc, text=None):
    """README 里的数字最容易变成"骗人的旧数据" —— 它不会报错，只是慢慢变成假话。

    实测事故：openapi.json 已同步到 44 个操作，README 还写着"共 40 个接口 / 34 个路径"，
    员工管理分组也只列了 2 个接口。**生成物同步了，说明文档没同步。**
    """
    print("\n[S6] docs/apifox/README.md 与实际文档一致")
    readme = DEFAULT.parent / "README.md"
    if text is None:
        if not readme.exists():
            check("S6.1 README 存在", False, "缺 %s" % readme)
            return
        text = readme.read_text(encoding="utf-8")
    ops, paths = count_ops(doc)

    m = re.search(r"共\s*(\d+)\s*个接口\s*/\s*(\d+)\s*个路径", text)
    check("S6.1 README 声明了接口数与路径数", bool(m), "没找到『共 N 个接口 / M 个路径』")
    if m:
        check("S6.2 接口数与实际一致（%d）" % ops, int(m.group(1)) == ops,
              "README 写 %s，实际 %d" % (m.group(1), ops))
        check("S6.3 路径数与实际一致（%d）" % paths, int(m.group(2)) == paths,
              "README 写 %s，实际 %d" % (m.group(2), paths))

    # 逐个接口核对：文档里有的路径，README 里得提到
    missing = [p for p in doc.get("paths", {}) if p not in text]
    check("S6.4 README 覆盖全部路径（缺 %d 个）" % len(missing), not missing,
          "未提及: %s" % ", ".join(sorted(missing)[:6]))

    # README 自己也不该再教人用 `>` 重定向（会把 JSON 写成 0 字节 / UTF-16）
    check("S6.5 README 的重生成命令用的是 --out（不是 > 重定向）",
          "export_openapi.py --out" in text and "export_openapi.py >" not in text,
          "README 里仍存在 `> docs/apifox/openapi.json` 的写法")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = pathlib.Path(args[0]) if args else DEFAULT
    if not path.exists():
        raise SystemExit("[FATAL] 文件不存在: %s" % path)

    if "--selftest" in sys.argv:
        return selftest(path)

    print("=" * 68)
    print("openapi.json 同步自检  |  %s" % path)
    print("=" * 68)

    doc = s1_loadable(path)
    if doc is None:
        return 1
    s2_paths(doc)
    s3_body_refs(doc)
    s4_dangling(doc)
    s5_schema_body(doc)
    s6_readme(doc)

    print("\n" + "=" * 68)
    print("结果：通过 %d / 失败 %d" % (len(_passed), len(_failed)))
    for f in _failed:
        print("  - %s" % f)
    print("=" * 68)
    return 1 if _failed else 0


def selftest(path):
    """变异自检：故意把文档改坏，确认断言真的会变红。全程只动内存，不写文件。"""
    print("=" * 68)
    print("[SELFTEST] 改坏文档，确认断言有判别力（不写文件）")
    print("=" * 68)
    base = json.loads(path.read_text(encoding="utf-8"))

    def rerun(mutate, checker, expect_name, label):
        _failed.clear()
        doc = json.loads(json.dumps(base))
        mutate(doc)
        print("\n[%s]" % label)
        checker(doc)
        red = any(expect_name in f for f in _failed)
        _failed.clear()
        check("%s -> 对应断言变红" % label, red, "断言没红 = 没有判别力")

    def _post_op(doc):
        # setdefault 而不是直接下标：这样同步前后（文件里还没有这个路径时）都能跑
        return doc["paths"].setdefault("/admin/employee", {}).setdefault("post", {})

    def inject_dangling(doc):
        # 注入一个指向"不存在组件"的 $ref —— 这正是"REF_TYPES 加了、schemas 忘了补"的形态。
        # 用必然不存在的名字，好在文件同步前后都能稳定复现。
        _post_op(doc)["requestBody"] = {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/NoSuchDTO"}}}}

    def flatten_body(doc):
        # 直接换成空壳 schema（不假设当前有没有 requestBody，否则在未同步的文件上会崩）
        _post_op(doc)["requestBody"] = {"content": {"application/json": {
            "schema": {"type": "object"}}}}

    rerun(inject_dangling, s4_dangling, "S4.1", "M1 注入悬空 $ref（模拟只改 REF_TYPES）")
    rerun(flatten_body, s3_body_refs, "S3 POST /admin/employee", "M2 把 requestBody 打回 {\"type\":\"object\"}")

    # S6：README 一致性。用改后的文本喂进去，不碰磁盘上的 README。
    readme_text = (DEFAULT.parent / "README.md").read_text(encoding="utf-8")

    def run_s6(label, mutated_text, expect_name):
        _failed.clear()
        print("\n[%s]" % label)
        s6_readme(json.loads(json.dumps(base)), text=mutated_text)
        red = any(expect_name in f for f in _failed)
        _failed.clear()
        check("%s -> 对应断言变红" % label, red, "断言没红 = 没有判别力")

    run_s6("M3 README 接口数写小 1（改成 43）",
           re.sub(r"共\s*\d+\s*个接口", "共 43 个接口", readme_text), "S6.2")
    run_s6("M4 README 的 --out 退回 `>` 重定向",
           readme_text.replace("export_openapi.py --out", "export_openapi.py >"),
           "S6.5")

    print("\n" + "=" * 68)
    print("[SELFTEST] 通过 %d / 失败 %d" % (len(_passed), len(_failed)))
    print("=" * 68)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
