"""验收脚本共用配置：路径与连接信息一律可被环境变量覆盖。

与 `sql/benchmark/*.sh` 保持同一套命名（MYSQL_BIN/HOST/PORT/USER/PASSWORD/DB、BASE_URL、
DEMO_PASSWORD），理由：**不把本机绝对路径和口令硬编码进仓库**。
默认值都是"能开箱跑"的口径，本机特殊（如 mysql 不在 PATH）用环境变量覆盖即可。

本机运行示例（Git Bash）：
    export MYSQL_BIN="/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe"
    export PYTHON="/c/Users/<you>/.workbuddy/binaries/python/versions/3.13.12/python.exe"
"""
import os
import pathlib
import re
import shutil
import sys


def _native(p):
    """把 Git Bash 形式的路径转成原生 Windows 进程能解析的形式。

    Bash 里习惯写 MYSQL_BIN=/c/Program Files/.../mysql.exe，
    但本脚本是**原生进程**，`/c/...` 它解析不了，会直接
    FileNotFoundError: [WinError 2] 系统找不到指定的文件 —— 看着像"mysql 没装"。
    """
    if os.name == "nt" and re.match(r"^/[a-zA-Z]/", p):
        return p[1].upper() + ":" + p[2:]
    return p


# 仓库根目录：本文件位于 <root>/tools/verify/_env.py
ROOT = pathlib.Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------- 数据库
MYSQL_BIN = _native(os.environ.get("MYSQL_BIN", "mysql"))   # 默认取 PATH 中的客户端
MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = os.environ.get("MYSQL_PORT", "3306")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "123456")
DB = os.environ.get("MYSQL_DB", "mall_order")

# ---------------------------------------------------------------- 应用
BASE = os.environ.get("BASE_URL", "http://localhost:8080")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "123456")   # 与种子数据一致
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")

# 应用日志落点（application.yml 的 logging.file.name），SQL 证据链要用
LOG = pathlib.Path(os.environ.get("APP_LOG", str(ROOT / "logs" / "mall-order.log")))

# Java 源码根，供"源码断言"类用例读取（如白名单字段核对）
SRC = ROOT / "src" / "main" / "java"

# 子流程解释器：默认用当前解释器，避免写死本机 Python 路径
PYTHON = _native(os.environ.get("PYTHON", sys.executable))

# openapi.json 产物位置
OPENAPI = ROOT / "docs" / "apifox" / "openapi.json"


def mysql_argv(query):
    """拼出调用 mysql 客户端执行一条 SQL 的参数表。

    找不到客户端时直接给出可操作的报错，而不是让 subprocess 抛裸 FileNotFoundError
    （[WinError 2] 看起来像"数据库没装"，实际只是 mysql 不在 PATH 里）。
    """
    if shutil.which(MYSQL_BIN) is None and not pathlib.Path(MYSQL_BIN).exists():
        raise SystemExit(
            "[FATAL] 找不到 mysql 客户端：%r\n"
            "        mysql 不在本机 PATH 里，脚本里也没默认写死绝对路径（仓库不应含本机路径）。\n"
            "        请先用环境变量指定完整路径再跑：\n"
            "          Git Bash  : export MYSQL_BIN=\"/c/<盘>/<MySQL 安装目录>/bin/mysql.exe\"\n"
            "          PowerShell: $env:MYSQL_BIN = \"<盘>:\\<MySQL 安装目录>\\bin\\mysql.exe\"\n"
            % MYSQL_BIN)
    return [MYSQL_BIN, "-h", MYSQL_HOST, "-P", MYSQL_PORT,
            "-u", MYSQL_USER, "-p" + MYSQL_PASSWORD, "-D", DB,
            "--default-character-set=utf8mb4", "-N", "-B", "-e", query]
