package com.mall.common.constant;

/**
 * 提示信息常量
 */
public class MessageConstant {

    public static final String ALREADY_EXISTS = " 已存在";
    public static final String UNKNOWN_ERROR = "系统繁忙，请稍后再试";
    public static final String PASSWORD_ERROR = "用户名或密码错误";
    public static final String ACCOUNT_DISABLED = "账号已被禁用";
    public static final String LOGIN_FAILED = "登录失败";
    public static final String CATEGORY_USED_BY_DISH = "当前分类下存在商品，无法删除";
    public static final String CATEGORY_USED_BY_SETMEAL = "当前分类下存在组合套装，无法删除";
    public static final String DISH_ON_SALE = "存在起售中的商品，无法删除";
    public static final String DISH_IN_SETMEAL = "存在组合套装关联的商品，无法删除";
    public static final String SETMEAL_ON_SALE = "存在起售中的组合套装，无法删除";
    public static final String SETMEAL_CONTAINS_DISABLE_DISH = "组合套装内包含停售商品，无法起售";
    public static final String CART_EMPTY = "购物车为空，不能下单";
    public static final String ORDER_DUPLICATE_SUBMIT = "订单提交中，请勿重复点击";
    public static final String ORDER_NOT_FOUND = "订单不存在";
    /** 状态迁移被并发抢先：提示前端刷新后重试，而不是报"系统繁忙" */
    public static final String ORDER_STATUS_CHANGED = "订单状态已变更，请刷新后重试";
    /** 库存不足（下单时扣减未命中） */
    public static final String STOCK_NOT_ENOUGH = "库存不足";
    public static final String EMPLOYEE_NOT_FOUND = "员工不存在";
    public static final String CANNOT_DISABLE_SELF = "不能禁用自己";
    public static final String STATUS_INVALID = "状态值不合法，只允许 0(禁用) 或 1(启用)";
    public static final String NO_PERMISSION = "无权限操作该订单";
    public static final String OPERATION_FAILED = "操作失败";
}
