package com.mall.common.exception;

/**
 * 订单状态机非法流转异常
 */
public class OrderStatusException extends BaseException {

    public OrderStatusException(String msg) {
        super(msg);
    }
}
