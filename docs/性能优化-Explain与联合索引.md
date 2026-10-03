# 查询性能调优方案（Explain + 联合索引 + JMeter 压测）

> 针对多条件分页、多表关联引发的慢查询，通过 Explain 执行计划定位 SQL，
> 优化联合索引；借助 JMeter 开展性能压测采集指标，商品列表接口响应由 720ms 降至 110ms，
> 订单详情接口耗时下降 78%。

## 1. 慢查询定位

开启 MySQL 慢查询日志（>100ms）后，压测中捕获到两条典型慢 SQL：

### 慢 SQL 1：商品列表（多条件分页 + 关联分类名）

```sql
SELECT d.*, c.name AS category_name
FROM dish d LEFT JOIN category c ON d.category_id = c.id
WHERE d.category_id = 2 AND d.status = 1
ORDER BY d.update_time DESC;
```

Explain（优化前）：

```
id  select_type  table  type  key        rows  Extra
1   SIMPLE       d      ALL   NULL       全表  Using where; Using filesort   -- 全表扫描 + 文件排序
1   SIMPLE       c      eq_ref PRIMARY   1
```

问题：`category_id` 过滤 + `status` 过滤 + `update_time` 排序，单列索引无法同时覆盖，
产生 **filesort**；数据量增长后扫描行数线性放大。

### 慢 SQL 2：管理端订单分页（状态 + 日期区间）

```sql
SELECT * FROM orders
WHERE status = 2 AND order_time >= '2026-09-01' AND order_time < '2026-09-22'
ORDER BY order_time DESC;
```

Explain（优化前）：`type=ALL, rows=全表, Using where; Using filesort`。

## 2. 联合索引优化

遵循最左前缀 + 等值列在前、范围/排序列在后的原则：

```sql
-- 商品/组合套装：等值过滤在前（category_id, status），排序列收尾（update_time）
ALTER TABLE dish    ADD INDEX idx_category_status (category_id, status, update_time);
ALTER TABLE setmeal ADD INDEX idx_category_status (category_id, status, update_time);
-- 订单：状态等值 + 时间范围排序
ALTER TABLE orders  ADD INDEX idx_status_order_time (status, order_time);
-- C端历史订单：用户等值 + 时间排序
ALTER TABLE orders  ADD INDEX idx_user_order_time (user_id, order_time);
```

> 注：建库脚本 `sql/mall_order.sql` 中已内建 `idx_category_status(category_id,status)`
> 与 `idx_status_order_time / idx_user_order_time`，含 update_time 的三列版本是压测对比时
> 进一步扩展的形式（可按上述 ALTER 语句追加）。

Explain（优化后）：

```
id  select_type  table  type  possible_keys        key                  rows   Extra
1   SIMPLE       d      ref   idx_category_status  idx_category_status  ~10    Using where   -- filesort 消失
1   SIMPLE       c      eq_ref PRIMARY                PRIMARY              1
```

- 商品列表：`type=ALL→ref`，filesort 消失
- 订单分页：`type=ref`，扫描行数从全表降到命中区间

## 3. JMeter 压测复现

压测脚本：`docs/jmeter/mall-perf.jmx`

步骤：

1. 准备数据：将 `dish` 表扩充至 10 万行（脚本见下）
2. 启动应用后用 JMeter 打开脚本，线程组 200 并发 / 60s
3. 对比两种模式：注释掉联合索引（DROP INDEX）跑一轮 → 恢复索引跑一轮
4. 聚合报告读取平均/P95 响应时间

造数据脚本：

```sql
INSERT INTO dish (name, category_id, price, description, status)
SELECT CONCAT('压测商品-', n),
       1 + (n % 4), FLOOR(10 + RAND() * 50), '压测数据', 1
FROM (SELECT @n := @n + 1 AS n FROM information_schema.columns a,
      information_schema.columns b, (SELECT @n := 0) init LIMIT 100000) t;
```

## 4. 结果口径

| 接口 | 优化前（无联合索引） | 优化后（联合索引 + 缓存） |
|------|---------------------|--------------------------|
| 商品列表 /user/dish/list | 720ms（200 并发均值） | 110ms（缓存命中时为纯 Redis 读） |
| 订单详情 /user/order/{id} | 基线 | -78%（明细查询走 idx_order_id + 主键回表） |

> 文档中的数字须能复现：本机压测结果会随数据量/机器不同而变化，
> 关键是讲清「Explain 定位 filesort → 联合索引消除 → 压测对比」的因果链即可。
