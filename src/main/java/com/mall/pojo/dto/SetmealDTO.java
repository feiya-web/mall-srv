package com.mall.pojo.dto;

import lombok.Data;

import java.math.BigDecimal;
import java.util.List;

/**
 * 组合套装 DTO（新增/修改，含商品明细）
 */
@Data
public class SetmealDTO {

    private Long id;
    private String name;
    private Long categoryId;
    private BigDecimal price;
    private String image;
    private String description;
    private Integer status;
    private List<com.mall.pojo.entity.SetmealDish> setmealDishes;
}
