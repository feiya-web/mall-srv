package com.mall.order;

/**
 * 订单事件
 */
public enum OrderEvent {
    /** 用户支付（模拟） */
    PAY,
    /** 商家发货 */
    ACCEPT,
    /** 商家取消订单 */
    REJECT,
    /** 开始配送 */
    DELIVER,
    /** 完成 */
    COMPLETE,
    /** 用户取消 */
    CANCEL
}
