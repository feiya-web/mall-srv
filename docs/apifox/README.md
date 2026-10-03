# Apifox 导入指南

接口文件：[`openapi.json`](./openapi.json)（OpenAPI 3.0 格式，Apifox 原生支持）

**共 44 个接口 / 37 个路径 / 10 个分组**，由 `tools/export_openapi.py` 从 Controller 源码自动提取生成，
接口改了重新跑一次脚本即可同步，文档不会和实现脱节。

---

## 一、导入步骤（3 步）

1. 打开 Apifox → 进入你的项目 → **项目设置 → 导入数据**
   （或在「接口管理」页面点右上角 `...` → 导入）
2. 格式选 **OpenAPI 3.0**（Swagger），上传 `docs/apifox/openapi.json`
3. 导入范围保持默认全选 → 确认导入

导入后会自动生成 10 个分组目录：员工管理 / 分类管理 / 商品管理 / 组合套装管理 / 订单管理 /
缓存监控 / 用户登录 / 商品浏览 / 购物车 / 用户订单。

---

## 二、配置鉴权（关键）

项目里有 JWT 拦截器，除登录接口外**全部接口都要在 Header 带 `token`**，否则返回 401。

OpenAPI 文件里已声明 `token` 鉴权方案，导入后：

1. 进入 **项目设置 → 鉴权 / Auth**（或在环境管理里）
2. 类型选 `API Key`，字段填：

   | 项 | 值 |
   |----|----|
   | Key（参数名） | `token` |
   | Value | 登录返回的 JWT |
   | 添加到 | `Header` |

3. 建议配成**环境变量** `{{TOKEN}}`，避免每个接口手填

> 登录接口（`/admin/employee/login`、`/user/login`）在文件里已标记免鉴权，导入后不会被加上 token。

---

## 三、推荐调试顺序（照这个跑一遍全通）

| 步骤 | 接口 | 说明 |
|------|------|------|
| 1 | `POST /admin/employee/login` | body: `{"username":"admin","password":"123456"}`，返回 token |
| 2 | `GET /admin/dish/page` | 带 token，分页查商品（多表关联分页） |
| 3 | `POST /user/login` | body: `{"username":"zhangsan","password":"123456"}` |
| 4 | `GET /user/dish/list?categoryId=2` | 带用户 token，走缓存的商品列表 |
| 5 | `POST /user/shoppingCart/add` | body: `{"dishId":3,"dishFlavor":"微辣"}` |
| 6 | `GET /user/shoppingCart/list` | 拿到购物车条目 id |
| 7 | `POST /user/order/submit` | body 填 `cartItemIds:[上面拿到的id]` + 收货信息，返回订单号 |
| 8 | `PUT /user/order/payment/{orderNumber}` | 模拟支付，状态 1→2 |
| 9 | `PUT /admin/order/accept/{id}` | 商家发货，状态 2→3 |
| 10 | `GET /admin/cache/stats` | 查看缓存命中率 |

**想验证状态机拦截**：订单走到「已完成(5)」后再调一次 `PUT /admin/order/complete/{id}`，
会返回 `非法订单状态流转: 当前状态 5 不允许执行事件 COMPLETE`。

---

## 四、接口清单（44 个）

| 分组 | 接口 |
|------|------|
| 员工管理 | `POST /admin/employee/login`、`GET /admin/employee/{id}`、`POST /admin/employee`（新增）、`PUT /admin/employee/{id}`（编辑）、`PUT /admin/employee/{id}/status/{status}`（启用禁用）、`GET /admin/employee/page`（分页查询） |
| 分类管理 | `POST /admin/category`、`PUT /admin/category`、`DELETE /admin/category`、`GET /admin/category/page`、`GET /admin/category/list` |
| 商品管理 | `POST /admin/dish`、`PUT /admin/dish`、`DELETE /admin/dish`、`GET /admin/dish/page`、`GET /admin/dish/list`、`POST /admin/dish/status/{status}` |
| 组合套装管理 | `POST /admin/setmeal`、`PUT /admin/setmeal`、`DELETE /admin/setmeal`、`GET /admin/setmeal/page`、`GET /admin/setmeal/{id}`、`POST /admin/setmeal/status/{status}` |
| 订单管理 | `GET /admin/order/page`、`GET /admin/order/details/{id}`、`PUT /admin/order/accept/{id}`、`PUT /admin/order/reject/{id}`、`PUT /admin/order/delivery/{id}`、`PUT /admin/order/complete/{id}`、`GET /admin/order/statistics` |
| 缓存监控 | `GET /admin/cache/stats` |
| 商品浏览 | `GET /user/dish/list`、`GET /user/setmeal/list`、`GET /user/setmeal/{id}` |
| 购物车 | `POST /user/shoppingCart/add`、`POST /user/shoppingCart/sub`、`GET /user/shoppingCart/list`、`DELETE /user/shoppingCart/clean` |
| 用户订单 | `POST /user/order/submit`、`PUT /user/order/payment/{orderNumber}`、`PUT /user/order/cancel/{id}`、`GET /user/order/history`、`GET /user/order/{id}` |
| 用户登录 | `POST /user/login` |

---

## 五、重新生成文档

接口有增删改后，重新生成：

```bash
python tools/export_openapi.py --out docs/apifox/openapi.json
```

看到 `written docs/apifox/openapi.json (34149 bytes, utf-8 no BOM)` 才算成功。

> ⚠️ **不要用 `>` 重定向**：PowerShell 的 `>` 会在命令执行前先把目标文件截断成 0 字节，
> 且写出的是 UTF-16LE（中文还会变乱码）。`--out` 由脚本自己以 UTF-8 无 BOM 落盘。
> 另外本机 PATH 上的 `python` 可能是 Microsoft Store 的占位别名，会**零输出、退出码 0**地空转——
> 命令"成功但没输出"就当失败处理。

生成后跑一遍自检，确认文档与代码同步：

```bash
python tools/verify/check_openapi.py    # 期望：通过 24 / 失败 0
```

脚本会重新扫描 `src/main/java/com/mall/controller` 下所有 Controller，
包括分页查询 DTO 的字段（自动展开成 query 参数）。

> 注：本机 Maven 镜像拉不到 `org.springdoc` 依赖，因此没有走 springdoc 运行时自动生成，
> 改用源码静态提取——好处是零依赖且文档与实现同源，改了接口重跑脚本即可。
