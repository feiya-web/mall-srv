package com.mall.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.pojo.entity.Orders;
import com.mall.pojo.dto.OrdersPageQueryDTO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface OrdersMapper extends BaseMapper<Orders> {

    /**
     * 管理端订单分页（条件：订单号/状态/下单日期区间，走 idx_status_order_time 联合索引）
     */
    Page<Orders> pageQuery(Page<Orders> page, @Param("q") OrdersPageQueryDTO query);

    /**
     * 状态机落库（CAS，compare-and-set）：只有库里<b>此刻仍是 {@code fromStatus}</b> 时才改成
     * {@code toStatus}。
     *
     * <p><b>为什么必须这样写：</b>原实现是「查出来 → 在 Java 里判状态机 → updateById 写绝对值」。
     * 两个并发请求（比如商家发货 与 用户取消）会读到同一个 status=2，各自算出合法的目标状态，
     * 然后后写的把先写的覆盖掉 —— 两条路径都返回成功，订单却被后写的那条悄悄改掉，
     * 而用户/商家看到的是"我确实操作成功了"。这不是脏数据，是<b>业务规则被绕过</b>。
     *
     * <p>把"判断当前状态"和"写新状态"压进同一条 UPDATE，由 InnoDB 行锁串行化：
     * 先到者把 status 改掉，后到者的 WHERE 不再命中，<b>影响行数为 0</b>，
     * 调用方据此判定"状态已被别人推进"，直接拒绝 —— 不需要悲观锁，也不需要乐观锁字段。
     *
     * @return 1 = 抢到本次状态迁移；0 = 状态已被别人改掉，本次是并发输家
     */
    @Update("UPDATE orders SET status = #{toStatus} WHERE id = #{id} AND status = #{fromStatus}")
    int casStatus(@Param("id") Long id, @Param("fromStatus") Integer fromStatus,
                  @Param("toStatus") Integer toStatus);
}
