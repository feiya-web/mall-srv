package com.mall.mq;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serializable;

/**
 * 订单超时取消消息体。
 *
 * <p>刻意只带 id，不带订单快照：消息在队列里可能躺十几分钟，
 * 带快照等于把可能已过期的数据也带进去，消费时反而要以数据库为准。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class OrderTimeoutMessage implements Serializable {

    /** 订单 id */
    private Long orderId;
    /** 下单用户 id（仅用于日志定位） */
    private Long userId;
    /** 下单时间戳（毫秒），便于排查"消息早于/晚于预期"的问题 */
    private Long orderTime;
}
