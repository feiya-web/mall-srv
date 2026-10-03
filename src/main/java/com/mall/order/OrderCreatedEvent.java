package com.mall.order;

import lombok.AllArgsConstructor;
import lombok.Getter;

/**
 * 「订单已创建」事件。
 *
 * <p>下单方法在事务里发布它，监听者用 {@code @TransactionalEventListener(AFTER_COMMIT)} 消费，
 * 保证<b>事务提交成功后</b>才去投递延迟消息。
 * 直接在事务里发消息的风险：事务后续回滚，队列里却已经有一条指向不存在订单的消息。
 */
@Getter
@AllArgsConstructor
public class OrderCreatedEvent {

    private final Long orderId;
    private final Long userId;
}
