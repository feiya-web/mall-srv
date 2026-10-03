package com.mall.annotation;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * 操作日志注解：标注在需要记录操作日志的 Controller 方法上，
 * 由 OperationLogAspect 统一切面处理
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface AutoLog {

    /** 模块名 */
    String module() default "";

    /** 操作类型 */
    String type() default "";
}
