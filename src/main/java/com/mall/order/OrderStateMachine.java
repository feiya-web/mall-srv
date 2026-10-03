package com.mall.order;

import com.mall.common.exception.OrderStatusException;
import org.springframework.stereotype.Component;

import java.util.EnumMap;
import java.util.Map;

import static com.mall.pojo.entity.Orders.*;

/**
 * 订单状态机：集中定义「当前状态 x 事件 -> 目标状态」的合法流转表，
 * 所有状态变更必须经由本组件校验，杜绝越权流转（如：待收货后再取消、已完成后取消订单）
 */
@Component
public class OrderStateMachine {

    /** 状态流转表：事件 -> (当前状态 -> 目标状态) */
    private static final Map<OrderEvent, Map<Integer, Integer>> TRANSITIONS = new EnumMap<>(OrderEvent.class);

    static {
        // 支付：待付款 -> 待发货
        TRANSITIONS.put(OrderEvent.PAY, Map.of(PENDING_PAYMENT, TO_BE_ACCEPTED));
        // 发货：待发货 -> 待收货
        TRANSITIONS.put(OrderEvent.ACCEPT, Map.of(TO_BE_ACCEPTED, ACCEPTED));
        // 取消订单：待发货 -> 已取消
        TRANSITIONS.put(OrderEvent.REJECT, Map.of(TO_BE_ACCEPTED, CANCELLED));
        // 发货配送：待收货 -> 配送中
        TRANSITIONS.put(OrderEvent.DELIVER, Map.of(ACCEPTED, DELIVERING));
        // 完成：配送中 -> 已完成
        TRANSITIONS.put(OrderEvent.COMPLETE, Map.of(DELIVERING, COMPLETED));
        // 取消：待付款/待发货 -> 已取消
        Map<Integer, Integer> cancel = Map.of(PENDING_PAYMENT, CANCELLED, TO_BE_ACCEPTED, CANCELLED);
        TRANSITIONS.put(OrderEvent.CANCEL, cancel);
    }

    /**
     * 校验并计算目标状态；非法流转抛出 OrderStatusException
     */
    public int apply(int currentStatus, OrderEvent event) {
        Map<Integer, Integer> table = TRANSITIONS.get(event);
        if (table == null) {
            throw new OrderStatusException("未知订单事件: " + event);
        }
        Integer target = table.get(currentStatus);
        if (target == null) {
            throw new OrderStatusException(
                    String.format("非法订单状态流转: 当前状态 %d 不允许执行事件 %s", currentStatus, event));
        }
        return target;
    }
}
