# 订单超时未支付自动取消（RabbitMQ 延迟消息 + 状态条件更新）

> 未支付订单超过约定时限后由系统自动取消并回补库存。本文说明为什么用「延迟队列 + 死信」
> 而不是定时轮询、投递时机怎么选、以及重复消费下如何保证只取消一次。

## 一、要解决的两个问题

1. **订单挂着不付怎么办**：待付款订单如果一直没人付，库存被占着、订单数据悬着。
   业务上需要在"超过支付时限"后自动取消。
2. **自动取消不能只执行一次**：消息可能重投、消费者可能重启、多实例部署时同一消息
   可能被两个消费者同时拿到。**如果取消动作不是幂等的，库存会被反复回补，越加越多。**

## 二、为什么用「延迟队列 + 死信」而不是定时轮询

定时任务每分钟扫一次待付款订单，代价随订单量线性增长；订单越多扫得越慢，
而"卡着点的那一分钟"正好是订单最密集的时候。延迟队列把"到点该处理"交给 broker：

- 投递 O(1)，不随订单量增加扫描成本；
- 消费端只在真的有消息到期时才干活；
- 不依赖任何延迟插件 —— RabbitMQ 3.8+ 自带 TTL + 死信交换��。

### 拓扑（`mq/OrderMQConfig`）

```
下单成功（事务提交后）
   │  convertAndSend(expiration = mall.order.timeout-ms)
   ▼
mall.order.delay.exchange ──► mall.order.delay.queue      ← 只进不出，当"定时器"用
                                  │ 消息到期（TTL 到了才过期）
                                  ↓ 死信交换机 mall.order.dlx
                          mall.order.timeout.queue ──► @RabbitListener 消费
```

**为什么 TTL 不设在队列上**：队列级 `x-message-ttl` 会造成"头部阻塞" ——
队头那条消息 TTL 没到，后面 TTL 更短的消息也一起被卡住。本项目每单超时时长相同，
所以用 per-message expiration（每条消息自带过期时间），语义更直白也更安全。

## 三、投递时机：必须在事务提交之后

`OrderService.submit` 里不直接发消息，而是发一个 `OrderCreatedEvent`，
由 `OrderTimeoutEventListener` 用 `@TransactionalEventListener(AFTER_COMMIT)` 投递：

```java
@TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
public void onOrderCreated(OrderCreatedEvent event) { producer.send(...); }
```

原因很直接：事务里发消息，事务后续回滚了，队列里就多出一条指向不存在订单的幽灵消息，
消费者到点去取消一个查不到的订单。AFTER_COMMIT 把"消息已发"和"订单已入库"绑在一起。

## 四、幂等怎么保证：一条 UPDATE 定输赢

`OrderTimeoutService.cancelIfUnpaid` 的核心只有一句 SQL：

```sql
UPDATE orders SET status = 6 WHERE id = ? AND status = 1   -- 待付款才能取消
```

- 影响 **1 行** → 本次调用抢到了取消权 → 才允许回补库存、写取消原因；
- 影响 **0 行** → 订单已被支付/已取消/商家待收货 → 消息作废，直接返回 false。

如果这里写成"先查状态 → 判断 → updateById 写绝对值"（本项目原来的写法），
两个消费者会各自读到"还是待付款"，双双取消成功、库存回补两次。
**本仓库有过这个真实缺陷**：`m3_order_state_race.py` 的案例 C/D 在修复前 40/40 轮命中，
修复后 0/40。同一套 CAS 写法也用在了 `accept / reject / delivery / complete / pay / cancel` 上。

## 五、库存扣减与回补

| 时机 | 语句 | 为什么这么写 |
|---|---|---|
| 下单 | `UPDATE dish SET stock = stock - n WHERE id=? AND stock >= n` | 判断与扣减在同一条语句，行锁兜住并发，不超卖；影响 0 行即库存不足，抛异常让整个下单事务回滚 |
| 超时取消 | `UPDATE dish SET stock = stock + n WHERE id=?` | 只由"抢到取消权"的那次调用执行；回补与状态机共用同一个 CAS 赢家，天然不会重复 |

库存列是这次为实现"库存重复回补"补上的（`dish.stock` / `setmeal.stock`，默认 999）。

## 六、投递失败怎么办（如实说明取舍）

`OrderTimeoutProducer` 捕获 `AmqpException` 后**只记日志不抛出**：下单已经提交成功，
消息只是超时取消这条辅助链路，为它把下单接口拖垮是本末倒置。

代价是"broker 挂掉期间可能有订单不会被自动取消"。这个缺口要靠补偿兜底
（定时扫表 + 告警）。这个缺口是明确存在的取舍：宁可少一条兜底路径，
也不让辅助链路的故障影响主链路。

## 七、怎么验证

```bash
# 单测：不依赖 RabbitMQ，直接验"收到超时消息之后那段逻辑"
mvn test -Dtest=OrderTimeoutServiceTest
#   5 条用例：首次取消回补库存 / 二次调用幂等 / 8 线程并发只有 1 个赢家 /
#             订单不存在时安静返回 / 商品与组合套装两侧都回补

# 端到端（需要本机有 RabbitMQ 5672）
mvn spring-boot:run --mall.order.timeout-ms=10000    # 把超时调成 10 秒便于观察
# 下单后不支付，10 秒后看日志：订单超时消息已投递 → 订单超时已取消并回补库存

# 状态流转并发（回归）
python tools/verify/m3_order_state_race.py --case all   # 13 条断言全 PASS
```

本机没装 RabbitMQ 时，加 `--mall.order.timeout-enabled=false` 关掉消费者，
其它功能不受影响。

## 八、设计取舍小结

1. 为什么不用定时轮询：成本随订单量线性增长，且高发时段扫描最慢；
2. 为什么投递在事务提交后：否则回滚会留下幽灵消息；
3. 为什么不用队列级 TTL：头部阻塞；
4. **为什么幂等靠一条带原状态条件的 UPDATE**：判断与写入同一条语句，
   由行锁串行化，不引入额外锁；影响行数就是"赢家判定"，不用额外加锁字段或乐观锁版本号；
5. 投递失败怎么办：只记日志 + 靠补偿兜底，并诚实说明这是当前链路的缺口。
