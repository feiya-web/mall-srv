"""M1 现场准备：为 zhangsan / lisi 铺好购物车，输出可直接在 Apifox 里发起的【越权下单】用例。

用法：
    python m1_setup.py          # 清空双方购物车并重新铺数据
    python m1_setup.py --check  # 只读：打印双方购物车现状（排查用）

依赖：应用已在 http://localhost:8080 运行，Redis 已带密码启动。
"""
import json
import sys
import urllib.error
import urllib.request

from _env import BASE, DEMO_PASSWORD as PASSWORD


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


def login(username):
    status, js = call("POST", "/user/login", body={"username": username, "password": PASSWORD})
    if js.get("code") != 1:
        raise SystemExit("[FAIL] %s 登录失败: HTTP %s %s" % (username, status, js))
    return js["data"]["token"]


def cart_add(token, dish_id):
    status, js = call("POST", "/user/shoppingCart/add", token=token, body={"dishId": dish_id})
    if js.get("code") != 1:
        raise SystemExit("[FAIL] 加购 dishId=%s 失败: HTTP %s %s" % (dish_id, status, js))


def cart_list(token):
    status, js = call("GET", "/user/shoppingCart/list", token=token)
    if js.get("code") != 1:
        raise SystemExit("[FAIL] 购物车查询失败: HTTP %s %s" % (status, js))
    return js["data"]


def dump(title, items):
    print("=== %s ===" % title)
    if not items:
        print("  (空)")
    for it in items:
        print("  cartItemId=%-4s %-8s 单价=%-7s x%s" % (it["id"], it["name"], it["amount"], it["number"]))


def main():
    zs = login("zhangsan")
    ls = login("lisi")

    if "--check" in sys.argv:
        dump("zhangsan(userId=1) 购物车", cart_list(zs))
        dump("lisi(userId=2) 购物车", cart_list(ls))
        return

    # 清空历史，保证现场干净
    call("DELETE", "/user/shoppingCart/clean", token=zs)
    call("DELETE", "/user/shoppingCart/clean", token=ls)

    # zhangsan：1 件；lisi：3 件
    cart_add(zs, 1)                      # 拍黄瓜 8.00
    for dish_id in (3, 5, 6):            # 机械键盘 299 / 空气炸锅 399 / 无线鼠标 89
        cart_add(ls, dish_id)

    zs_items = cart_list(zs)
    ls_items = cart_list(ls)
    dump("zhangsan(userId=1) 购物车", zs_items)
    dump("lisi(userId=2) 购物车", ls_items)

    zs_ids = [it["id"] for it in zs_items]
    ls_ids = [it["id"] for it in ls_items]
    # 越权参数：zhangsan 提交自己的 1 条 + lisi 的 2 条
    payload = {
        "consignee": "徐望纾",
        "phone": "13800000000",
        "address": "上海市浦东新区科苑路 100 号",
        "remark": "M1 越权用例",
        "payMethod": 1,
        "cartItemIds": zs_ids + ls_ids[:2],
    }

    print()
    print("=" * 74)
    print("【待执行用例】用 zhangsan 的 token 调用下单接口，参数里混入 lisi 的 2 条购物车 id")
    print("=" * 74)
    print("token 头 zhangsan : %s" % zs)
    print("token 头 lisi     : %s" % ls)
    print()
    print("zhangsan 自己的 cartItemId : %s" % zs_ids)
    print("lisi 的 cartItemId         : %s" % ls_ids)
    print()
    print("POST %s/user/order/submit" % BASE)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()
    print("执行后请查库核验：lisi(userId=2) 的购物车应该剩 %d 条，若少于这个数即为缺陷。" % len(ls_ids))
    print("对照 SQL：")
    print("  SELECT id,user_id,name,amount FROM shopping_cart WHERE user_id=2;")


if __name__ == "__main__":
    main()
