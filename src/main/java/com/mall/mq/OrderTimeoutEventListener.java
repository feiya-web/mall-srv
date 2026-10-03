package com.mall.mq;

import com.mall.order.OrderCreatedEvent;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

/**
 * 订单创建事件监听者：<b>事务提交后</b>才投递超时取消消息。
 *
 * <p>为什么用事件而不是在 OrderService 里直接调 Producer：
 * 直接调就等于"事务还没提交，消息已经发出去了"，一旦后面回滚，
 * 消费者会去取消一个根本不存在的订单。加了 AFTER_COMMIT 之后，
 * 投递与业务事务的成败绑在一起，顺序也说得清。
 */
@Slf4j
@Component
public class OrderTimeoutEventListener {

    private final OrderTimeoutProducer producer;

    public OrderTimeoutEventListener(OrderTimeoutProducer producer) {
        this.producer = producer;
    }

    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onOrderCreated(OrderCreatedEvent event) {
        log.info("订单已提交，触发超时取消投递：orderId={}", event.getOrderId());
        producer.send(event.getOrderId(), event.getUserId());
    }
}
