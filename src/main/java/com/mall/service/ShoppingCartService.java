package com.mall.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mall.common.exception.ParameterException;
import com.mall.mapper.DishMapper;
import com.mall.mapper.SetmealMapper;
import com.mall.mapper.ShoppingCartMapper;
import com.mall.pojo.dto.ShoppingCartDTO;
import com.mall.pojo.entity.Dish;
import com.mall.pojo.entity.Setmeal;
import com.mall.pojo.entity.ShoppingCart;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 购物车服务：同一用户 + 同一商品（商品 / 组合套装）+ 同一喜好 视为同一条目，重复添加只累加数量。
 *
 * <p>M3-1 修复后的三条并发要点：
 * <ol>
 *   <li><b>键要一致</b>：入参喜好先归一化（{@code null → ""}），保证「查询用的键」与
 *       「落库的键」逐字节相同。MySQL 里 {@code NULL = ''} 恒为假（三值逻辑），
 *       历史缺陷（重复点击产生多行）正是从这里来的。</li>
 *   <li><b>增量交给数据库</b>：用 {@code number = number + 1} 的相对写，
 *       不用「读出来 +1 再写回」的绝对值写，否则并发下丢更新。</li>
 *   <li><b>兜底要显式</b>：唯一键 {@code uk_cart_user_item} 兜住极端并发下的重复插入；
 *       被它拦下时<b>回退成一次自增并记日志</b>，而不是把异常交给全局处理器变成一句
 *       「已存在」（那种静默吞异常正是本项目缺陷清单里的一条）。</li>
 * </ol>
 *
 * <p>本类不标 {@code @Transactional}：所有写操作都是**单条** SQL，
 * 原子性由数据库保证（与 {@code EmployeeService} 里的既有约定一致）。
 */
@Slf4j
@Service
public class ShoppingCartService {

    @Autowired
    private ShoppingCartMapper shoppingCartMapper;
    @Autowired
    private DishMapper dishMapper;
    @Autowired
    private SetmealMapper setmealMapper;

    /**
     * 加一份：已存在则累加，不存在则新建。
     *
     * <p>路径设计：先打一发原子自增（命中即返回 —— 覆盖绝大多数正常点击）
     * → 没命中才走「校验 + 新建」→ 新建被唯一键拦下时再回退自增。
     * 这样既没有「先查后写」的竞态，也不需要悲观锁或乐观锁字段。
     */
    public void add(Long userId, ShoppingCartDTO dto) {
        requireTarget(dto);
        String flavor = normalizeFlavor(dto.getDishFlavor());

        // 快路径：条目已存在 → 原子 +1，直接返回。
        if (shoppingCartMapper.incrementNumber(userId, dto.getDishId(), dto.getSetmealId(), flavor) > 0) {
            return;
        }

        // 慢路径：条目不存在 → 校验商品 → 新建。
        ShoppingCart cart = new ShoppingCart();
        cart.setUserId(userId);
        cart.setDishFlavor(flavor);
        if (dto.getDishId() != null) {
            Dish dish = dishMapper.selectById(dto.getDishId());
            if (dish == null) {
                throw new ParameterException("商品不存在");
            }
            cart.setDishId(dto.getDishId());
            cart.setName(dish.getName());
            cart.setImage(dish.getImage());
            cart.setAmount(dish.getPrice());
        } else {
            Setmeal setmeal = setmealMapper.selectById(dto.getSetmealId());
            if (setmeal == null) {
                throw new ParameterException("组合套装不存在");
            }
            cart.setSetmealId(dto.getSetmealId());
            cart.setName(setmeal.getName());
            cart.setImage(setmeal.getImage());
            cart.setAmount(setmeal.getPrice());
        }
        cart.setNumber(1);
        try {
            shoppingCartMapper.insert(cart);
        } catch (DuplicateKeyException e) {
            // 并发窗口：自增落空的那一瞬间，别的线程刚好把同一业务键插进去了。
            // 唯一键 uk_cart_user_item 把它拦下 —— 这不是错误，而是「本该累加」的信号。
            int retried = shoppingCartMapper.incrementNumber(
                    userId, dto.getDishId(), dto.getSetmealId(), flavor);
            if (retried == 0) {
                // 极端情况：插入被拒后条目又消失了。留证据，绝不静默吞掉。
                log.warn("购物车并发插入回退自增仍未命中：userId={}, dishId={}, setmealId={}, flavor=[{}]",
                        userId, dto.getDishId(), dto.getSetmealId(), flavor, e);
                throw new ParameterException("购物车添加失败，请重试");
            }
            log.info("购物车并发插入被唯一键拦下，已回退为自增：userId={}, dishId={}, setmealId={}, flavor=[{}]",
                    userId, dto.getDishId(), dto.getSetmealId(), flavor);
        }
    }

    /**
     * 减一份：减到 0 删除条目。
     *
     * <p>两条原子语句顺序执行，谁命中谁负责：
     * <ol>
     *   <li>{@code number > 1} → 自减；</li>
     *   <li>否则（number 已是 1）→ 删除「最后一件」。</li>
     * </ol>
     * 这个顺序在并发下同样成立：两个请求同时减一个 {@code number = 2} 的条目时，
     * 前者自减成功（2 → 1），后者自减落空（因为 {@code number > 1} 已不成立）后走到第 2 步，
     * 把「最后一件」删掉 —— 效果正好等于减了两次。
     */
    public void sub(Long userId, ShoppingCartDTO dto) {
        requireTarget(dto);
        String flavor = normalizeFlavor(dto.getDishFlavor());

        if (shoppingCartMapper.decrementNumber(userId, dto.getDishId(), dto.getSetmealId(), flavor) > 0) {
            return;
        }
        if (shoppingCartMapper.deleteWhenLastOne(userId, dto.getDishId(), dto.getSetmealId(), flavor) > 0) {
            return;
        }
        throw new ParameterException("购物车中不存在该商品");
    }

    public List<ShoppingCart> list(Long userId) {
        return shoppingCartMapper.selectList(new LambdaQueryWrapper<ShoppingCart>()
                .eq(ShoppingCart::getUserId, userId)
                .orderByAsc(ShoppingCart::getCreateTime));
    }

    public void clean(Long userId) {
        shoppingCartMapper.delete(new LambdaQueryWrapper<ShoppingCart>()
                .eq(ShoppingCart::getUserId, userId));
    }

    /** 商品 / 组合套装二选一，不能都为空。 */
    private static void requireTarget(ShoppingCartDTO dto) {
        if (dto.getDishId() == null && dto.getSetmealId() == null) {
            throw new ParameterException("购物车参数错误");
        }
    }

    /**
     * 喜好归一化：{@code null} 一律折叠成空串。
     *
     * <p>这是 M3-1 的第一个根因 —— 历史代码<b>查询时用 {@code ""}、写入时用 {@code null}</b>，
     * 而 MySQL 中 {@code NULL = ''} 恒为假，于是永远查不到、永远新增。
     *
     * <p>注意这里<b>不依赖</b> DDL 的 {@code DEFAULT ''}：即便把列默认值去掉，
     * 代码也要自己把键归一好。DDL 是第二道防线，不是第一道。
     */
    private static String normalizeFlavor(String flavor) {
        return flavor == null ? "" : flavor;
    }
}
