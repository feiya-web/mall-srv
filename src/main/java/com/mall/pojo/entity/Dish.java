package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 商品
 */
@Data
public class Dish implements Serializable {

    private Long id;
    private String name;
    private Long categoryId;
    private BigDecimal price;
    private String image;
    private String description;
    /** 状态 0停售 1起售 */
    private Integer status;
    /**
     * 库存。下单时按 {@code stock = stock - n} 条件扣减（防超卖），
     * 订单超时取消时由"抢到取消状态的那一次"回补，见 DishMapper#deductStock / restoreStock。
     */
    private Integer stock;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
