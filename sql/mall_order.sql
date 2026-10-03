-- ============================================================
-- 网上商城系统 数据库初始化脚本
-- 使用：mysql -u root -p < mall_order.sql
-- （按提示输入密码；密码由环境变量 MYSQL_PASSWORD 配置，默认 123456）
-- ============================================================
DROP DATABASE IF EXISTS mall_order;
CREATE DATABASE mall_order DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE mall_order;

-- 员工表（管理端）
CREATE TABLE employee (
    id          BIGINT AUTO_INCREMENT COMMENT '主键' PRIMARY KEY,
    name        VARCHAR(32)  NOT NULL COMMENT '姓名',
    username    VARCHAR(32)  NOT NULL COMMENT '用户名',
    password    CHAR(64)     NOT NULL COMMENT '密码(SHA-256)',
    phone       VARCHAR(20)  NULL COMMENT '手机号',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态 0禁用 1启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) COMMENT '员工表';

-- 用户表（C端）
CREATE TABLE user (
    id          BIGINT AUTO_INCREMENT COMMENT '主键' PRIMARY KEY,
    username    VARCHAR(32)  NOT NULL COMMENT '用户名',
    password    CHAR(64)     NOT NULL COMMENT '密码(SHA-256)',
    phone       VARCHAR(20)  NULL COMMENT '手机号',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '状态 0禁用 1启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_username (username)
) COMMENT '用户表';

-- 分类表
CREATE TABLE category (
    id          BIGINT AUTO_INCREMENT COMMENT '主键' PRIMARY KEY,
    name        VARCHAR(32)  NOT NULL COMMENT '分类名称',
    type        TINYINT      NOT NULL COMMENT '类型 1商品分类 2组合套装分类',
    sort        INT          NOT NULL DEFAULT 0 COMMENT '排序',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_name (name)
) COMMENT '分类表';

-- 商品表
CREATE TABLE dish (
    id          BIGINT AUTO_INCREMENT COMMENT '主键' PRIMARY KEY,
    name        VARCHAR(64)   NOT NULL COMMENT '商品名称',
    category_id BIGINT        NOT NULL COMMENT '分类id',
    price       DECIMAL(10,2) NOT NULL COMMENT '价格',
    image       VARCHAR(200)  NULL COMMENT '图片',
    description VARCHAR(400)  NULL COMMENT '描述',
    status      TINYINT       NOT NULL DEFAULT 1 COMMENT '状态 0停售 1起售',
    -- 库存：下单走「条件相对写」扣减（stock = stock - n AND stock >= n），防超卖；
    -- 订单超时未支付被取消时，由抢到状态 CAS 的那次调用回补（stock = stock + n），
    -- 所以这一列天然成对出现，不会只加不减。
    stock       INT           NOT NULL DEFAULT 999 COMMENT '库存',
    create_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    -- 联合索引：C端「按分类查起售商品」走 (category_id, status)，
    -- 管理端分页「按名称模糊 + 按更新时间倒序」避免全表 filesort
    INDEX idx_category_status (category_id, status),
    INDEX idx_update_time (update_time)
) COMMENT '商品表';

-- 商品喜好表
CREATE TABLE dish_flavor (
    id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    dish_id BIGINT       NOT NULL COMMENT '商品id',
    name    VARCHAR(32)  NOT NULL COMMENT '喜好名称',
    value   VARCHAR(255) NULL COMMENT '喜好取值(JSON数组字符串)',
    INDEX idx_dish_id (dish_id)
) COMMENT '商品喜好表';

-- 组合套装表
CREATE TABLE setmeal (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(64)   NOT NULL COMMENT '组合套装名称',
    category_id BIGINT        NOT NULL COMMENT '分类id',
    price       DECIMAL(10,2) NOT NULL COMMENT '价格',
    image       VARCHAR(200)  NULL COMMENT '图片',
    description VARCHAR(400)  NULL COMMENT '描述',
    status      TINYINT       NOT NULL DEFAULT 1 COMMENT '状态 0停售 1起售',
    -- 库存，语义同 dish.stock：下单条件扣减，超时取消回补
    stock       INT           NOT NULL DEFAULT 999 COMMENT '库存',
    create_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category_status (category_id, status),
    INDEX idx_update_time (update_time)
) COMMENT '组合套装表';

-- 组合套装商品关联表
CREATE TABLE setmeal_dish (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    setmeal_id BIGINT        NOT NULL COMMENT '组合套装id',
    dish_id    BIGINT        NOT NULL COMMENT '商品id',
    name       VARCHAR(64)   NULL COMMENT '商品名称冗余',
    price      DECIMAL(10,2) NULL COMMENT '商品单价冗余',
    copies     INT           NOT NULL DEFAULT 1 COMMENT '份数',
    INDEX idx_setmeal_id (setmeal_id),
    INDEX idx_dish_id (dish_id)
) COMMENT '组合套装商品关联表';

-- 购物车表
--
-- 唯一约束 uk_cart_user_item 是「同一用户 + 同一商品 + 同一喜好 只占一行」这条不变量的
-- 最后一道防线（应用层已用原子自增保证，唯一键负责兜住极端并发下的重复插入）。
--
-- 两个设计要点，都不是可有可无的：
--   1. dish_flavor 由 NULL 改为 NOT NULL DEFAULT ''：应用层把 null 归一成 ''，
--      DDL 这里同步收紧，避免"代码忘了归一"时静默退化成 NULL 语义。
--   2. dish_key / setmeal_key 是生成列（= IFNULL(dish_id, 0)）：商品行的 setmeal_id 是
--      NULL、组合套装行的 dish_id 是 NULL，而 SQL 三值逻辑里 `NULL = NULL` 恒为假 ——
--      直接把可空列放进唯一键，NULL 之间互不判重，组合套装侧会整条失效。
--      生成列把 NULL 折叠成 0，四元键才真正可比。
CREATE TABLE shopping_cart (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id     BIGINT        NOT NULL COMMENT '用户id',
    dish_id     BIGINT        NULL COMMENT '商品id',
    setmeal_id  BIGINT        NULL COMMENT '组合套装id',
    name        VARCHAR(64)   NOT NULL COMMENT '名称',
    image       VARCHAR(200)  NULL COMMENT '图片',
    dish_flavor VARCHAR(200)  NOT NULL DEFAULT '' COMMENT '喜好（应用层归一：null -> ""）',
    amount      DECIMAL(10,2) NOT NULL COMMENT '单价',
    number      INT           NOT NULL DEFAULT 1 COMMENT '数量',
    create_time DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    dish_key    BIGINT GENERATED ALWAYS AS (IFNULL(dish_id, 0)) STORED,
    setmeal_key BIGINT GENERATED ALWAYS AS (IFNULL(setmeal_id, 0)) STORED,
    UNIQUE KEY uk_cart_user_item (user_id, dish_key, setmeal_key, dish_flavor),
    INDEX idx_user_id (user_id)
) COMMENT '购物车表';

-- 订单表
CREATE TABLE orders (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    number       VARCHAR(50)   NOT NULL COMMENT '订单号',
    user_id      BIGINT        NOT NULL COMMENT '用户id',
    consignee    VARCHAR(32)   NOT NULL COMMENT '收货人',
    phone        VARCHAR(20)   NOT NULL COMMENT '联系电话',
    address      VARCHAR(255)  NOT NULL COMMENT '收货地址',
    remark       VARCHAR(255)  NULL COMMENT '备注',
    amount       DECIMAL(10,2) NOT NULL COMMENT '实收金额',
    pay_status   TINYINT       NOT NULL DEFAULT 0 COMMENT '支付状态 0未支付 1已支付',
    pay_method   TINYINT       NULL COMMENT '支付方式 1微信 2支付宝',
    status       TINYINT       NOT NULL DEFAULT 1 COMMENT '订单状态 1待付款 2待发货 3待收货 4配送中 5已完成 6已取消',
    order_time   DATETIME      NOT NULL COMMENT '下单时间',
    checkout_time DATETIME     NULL COMMENT '支付时间',
    cancel_time  DATETIME      NULL COMMENT '取消时间',
    cancel_reason VARCHAR(255) NULL COMMENT '取消原因',
    rejection_reason VARCHAR(255) NULL COMMENT '取消订单原因',
    -- 联合索引1：C端历史订单查询 WHERE user_id = ? ORDER BY order_time DESC
    -- 联合索引2：管理端订单搜索 WHERE status = ? AND order_time BETWEEN ? AND ?
    UNIQUE KEY uk_number (number),
    INDEX idx_user_order_time (user_id, order_time),
    INDEX idx_status_order_time (status, order_time)
) COMMENT '订单表';

-- 订单明细表
CREATE TABLE order_detail (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id   BIGINT        NOT NULL COMMENT '订单id',
    dish_id    BIGINT        NULL COMMENT '商品id',
    setmeal_id BIGINT        NULL COMMENT '组合套装id',
    name       VARCHAR(64)   NOT NULL COMMENT '名称',
    image      VARCHAR(200)  NULL COMMENT '图片',
    amount     DECIMAL(10,2) NOT NULL COMMENT '单价',
    number     INT           NOT NULL DEFAULT 1 COMMENT '数量',
    INDEX idx_order_id (order_id)
) COMMENT '订单明细表';

-- 操作日志表（AOP 自动记录）
CREATE TABLE operation_log (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    oper_user   VARCHAR(32)  NULL COMMENT '操作人',
    oper_module VARCHAR(64)  NULL COMMENT '模块',
    oper_type   VARCHAR(32)  NULL COMMENT '操作类型',
    oper_method VARCHAR(200) NULL COMMENT '方法名',
    oper_params TEXT         NULL COMMENT '请求参数',
    oper_time   DATETIME     NOT NULL COMMENT '操作时间',
    cost_time   INT          NULL COMMENT '耗时(ms)'
) COMMENT '操作日志表';

-- ============================================================
-- 种子数据（密码均为 123456 的 SHA-256）
-- ============================================================
INSERT INTO employee (name, username, password, phone, status) VALUES
('管理员', 'admin', SHA2('123456', 256), '13800000001', 1);

INSERT INTO user (username, password, phone, status) VALUES
('zhangsan', SHA2('123456', 256), '13900000001', 1),
('lisi',     SHA2('123456', 256), '13900000002', 1);

INSERT INTO category (name, type, sort) VALUES
('手机通讯', 1, 1), ('电脑办公', 1, 2), ('家用电器', 1, 3), ('服饰鞋包', 1, 4),
('食品生鲜', 1, 5), ('超值组合套装', 2, 6);

INSERT INTO dish (name, category_id, price, description, status) VALUES
('智能手机 Pro',   1, 4999.00, '6.7 英寸全面屏，长续航', 1),
('轻薄笔记本',      2, 6299.00, '14 英寸轻薄本，16G 内存', 1),
('机械键盘',        2, 299.00,  '87 键，茶轴，RGB 背光', 1),
('无线鼠标',        2, 89.00,   '2.4G 无线，静音微动', 1),
('空气炸锅',        3, 399.00,  '5L 容量，可视化窗口', 1),
('扫地机器人',      3, 1299.00, '激光导航，自动集尘', 1),
('纯棉 T 恤',       4, 79.00,   '100% 精梳棉，多色可选', 1),
('休闲运动鞋',      4, 359.00,  '缓震鞋底，通勤百搭', 1),
('坚果礼盒',        5, 128.00,  '每日坚果 30 包，独立装', 1),
('有机咖啡豆',      5, 88.00,   '中度烘焙，500g', 1),
('蓝牙耳机',        1, 599.00,  '主动降噪，40 小时续航', 0);

INSERT INTO dish_flavor (dish_id, name, value) VALUES
(1, '颜色', '["深空灰","月光银","远峰蓝"]'),
(2, '内存', '["16G+512G","32G+1T"]'),
(3, '轴体', '["红轴","茶轴","青轴"]'),
(4, '颜色', '["白色","深灰"]'),
(7, '颜色', '["白色","黑色","藏青"]'),
(8, '尺码', '["39","40","41","42","43"]');

INSERT INTO setmeal (name, category_id, price, description, status) VALUES
('居家办公组合套装', 6, 6298.00, '笔记本 + 机械键盘 + 鼠标', 1),
('智能家居入门套装', 6, 1598.00, '空气炸锅 + 扫地机器人', 1);

INSERT INTO setmeal_dish (setmeal_id, dish_id, name, price, copies) VALUES
(1, 2, '轻薄笔记本', 6299.00, 1),
(1, 3, '机械键盘', 299.00, 1),
(1, 4, '无线鼠标', 89.00, 1),
(2, 5, '空气炸锅', 399.00, 1),
(2, 6, '扫地机器人', 1299.00, 1);
