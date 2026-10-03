package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mall.mapper.DishMapper;
import com.mall.mapper.OrderDetailMapper;
import com.mall.mapper.OrdersMapper;
import com.mall.mapper.SetmealMapper;
import com.mall.pojo.entity.OrderDetail;
import com.mall.pojo.entity.Orders;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 订单超时处理：未支付自动取消 + 库存回补。
 *
 * <p>由 RabbitMQ 延迟消息触发（消息到期 → 死信路由 → 消费端调用
 * {@link #cancelIfUnpaid(Long)}）。刻意<b>不直接依赖 RabbitMQ 类型</b>，
 * 这样定时任务扫表补偿、HTTP 回调都能复用同一份逻辑。
 *
 * <p><b>防重复的关键只有一句话</b>：把"改状态"做成带原状态条件的 UPDATE，
 * 只有影响行数为 1 的那次调用才算抢到取消权，才允许回补库存。
 *
 * <pre>
 *   UPDATE orders SET status=6 WHERE id=? AND status=1   -- 待付款才能取消
 *        ├─ 影响 1 行 → 我抢到了 → 回补库存（只回补这一次）
 *        └─ 影响 0 行 → 订单已被支付/已取消/待收货 → 消息作废，什么都不做
 * </pre>
 *
 * 如果这里用"先查后写"（查状态 → 判 → updateById），两个消息副本 / 消息重投
 * 就会各自看到"还是待付款"，双双取消成功、库存回补两次 —— 库存会凭空多出来。
 */
@Slf4j
@Service
public class OrderTimeoutService {

    @Autowired
    private OrdersMapper ordersMapper;
    @Autowired
    private OrderDetailMapper orderDetailMapper;
    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private SetmealMapper setmealMapper;

    /**
     * 超时未支付 → 取消订单并回补库存。
     *
     * @return true = 本次调用抢到了取消权（订单确实是这次被取消的）；
     *         false = 订单已不在待付款状态，消息作废（幂等，不抛异常）
     */
    @Transactional
    public boolean cancelIfUnpaid(Long orderId) {
        // ① 状态条件更新：只有仍是「待付款」才改成「已取消」
        int updated = ordersMapper.casStatus(orderId, Orders.PENDING_PAYMENT, Orders.CANCELLED);
        if (updated == 0) {
            Orders current = ordersMapper.selectById(orderId);
            log.info("超时消息作废：订单不处于待付款状态。orderId={}, currentStatus={}",
                    orderId, current == null ? "订单不存在" : current.getStatus());
            return false;
        }

        // ② 只有拿到取消权的那次调用才回补库存 —— 这一步放在 CAS 之后是关键
        List<OrderDetail> details = orderDetailMapper.selectList(
                new LambdaQueryWrapper<OrderDetail>().eq(OrderDetail::getOrderId, orderId));
        for (OrderDetail d : details) {
            if (d.getDishId() != null) {
                dishMapper.restoreStock(d.getDishId(), d.getNumber());
            } else if (d.getSetmealId() != null) {
                setmealMapper.restoreStock(d.getSetmealId(), d.getNumber());
            }
        }

        // ③ 补写取消原因与时间（此时状态已定，重复写同值无害）
        Orders patch = new Orders();
        patch.setId(orderId);
        patch.setCancelReason("超时未支付，系统自动取消");
        patch.setCancelTime(LocalDateTime.now());
        ordersMapper.updateById(patch);

        log.info("订单超时已取消并回补库存：orderId={}, 明细 {} 条", orderId, details.size());
        return true;
    }
}
