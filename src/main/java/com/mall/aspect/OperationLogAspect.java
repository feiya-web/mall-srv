package com.mall.aspect;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mall.annotation.AutoLog;
import com.mall.common.context.BaseContext;
import com.mall.pojo.entity.OperationLog;
import com.mall.mapper.OperationLogMapper;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.Arrays;

/**
 * 操作日志切面：拦截所有 @AutoLog 注解方法，
 * 记录操作人、模块、方法、参数与耗时，落库 operation_log
 */
@Slf4j
@Aspect
@Component
public class OperationLogAspect {

    @Autowired
    private OperationLogMapper operationLogMapper;

    @Autowired
    private ObjectMapper objectMapper;

    @Around("@annotation(autoLog)")
    public Object around(ProceedingJoinPoint joinPoint, AutoLog autoLog) throws Throwable {
        long start = System.currentTimeMillis();
        try {
            return joinPoint.proceed();
        } finally {
            long cost = System.currentTimeMillis() - start;
            try {
                saveLog(joinPoint, autoLog, cost);
            } catch (Exception e) {
                // 日志失败不影响业务
                log.warn("操作日志记录失败: {}", e.getMessage());
            }
        }
    }

    private void saveLog(ProceedingJoinPoint joinPoint, AutoLog autoLog, long cost) {
        OperationLog operLog = new OperationLog();
        operLog.setOperModule(autoLog.module());
        operLog.setOperType(autoLog.type());
        operLog.setOperMethod(joinPoint.getSignature().getDeclaringTypeName()
                + "." + joinPoint.getSignature().getName());
        try {
            Object[] args = joinPoint.getArgs();
            String params = args.length > 0 ? objectMapper.writeValueAsString(args) : "[]";
            operLog.setOperParams(params.length() > 2000 ? params.substring(0, 2000) : params);
        } catch (Exception ignore) {
            operLog.setOperParams("[]");
        }
        Long currentId = BaseContext.getCurrentId();
        operLog.setOperUser(currentId == null ? "anonymous" : String.valueOf(currentId));
        operLog.setOperTime(LocalDateTime.now());
        operLog.setCostTime((int) cost);
        operationLogMapper.insert(operLog);
    }
}
