package com.mall.pojo.entity;

import com.baomidou.mybatisplus.annotation.FieldStrategy;
import com.baomidou.mybatisplus.annotation.TableField;
import lombok.Data;

import java.io.Serializable;
import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 订单
 */
@Data
public class Orders implements Serializable {

    /** 订单状态：1待付款 2待发货 3待收货 4配送中 5已完成 6已取消 */
    public static final int PENDING_PAYMENT = 1;
    public static final int TO_BE_ACCEPTED = 2;
    public static final int ACCEPTED = 3;
    public static final int DELIVERING = 4;
    public static final int COMPLETED = 5;
    public static final int CANCELLED = 6;

    private Long id;
    private String number;
    private Long userId;
    private String consignee;
    private String phone;
    private String address;
    private String remark;
    private BigDecimal amount;
    private Integer payStatus;
    private Integer payMethod;
    private Integer status;
    private LocalDateTime orderTime;
    private LocalDateTime checkoutTime;
    private LocalDateTime cancelTime;
    private String cancelReason;
    private String rejectionReason;
}
