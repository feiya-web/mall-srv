package com.mall.pojo.dto;

import lombok.Data;

import java.math.BigDecimal;

/**
 * 商品 DTO（新增/修改，含喜好）
 */
@Data
public class DishDTO {

    private Long id;
    private String name;
    private Long categoryId;
    private BigDecimal price;
    private String image;
    private String description;
    private Integer status;
    private java.util.List<com.mall.pojo.entity.DishFlavor> flavors;
}
