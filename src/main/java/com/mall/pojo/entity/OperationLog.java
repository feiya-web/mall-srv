package com.mall.pojo.entity;

import lombok.Data;

import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 操作日志（AOP 自动记录）
 */
@Data
public class OperationLog implements Serializable {

    private Long id;
    private String operUser;
    private String operModule;
    private String operType;
    private String operMethod;
    private String operParams;
    private LocalDateTime operTime;
    private Integer costTime;
}
