package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mall.common.constant.MessageConstant;
import com.mall.common.constant.StatusConstant;
import com.mall.common.context.BaseContext;
import com.mall.common.exception.BaseException;
import com.mall.common.properties.JwtProperties;
import com.mall.common.utils.JwtUtil;
import com.mall.common.utils.PasswordUtil;
import com.mall.mapper.EmployeeMapper;
import com.mall.pojo.dto.EmployeeDTO;
import com.mall.pojo.dto.EmployeePageQueryDTO;
import com.mall.pojo.dto.EmployeeUpdateDTO;
import com.mall.pojo.dto.LoginDTO;
import com.mall.pojo.entity.Employee;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

/**
 * 员工服务：管理端登录 + 员工信息的查询与维护
 */
@Service
public class EmployeeService {

    /** 分页兜底值，与 EmployeePageQueryDTO 的字段默认值保持一致 */
    private static final int DEFAULT_PAGE = 1;
    private static final int DEFAULT_PAGE_SIZE = 10;

    /**
     * 单页条数上限。取 100 的依据：
     * 1) 后台员工列表一屏最多展示 50~100 行，再大对使用者没有意义；
     * 2) 按每行 JSON 约 200 字节估算，100 行 ≈ 20KB 响应体，远低于网关/浏览器阈值；
     * 3) 上限的意义是"防呆 + 防放大"，不是满足业务最大值，所以取能覆盖真实用量的最小值。
     */
    private static final int MAX_PAGE_SIZE = 100;

    @Autowired
    private EmployeeMapper employeeMapper;

    @Autowired
    private JwtProperties jwtProperties;

    /**
     * 员工登录：校验密码与账号状态，签发管理端 JWT
     */
    public String login(LoginDTO loginDTO) {
        Employee employee = employeeMapper.selectOne(
                new LambdaQueryWrapper<Employee>()
                        .eq(Employee::getUsername, loginDTO.getUsername()));
        if (employee == null || !PasswordUtil.matches(loginDTO.getPassword(), employee.getPassword())) {
            throw new BaseException(MessageConstant.PASSWORD_ERROR);
        }
        if (StatusConstant.DISABLE.equals(employee.getStatus())) {
            throw new BaseException(MessageConstant.ACCOUNT_DISABLED);
        }
        return JwtUtil.createToken(jwtProperties.getAdminSecret(), jwtProperties.getAdminTtl(), employee.getId());
    }

    /**
     * 详情
     */
    public Employee getById(Long id) {
        Employee employee = employeeMapper.selectById(id);
        if (employee != null) {
            employee.setPassword(null);
        }
        return employee;
    }

    /**
     * 新增员工：用户名唯一预检 → 密码加密 → 落库
     * <p>
     * 关于唯一性：下面的预检只是为了给用户一个能看懂的错误提示，
     * <b>它挡不住并发</b>——两个请求可能同时查到 count=0。
     * 真正保证唯一性的是 employee 表上的唯一索引 uk_username：
     * 并发下必有一个 insert 抛 DuplicateKeyException。
     * 预检 + 唯一索引，两者都要有，缺一不可。
     */
    public void save(EmployeeDTO employeeDTO) {
        Long count = employeeMapper.selectCount(new LambdaQueryWrapper<Employee>()
                .eq(Employee::getUsername, employeeDTO.getUsername()));
        if (count > 0) {
            throw new BaseException("用户名" + MessageConstant.ALREADY_EXISTS);
        }

        Employee employee = new Employee();
        employee.setName(employeeDTO.getName());
        employee.setUsername(employeeDTO.getUsername());
        // 密码必须加密后入库，口径与建库脚本的 SHA2(pwd,256) 一致
        employee.setPassword(PasswordUtil.encode(employeeDTO.getPassword()));
        employee.setPhone(employeeDTO.getPhone());
        // 显式设默认状态，不依赖数据库的 DEFAULT 1
        employee.setStatus(StatusConstant.ENABLE);

        employeeMapper.insert(employee);
    }

    /**
     * 编辑员工资料：只允许改 name / phone
     * <p>
     * <b>为什么这么写：</b>构造出来的 Employee 只有 id / name / phone 三个非 null 字段，
     * 其余全部留 null。MyBatis-Plus 的 {@code updateById} 默认字段策略是
     * {@code FieldStrategy.NOT_NULL} —— null 字段不拼进 SQL，
     * 所以 password / username / status 天然不会被覆盖，不需要手动搬值。
     * <p>
     * <b>同一件事的陷阱面：</b>正因为 null 会被忽略，将来若要把 phone 真正清空（置 null），
     * {@code updateById} 会静默不生效（用户以为清了、库里没变）。
     * 那种需求必须显式用 {@code UpdateWrapper.set()} 处理，不能靠 updateById。
     * <p>
     * 单条 UPDATE 由数据库保证原子性，不需要 {@code @Transactional}。
     */
    public void update(Long id, EmployeeUpdateDTO employeeUpdateDTO) {
        Employee employee = new Employee();
        employee.setId(id);
        employee.setName(employeeUpdateDTO.getName());
        employee.setPhone(employeeUpdateDTO.getPhone());

        int rows = employeeMapper.updateById(employee);
        // rows == 0 说明 id 不存在。不能直接返回成功：
        // update 影响 0 行和"更新成功"是两个完全不同的结果，蒙过去前端会以为改成功了。
        if (rows == 0) {
            throw new BaseException(MessageConstant.EMPLOYEE_NOT_FOUND);
        }
    }

    /**
     * 启用 / 禁用员工
     * <p>
     * <b>校验一：status 只允许 0/1。</b>
     * status 列是 tinyint，而 tinyint 能存 -128~127 —— 传 5 数据库会照单全收，不报错。
     * 后果是静默失效：业务里到处是 {@code status == 1} 判启用、
     * {@code StatusConstant.DISABLE.equals(status)} 判禁用，一个 5 两头都不算，
     * 这个员工"既不是启用也不是禁用"，按状态过滤的列表页两边都查不到他。
     * <b>数据库的列类型兜不住合法值域，必须在应用层判。</b>
     * <p>
     * <b>校验二：不能把自己禁用。</b>
     * 禁用是幂等操作，但禁掉当前登录人他就立刻失去管理能力；
     * 极端情况是最后一个可用管理员把自己禁掉 → 谁也进不了后台（lockout），
     * 只能去数据库改回来。所以这里只拦"禁用"，"启用自己"无害、放行。
     * <p>
     * <b>为什么这条校验放在 Service 而不是 Controller：</b>
     * 它是业务规则（管理员不能自锁），不是参数格式。放这儿将来任何入口
     * （批量禁用、定时任务）都能复用；放 Controller 就只有那一个入口受保护。
     * <p>
     * <b>为什么用 equals 而不是 == 比 Long：</b>
     * Long 是包装类型，{@code ==} 比的是引用。只有 -128~127 走缓存池时"碰巧相等"，
     * 一旦员工 id 超过 127，{@code id == BaseContext.getCurrentId()} 会恒为 false，
     * 这条保护就悄悄失效了 —— 典型的"测试期正确、上线后失效"。
     * <p>
     * 单条 UPDATE 由数据库保证原子性，不需要 {@code @Transactional}。
     */
    public void startOrStop(Integer status, Long id) {
        if (!StatusConstant.ENABLE.equals(status) && !StatusConstant.DISABLE.equals(status)) {
            throw new BaseException(MessageConstant.STATUS_INVALID);
        }

        // id 来自路径参数，不可能是 null；currentId 理论上也已由拦截器写入。
        // 用 id.equals(currentId) 的方向写，currentId 为 null 时返回 false 而不是 NPE。
        if (StatusConstant.DISABLE.equals(status) && id.equals(BaseContext.getCurrentId())) {
            throw new BaseException(MessageConstant.CANNOT_DISABLE_SELF);
        }

        Employee employee = new Employee();
        employee.setId(id);
        employee.setStatus(status);

        int rows = employeeMapper.updateById(employee);
        if (rows == 0) {
            throw new BaseException(MessageConstant.EMPLOYEE_NOT_FOUND);
        }
    }

    /**
     * 分页条件查询：姓名模糊 + 状态精确，两个条件都可为空（为空则不参与过滤）
     * <p>
     * 注意：查询结果必须脱敏。密码摘要是员工表里最敏感的字段，
     * 列表接口是最容易漏掉脱敏的地方——实体直接返回给前端时，
     * 只要忘了 setPassword(null)，整个员工表的密码哈希就全量泄露了。
     */
    public Page<Employee> page(EmployeePageQueryDTO dto) {
        /*---- 参数归一：必须在构造 Page 之前做 ----
         MyBatis-Plus 的 PaginationInnerInterceptor 把 size < 0 当成"不分页"语义：
         willDoQuery 里  size < 0  → 直接跳过 COUNT（total 恒为 0）
         beforeQuery 里  size < 0 且没配 maxLimit → 直接 return，LIMIT 不拼进 SQL
         两者叠加 = LIMIT 消失 + total=0 + 返回全表。
         而这个 size 直接来自客户端查询参数，不夹紧就等于开了个全表拉取的洞。

         为什么放 Service：这是业务规则（分页参数必须落在安全区间），放这儿任何入口
        （导出全量、内部调用、定时任务）都受保护；只夹 Controller 会漏。*/
        int pageNum = (dto.getPage()==null || dto.getPage()<1)?DEFAULT_PAGE: dto.getPage();
        int pageSize = (dto.getPageSize()==null || dto.getPageSize()<1)?DEFAULT_PAGE_SIZE:Math.min(dto.getPageSize(),MAX_PAGE_SIZE);

        if(dto.getStatus()!=null
            && !StatusConstant.ENABLE.equals(dto.getStatus())
            && !StatusConstant.DISABLE.equals(dto.getStatus())){
                throw new BaseException(MessageConstant.STATUS_INVALID);
        }
        /*---- status 值域白名单，与 M2-4 启用禁用接口复用同一套口径 ----
         不能"忽略非法条件"静默返回全量：前端少传或传错时会拿到看似正确的
         全量数据，以为自己筛过了 —— 这正是最该避免的静默失效。*/
        LambdaQueryWrapper<Employee> wrapper = new LambdaQueryWrapper<Employee>()
                // hasText 判空：name 为空/空白字符串时不拼这个条件
                .like(StringUtils.hasText(dto.getName()), Employee::getName, dto.getName())
                .eq(dto.getStatus() != null, Employee::getStatus, dto.getStatus())
                // 按主键倒序：稳定分页，且走主键索引，不产生 filesort
                .orderByDesc(Employee::getId);

        Page<Employee> result = employeeMapper.selectPage(
                new Page<>(pageNum,pageSize), wrapper);

        result.getRecords().forEach(employee -> employee.setPassword(null));
        return result;
    }
}
