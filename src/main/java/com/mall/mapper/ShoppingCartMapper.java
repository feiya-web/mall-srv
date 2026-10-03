package com.mall.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.mall.pojo.entity.ShoppingCart;
import org.apache.ibatis.annotations.Delete;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

/**
 * 购物车 Mapper。
 *
 * <p>除 BaseMapper 的通用 CRUD 外，额外提供三条<b>相对量</b>的原子写语句。
 *
 * <p><b>为什么必须自定义？</b>{@code number} 是「累加型」字段，而 BaseMapper 的
 * {@code updateById} 只能写<b>绝对值</b>（{@code SET number = 5}）。
 * 两个并发请求会读到同一个旧值、算出同一个新值，后写的把先写的覆盖掉 ——
 * 这就是丢更新（lost update），接口每次都返回成功，数字却少涨了。
 * 改写成 {@code number = number + 1} 之后，加减在数据库行锁内完成，天然并发安全。
 *
 * <p><b>WHERE 为什么用生成列？</b>条件统一走生成列 {@code dish_key / setmeal_key}
 * （其定义为 {@code IFNULL(dish_id, 0)}），而不是物理列。原因：商品行的 {@code setmeal_id}
 * 是 NULL、组合套装行的 {@code dish_id} 是 NULL，而 SQL 三值逻辑里 {@code NULL = NULL} 恒为假，
 * 拿物理列做等值匹配会永远匹配不上。生成列把 NULL 折叠成 0，四元业务键才真正可比。
 *
 * <p><b>返回值语义</b>（三个方法一致）：
 * <ul>
 *   <li>{@code 0} —— 条件没命中（条目不存在，或状态不满足）；</li>
 *   <li>{@code >0} —— 命中并已改写，值即受影响行数。</li>
 * </ul>
 * 调用方靠这个返回值分支，不再需要「先查一次再决定」。
 *
 * <p>⚠️ 所有可能为 null 的参数都显式声明了 {@code jdbcType}：MyBatis 在参数为 null
 * 且未指定 jdbcType 时会退回 {@code JdbcType.OTHER}，部分驱动会直接报
 * 「无效的列类型」。本方法天然会被传入 null（商品场景 setmealId 为 null，反之亦然）。
 */
@Mapper
public interface ShoppingCartMapper extends BaseMapper<ShoppingCart> {

    /** 原子自增一次：{@code number = number + 1}。命中已有条目时用它，替代历史「先查后写」。 */
    @Update("UPDATE shopping_cart SET number = number + 1 "
            + "WHERE user_id = #{userId} "
            + "AND dish_key = IFNULL(#{dishId,jdbcType=BIGINT}, 0) "
            + "AND setmeal_key = IFNULL(#{setmealId,jdbcType=BIGINT}, 0) "
            + "AND dish_flavor = #{dishFlavor,jdbcType=VARCHAR}")
    int incrementNumber(@Param("userId") Long userId,
                        @Param("dishId") Long dishId,
                        @Param("setmealId") Long setmealId,
                        @Param("dishFlavor") String dishFlavor);

    /**
     * 原子自减一次：{@code number = number - 1}，且仅在 {@code number > 1} 时执行
     * —— 「减到 0 即删除」的语义由 {@link #deleteWhenLastOne} 承担，两条语句配合使用。
     */
    @Update("UPDATE shopping_cart SET number = number - 1 "
            + "WHERE user_id = #{userId} "
            + "AND dish_key = IFNULL(#{dishId,jdbcType=BIGINT}, 0) "
            + "AND setmeal_key = IFNULL(#{setmealId,jdbcType=BIGINT}, 0) "
            + "AND dish_flavor = #{dishFlavor,jdbcType=VARCHAR} "
            + "AND number > 1")
    int decrementNumber(@Param("userId") Long userId,
                        @Param("dishId") Long dishId,
                        @Param("setmealId") Long setmealId,
                        @Param("dishFlavor") String dishFlavor);

    /** 原子删除「最后一件」：仅在 {@code number = 1} 时删除，避免把还有多份的条目误删。 */
    @Delete("DELETE FROM shopping_cart "
            + "WHERE user_id = #{userId} "
            + "AND dish_key = IFNULL(#{dishId,jdbcType=BIGINT}, 0) "
            + "AND setmeal_key = IFNULL(#{setmealId,jdbcType=BIGINT}, 0) "
            + "AND dish_flavor = #{dishFlavor,jdbcType=VARCHAR} "
            + "AND number = 1")
    int deleteWhenLastOne(@Param("userId") Long userId,
                          @Param("dishId") Long dishId,
                          @Param("setmealId") Long setmealId,
                          @Param("dishFlavor") String dishFlavor);
}
