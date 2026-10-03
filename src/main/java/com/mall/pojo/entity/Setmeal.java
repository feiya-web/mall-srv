package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 组合套装
 */
@Data
public class Setmeal implements Serializable {

    private Long id;
    private String name;
    private Long categoryId;
    private BigDecimal price;
    private String image;
    private String description;
    private Integer status;
    /**
     * 库存，语义同 {@link com.mall.pojo.entity.Dish#getStock()}：
     * 下单条件扣减，超时取消由抢到取消状态的一方回补。
     */
    private Integer stock;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
