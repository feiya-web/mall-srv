package com.mall.pojo.vo;

import com.mall.pojo.entity.OrderDetail;
import lombok.Data;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 订单 VO
 */
@Data
public class OrderVO {

    private Long id;
    private String number;
    private Long userId;
    private String consignee;
    private String phone;
    private String address;
    private String remark;
    private BigDecimal amount;
    private Integer payStatus;
    private Integer status;
    private LocalDateTime orderTime;
    private List<OrderDetail> orderDetailList;
}
