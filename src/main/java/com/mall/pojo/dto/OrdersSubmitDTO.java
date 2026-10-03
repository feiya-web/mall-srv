package com.mall.pojo.dto;

import lombok.Data;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.NotEmpty;
import java.util.List;

/**
 * 用户下单 DTO
 */
@Data
public class OrdersSubmitDTO {

    @NotBlank(message = "收货人不能为空")
    private String consignee;

    @NotBlank(message = "联系电话不能为空")
    private String phone;

    @NotBlank(message = "收货地址不能为空")
    private String address;

    private String remark;

    /** 支付方式 1微信 2支付宝 */
    private Integer payMethod;

    @NotEmpty(message = "购物车不能为空")
    private List<Long> cartItemIds;
}
