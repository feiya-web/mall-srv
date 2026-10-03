package com.mall.controller.admin;

import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.annotation.AutoLog;
import com.mall.common.result.Result;
import com.mall.pojo.dto.EmployeeDTO;
import com.mall.pojo.dto.EmployeePageQueryDTO;
import com.mall.pojo.dto.EmployeeUpdateDTO;
import com.mall.pojo.dto.LoginDTO;
import com.mall.pojo.entity.Employee;
import com.mall.service.EmployeeService;
import org.springframework.beans.factory.annotation.Autowired;

import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;
import java.util.Map;

/**
 * 管理端 - 员工
 */
@RestController
@RequestMapping("/admin/employee")
public class EmployeeController {

    @Autowired
    private EmployeeService employeeService;

    /**
     * 登录（唯一免鉴权的管理端接口）
     */
    @AutoLog(module = "员工模块", type = "登录")
    @PostMapping("/login")
    public Result<Map<String, String>> login(@RequestBody LoginDTO loginDTO) {
        String token = employeeService.login(loginDTO);
        return Result.success(Map.of("token", token));
    }

    /**
     * 新增员工
     */
    @AutoLog(module = "员工模块", type = "新增")
    @PostMapping
    public Result<Void> save(@RequestBody @Valid EmployeeDTO employeeDTO) {
        employeeService.save(employeeDTO);
        return Result.success();
    }

    /**
     * 编辑员工（只允许改 name / phone，见 EmployeeUpdateDTO 的说明）
     * <p>
     * id 走路径参数，与下面的 GET /{id} 保持一致的风格。
     */
    @AutoLog(module = "员工模块", type = "编辑")
    @PutMapping("/{id}")
    public Result<Void> update(@PathVariable Long id, @RequestBody @Valid EmployeeUpdateDTO employeeUpdateDTO) {
        employeeService.update(id, employeeUpdateDTO);
        return Result.success();
    }

    /**
     * 启用 / 禁用员工
     * <p>
     * <b>为什么 id 和 status 都走路径参数</b>，而不是写成裸参数 {@code Long id}：
     * 裸参数默认不是必填的，客户端漏传时它是 null；实现里若用 UpdateWrapper
     * 而忘了补 id 条件，一条漏传就变成"全表更新"，整张员工表被改成同一个状态。
     * 路径参数天然不可能缺失 —— 少一段直接匹配不上路由返回 404，这层保护是白送的。
     * <p>
     * <b>为什么用 PUT 而不是 POST：</b>启禁用是幂等操作（连点两次结果一致），
     * 语义上 PUT 比 POST 更准。
     */
    @AutoLog(module = "员工模块", type = "启用禁用")
    @PutMapping("/{id}/status/{status}")
    public Result<Void> startOrStop(@PathVariable Long id, @PathVariable Integer status) {
        employeeService.startOrStop(status, id);
        return Result.success();
    }

    /**
     * 分页条件查询
     * <p>
     * 路径 /page 是字面量，优先级高于下面的 /{id} 模板，不会被当成 id=page 处理。
     */
    @GetMapping("/page")
    public Result<Page<Employee>> page(EmployeePageQueryDTO dto) {
        return Result.success(employeeService.page(dto));
    }

    @GetMapping("/{id}")
    public Result<Employee> getById(@PathVariable Long id) {
        return Result.success(employeeService.getById(id));
    }

}
