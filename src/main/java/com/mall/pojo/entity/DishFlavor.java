package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;

/**
 * 商品喜好
 */
@Data
public class DishFlavor implements Serializable {

    private Long id;
    private Long dishId;
    private String name;
    private String value;
}
