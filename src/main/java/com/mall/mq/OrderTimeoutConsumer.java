package com.mall.mq;

import com.mall.service.OrderTimeoutService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

/**
 * 订单超时消费者：消息从延迟队列经死信路由到这里，触发"未支付自动取消"。
 *
 * <p>真正的业务判断在 {@link OrderTimeoutService#cancelIfUnpaid(Long)} 里，
 * 消费端只负责"把消息转成一次调用"—— 这样即使将来换成定时任务、HTTP 回调，
 * 业务逻辑也不用改。
 *
 * <p>{@code @ConditionalOnProperty} 是为了让没装 RabbitMQ 的本机环境能关掉消费者：
 * 加 {@code --mall.order.timeout-enabled=false} 即可，不影响其它功能。
 */
@Slf4j
@Component
@ConditionalOnProperty(name = "mall.order.timeout-enabled", havingValue = "true", matchIfMissing = true)
public class OrderTimeoutConsumer {

    private final OrderTimeoutService orderTimeoutService;

    public OrderTimeoutConsumer(OrderTimeoutService orderTimeoutService) {
        this.orderTimeoutService = orderTimeoutService;
    }

    @RabbitListener(queues = OrderMQConfig.TIMEOUT_QUEUE)
    public void onTimeout(OrderTimeoutMessage message) {
        if (message == null || message.getOrderId() == null) {
            log.warn("收到无法解析的超时消息：{}", message);
            return;
        }
        boolean won = orderTimeoutService.cancelIfUnpaid(message.getOrderId());
        log.info("超时消息处理完毕：orderId={}, 是否由本次取消 = {}",
                message.getOrderId(), won);
    }
}
