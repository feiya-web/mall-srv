package com.mall.pojo.vo;

import lombok.Data;

/**
 * 订单统计 VO（管理端看板）
 */
@Data
public class OrderStatisticsVO {

    private Long toBeConfirmed;
    private Long confirmed;
    private Long deliveryInProgress;
}
