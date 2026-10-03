package com.mall.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.pojo.entity.Setmeal;
import com.mall.pojo.vo.SetmealVO;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface SetmealMapper extends BaseMapper<Setmeal> {

    Page<SetmealVO> pageQuery(Page<SetmealVO> page, @Param("name") String name,
                              @Param("categoryId") Long categoryId, @Param("status") Integer status);

    /**
     * 扣库存：{@code stock = stock - n}，只在 {@code stock >= n} 时才扣。
     * 语义与 {@link DishMapper#deductStock} 相同：相对写 + 条件判断，防超卖。
     *
     * @return 1 = 扣成功；0 = 库存不足
     */
    @Update("UPDATE setmeal SET stock = stock - #{n} WHERE id = #{id} AND stock >= #{n}")
    int deductStock(@Param("id") Long id, @Param("n") Integer n);

    /**
     * 回补库存：{@code stock = stock + n}，只由抢到"取消"那次状态的调用方执行。
     *
     * @return 1 = 回补成功；0 = 该组合套装不存在
     */
    @Update("UPDATE setmeal SET stock = stock + #{n} WHERE id = #{id}")
    int restoreStock(@Param("id") Long id, @Param("n") Integer n);
}
