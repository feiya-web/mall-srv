package com.mall.pojo.vo;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.math.BigDecimal;

/**
 * 下单结果 VO
 */
@Data
@AllArgsConstructor
public class OrderSubmitVO {

    private Long id;
    private String orderNumber;
    private BigDecimal orderAmount;
    private Integer status;
}
