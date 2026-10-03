package com.mall.common.exception;

import com.mall.common.constant.MessageConstant;
import com.mall.common.result.Result;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.sql.SQLIntegrityConstraintViolationException;

/**
 * 全局异常处理器：统一异常错误返回
 */
@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BaseException.class)
    public Result<Void> handleBaseException(BaseException e) {
        log.warn("业务异常: {}", e.getMessage());
        return Result.error(e.getMessage());
    }

    @ExceptionHandler(SQLIntegrityConstraintViolationException.class)
    public Result<Void> handleSqlIntegrity(SQLIntegrityConstraintViolationException e) {
        String msg = e.getMessage();
        if (msg != null && msg.contains("Duplicate entry")) {
            String duplicate = msg.split("Duplicate entry")[1].trim();
            return Result.error(duplicate.split("'")[1] + MessageConstant.ALREADY_EXISTS);
        }
        log.error("数据库异常", e);
        return Result.error(MessageConstant.UNKNOWN_ERROR);
    }

    @ExceptionHandler(DuplicateKeyException.class)
    public Result<Void> handleDuplicateKey(DuplicateKeyException e) {
        return Result.error(MessageConstant.ALREADY_EXISTS);
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public Result<Void> handleValidation(MethodArgumentNotValidException e) {
        String msg = e.getBindingResult().getFieldErrors().stream()
                .map(f -> f.getField() + ": " + f.getDefaultMessage())
                .findFirst().orElse("参数错误");
        return Result.error(msg);
    }

    @ExceptionHandler(Exception.class)
    public Result<Void> handleException(Exception e) {
        log.error("系统异常", e);
        return Result.error(MessageConstant.UNKNOWN_ERROR);
    }
}
