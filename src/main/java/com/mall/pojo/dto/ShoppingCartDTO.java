package com.mall.pojo.dto;

import lombok.Data;

/**
 * 购物车新增 DTO
 */
@Data
public class ShoppingCartDTO {

    private Long dishId;
    private Long setmealId;
    private String dishFlavor;
}
