-- ============================================================
-- 压测数据构造脚本（在已有种子数据基础上追加，不删除原数据）
-- 目标：orders 10 万行 / order_detail 25 万行 / dish 5000 行 / user 1000 行
-- 用法：mysql -u root -p --default-character-set=utf8mb4 mall_order < 01_seed_bulk_data.sql
-- ============================================================
SET SESSION cte_max_recursion_depth = 1000000;

-- 1. 用户 1000 个
INSERT INTO user (username, password, phone, status)
WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 1000)
SELECT CONCAT('perf_u', LPAD(n, 6, '0')), SHA2('123456', 256),
       CONCAT('139', LPAD(n, 8, '0')), 1
FROM seq;

-- 2. 商品 5000 道（分布到 5 个分类，约 20% 停售）
INSERT INTO dish (name, category_id, price, description, status)
WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 5000)
SELECT CONCAT('压测商品', LPAD(n, 5, '0')),
       FLOOR(1 + RAND() * 5),
       ROUND(5 + RAND() * 95, 2),
       CONCAT('压测描述-', n),
       IF(RAND() > 0.2, 1, 0)
FROM seq;

-- 3. 订单 10 万条（status 1~6 均匀分布，order_time 分布在 2025 全年）
INSERT INTO orders (number, user_id, consignee, phone, address, amount,
                    pay_status, pay_method, status, order_time)
WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 100000)
SELECT CONCAT('PERF', LPAD(n, 12, '0')),
       FLOOR(1 + RAND() * 1000),
       CONCAT('收货人', n),
       CONCAT('139', LPAD(n, 8, '0')),
       CONCAT('上海市嘉定区', n, '号'),
       ROUND(20 + RAND() * 200, 2),
       1,
       FLOOR(1 + RAND() * 2),
       FLOOR(1 + RAND() * 6),
       DATE_ADD('2025-01-01 08:00:00', INTERVAL FLOOR(RAND() * 360 * 24) HOUR)
FROM seq;

-- 4. 订单明细 25 万条（每单 1~3 条）
INSERT INTO order_detail (order_id, dish_id, name, amount, number)
WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 250000)
SELECT FLOOR(1 + RAND() * 100000),
       FLOOR(1 + RAND() * 5000),
       CONCAT('压测商品', LPAD(FLOOR(1 + RAND() * 5000), 5, '0')),
       ROUND(5 + RAND() * 95, 2),
       FLOOR(1 + RAND() * 3)
FROM seq;

-- 5. 刷新统计信息，保证优化器基于真实数据分布选择索引
ANALYZE TABLE orders;
ANALYZE TABLE order_detail;
ANALYZE TABLE dish;

SELECT COUNT(*) AS orders_count FROM orders;
SELECT COUNT(*) AS order_detail_count FROM order_detail;
SELECT COUNT(*) AS dish_count FROM dish;
SELECT COUNT(*) AS user_count FROM user;
