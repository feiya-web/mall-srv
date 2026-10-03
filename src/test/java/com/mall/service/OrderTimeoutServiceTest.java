package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mall.mapper.DishMapper;
import com.mall.mapper.OrderDetailMapper;
import com.mall.mapper.OrdersMapper;
import com.mall.mapper.SetmealMapper;
import com.mall.pojo.entity.OrderDetail;
import com.mall.pojo.entity.Orders;
import lombok.extern.slf4j.Slf4j;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 订单超时取消（RabbitMQ 延迟消息的落点）的行为测试。
 *
 * <p>刻意<b>不启动 RabbitMQ</b>：这里要验的是"收到超时消息之后的那段逻辑"，
 * 也就是状态条件更新 + 库存回补的幂等性。消息能不能准时到是 broker 的事，
 * 这段逻辑的正确性不该依赖 broker 才能测。
 *
 * <p>前置条件：本机 MySQL（库 mall_order）与 Redis（6379）在跑。
 */
@Slf4j
@SpringBootTest
class OrderTimeoutServiceTest {

    private static final Long DISH_ID = 1L;
    private static final Long SETMEAL_ID = 1L;
    private static final int NUMBER = 3;

    @Autowired
    private OrderTimeoutService orderTimeoutService;
    @Autowired
    private OrdersMapper ordersMapper;
    @Autowired
    private OrderDetailMapper orderDetailMapper;
    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private SetmealMapper setmealMapper;

    private Long orderId;
    private int dishStockBefore;
    private int setmealStockBefore;

    @BeforeEach
    void setUp() {
        dishStockBefore = dishMapper.selectById(DISH_ID).getStock();
        setmealStockBefore = setmealMapper.selectById(SETMEAL_ID).getStock();

        Orders orders = new Orders();
        orders.setNumber("TESTTIMEOUT" + System.currentTimeMillis());
        orders.setUserId(1L);
        orders.setConsignee("测试收货人");
        orders.setPhone("13800000000");
        orders.setAddress("测试地址 1 号");
        orders.setAmount(new BigDecimal("59.00"));
        orders.setPayStatus(0);
        orders.setStatus(Orders.PENDING_PAYMENT);
        orders.setOrderTime(LocalDateTime.now());
        ordersMapper.insert(orders);
        orderId = orders.getId();

        addDetail(DISH_ID, null, NUMBER, "测试商品", "19.70");
        dishMapper.deductStock(DISH_ID, NUMBER);   // 模拟下单时已扣
    }

    @AfterEach
    void tearDown() {
        orderDetailMapper.delete(
                new LambdaQueryWrapper<OrderDetail>().eq(OrderDetail::getOrderId, orderId));
        ordersMapper.deleteById(orderId);
        // 还原库存，避免污染其它用例与压测数据
        dishMapper.restoreStock(DISH_ID, NUMBER);
    }

    private void addDetail(Long dishId, Long setmealId, int number, String name, String amount) {
        OrderDetail d = new OrderDetail();
        d.setOrderId(orderId);
        d.setDishId(dishId);
        d.setSetmealId(setmealId);
        d.setName(name);
        d.setAmount(new BigDecimal(amount));
        d.setNumber(number);
        orderDetailMapper.insert(d);
    }

    private int dishStock() {
        return dishMapper.selectById(DISH_ID).getStock();
    }

    @Test
    @DisplayName("超时取消：抢到 CAS 的一方取消订单并回补库存，库存不多不少")
    void cancelOnceRestoresStock() {
        int afterDeduct = dishStock();

        boolean won = orderTimeoutService.cancelIfUnpaid(orderId);

        assertTrue(won, "第一次调用应当抢到取消权");
        assertEquals(Orders.CANCELLED, (int) ordersMapper.selectById(orderId).getStatus());
        assertEquals(afterDeduct + NUMBER, dishStock(), "库存应恰好回补一次");
    }

    @Test
    @DisplayName("超时取消：订单已不在待付款时调用是幂等的（不重复取消、不重复回补）")
    void secondCallIsIdempotent() {
        assertTrue(orderTimeoutService.cancelIfUnpaid(orderId));
        int afterFirst = dishStock();

        boolean second = orderTimeoutService.cancelIfUnpaid(orderId);

        assertFalse(second, "第二次调用不该再抢到取消权");
        assertEquals(Orders.CANCELLED, (int) ordersMapper.selectById(orderId).getStatus());
        assertEquals(afterFirst, dishStock(), "库存不能被回补第二次");
    }

    @Test
    @DisplayName("超时取消：8 线程同时打一条订单，只有 1 个赢家，库存只回补 1 次")
    void concurrentCancelOnlyOneWinner() throws Exception {
        int threads = 8;
        int afterDeduct = dishStock();

        CountDownLatch ready = new CountDownLatch(threads);
        CountDownLatch fire = new CountDownLatch(1);
        AtomicInteger winners = new AtomicInteger();
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            for (int i = 0; i < threads; i++) {
                pool.submit(() -> {
                    ready.countDown();
                    try {
                        fire.await(10, TimeUnit.SECONDS);
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                    if (orderTimeoutService.cancelIfUnpaid(orderId)) {
                        winners.incrementAndGet();
                    }
                });
            }
            assertTrue(ready.await(10, TimeUnit.SECONDS), "线程未就绪");
            fire.countDown();
            pool.shutdown();
            assertTrue(pool.awaitTermination(30, TimeUnit.SECONDS), "线程未在预期时间内结束");
        } finally {
            pool.shutdownNow();
        }

        assertEquals(1, winners.get(), "只允许一个赢家，实际 " + winners.get() + " 个");
        assertEquals(Orders.CANCELLED, (int) ordersMapper.selectById(orderId).getStatus());
        assertEquals(afterDeduct + NUMBER, dishStock(), "并发下库存也只应回补一次");
    }

    @Test
    @DisplayName("超时取消：订单不存在时安静返回 false，不抛异常（消息重投不会把消费打挂）")
    void missingOrderIsQuiet() {
        assertFalse(orderTimeoutService.cancelIfUnpaid(999999999L));
    }

    @Test
    @DisplayName("超时取消：明细同时含商品与组合套装时，两侧库存都回补")
    void restoresDishAndSetmeal() {
        addDetail(null, SETMEAL_ID, 1, "测试组合套装", "39.00");
        setmealMapper.deductStock(SETMEAL_ID, 1);
        int dishAfterDeduct = dishStock();
        int setmealAfterDeduct = setmealMapper.selectById(SETMEAL_ID).getStock();

        assertTrue(orderTimeoutService.cancelIfUnpaid(orderId));

        assertEquals(dishAfterDeduct + NUMBER, dishStock(), "商品库存应回补");
        assertEquals(setmealAfterDeduct + 1, setmealMapper.selectById(SETMEAL_ID).getStock(),
                "组合套装库存应回补");
        setmealMapper.restoreStock(SETMEAL_ID, 1);   // 用例自己加的组合套装明细，单独还原
    }
}
