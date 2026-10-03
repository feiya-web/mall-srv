package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.common.constant.MessageConstant;
import com.mall.common.exception.BaseException;
import com.mall.common.exception.OrderStatusException;
import com.mall.common.exception.ParameterException;
import com.mall.mapper.DishMapper;
import com.mall.mapper.OrderDetailMapper;
import com.mall.mapper.OrdersMapper;
import com.mall.mapper.SetmealMapper;
import com.mall.mapper.ShoppingCartMapper;
import com.mall.order.OrderCreatedEvent;
import com.mall.order.OrderEvent;
import com.mall.order.OrderStateMachine;
import com.mall.pojo.dto.OrdersPageQueryDTO;
import com.mall.pojo.dto.OrdersSubmitDTO;
import com.mall.pojo.entity.OrderDetail;
import com.mall.pojo.entity.Orders;
import com.mall.pojo.entity.ShoppingCart;
import com.mall.pojo.vo.OrderSubmitVO;
import com.mall.pojo.vo.OrderVO;
import com.mall.pojo.vo.OrderStatisticsVO;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.CollectionUtils;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

/**
 * 订单服务：
 * 1. 下单 @Transactional 保证 orders / order_detail / shopping_cart 多表一致（含库存扣减）
 * 2. Redis SETNX 幂等锁防止重复提交
 * 3. 所有状态流转经由 OrderStateMachine 校验，<b>落库一律走 OrdersMapper#casStatus</b>
 * 4. 下单成功后发布事件，由监听者在事务提交后投递 RabbitMQ 延迟消息（超时未支付自动取消）
 */
@Slf4j
@Service
public class OrderService {

    /** 下单幂等锁 key 前缀（5 秒防重） */
    private static final String SUBMIT_LOCK_PREFIX = "order:submit:lock:";

    @Autowired
    private OrdersMapper ordersMapper;
    @Autowired
    private OrderDetailMapper orderDetailMapper;
    @Autowired
    private ShoppingCartMapper shoppingCartMapper;
    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private SetmealMapper setmealMapper;
    @Autowired
    private OrderStateMachine orderStateMachine;
    @Autowired
    private StringRedisTemplate stringRedisTemplate;
    @Autowired
    private ApplicationEventPublisher eventPublisher;

    /**
     * 用户下单（事务 + 幂等）
     */
    @Transactional
    public OrderSubmitVO submit(Long userId, OrdersSubmitDTO dto) {
        //下单入口
        log.info("用户开始下单,userId={},cartItemIds={}",userId,dto.getCartItemIds());
        // 幂等防重：同一用户 5 秒内只允许一次下单请求
        String lockKey = SUBMIT_LOCK_PREFIX + userId;
        Boolean locked = stringRedisTemplate.opsForValue().setIfAbsent(lockKey, "1", Duration.ofSeconds(5));
        if (!Boolean.TRUE.equals(locked)) {
            log.warn("用户重复下单,userId={}",userId);//业务警告：重复提交
            throw new BaseException(MessageConstant.ORDER_DUPLICATE_SUBMIT);
        }
        try {
            // 只采信当前用户购物车中真实存在的条目，金额由服务端计算
            List<ShoppingCart> cartItems = dto.getCartItemIds().isEmpty() ? List.of()
                    : shoppingCartMapper.selectList(new LambdaQueryWrapper<ShoppingCart>()
                    .in(ShoppingCart::getId, dto.getCartItemIds())
                    .eq(ShoppingCart::getUserId, userId));
            if (CollectionUtils.isEmpty(cartItems)) {
                log.warn("用户下单购物车为空,userId={}",userId);//业务警告：购物车为空
                throw new BaseException(MessageConstant.CART_EMPTY);
            }
            BigDecimal amount = cartItems.stream()
                    .map(c -> c.getAmount().multiply(BigDecimal.valueOf(c.getNumber())))
                    .reduce(BigDecimal.ZERO, BigDecimal::add);
            log.info("用户下单金额计算,userId={},amount={}",userId,amount);

            // 订单主表
            Orders orders = new Orders();
            orders.setNumber(generateOrderNumber(userId));
            orders.setUserId(userId);
            orders.setConsignee(dto.getConsignee());
            orders.setPhone(dto.getPhone());
            orders.setAddress(dto.getAddress());
            orders.setRemark(dto.getRemark());
            orders.setAmount(amount);
            orders.setPayMethod(dto.getPayMethod());
            orders.setPayStatus(com.mall.common.constant.StatusConstant.PAY_UNPAID);
            orders.setStatus(Orders.PENDING_PAYMENT);
            orders.setOrderTime(LocalDateTime.now());
            ordersMapper.insert(orders);
            log.info("订单主表入库成功,userId={},orderId={},orderNumber={}",userId,orders.getId(),orders.getNumber());

            // 订单明细（购物车快照）+ 逐条扣库存
            // 扣减用「条件相对写」：库存不足时影响行数为 0，直接抛异常让整个事务回滚，
            // 不会出现"订单建成了但库存超卖"。
            for (ShoppingCart cart : cartItems) {
                int deducted = (cart.getDishId() != null)
                        ? dishMapper.deductStock(cart.getDishId(), cart.getNumber())
                        : setmealMapper.deductStock(cart.getSetmealId(), cart.getNumber());
                if (deducted == 0) {
                    log.warn("库存不足，下单失败：dishId={}, setmealId={}, need={}",
                            cart.getDishId(), cart.getSetmealId(), cart.getNumber());
                    throw new BaseException(MessageConstant.STOCK_NOT_ENOUGH);
                }
                OrderDetail detail = new OrderDetail();
                detail.setOrderId(orders.getId());
                detail.setDishId(cart.getDishId());
                detail.setSetmealId(cart.getSetmealId());
                detail.setName(cart.getName());
                detail.setImage(cart.getImage());
                detail.setAmount(cart.getAmount());
                detail.setNumber(cart.getNumber());
                orderDetailMapper.insert(detail);
            }

            // 清除已下单的购物车条目
            shoppingCartMapper.delete(new LambdaQueryWrapper<ShoppingCart>()
                    .in(ShoppingCart::getId, dto.getCartItemIds())
                    .eq(ShoppingCart::getUserId, userId));
            log.info("下单流程全部完成,userId={},orderId={}",userId,orders.getId());

            // 通知「订单已创建」：监听者在**事务提交后**才投递 RabbitMQ 延迟消息。
            // 放在事务里直接发消息的话，回滚后队列里会多出一条指向不存在订单的幽灵消息。
            eventPublisher.publishEvent(new OrderCreatedEvent(orders.getId(), userId));
            return new OrderSubmitVO(orders.getId(), orders.getNumber(), amount, orders.getStatus());
        } catch (Exception e) {
            log.error("下单发生异常,userId={}", userId);
            throw e;
        }
        finally {
            // 无论成功失败都释放防重锁（正常重试不受影响）
            stringRedisTemplate.delete(lockKey);
        }
    }

    /**
     * 状态流转的唯一入口：先问状态机"这个事件允许吗"，再让数据库用 CAS 落库。
     *
     * <p>为什么必须是两步（Java 判 + SQL 条件）而不是只判一个：
     * <ul>
     *   <li>只在 Java 里判 —— 两个并发请求会各自读到同一个"允许"状态，双双通过（本项目踩过）；</li>
     *   <li>只在 SQL 里判 —— 业务规则散落在 SQL 里，状态机那套流转表白写了。</li>
     * </ul>
     * 两步合起来：Java 负责"规则可读"，SQL 负责"同一时刻只有一个赢家"。
     *
     * @return 抢到状态迁移返回 true；被别人抢先一步返回 false（调用方需按业务报错）
     */
    private boolean casTransition(Orders orders, OrderEvent event) {
        Integer from = orders.getStatus();
        Integer to = orderStateMachine.apply(from, event);   // 非法流转在这里抛 OrderStatusException
        if (ordersMapper.casStatus(orders.getId(), from, to) == 0) {
            log.warn("状态迁移被并发抢先：orderId={}, from={}, event={}",
                    orders.getId(), from, event);
            return false;
        }
        orders.setStatus(to);
        return true;
    }

    /**
     * 模拟支付成功：待付款 -> 待发货
     */
    public void paySuccess(String orderNumber, Long userId) {
        Orders orders = getByNumberAndUser(orderNumber, userId);
        if (!casTransition(orders, OrderEvent.PAY)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        orders.setPayStatus(com.mall.common.constant.StatusConstant.PAY_PAID);
        orders.setCheckoutTime(LocalDateTime.now());
        ordersMapper.updateById(orders);
    }

    /**
     * 用户取消订单：仅待付款/待发货可取消
     */
    public void cancel(Long orderId, Long userId) {
        Orders orders = getByIdAndUser(orderId, userId);
        if (!casTransition(orders, OrderEvent.CANCEL)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        orders.setCancelReason("用户取消");
        orders.setCancelTime(LocalDateTime.now());
        ordersMapper.updateById(orders);
    }

    /**
     * 商家发货：待发货 -> 待收货
     */
    public void accept(Long orderId) {
        Orders orders = getById(orderId);
        if (!casTransition(orders, OrderEvent.ACCEPT)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        ordersMapper.updateById(orders);
    }

    /**
     * 商家取消订单：待发货 -> 已取消
     */
    public void reject(Long orderId, String reason) {
        Orders orders = getById(orderId);
        if (!casTransition(orders, OrderEvent.REJECT)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        orders.setRejectionReason(reason);
        orders.setCancelTime(LocalDateTime.now());
        ordersMapper.updateById(orders);
    }

    /**
     * 开始配送：待收货 -> 配送中
     */
    public void delivery(Long orderId) {
        Orders orders = getById(orderId);
        if (!casTransition(orders, OrderEvent.DELIVER)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        ordersMapper.updateById(orders);
    }

    /**
     * 完成订单：配送中 -> 已完成
     */
    public void complete(Long orderId) {
        Orders orders = getById(orderId);
        if (!casTransition(orders, OrderEvent.COMPLETE)) {
            throw new OrderStatusException(MessageConstant.ORDER_STATUS_CHANGED);
        }
        ordersMapper.updateById(orders);
    }

    /**
     * 用户历史订单分页（走联合索引 idx_user_order_time）
     */
    public Page<OrderVO> userPage(Long userId, int page, int pageSize, Integer status) {
        Page<Orders> ordersPage = ordersMapper.selectPage(new Page<>(page, pageSize),
                new LambdaQueryWrapper<Orders>()
                        .eq(Orders::getUserId, userId)
                        .eq(status != null, Orders::getStatus, status)
                        .orderByDesc(Orders::getOrderTime));
        return (Page<OrderVO>) ordersPage.convert(orders -> toVO(orders, false));
    }

    public OrderVO details(Long orderId, Long userId) {
        Orders orders = getById(orderId);
        // userId 为 null 表示管理端查询，跳过归属校验
        if (userId != null && !orders.getUserId().equals(userId)) {
            throw new BaseException(MessageConstant.NO_PERMISSION);
        }
        return toVO(orders, true);
    }

    /**
     * 管理端订单分页（走联合索引 idx_status_order_time）
     */
    public Page<Orders> adminPage(OrdersPageQueryDTO dto) {
        Page<Orders> page = new Page<>(dto.getPage(), dto.getPageSize());
        return ordersMapper.pageQuery(page, dto);
    }

    public OrderStatisticsVO statistics() {
        OrderStatisticsVO vo = new OrderStatisticsVO();
        vo.setToBeConfirmed(countByStatus(Orders.TO_BE_ACCEPTED));
        vo.setConfirmed(countByStatus(Orders.ACCEPTED));
        vo.setDeliveryInProgress(countByStatus(Orders.DELIVERING));
        return vo;
    }

    private Long countByStatus(int status) {
        return ordersMapper.selectCount(new LambdaQueryWrapper<Orders>().eq(Orders::getStatus, status));
    }

    private Orders getById(Long orderId) {
        Orders orders = ordersMapper.selectById(orderId);
        if (orders == null) {
            throw new ParameterException(MessageConstant.ORDER_NOT_FOUND);
        }
        return orders;
    }

    private Orders getByIdAndUser(Long orderId, Long userId) {
        Orders orders = getById(orderId);
        if (!orders.getUserId().equals(userId)) {
            throw new BaseException(MessageConstant.NO_PERMISSION);
        }
        return orders;
    }

    private Orders getByNumberAndUser(String number, Long userId) {
        Orders orders = ordersMapper.selectOne(new LambdaQueryWrapper<Orders>()
                .eq(Orders::getNumber, number));
        if (orders == null) {
            throw new ParameterException(MessageConstant.ORDER_NOT_FOUND);
        }
        if (!orders.getUserId().equals(userId)) {
            throw new BaseException(MessageConstant.NO_PERMISSION);
        }
        return orders;
    }

    private OrderVO toVO(Orders orders, boolean withDetails) {
        OrderVO vo = new OrderVO();
        BeanUtils.copyProperties(orders, vo);
        if (withDetails) {
            vo.setOrderDetailList(orderDetailMapper.selectList(
                    new LambdaQueryWrapper<OrderDetail>().eq(OrderDetail::getOrderId, orders.getId())));
        }
        return vo;
    }

    /**
     * 订单号：时间戳 + 用户id后四位 + 两位随机数
     */
    private String generateOrderNumber(Long userId) {
        return System.currentTimeMillis() + String.format("%04d", userId % 10000)
                + String.format("%02d", (int) (Math.random() * 100));
    }
}
