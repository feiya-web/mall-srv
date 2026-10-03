package com.mall.mq;

import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.AmqpException;
import org.springframework.amqp.core.MessageDeliveryMode;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 订单超时消息的投递方。
 *
 * <p>消息走延迟队列：投递时给每条消息带 {@code expiration}（毫秒），
 * 消息在 broker 里待够这个时间才过期，然后被死信交换机转发到真正的消费队列。
 *
 * <p><b>投递失败为什么不抛</b>：下单已经提交成功，消息只是"超时取消"这条辅助链路。
 * 如果 broker 挂了就把下单接口一起拖垮，是本末倒置。所以这里只记 error 日志。
 * 代价是"极端情况下订单可能不会被自动取消"，这个缺口要靠补偿（定时扫表兜底 + 告警）补上，
 * 不能假装不存在：宁可留一个明确的缺口并兜底，也不假装能做到 Exactly-Once。
 */
@Slf4j
@Component
public class OrderTimeoutProducer {

    private final RabbitTemplate rabbitTemplate;

    /** 未支付订单多久之后自动取消（毫秒），默认 15 分钟 */
    @Value("${mall.order.timeout-ms:900000}")
    private long timeoutMs;

    public OrderTimeoutProducer(RabbitTemplate rabbitTemplate) {
        this.rabbitTemplate = rabbitTemplate;
    }

    public void send(Long orderId, Long userId) {
        OrderTimeoutMessage payload = new OrderTimeoutMessage(orderId, userId, System.currentTimeMillis());
        try {
            rabbitTemplate.convertAndSend(OrderMQConfig.DELAY_EXCHANGE, "", payload, msg -> {
                msg.getMessageProperties().setExpiration(String.valueOf(timeoutMs));
                // 持久化投递：broker 重启后延迟消息还在（配合持久化 exchange/queue）
                msg.getMessageProperties().setDeliveryMode(MessageDeliveryMode.PERSISTENT);
                return msg;
            });
            log.info("订单超时消息已投递：orderId={}, timeoutMs={}", orderId, timeoutMs);
        } catch (AmqpException e) {
            log.error("订单超时消息投递失败（不影响下单，需靠补偿兜底）：orderId={}", orderId, e);
        }
    }
}
