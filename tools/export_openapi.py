# -*- coding: utf-8 -*-
"""
接口文档导出工具：直接从 Controller 源码提取接口定义，生成 OpenAPI 3.0 JSON。

为什么不引入 springdoc：本机 Maven 镜像拉不到 org.springdoc 依赖。
改为源码静态提取，零依赖，且文档与实现同源、不会脱节。

用法：python tools/export_openapi.py > docs/apifox/openapi.json
"""
import json
import pathlib
import re
import sys


# 仓库根目录 = 本脚本所在目录的上一级，保证在任何机器上克隆后都能直接运行
ROOT = pathlib.Path(__file__).resolve().parent.parent
CTRL_DIR = ROOT / "src/main/java/com/mall/controller"

HTTP_MAP = {"Get": "get", "Post": "post", "Put": "put", "Delete": "delete"}

JAVA_TYPE = {
    "String": "string", "Long": "integer", "Integer": "integer",
    "int": "integer", "Boolean": "boolean", "LocalDate": "string",
}

# Controller -> 中文分组名 / 描述
TAG_DESC = {
    "EmployeeController": ("员工管理", "管理端员工登录、信息查询、新增/编辑、启用禁用与分页条件查询"),
    "CategoryController": ("分类管理", "商品分类与组合套装分类的增删改查"),
    "DishController": ("商品管理", "商品及喜好维护、上下架，变更后清理 Cache Aside 缓存"),
    "SetmealController": ("组合套装管理", "组合套装与组合套装明细维护、上下架"),
    "OrderController": ("订单管理", "订单分页、发货/取消订单/配送/完成，全部状态流转受订单状态机约束"),
    "CacheController": ("缓存监控", "Cache Aside 缓存命中率统计"),
    "UserLoginController": ("用户登录", "用户端登录，签发用户 JWT"),
    "UserDishController": ("商品浏览", "按分类浏览商品与组合套装（高频读，走 Cache Aside 缓存）"),
    "ShoppingCartController": ("购物车", "购物车加购、减少、列表与清空"),
    "UserOrderController": ("用户订单", "下单（事务+幂等）、模拟支付、取消、历史订单与详情"),
}

REF_TYPES = {"LoginDTO", "ShoppingCartDTO", "OrdersSubmitDTO", "Category", "DishDTO", "SetmealDTO","EmployeeDTO", "EmployeeUpdateDTO"}


def split_top_level(text: str):
    """按顶层逗号切分（忽略 <> 与 () 内部的逗号）"""
    depth, cur, out = 0, "", []
    for ch in text:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def load_dto_fields(dto_type: str):
    """读取分页/查询 DTO 的字段，展开为 query 参数（Apifox 里可直接填值）"""
    p = ROOT / "src/main/java/com/mall/pojo/dto" / ("%s.java" % dto_type)
    if not p.exists():
        return None
    src = p.read_text(encoding="utf-8")
    seen, fields = set(), []
    for m in re.finditer(r'private\s+([\w<>]+)\s+(\w+)\s*[=;]', src):
        jtype, name = m.group(1), m.group(2)
        if name in seen:
            continue
        seen.add(name)
        fields.append({"name": name, "in": "query", "required": False,
                       "schema": {"type": JAVA_TYPE.get(jtype, "string")}})
    return fields or None


def parse_params(inner: str):
    """从括号内的参数文本提取参数，判定 path / query / body"""
    params, has_body = [], False
    for p in split_top_level(inner):
        # 类型部分允许泛型写法，如 List<Long> ids
        m = re.search(r'([\w<>,\[\]]+)\s+(\w+)$', p)
        if not m:
            continue
        jtype, name = m.group(1), m.group(2)
        # 查询类 DTO（无注解绑定）展开为其字段
        if jtype.endswith("DTO") and "@RequestBody" not in p and "@PathVariable" not in p:
            expanded = load_dto_fields(jtype)
            if expanded:
                params.extend(expanded)
                continue
        if "@RequestBody" in p:
            has_body = True
            schema = ({"$ref": "#/components/schemas/%s" % jtype} if jtype in REF_TYPES
                      else {"type": "object"})
            params.append({"name": name, "in": "body", "schema": schema})
        elif "@PathVariable" in p:
            params.append({"name": name, "in": "path", "required": True,
                           "schema": {"type": JAVA_TYPE.get(jtype, "string")}})
        elif jtype.startswith("List<"):
            params.append({"name": name, "in": "query", "required": False,
                           "schema": {"type": "array", "items": {"type": "integer"}}})
        else:
            params.append({"name": name, "in": "query", "required": False,
                           "schema": {"type": JAVA_TYPE.get(jtype, "string")}})
    return params, has_body


def parse_controller(src: str):
    """逐行扫描 Controller 源码，兼容跨行签名与前置注解"""
    base_m = re.search(r'@RequestMapping\("([^"]*)"\)', src)
    base_path = base_m.group(1) if base_m else ""

    lines, ops, i = src.splitlines(), [], 0
    while i < len(lines):
        m = re.match(r'@(Get|Post|Put|Delete)Mapping\b(.*)$', lines[i].strip())
        if not m:
            i += 1
            continue
        http = HTTP_MAP[m.group(1)]
        vm = re.search(r'"([^"]*)"', m.group(2))
        sub = vm.group(1) if vm else ""

        # 跳过 @AutoLog 等前置注解，找到方法声明行
        j = i + 1
        while j < len(lines) and not re.search(r'\bpublic\s+', lines[j]):
            j += 1
        i = j + 1
        if j >= len(lines):
            break

        # 拼接跨行签名直到括号平衡
        sig, k = lines[j].strip(), j
        while sig.count("(") > sig.count(")") and k + 1 < len(lines):
            k += 1
            sig += " " + lines[k].strip()

        nm = re.search(r'public\s+[\w<>,\[\]\. ]+\s+(\w+)\s*\(', sig)
        if not nm or "(" not in sig or ")" not in sig:
            continue
        method_name = nm.group(1)
        inner = sig[sig.index("(") + 1: sig.rindex(")")]
        params, has_body = parse_params(inner)
        full = re.sub(r"/+", "/", (base_path + "/" + sub) if sub else (base_path or "/"))
        ops.append((full, http, method_name, params, has_body))
    return ops


def build():
    paths, tags = {}, []
    for f in sorted(CTRL_DIR.rglob("*.java")):
        cls = f.stem
        if cls not in TAG_DESC:
            continue
        tag, desc = TAG_DESC[cls]
        tags.append({"name": tag, "description": desc})

        for full, http, method_name, params, has_body in parse_controller(f.read_text(encoding="utf-8")):
            op = {
                "tags": [tag],
                "summary": method_name,
                "operationId": "%s_%s" % (cls, method_name),
                "parameters": [p for p in params if p["in"] != "body"],
                "responses": {"200": {"description": "OK"}},
            }
            # 路径占位符未在签名中解析到时补齐
            for ph in re.findall(r"\{(\w+)\}", full):
                if ph not in {p["name"] for p in op["parameters"]}:
                    op["parameters"].append(
                        {"name": ph, "in": "path", "required": True, "schema": {"type": "integer"}})
            if has_body:
                body = next(p for p in params if p["in"] == "body")
                op["requestBody"] = {"required": True,
                                     "content": {"application/json": {"schema": body["schema"]}}}
            if method_name == "login":          # 登录接口免鉴权
                op["security"] = []
            paths.setdefault(full, {})[http] = op

    return {
        "openapi": "3.0.1",
        "info": {
            "title": "网上商城系统 API",
            "description": "Spring Boot 2.7 + MyBatis-Plus + MySQL + Redis + JWT。"
                           "管理端前缀 /admin，用户端前缀 /user；除登录接口外均需在 Header 携带 token。"
                           "本文件由 tools/export_openapi.py 从 Controller 源码自动提取生成，与实现同源。",
            "version": "1.0.0",
        },
        "servers": [{"url": "http://localhost:8080", "description": "本地环境"}],
        "tags": tags,
        "components": {
            "securitySchemes": {"token": {"type": "apiKey", "in": "header", "name": "token",
                                          "description": "登录接口返回的 JWT，放在请求头 token 字段"}},
            "schemas": {
                "LoginDTO": {"type": "object", "required": ["username", "password"], "properties": {
                    "username": {"type": "string", "example": "zhangsan"},
                    "password": {"type": "string", "example": "123456"}}},
                "ShoppingCartDTO": {"type": "object", "properties": {
                    "dishId": {"type": "integer", "example": 3},
                    "setmealId": {"type": "integer", "example": 1},
                    "dishFlavor": {"type": "string", "example": "微辣"}}},
                "OrdersSubmitDTO": {"type": "object",
                                    "required": ["consignee", "phone", "address", "cartItemIds"],
                                    "properties": {
                                        "consignee": {"type": "string", "example": "张三"},
                                        "phone": {"type": "string", "example": "13900000001"},
                                        "address": {"type": "string", "example": "上海市嘉定区1号"},
                                        "remark": {"type": "string"},
                                        "payMethod": {"type": "integer", "example": 1},
                                        "cartItemIds": {"type": "array", "items": {"type": "integer"},
                                                        "example": [1, 2]}}},
                "Category": {"type": "object", "required": ["name", "type"], "properties": {
                    "id": {"type": "integer"}, "name": {"type": "string", "example": "手机通讯"},
                    "type": {"type": "integer", "description": "1商品分类 2组合套装分类", "example": 1},
                    "sort": {"type": "integer", "example": 10}}},
                "DishDTO": {"type": "object", "required": ["name", "categoryId", "price"], "properties": {
                    "id": {"type": "integer"}, "name": {"type": "string", "example": "机械键盘"},
                    "categoryId": {"type": "integer", "example": 2},
                    "price": {"type": "number", "example": 32.00},
                    "image": {"type": "string"}, "description": {"type": "string"},
                    "status": {"type": "integer", "description": "0停售 1起售", "example": 1},
                    "flavors": {"type": "array", "items": {"type": "object"}}}},
                "SetmealDTO": {"type": "object", "required": ["name", "categoryId", "price"], "properties": {
                    "id": {"type": "integer"}, "name": {"type": "string", "example": "双人组合套装A"},
                    "categoryId": {"type": "integer", "example": 6},
                    "price": {"type": "number", "example": 68.00},
                    "image": {"type": "string"}, "description": {"type": "string"},
                    "status": {"type": "integer", "example": 1},
                    "setmealDishes": {"type": "array", "items": {"type": "object"}}}},
                "EmployeeDTO": {"type": "object",
                                "required": ["name", "username", "password"],
                                "properties": {
                                    "name": {"type": "string", "example": "张小厨"},
                                    "username": {"type": "string", "example": "kitchen01"},
                                    "password": {"type": "string", "example": "Test@12345"},
                                    "phone": {"type": "string", "description": "选填，^1[3-9]\\d{9}$",
                                              "example": "13900001111"}}},
                "EmployeeUpdateDTO": {"type": "object",
                                      "required": ["name"],
                                      "properties": {
                                          "name": {"type": "string", "example": "张小厨"},
                                          "phone": {"type": "string", "description": "选填，^1[3-9]\\d{9}$",
                                                    "example": "13900001111"}}},
            },
        },
        "security": [{"token": []}],
        "paths": dict(sorted(paths.items())),
    }


if __name__ == "__main__":
    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if "--out" in sys.argv:
        out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
        # 自己落盘：绕开 shell 的 ①预截断 ②编码转换 ③PS 的 cp936 误解码
        out.write_text(text, encoding="utf-8", newline="\n")
        print("written %s (%d bytes, utf-8 no BOM)" % (out, out.stat().st_size))
    else:
        print(text, end="")

