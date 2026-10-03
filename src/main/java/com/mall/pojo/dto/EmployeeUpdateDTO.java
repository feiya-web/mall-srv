package com.mall.pojo.dto;

import lombok.Data;

import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Pattern;

/**
 * 编辑员工入参
 * <p>
 * 刻意只收 {@code name} / {@code phone} 两个字段，另外三个一律不给：
 * <ul>
 *   <li>{@code username} —— 登录标识。改了等于换账号，还会牵动 uk_username 唯一性；</li>
 *   <li>{@code password} —— 属于"重置密码"这个独立操作（独立权限），
 *       编辑资料和改密码不该共用一个接口，这是安全边界；</li>
 *   <li>{@code status} —— 属于启禁用接口（M2-4），混进来会让两个接口互相覆盖。</li>
 * </ul>
 * <b>为什么用"新建 DTO"而不是"复用 EmployeeDTO + 分组校验"：</b>
 * 分组标记是编译期看不见的契约，Controller 上漏写 {@code groups} 编译器不报错，
 * 校验会静默失效（password 的 @NotBlank 照旧拦你）——正是本项目最怕的失败模式。
 * 而字段"压根不在 DTO 里"是类型层面的保证：请求体里塞 password 也进不来，
 * 不需要任何运行期校验兜着。
 * <p>
 * 注意不设 {@code id} 字段：id 从路径 {@code /{id}} 取，避免 path 与 body 两个 id 打架。
 */
@Data
public class EmployeeUpdateDTO {

    @NotBlank(message = "姓名不能为空")
    private String name;

    /** 选填；@Pattern 对 null 放行，所以不填也能过 */
    @Pattern(regexp = "^1[3-9]\\d{9}$", message = "手机号格式不正确")
    private String phone;
}
