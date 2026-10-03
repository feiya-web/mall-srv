"""源码"粘贴污染"扫描器 —— 抓那些编译器和解释器都不会报错的隐藏字符。

用法：
    python scan_poisoned.py                    # 扫默认范围（项目源码 + 工具脚本）
    python scan_poisoned.py <路径> [<路径>...]  # 扫指定文件或目录（目录会递归）
    python scan_poisoned.py --selftest         # 变异自检：造一个污染文件，确认真能抓到

为什么需要它（本项目一天内中了两次）：
    1. 整文件粘贴 → 把 `package` / `class` 声明吃掉 → javac 报 "需要 class、interface、enum 或 record"。
       这类至少编译器会喊。
    2. 粘贴把 HTML 实体 `&#8203;`（零宽空格）当成**字面字符**带了进来：
           if __name__ == "__&#8203;main__":
       于是这个守卫永远为假 —— 脚本一行都不执行、**零输出、退出码 0**。
       编译器/解释器**都不会报错**，因为语法完全合法，只是字符串内容变了。

    第 2 类才是真正危险的：它不报错。凡是"不报错的错误"，都要给它装一个会叫的报警器。
"""
import pathlib
import re
import sys

import _env

# 只扫代码类文件；.md 不扫（文档里出现 &amp; 之类是合法的）
SUFFIXES = {".py", ".java", ".xml", ".yml", ".yaml", ".properties", ".sql", ".json", ".cmd", ".sh"}

SKIP_DIRS = {".git", ".idea", "target", "node_modules", "__pycache__", "logs"}

# 扫描器自身的源码必然包含这些模式（文档示例、白名单常量、selftest 样本），
# 不排除的话它每次都会报自己 —— 16 处全在自己身上，一条真问题都淹没在里面。
# 误报的代价不是"多看一眼"，而是**训练人忽略这个工具的输出**（与缺陷①同类后果）。
SELF = pathlib.Path(__file__).resolve()

# HTML 实体：&#8203; &#x200B; &nbsp; &amp; ...
RE_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,8});")

# XML 里这 5 个预定义实体是**合法且必需**的（MyBatis mapper 里写 `<` 就得写 &lt;），
# 在 .xml 文件里必须放行，否则整个 mapper 目录全是误报。
XML_OK = {"&lt;", "&gt;", "&amp;", "&quot;", "&apos;"}

# 零宽 / 双向 / BOM / 不换行空格 —— 在编辑器里都看不见
INVISIBLE = {
    0x00A0: "NBSP 不换行空格",
    0x200B: "ZWSP 零宽空格",
    0x200C: "ZWNJ 零宽不连字",
    0x200D: "ZWJ 零宽连字",
    0x200E: "LRM 从左到右标记",
    0x200F: "RLM 从右到左标记",
    0x202A: "LRE",
    0x202B: "RLE",
    0x202C: "PDF",
    0x202D: "LRO",
    0x202E: "RLO",
    0x2060: "WJ 词连接符",
    0xFEFF: "BOM/零宽不换行空格",
    0x3164: "HANGUL FILLER",
}

HITS = []


def report(path, lineno, kind, line, detail):
    HITS.append((path, lineno, kind, line, detail))
    print("  [HIT] %s:%d  %s" % (path, lineno, kind))
    print("        %s" % detail)
    print("        %r" % line[:120])


def scan_file(p: pathlib.Path):
    if p.resolve() == SELF:
        print("  [SKIP] 扫描器自身（源码里必然含模式样本，扫自己只会制造误报）")
        return
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print("  [SKIP] %s 不是 UTF-8（编码本身就该查一下）" % p)
        return
    is_xml = p.suffix.lower() in (".xml", ".yml", ".yaml")
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in RE_ENTITY.finditer(line):
            tok = m.group(0)
            if is_xml and tok in XML_OK:
                continue          # mapper XML 里的 &lt; / &gt; 是正常的
            report(p, lineno, "HTML 实体字面量", line, "发现 %r" % tok)
        bad = [(j, INVISIBLE[ord(ch)]) for j, ch in enumerate(line) if ord(ch) in INVISIBLE]
        if bad:
            report(p, lineno, "不可见字符", line,
                   "; ".join("第 %d 列 %s" % (j + 1, name) for j, name in bad))


def iter_files(targets):
    for t in targets:
        p = pathlib.Path(t)
        if p.is_file():
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUFFIXES:
                    if f.resolve() == SELF:
                        continue      # 见 SELF 的说明
                    if not any(part in SKIP_DIRS for part in f.parts):
                        yield f
        else:
            print("  [WARN] 路径不存在: %s" % p)


def default_targets():
    """默认扫：仓库根下的 src/ 与 tools/。"""
    root = _env.ROOT
    return [root / "src", root / "tools"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--selftest" in sys.argv:
        return selftest()

    targets = args or [str(x) for x in default_targets()]
    print("=" * 68)
    print("粘贴污染扫描")
    for t in targets:
        print("  目标: %s" % t)
    print("=" * 68)

    n = 0
    for f in iter_files(targets):
        n += 1
        scan_file(f)

    print("\n扫了 %d 个文件，命中 %d 处" % (n, len(HITS)))
    if HITS:
        print("\n修法：把那几个字符删掉手打一遍。**不要重新粘贴** —— 大概率再中一次。")
        print("      看不见的字符在编辑器里删不干净（你以为删了，其实只删了一半）。")
        return 1
    print("干净。")
    return 0


def selftest():
    """造一个含污染的临时文件，确认两种模式都能被抓到。"""
    import tempfile
    print("=" * 68)
    print("[SELFTEST] 造污染文件，确认扫描器真的会叫")
    print("=" * 68)
    tmp = pathlib.Path(tempfile.mkdtemp()) / "poisoned.py"

    cases = [
        ("HTML 实体写字面量", 'if __name__ == "__&#8203;main__":\n'),
        ("零宽空格直接混入", 'if __name__ == "__\u200bmain__":\n'),
        ("BOM 在行首", '\ufeffimport os\n'),
    ]
    ok = True
    for name, content in cases:
        tmp.write_text(content, encoding="utf-8")
        before = len(HITS)
        print("\n[%s]" % name)
        scan_file(tmp)
        caught = len(HITS) > before
        print("       -> %s" % ("抓到 ✓" if caught else "没抓到 ✗ 判别力不足"))
        ok = ok and caught

    # 反向：干净文件必须不报
    tmp.write_text('if __name__ == "__main__":\n', encoding="utf-8")
    before = len(HITS)
    scan_file(tmp)
    print("\n[反向] 干净文件不应命中")
    clean = len(HITS) == before
    print("       -> %s" % ("不误报 ✓" if clean else "误报了 ✗"))

    # 反向：扫描器自己也不该被报（它源码里全是模式样本）
    before = len(HITS)
    scan_file(SELF)
    print("\n[反向] 扫描器自身不应命中")
    selfclean = len(HITS) == before
    print("       -> %s" % ("正确跳过 ✓" if selfclean else "报了自己 ✗（会淹没真问题）"))

    # 反向：默认扫描目标下不该再出现"全是自己的命中"
    print("\n[反向] 默认扫描根内，命中数应为 0")
    n_before = len(HITS)
    for f in iter_files([str(x) for x in default_targets()]):
        scan_file(f)
    real = len(HITS) - n_before
    print("       -> 命中 %d 处  %s" % (real, "干净 ✓" if real == 0 else "有残留 ✗"))

    allok = ok and clean and selfclean and real == 0
    print("\n" + "=" * 68)
    print("[SELFTEST] %s" % ("全部通过" if allok else "有失败"))
    print("=" * 68)
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
