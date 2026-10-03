package com.mall.mq;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.core.QueueBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * 订单超时取消所需的 RabbitMQ 拓扑。
 *
 * <p><b>为什么用「延迟队列 + 死信」而不是定时任务轮询</b>：
 * 轮询的代价随订单量线性增长（每分钟全表扫一次待付款订单），而订单量越大越慢；
 * 延迟队列把"到点该处理"这件事交给 broker，O(1) 投递、到点即到，消费端只在真的有消息时才动。
 *
 * <p><b>拓扑</b>：
 * <pre>
 *   [下单] --(per-message TTL ms)--> mall.order.delay.queue
 *                │ 消息过期（到期才过期）
 *                ↓ 死信交换机 mall.order.dlx
 *           mall.order.timeout.queue  --(binding: order.timeout)-->  @RabbitListener 消费
 * </pre>
 * 关键点：延迟队列本身不消费，只当"定时器"用；消息到期被 broker 转发到死信交换机，
 * 由死信队列承载真正的消费。这样不依赖任何延迟插件（RabbitMQ 3.8+ 自带 TTL + DLX）。
 *
 * <p><b>TTL 为什么不设在队列上</b>：队列级 x-message-ttl 会让"队头的长 TTL 消息"
 * 把后面的短 TTL 消息一起卡住（头部阻塞）。本项目每单的超时时长相同，
 * 因此用 per-message expiration（每条消息自己带过期时间），语义更直白，也更安全。
 *
 * <p>队列都是<b>惰性声明</b>（lazy）：没有消费者/生产者时也不占 broker 资源，
 * 本机没装 RabbitMQ 时启动不会报错，只是消费端连不上（连接失败会持续重试并打日志）。
 */
@Configuration
public class OrderMQConfig {

    /** 延迟队列交换机（直连，路由键固定） */
    public static final String DELAY_EXCHANGE = "mall.order.delay.exchange";
    /** 延迟队列：只进不出，等消息过期 */
    public static final String DELAY_QUEUE = "mall.order.delay.queue";
    /** 死信交换机：延迟队列的过期消息被转发到这里 */
    public static final String DLX_EXCHANGE = "mall.order.dlx";
    /** 真正被消费的队列 */
    public static final String TIMEOUT_QUEUE = "mall.order.timeout.queue";
    /** 死信路由键 */
    public static final String TIMEOUT_ROUTING_KEY = "order.timeout";

    @Bean
    public DirectExchange orderDelayExchange() {
        return new DirectExchange(DELAY_EXCHANGE, true, false);
    }

    @Bean
    public DirectExchange orderDlxExchange() {
        return new DirectExchange(DLX_EXCHANGE, true, false);
    }

    /**
     * 延迟队列：不设 x-message-ttl，改由每条消息带 expiration；
     * 设 x-dead-letter-* 让过期消息自动转发到死信交换机。
     */
    @Bean
    public Queue orderDelayQueue() {
        return QueueBuilder.durable(DELAY_QUEUE)
                .deadLetterExchange(DLX_EXCHANGE)
                .deadLetterRoutingKey(TIMEOUT_ROUTING_KEY)
                .lazy()
                .build();
    }

    @Bean
    public Queue orderTimeoutQueue() {
        return QueueBuilder.durable(TIMEOUT_QUEUE).lazy().build();
    }

    @Bean
    public Binding orderTimeoutBinding() {
        return BindingBuilder.bind(orderTimeoutQueue()).to(orderDlxExchange()).with(TIMEOUT_ROUTING_KEY);
    }
}
