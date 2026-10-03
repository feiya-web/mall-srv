package com.mall.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.pojo.entity.Dish;
import com.mall.pojo.vo.DishVO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface DishMapper extends BaseMapper<Dish> {

    /**
     * 分页查询：dish 关联 category 取分类名（管理端）
     */
    Page<DishVO> pageQuery(Page<DishVO> page, @Param("name") String name,
                           @Param("categoryId") Long categoryId, @Param("status") Integer status);

    /**
     * 扣库存：{@code stock = stock - n}，且只在 {@code stock >= n} 时才扣。
     *
     * <p>为什么不用「查出来 -1 再写回」：那是绝对值写，两个并发下单会读到同一个旧库存、
     * 算出同一个新库存，后写的把先写的覆盖掉 —— 卖超了，接口却都返回成功。
     * 相对写 + 条件判断把"判断"和"扣减"压进同一条语句，行锁兜住并发。
     *
     * @return 1 = 扣成功；0 = 库存不足（条件没命中）
     */
    @Update("UPDATE dish SET stock = stock - #{n} WHERE id = #{id} AND stock >= #{n}")
    int deductStock(@Param("id") Long id, @Param("n") Integer n);

    /**
     * 回补库存：{@code stock = stock + n}。
     *
     * <p>只允许"把订单从待付款改成已取消的那一次"调用（见 OrderTimeoutService），
     * 否则并发回补会把库存越加越多。
     *
     * @return 1 = 回补成功；0 = 该商品不存在（数据异常，调用方要留证据）
     */
    @Update("UPDATE dish SET stock = stock + #{n} WHERE id = #{id}")
    int restoreStock(@Param("id") Long id, @Param("n") Integer n);
}
