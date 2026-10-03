package com.mall.common.exception;

/**
 * 删除校验不通过异常（商品起售中/分类关联等）
 */
public class DeletionNotAllowedException extends BaseException {

    public DeletionNotAllowedException(String msg) {
        super(msg);
    }
}
