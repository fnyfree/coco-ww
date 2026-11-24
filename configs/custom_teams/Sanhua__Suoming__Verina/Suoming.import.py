import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.Suoming import Suoming as NativeSuoming
from src.combat.MacroKit import MacroKit, MacroBroken, send_input_left

# ==================== 物理 F 键监视 ====================
# 跟 Iuno__JingRan__ShoreKeeper/JingRan.py 里那份逐字相同, 说明见 Chisa__Suisui__YangYangSp/YangYangSp.py。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
F_TASK_NAME = 'FStartCombatTask'


# 监视线程: 椰果版用主程序 src/combat/AxisWatch.py 那份; 原版 OKWW 没有这个模块, 导入失败就用下面自带的同一份
# (逻辑和 AxisWatch.py 一致, 写到 task 上的标记也一样, 线程按标记只起一次)
try:
    from src.combat.AxisWatch import f_settings, start_enable_watch, start_f_watch
except ImportError:
    def f_settings(task):
        executor = getattr(task, 'executor', None)
        for group in ('trigger_tasks', 'onetime_tasks'):
            for other in getattr(executor, group, None) or ():
                if type(other).__name__ == 'FStartCombatTask':
                    try:
                        return bool(other.enabled), float(getattr(other, 'f_start_delay', 0) or 0)
                    except Exception:
                        return True, 0.0
        return True, 0.0

    def start_enable_watch(task, logger=None):
        if getattr(task, '_ww_enable_watch_started', False):
            return
        task._ww_enable_watch_started = True
        if not hasattr(task, '_ww_task_restarted_at'):
            task._ww_task_restarted_at = -1

        def watch():
            was_on = None
            while True:
                try:
                    now_on = bool(getattr(task, 'enabled', False))
                    if was_on is False and now_on:
                        task._ww_task_restarted_at = time.time()
                        if logger:
                            logger.info('auto combat re-enabled -> opener re-armed')
                    was_on = now_on
                    time.sleep(0.2)
                except Exception:
                    return

        threading.Thread(target=watch, name='ok_ww_enable_watch', daemon=True).start()

    def start_f_watch(task, logger=None):
        if getattr(task, '_f_watch_started', False):
            return
        task._f_watch_started = True
        if not hasattr(task, '_f_last_press'):
            task._f_last_press = -1

        def watch():
            try:
                user32 = ctypes.windll.user32
                user32.GetAsyncKeyState.restype = ctypes.c_short
            except Exception as e:
                task._f_watch_started = False
                if logger:
                    logger.error(f'f watch: GetAsyncKeyState unavailable ({e}), '
                                 f'the "press F to re-arm the opener" shortcut stays off')
                return
            last_log = 0
            was_down = False
            while True:
                try:
                    # 只读高位: 低位读一次就清而且整个进程共享
                    down = bool(user32.GetAsyncKeyState(0x46) & 0x8000)
                    if down and not was_down and f_settings(task)[0]:
                        now = time.time()
                        if getattr(task, '_three_fire_macro_busy', False) or getattr(task, '_ww_macro_busy', False):
                            if logger and now - last_log >= 1.0:
                                last_log = now
                                logger.info('physical F pressed while the macro is running '
                                            '(manual execution) - opener NOT re-armed')
                            was_down = down
                            time.sleep(0.05)
                            continue
                        task._f_last_press = now
                        task._f_pressed_at = now
                        if logger and now - last_log >= 1.0:
                            last_log = now
                            logger.info('physical F pressed, opener re-armed')
                    was_down = down
                    time.sleep(0.05)
                except Exception:
                    return

        threading.Thread(target=watch, name='ok_ww_f_watch', daemon=True).start()


# ==================== 自动战斗开关监视 ====================
# 【玩家手动开关自动战斗, 必须从启动轴重跑】。手动停任务不走 combat_end(), opener_armed 会跨局留在
# False, 而角色对象常被粘性复用、reset_state() 不跑。只有独立线程能观察到 enabled 的 True->False->True。
ENABLE_POLL_INTERVAL = 0.2


class Suoming(MacroKit, NativeSuoming):
    """锁暝。锁散维(锁暝/散华/维里奈)里她是主 C, 兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    E:/轴视频/【锁散维】输出还行.mp4(B 站, 1080p 30fps, 1 倍速直录, 底部字幕, 没有键盘浮层)。
    墙钟用视频时间; 倒计时在大招演出 / 刻心念转圈里会停。下面的秒数都是视频秒。

    ================= 轴的形状 =================
    启动轴(开局散华):
      散华  E -> 按住普攻 -> 按着放 R(演出 ~1.2s) -> 光标进冰痕区松手(重击·爆裂, 三块冰一起炸) -> 切维里奈
      维里奈 E -> 声骸 -> R(演出 ~2.1s) -> 跳A 到协奏满 -> 变奏锁暝
      锁暝  变奏·闪裂(念起 -> 念深), R 亮着【不放】, 站 ~1.4s -> 切散华(普通入场)
      散华  点 A ~0.9s -> 声骸 -> [协奏不满接着点 A] -> 变奏锁暝
      锁暝  变奏·碎霆(+200 妄执) -> R(演出 ~5s, 给自己同奏 5s) -> E 破隙(吃掉同奏换封印之主)
            -> 按住普攻到妄执满(视频 7.4s; 挨打掉血就松开重按) -> E 无舍念
            -> 按住普攻刻心念(转圈后进时停, 里面放声骸; 时停结束后 ~1.1s 收招才能出念起 A)
            -> 念起点 A 两套到妄执满(视频 6.4s) -> E 锁妄念(回念深) -> 协奏满变奏散华
    循环轴(散华变奏入场开始): 跟启动轴一样(散华 E 等变奏入场动作); 锁暝第二次上场 R 多半还差 ~1s 冷却, 边 A 边等。
    一圈实机 29~31.6s 游戏时间(视频 ~26s), 被锁暝 R 的 25s 冷却卡住。
    配置: 散华带暗鸟(变身型), 锁暝带脱手声骸; 维里奈 0 链也行(跳A 攒协奏久一点, 上限 VE_JUMP_A_CAP); 16:9, 默认键位。

    ================= 为什么这么打(nanoka 锁暝 1312) =================
    - 锁暝的 R 只在念深能放, 放完给自己 5s 同奏; 有同奏时按 E 破隙 = 吃掉同奏、清妄执、扣 20 协奏, 换「封印之主」12s:
      解伞形普攻/旋雷破倍率 +100%、每段命中 +5 协奏、暴伤 +100%, 切走就没。所以 R 之后整段输出都不能切人。
    - 念深妄执满 E = 无舍念, 接着普攻出刻心念(按住 = 时停里一直转, 收招 +40 协奏, 结束回念起);
      念起妄执满 E = 锁妄念(回念深)。最后回到念深再切走, 下一次变奏进来才是碎霆(+200 妄执)而不是闪裂(清妄执)。
    - 散华的延奏是"下一位登场角色普攻伤害加深、切走就没" -> 散华必须是锁暝开 R 前最后一个上场的人(两次变奏锁暝的原因)。
      维里奈的延奏是全队增伤, 给谁都行, 顺手让锁暝第一次上场进念深。
    - 妄执只在锁暝在场时命中才给, 后台落地的伤害不算。

    ================= 保底 =================
    - 切人: 超时等 HUD 回来再切, 最多重试 SWITCH_RETRIES 次; 都失败这圈作废, 下一圈从散华重来。
    - 大招: 亮了没放出去重按、不亮攒能量(散华/维里奈点 A, 锁暝边 A 边按 R)。
    - E: 散华 E 没见进 CD 爆裂后补一轮; 散华第二次上场协奏不满补 E; 锁暝破隙没见进 CD 再按一轮;
      无舍念被打断松手连点 E 再按刻心念; 维里奈变奏入场在空中, 等落地再放。
    - 锁暝攒妄执挨打: 按住模式读血条掉了就重按; A2DODGE 模式本来就是点按 + 闪避。
    - 锁暝攒妄执/念起点 A 看到处决提示就处决(按住那段先松手, 处决完重新按住, 不算上限)。
    - 丢目标: 怪没了(打死/换波)松手按中键等新怪(不算上限); 怪还在但协奏 2.5s 没涨就松开 + 中键 + 重按。
    - 锁妄念必须打出来: 念起点 A 总上限 SO_AWAKE_CAP 20s, E 点了没出就回去点 A 再来。
    - 维里奈跳A 到协奏满上限 8s(照顾 0 链)。

    ================= 判据 =================
    - 妄执满 = E 位换成无舍念(框架模板 suoming_forte_e)或锁妄念(自己的字形 SO_WN_GLYPH, 框架模板认不出), 连 2 帧。
    - 协奏真满 = 另外两个人头像外面的细白环(心锁守标定的 GLOW_*), 读数快满时会误报 1.00。
    - 大招放没放出去 = 按过 R 之后 HUD 连续消失; 演完 = HUD 回来连读 2 帧(演出中途会闪)。

    ================= 改这个文件之前先读 =================
    - 引擎(macro_* / quick_switch / press_key_until_cd / fire_r / ensure_r / 光圈 / 处决提示)和锁暝的
      so_wuse / so_echo / so_r_asap 是从心锁守 v2.0.0 Hsin.py 逐字抽来的, 那边实机标定过。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 普攻一律走 tap()(按住 A_TAP 秒): task.click() 只按 10ms, 打不出来。
    - 长按期间不调 click()/mouse_down()/send_key() —— 它们先投 WM_ACTIVATE, 会把按住打断。
    """

    # ==================== 引擎(从心锁守 v2.0.0 Hsin.py 抽, 那边实机标定过) ====================
    A_INTERVAL = 0.12
    KEY_INTERVAL = 0.10
    A_TAP = 0.06                 # 普攻按住这么久才算一下; task.click() 的 10ms 会被吞
    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True
    # ---- 大招 / 演出 ----
    LIB_KEY_INTERVAL = 0.03
    LIB_ANIM_TIME_OUT = 8.0
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防假成功)
    # 【演出中途 HUD 会闪一两帧】等"HUD 回来"时要连读到这么多帧才算, 而且演出开始后不到 min_gone 秒的"回来"一律不认
    HUD_BACK_CONFIRM = 2
    # ---- 杂项 ----
    CON_SAMPLE_MAX = 24
    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上
    OPENER_MIN_GAP = 60.0

    # ==================== 保底 ====================
    # 原则: 只花时间的保底默认开(切人重试 / 等 HUD / 判据没成立就再按【那一个本该出去的键】);
    # 会改状态的只在判据明确说"这一下没出去"时才补, 不凭猜测多按
    # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)。2 -> 4: 一次切人最多耗 ~22s 才放弃,
    # 放弃 = 这圈作废、下一圈从散华重来(用户 10-25: 切人必须保证切到)
    SWITCH_RETRIES = 4
    SWITCH_RETRY_TIME_OUT = 3.00
    HUD_LOST_ABORT = 8.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃(最长的演出 ~5s)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    R_TRIES = 3                  # R 亮着但没放出去(被吞/被打断)就再按, 最多这么多次
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长
    # 【协奏真满看头像光圈】协奏满(切人能变奏)那一帧, 另外两个人的头像外面套上一圈细白环往外扩。
    # 框架的协奏环读数快满就报 1.00(心锁守开局比真满早 1.8s 就读到), 光圈跟真满同一帧出来。
    # 只看另外两格: 刚切进来的人自己那格还残留一圈。读法: 头像中心极坐标展开, 只取上半圈(避开数字牌/名字条/属性图标),
    # 半径 80~118(4K)里找白色细脊, 统计落在同一半径上的角度占比。
    # 心锁守 10-24 录屏离线(2K + 缩到 1080): 真满那帧两格都 ~0.9, 之前 <= 0.13; 单格误读最高 0.34;
    # 亮背景会把一格压到 0.1~0.3, 所以两格合起来判。认不出(1080 偶尔)就走读数
    GLOW_BOTH = 0.15             # 两格都 >= 这个, 而且有一格 >= GLOW_ONE
    GLOW_ONE = 0.35
    GLOW_SOLO = 0.60             # 一格就够
    GLOW_CONFIRM = 2
    SK_CON_ENOUGH = 1.0          # 0.99 是环没闭合的哨兵值, 要真满
    SK_CON_FULL_CONFIRM = 3
    SK_CON_POLL = 0.03
    # 处决提示: 只认怪身上的 F(屏幕中间 f_break >= 0.90, 不是拾取 F), 判据照抄嘉露莫 v1.1.0(2K/1080 实机过)
    BREAK_CENTER_MIN = 0.90
    BREAK_RING_MIN = 0.92        # 框架 check_f_break 同一个阈值
    BREAK_RESEND = 0.30          # 按完这么久提示还在 = 被吞了(硬直/上一个动作没收), 再按
    BREAK_PRESSES = 5
    BREAK_GONE_FRAMES = 3        # 提示单帧会掉到 0.85~0.88(特效闪), 连这么多帧不见才算处决起手了

    # ==================== 锁暝通用(从心锁守抽) ====================
    # 放 R(so_r_asap): R 没好(CD / 能量不够)不站着等, 一直点 A, 每下 A 后顺手按一下 R; 读到亮再连按一小段。
    # 心锁守 10-25: 读到亮就连按 2.5s 的旧版, 能量差一点图标也读成亮 -> 锁暝站着按 2.5s
    SO_R_LEAD = 0.25             # 切人键 -> 开始(变奏入场的锁里按的被吞)
    SO_R_CAP = 9.00              # 放 R 的总上限
    SO_R_ENTRY = 1.70            # 切人键起这么久是变奏入场动作(实机 R 在切人后 ~1.55s 才出得来): 读到亮就一直按 R
    SO_R_BURST = 0.30            # 入场之后读到亮: 连按 R 这么久, HUD 没消失 = 其实还没好
    SO_R_BACKOFF = 0.40          # 第 n 次连按没出之后 n 倍这么久只点 A(每下 A 后照样按一下 R)
    SO_R_E_FROM = 1.50           # 没变奏入场(可能在念起, R 不解锁)时: 进场这么久后妄执满就点 E(锁妄念进念深)
    SO_R_ANIM_MIN = 2.50         # 锁暝大招藏 HUD(心锁守实机 4.9s, 本视频 ~5s)
    SO_R_ANIM_CAP = 8.00
    # 声骸一直按到 CD 涨上来(心锁守 10-24: R 收招里连按 2 下, 技能栏有按下的圈但没进 CD)
    SO_ECHO_CAP = 1.00
    SO_ECHO_RISE = 1.0           # CD 比按之前高出这么多秒才算放出去
    SO_A_EVERY = 0.20            # 普攻点按间隔(动作里的点击被丢掉)。念深里只点不按住: A2 之后按住是旋雷破
    # 无舍念 / 锁妄念(so_wuse): E 位还是那个图标就这么密地点 E, 读到换掉了(连 2 帧)才停
    SO_KX_E_EVERY = 0.10
    SO_KX_E_TAP = 0.05           # 一下 E 按住这么久(10ms 的会被吞)
    SO_KX_WUSE_CAP = 2.50
    # 锁妄念(念起妄执满的 E 位: 紫圈+白色旋涡伞面)。框架 suoming_forte_e 只认无舍念, 认不出它:
    # 10-25 23:05 实机念起每圈都点满 9s cap, 锁妄念早亮了 1.4~2.5s 也没按, 直接切散华(下次变奏成了闪裂)。
    # 自己的 16x16 灰度字形(E 位圆内), 录屏 4 圈 2K/1080: 锁妄念 0.94~1.00, 其他(普通 E/无舍念/冷却/散华维里奈的 E) <= 0.61
    SO_WN_MIN = 0.80
    SO_WN_GLYPH = (
        'AAAAAAATQiIODAcAAAAAAAAAAAIcVYSgaB4YEwcAAAAAAAUTExATJ1yBNRMUCgAAAAI0lpiHej0NKHdKEhMIAAA/0VcRDB1MXxsUcF4cEAEN'
        'xHQpcpaYma1sGhJ2ZRIHKNQoEpP7++nJzmAOF48jCzDBGgqm5K/AzLWrcQ09aQszwBwJV5Tx+8/HochXH4gZHcksCVXx/8fh78zAxUFgRgi3'
        'Xw2sy/Kg/v3B6q1BOl8AdqEahGlktP//y937XS4PACXRXTFgDH/0/vul9WYYAAAAYLNALwoPeam6mXaHAwAAAABNfEEMCA1Mh3InBAAAAAAA'
        'AAQqKQgHBwkLAAAAAA=='
    )
    SO_E_SLOT = (3209, 1893, 78, 74)    # 锁暝 E 位(4K, 跟 suoming_forte_e 的标注框一样)
    SO_KX_A_FROM = 0.30          # 无舍念出了 -> 按住普攻出刻心念(刻心念图标在无舍念后 ~0.3s 出来)

    # ==================== 散华 ====================
    # 第一次上场: [变奏入场等锁] E -> R -> 演完 -> 切维里奈。变奏 QTE 第 53F 才放延奏、第 70F 前不能切人;
    # 视频变奏进场 -> E 0.95s, 开局普通进场 -> E 0.05s
    SH_INTRO_LOCK = 0.80         # 变奏入场: 切人键起这么久再开始按 E(入场动作里按的被吞, 连按盖住)
    SH_E_LEAD = 0.05             # 普通入场
    SH_E_CAP = 1.50              # E 按到进 CD 的上限
    SH_E_GRACE = 0.80            # 这么久一次都没见过 E 亮 = 本来就在 CD(变奏入场时 E 位会先显示成按下的样子)
    # E 按下 -> 最早按 R。E 命中在 19~31F, R 早了会把 E 打断(冰棱没出, 少 30 协奏): 10-25 实机隔 0.25s,
    # 散华回来协奏只有 0.37; 视频隔 0.6s, 回来时已经满了
    SH_E_TO_R = 0.60
    # 重击·爆裂(按住普攻, 光标进冰痕区松手): 引爆冰棱/冰川/冰棘各 +15 协奏, 爆裂自己 +15。v0.1.1 没打这一下,
    # 10-25 20:37 实机散华第二次上场协奏 0.47(视频 0.73), 点 A 3s 才到 0.88
    SH_E_TO_Z = 0.30             # 普通入场: E 按下 -> 按住普攻(视频 0.3s; R 在 E 后 SH_E_TO_R 按着放)
    SH_E_TO_Z_INTRO = 0.05       # 变奏入场: E 按下就按住(重击排在 E 后面, 视频 E -> 光标开始走 0.1s; R 一样在 E 后 SH_E_TO_R 按着放)
    SH_Z_MIN = 0.15              # 按住不到这么久不看光标
    # 认不出光标就按满这么久松手。变奏入场从按下算, 放了 R 从 R 演完 HUD 回来算(R 前按的不算)。
    # 光标 ~1.15 条宽/s(视频+10-25 22:44 实机一致); R 演完 HUD 回来后光标还要停 ~0.3s 才接着走(实机停在 0.16):
    # v0.1.2 的 0.85 把 R 前按的也算进去, HUD 回来只剩 0.45s, 光标 0.40 没进区(区 0.45~0.95)就松了。
    # 0.90: 变奏入场 E 收招 ~0.35s + 0.55s -> ~0.67, 放 R 那次 0.16 + 0.6s -> ~0.85, 都落在 2 层区里
    SH_Z_CAP = 0.90
    SH_Z_BACK = 0.35             # 光标读数比上一次读到的往回退超过这么多 = 不是同一个光标(松手后回 0.04 / 假光标), 不算
    SH_Z_TO_SW = 0.20            # 普通入场: 松手 -> 切维里奈(视频 0.17s, 切走后冰照样炸)
    SH_R_WINDOW = 1.50
    # 【保底】E 连按到上限也没见进 CD: 爆裂/R 打完、切人之前再按一轮(E 在 CD 上按了没反应, 不怕多按)
    SH_E_RETRY_CAP = 0.80
    SH_E_RETRY_GRACE = 0.25      # 这么久没见 E 亮 = 本来就在 CD, 不按了
    SH_R_CAP = 4.00              # R 不亮攒能量的上限
    SH_R_ANIM_MIN = 0.80         # 散华大招藏 HUD 72F, 视频 1.1~1.2s
    SH_R_ANIM_CAP = 4.00
    # 第二次上场(锁暝切过来, 普通入场): 点 A 到协奏满 -> 声骸 -> 变奏锁暝。视频进场 -> 声骸 0.95~1.15s, 在场 1.3~1.5s
    SH_B_A_LEAD = 0.10
    SH_B_A_UNTIL = 0.85          # 从切人键起至少点 A 到这一刻
    SH_A_EVERY = 0.20
    # 【必须变奏锁暝】散华延奏(普攻加深)给锁暝整段输出, 普通入场就丢了。协奏不满接着点 A, 这个上限只防打不到怪
    # (A1~A5 一整套才 26 协奏, 正常靠 E/R 那段的冰棱冰川给满)。没满就切: 锁暝普通入场丢延奏, 散华的协奏留到下一圈,
    # 下一圈散华一进场就满, 切维里奈变成变奏入场(维里奈在空中, E/R 放不了)。10-25 23:31 实机 3s 停在 0.99
    SH_CON_CAP = 4.50
    # 【保底】点 A 到上限还没满: E 好了就补一下 E(+15 协奏), 再等光圈这么久
    SH_B_E_CON_CAP = 1.50

    # ==================== 声骸 ====================
    # 散华带暗鸟(变身): 放完不切人会一直变身, 期间点 A 是鸟在打 -> 只放在交棒前一刻: 按一下 -> 什么都不按 -> 切人。
    # 锁暝换成脱手声骸, 照视频在刻心念时停里放(SO_KX_ECHO_AT); 切散华前 CD 没涨才补一下
    ECHO_ONCE_PRESSES = 3        # 没见 CD 涨最多按几下
    ECHO_ONCE_GAP = 0.15         # 每下之间等 CD 涨的时间
    ECHO_SKIP_CD = 1.0           # 按之前声骸 CD 读数高于这个 = 还在冷却, 不按
    ECHO_TO_SWITCH = 0.35        # 声骸按下 -> 切人(视频散华 0.35s)

    # ==================== 维里奈 ====================
    # E -> 声骸 -> R -> 演完 -> 跳A 到协奏满 -> 变奏锁暝。视频 E/Q/R 进场 0.15~0.45s 内按完;
    # 散华 -> 维里奈 正常是普通入场(散华协奏没满); 散华协奏满着切过来是变奏入场: 维里奈被带到空中, 落地前 E/R 图标是暗的
    # 按不出(10-25 23:32 实机 E 连按 19 下没进 CD, R 读成没亮去攒能量, 空中点 A 一直浮着, 3.7s 后落地才放出 R)。
    # 变奏入场就只连按 E 不点 A, 等她落地(上限 VE_E_CAP_INTRO)
    VE_E_LEAD = 0.10
    VE_E_CAP = 1.60
    VE_E_CAP_INTRO = 4.00
    VE_E_GRACE = 1.00
    VE_ECHO_CAP = 0.60
    VE_R_WINDOW = 1.50
    VE_R_CAP = 4.00
    VE_R_ANIM_MIN = 1.50         # 维里奈大招藏 HUD 136F(2.3s), 视频中途白闪一下 HUD 读成在场
    VE_R_ANIM_CAP = 5.00
    # 跳A: 起跳 -> 空中普攻(有光合能量时是强化空中 A, 一下 ~16 协奏)。视频 R 演完 -> 变奏锁暝 1.1~1.2s
    VE_JUMP_TO_A = 0.12
    VE_JUMP_A_TAPS = 2
    VE_JUMP_EVERY = 0.65         # 一轮跳A 的周期(空中 A1 结束 0.53s)
    # 【保底】跳A 到协奏满(光圈)的上限, 到了照切(没满 = 锁暝普通入场: 没有延奏加深, 启动轴还进不了念深放不了 R)。
    # 要照顾 0 链: 帧表 E 30 + R 20, 每层光合能量强化空中 A +12(16.5/下), 普通空中 A1+A2 一轮 ~8.8;
    # 2 链 E 多 10 协奏 + 1 层光合。用户 2 链实机: 循环 1.5s(x2, 有上一圈剩的光合), 启动轴光合从 0 起要 2.94s(x4, 差点撞旧上限 3s)。
    # 0 链启动轴少 ~22 协奏, 估计 ~5s, 打空还要更久, 所以给 8s
    VE_JUMP_A_CAP = 8.00

    # ==================== 锁暝 ====================
    SO_HOLD_SENDINPUT = True     # 按住普攻走 SendInput(游戏在前台时): PostMessage 的按下沿落在收招里会被丢掉
    # 维里奈变奏进来(启动轴 = 闪裂进念深, 循环 = 碎霆 +200 妄执): 什么都不按, 站这么久切散华(从切人键起)。
    # 视频 1.3~1.5s。不点 A: 碎霆后点 A 接旋雷破, 妄执多攒一截, 开 R 后破隙那一下可能已经满 800 变成无舍念, 封印之主就没了
    SO_C_STAY = 1.35
    # ---- 主段: R -> E 破隙(封印之主) -> A1A2闪避循环/按住普攻(SO_FILL_MODE)到妄执满 -> 无舍念 -> 刻心念 -> 念起 A -> 锁妄念 -> 变奏散华 ----
    SO_SEAL_E_CAP = 1.50         # R 演完按 E 破隙到进 CD 的上限(视频 HUD 回来 -> E 0.4s; 同奏只有 5s)
    SO_SEAL_E_GRACE = 0.60
    SO_SEAL_E_RETRY_CAP = 1.00   # 【保底】破隙按到上限没见进 CD(同奏还在 5s 里): 再按一轮
    SO_FILL_HOLD_LEAD = 0.15     # 破隙出了 -> 按住普攻
    SO_FILL_CAP = 10.00          # 按住普攻到妄执满的上限(视频 7.4s); 撞上限就不出刻心念, 停在念深切走
    # 按住期间挨打: 硬直完按着的左键不会自己接上连段, 人就站着(10-25 22:44 实机 273.2s 被打掉 23% 血后站了 5s 撞 cap)。
    # 读自己血条(白色填充列占比, 2K 满血 0.90 / 1080 0.87, 那一下掉到 0.68; 平时两帧之间抖 ±0.02),
    # 掉了 SO_HIT_DROP 就在挨打后这几个时刻松开重按(硬直多长不知道, 早了被吞, 多按几下盖住)
    SO_HIT_DROP = 0.05
    SO_HIT_REPRESS = (0.35, 0.85, 1.35)
    # 破隙后攒妄执的打法: 'A2DODGE' = 点 A1A2 -> 闪避 -> 点 A1A2 ...(心锁守 QY 循环的锁暝, 闪避把连段重置回 A1,
    # 只打攒妄执最快的两段; 闪避的无敌帧顺手躲掉一些攻击, 完美闪避后那下 A 是闪避反击·解伞形);
    # 'HOLD' = 一直按住(视频打法, 出旋雷破, 伤害高但挨打会断)。默认 HOLD: v0.1.6 默认过 A2DODGE, 10-25 23:54 实机
    # 攒满 6.95~9.25s 跟按住 7.15~9.15s 差不多, 没变快, 改回按住。
    # 估算: 心锁守教学3 两组 A1A2+闪避 3.5s 攒 ~500 妄执(~140/s); 10-25 23:41 实机按住 0->800 要 7.15~9.15s(~110/s,
    # 每圈挨打 1~4 下)。A1/A2 倍率比旋雷破低, 这段每秒伤害会少
    SO_FILL_MODE = 'HOLD'
    SO_A2_TAP_UNTIL = 0.65       # 每组从第一下 A 起点到这一刻(A1/A2 出手; A3 的接段窗口在 +1.0 左右, 那时手已经停了)
    SO_A2_DODGE_AT = 1.25        # 每组从第一下 A 起这一刻闪避(A2 的伤害进完条)
    SO_A2_AFTER_DODGE = 0.25     # 闪避后这一刻开始下一组
    SO_DODGE_HOLD = 0.07         # 闪避 = 右键按住这么久
    # 刻心念: 无舍念出了 SO_KX_A_FROM 后按住普攻, 按满 SO_KX_SPIN 松手。转圈是时停(倒计时不走), 松手才收招 +40 协奏
    # (心锁守 10-25 实机: 按住 4.0s 没自己收, 松手后 0.41s 光圈才出来)。视频无舍念 -> 转圈结束 ~5s
    # 10-25 20:37 实机按 3.8s: 时停在按下后 ~2.9s 才开始, 松手就结束, 只转了 0.95s; 视频时停 2.2s。
    # 10-25 23:41 实机 3 圈(录屏倒计时逐帧): 时停在按住后 2.5~2.7s 开始, 自己持续 2.0~2.2s 结束(按住后 4.5~4.8s);
    # 之后爆一下 + 站着收招, 第一下念起 A 在时停结束后 ~1.1s 才出来(视频也是), 跟松手时刻无关(晚松 0.24s/0.53s 都一样)。
    # 所以 5.0s 晚松不亏, 早松会把时停掐断; 别往下调
    # 长按伤害是固定段数(19.40%*10+24.24%*8), 松早了段数打不完; 时停里倒计时不走, 多按不花游戏时间
    SO_KX_SPIN = 5.00
    SO_KX_ECHO_AT = 3.00         # 按下后这么久放声骸(视频声骸在时停刚开始那一下, 实机时停 ~2.9s 开始; 声骸是脱手的)
    # 按住期间 E 位又连 2 帧是无舍念 = 无舍念没放出来(被打断): 松开, 连点 E 到放出来, 再按住(计时从头算)
    SO_KX_E_MAX = 3
    SO_KX_E_RESCUE_CAP = 6.00
    SO_KX_TAIL = 0.40            # 松手 -> 开始点 A(收招那一刀)
    # 【保底】念起点 A 到锁妄念打出来的总上限(视频 6.4s, 实机 4.1~7.8s)。锁妄念必须打出来: 不打就停在念起切走,
    # 下一圈变奏成闪裂、妄执攒不满, 整圈跟着乱(10-26 00:26 L1 念起中间处决一次 9.94s 撞了旧上限 9s, L2 按住 10s 没满)。
    # 锁妄念点到 SO_KX_WUSE_CAP 还没出(被打断/E 被吞)就回去接着点 A 再来, 都算在这个上限里
    SO_AWAKE_CAP = 20.00
    SO_LOCK_TO_SWITCH = 0.60     # 锁妄念出了 -> 切散华(锁妄念那几段伤害算普攻, 打完再走; 视频 0.9s)
    SO_TO_SH_CON_CAP = 3.00      # 切散华前协奏不满点 A 补的上限(不满散华普通入场, 只少个入场技)
    SO_TO_SH_TIME_OUT = 4.00
    # 处决: 按住普攻攒妄执、念起点 A 这两段看到怪身上的 F 提示(连 2 帧)就按(用户 10-26: 平 A 攒妄执时随时接 F 不打断流程)。
    # 按住那段先松开左键再按 F(按住期间发键会把按住打断), 处决完 HUD 回来重新按住, 处决花的时间不算进 SO_FILL_CAP
    SO_BREAK = True
    BREAK_PROMPT_CONFIRM = 2
    BREAK_ANIM = 1.50            # 迅刀谐度破坏派生 ~90F
    # 丢目标: 按住普攻/念起点 A 时怪被打死、换波(Boss 挑战下一轮), 宏里没人重新锁目标, 按住的左键过场后也不会自己接上,
    # 人就站着撞 10s 上限(10-26 00:43/00:52/00:55 实机三次, 撞完协奏 0.00 = 10s 一下没打中; 00:55 那次是处决把 Boss 打死了)。
    # 隔 SO_ENEMY_EVERY 读一次场上有没有怪(Boss 血条/破盾锁图标/怪物血条/锁定目标; Boss 挑战里锁定图标读不到, 所以并上前几个),
    # 连 TARGET_GONE_CONFIRM 次没有 -> 松手、每 WAVE_LOCK_INTERVAL 按中键等新怪(上限 SO_WAVE_WAIT, 心锁守录屏换波 ~7s),
    # 连 ENEMY_CONFIRM 次读到 -> 重新按住; 等的时间不算进上限
    SO_ENEMY_EVERY = 0.10
    TARGET_GONE_CONFIRM = 3
    ENEMY_CONFIRM = 2
    WAVE_LOCK_INTERVAL = 0.30
    SO_WAVE_WAIT = 9.00
    # 怪还在但打不到(跑远了/没锁上/按住没被认): 协奏 SO_STALL_EVERY 秒一点没涨(< SO_STALL_GAIN, 已满不算) -> 松开、中键锁目标、
    # 重新按住(念起点 A 那段只按中键)。正常打中时封印之主每段 +5 协奏, 2.5s 能涨 0.2 以上
    SO_STALL_EVERY = 2.50
    SO_STALL_GAIN = 0.03

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.holding_attack = False
        self.si_holding = False
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.loop_count = 0
        self.hud_seen_at = 0.0         # 最后一次读到队伍 HUD 在场的时刻(HUD_LOST_ABORT 用)
        self.hud_watch = False         # 宏跑着的时候才查 HUD 丢没丢
        self.drive_log_at = 0.0
        self.timeline_logged = True
        self.so_wuse_out = False       # 上一次 so_wuse 读到图标换掉了没有(锁妄念没出就回去补)
        self.so_waited = 0.0           # 念起点 A 那段等新怪等了多久(so_awake_lock 加回总上限)
        # reset_state() 只在 load_chars() 时跑, 而角色对象会被粘性复用 —— 这里也要启一次
        try:
            start_enable_watch(self.task, self.logger)
        except Exception:
            pass

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(这条轴不在切人时按处决)
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_ssw(self) -> bool:
        """本队 = 锁暝 + 散华 + 维里奈。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Sanhua', 'Verina'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_ssw:
            return 'team is not sanhua/suoming/verina'
        if not self.opener_armed:
            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            if restarted > self.last_opener:
                self.logger.info('auto combat 重开过 — 启动轴重新武装, 从头跑')
                self.opener_armed = True
                return None
            return 'already armed-down (ran once this combat)'
        f_press = getattr(self.task, '_f_last_press', -1)
        if f_press > self.last_opener:
            waited = time.time() - f_press
            delay = f_settings(self.task)[1]
            if waited < delay:
                return f'F pressed {waited:.2f}s ago, waiting {delay:.2f}s for the mobs'
            return None
        if self.last_opener >= 0 and time.time() - self.last_opener < self.OPENER_MIN_GAP:
            return f'ran {time.time() - self.last_opener:.1f}s ago (< {self.OPENER_MIN_GAP:.0f}s)'
        if not self.task.use_liberation:
            return 'task.use_liberation is off'
        return None

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')
        # 【看门狗】每一步都会打标记, 在这里顺手查: HUD 很久没出现 = 关卡结束/过场/窗口没了,
        # 再按下去就是在结算画面上乱按(景月守实机 2026-09-28 19:17 见过)。不读屏, 只比时间戳
        if self.hud_watch and time.time() - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {time.time() - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')

    def macro_spam(self, window, interval=None, tag='A'):
        """连点一个窗口。打出几段由 window 决定(动画期间的点击被丢掉, 连点保证总有一下落在动画刚结束时)。"""
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        cap = int(window / max(interval, 0.01)) + 2
        while time.time() - start < window and n < cap:
            self.click()
            n += 1
            remaining = window - (time.time() - start)
            if remaining <= 0:
                break
            # click 自己按住 A_TAP 秒, 不扣的话连点周期从 interval 变成 interval + A_TAP
            self.macro_wait(min(max(0.0, interval - self.A_TAP), remaining))
        self.macro_mark(f'{tag}(x{n})')
        return n

    def spam_key(self, send, window, interval=None, tag='key', poke=False, presses=None):
        """在一个窗口里反复发同一个技能键(动画期间的按键被丢掉、不排队, 单发必丢)。

        poke: 发键期间要不要照常点普攻(两条节奏各走各的)。presses: 只发这么多次, 均匀铺在窗口里。
        """
        interval = self.KEY_INTERVAL if interval is None else interval
        if presses is not None and presses > 0:
            interval = max(interval, window / presses)
        start = time.time()
        n = 0
        clicks = 0
        next_click = 0.0
        while True:
            send()
            n += 1
            if presses is not None:
                if n >= presses:
                    break
                left = interval
            else:
                left = window - (time.time() - start)
                if left <= 0:
                    break
            step_end = time.time() + min(interval, left)
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - time.time()))
        self.macro_mark(f'{tag}(x{n},A x{clicks})')
        return n

    def release_attack(self):
        # 【SendInput 按下的必须 SendInput 松开】: PostMessage 的 mouse_up 只进游戏窗口的消息队列,
        # 系统里那个左键会一直按着 —— 宏异常退出时整台电脑的左键都是按住的
        if self.si_holding:
            if not send_input_left(True):
                self.logger.warning('SendInput 左键松开没发出去 — 如果左键卡住, 手动点一下')
            self.si_holding = False
            self.holding_attack = False
        elif self.holding_attack:
            self.task.mouse_up()
            self.holding_attack = False

    def click(self, *args, **kwargs):
        """本队里所有的 self.click() 都改成按住 A_TAP(BaseChar.click 强制 10ms, 等于没点)。不在本队走原版。"""
        if not self.is_in_ssw:
            return super().click(*args, **kwargs)
        self.tap()

    def quick_switch(self, target, click_during=False, time_out=None, read_con=False, assume_con=False):
        """按数字键直切给指定角色(不走 switch_next_char 选人)。

        read_con: 按键之后读一次协奏决定按不按"带变奏"记账; assume_con: 不读屏直接按满协奏记。
        Returns: 是否真的切过去了。
        """
        if target is None:
            return False
        if target is self.cur:
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        source = self.cur if self.cur is not None else self
        slot = target.index + 1
        self.switch_press_at = time.time()
        self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
        if assume_con:
            con_full = True
        elif read_con:
            # 不用 char.is_con_full(): 那层读到过一次满就一直缓存成满
            try:
                con_full = float(self.task.get_current_con()) >= 1.0
            except Exception:
                con_full = False
        else:
            con_full = False
        con_cost = time.time() - entry

        start = time.time()
        last = start
        next_click = 0.0
        trace = [] if self.TRACE_SWITCH else None
        team_size = len([c for c in self.task.chars if c is not None])
        untrusted_hits = 0
        clicks = 0
        while time.time() - start < time_out:
            now = time.time()
            if now - last >= self.SWITCH_KEY_INTERVAL:
                self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
                last = now
            if click_during and now >= next_click:
                self.click()
                clicks += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            if in_team:
                self.hud_seen_at = time.time()
            hit = in_team and index == target.index
            trusted = in_team and count >= team_size
            untrusted_hits = untrusted_hits + 1 if (hit and not trusted) else 0
            if trace is not None:
                trace.append(f'{time.time() - start:.2f}:'
                             f'{(index if trusted else f"?{index}") if in_team else "x"}')
            if hit and (trusted or untrusted_hits >= self.UNTRUSTED_CONFIRM):
                self.handoff(source, target, con_full)
                self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}'
                                f'({time.time() - entry:.2f}|con{con_cost:.2f}|A x{clicks})')
                if trace and time.time() - entry > 0.4:
                    self.logger.info(f'slow switch trace -> {target.name}: {" ".join(trace)}')
                return True
        # 一直是旧角色的 index = 游戏在拒绝切人; 带 ? 的都是目标 = count 判据挡住了; 全是 x = HUD 不在
        self.logger.warning(f'switch to {target.name} slot {slot} timed out '
                            f'after {time_out:.1f}s (stuck in an animation?)'
                            + (f' trace: {" ".join(trace)}' if trace else ''))
        self.macro_mark(f'>{target.name[:3]}FAIL')
        return False

    def switch_retry(self, target, tag, **kw):
        """quick_switch, 超时了先等 HUD 回来再切, 最多再试 SWITCH_RETRIES 次。只花时间。"""
        if self.quick_switch(target, **kw):
            return True
        for i in range(self.SWITCH_RETRIES):
            self.release_attack()        # 按住的普攻会让角色一直在动作里, 游戏不放人
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
            self.macro_mark(f'{tag}切{target.name[:3]}重试#{i + 1}')
            kw2 = dict(kw)
            kw2['time_out'] = self.SWITCH_RETRY_TIME_OUT
            if self.quick_switch(target, **kw2):
                return True
        return False

    def wait_hud_back(self, tag='anim', time_out=None, min_gone=0.0, tail=0.0, hold_from=None):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。先确认消失过。

        min_gone: 从第一次读到消失起, 这么久之内读到的"回来"不认(演出中途 HUD 会闪一两帧)。
        回来要连读 HUD_BACK_CONFIRM 帧。
        hold_from: 消失后这么久按住普攻(预输入重击), 返回时还按着, 调用方负责松开。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_at = None
        back_hits = 0
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
            if (hold_from is not None and gone_at is not None and not self.holding_attack
                    and time.time() - gone_at >= hold_from):
                si = self.so_attack_down()
                self.macro_mark(f'{tag}按住预输入({"SI" if si else "PM"},{time.time() - gone_at:.2f}s)')
            if not self.hud_ok():
                if gone_at is None:
                    gone_at = time.time()
                back_hits = 0
            elif gone_at is not None:
                if time.time() - gone_at >= min_gone:
                    back_hits += 1
                    if back_hits >= self.HUD_BACK_CONFIRM:
                        back = True
                        break
            elif time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        if gone_at is not None:
            self.add_freeze_duration(gone_at, time.time() - gone_at)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_at is not None else "NOANIM"},{time.time() - start:.2f}s)')
        self.macro_wait(tail)
        return back

    def on_field_char(self, time_out=None):
        """现在场上是谁 —— 只在拿不到入口角色时才用(开局 HUD 渐入时 in_team() 会说谎, 要 count 达标)。"""
        time_out = self.FIELD_SETTLE_TIME_OUT if time_out is None else time_out
        expected = len([c for c in self.task.chars if c is not None])
        end = time.time() + time_out
        while True:
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            if in_team:
                self.hud_seen_at = time.time()
            if in_team and count >= expected:
                for char in self.task.chars:
                    if char is not None and char.index == index:
                        return char
                return None
            if time.time() >= end:
                self.logger.warning(f'team HUD never settled in {time_out:.1f}s '
                                    f'(in_team={in_team} index={index} count={count} '
                                    f'expected={expected}), cannot tell who is on field')
                return None
            self.macro_wait(0.03)

    def press_key_until_cd(self, send, available, cap, tag, ready_grace=0.15, poke=False):
        """一直按某个技能键到它进 CD。返回放出去了没有(本来就在 CD 上返回 False)。

        【先见过"亮"才认"暗"】: 刚切人那几帧技能栏还是上一个人的, 读到的是上一个人的 CD。
        """
        start = time.time()
        n = live = clicks = 0
        seen_ready = False
        next_click = 0.0
        while time.time() - start < cap:
            self.task.next_frame()
            hud = bool(self.hud_ok())
            if hud:
                if available():
                    seen_ready = True
                elif seen_ready and live > 0:
                    self.macro_mark(f'{tag}_ok(x{n},A x{clicks},{time.time() - start:.2f}s)')
                    return True
                elif not seen_ready and time.time() - start >= ready_grace:
                    self.macro_mark(f'{tag}_没见过亮(x{n},{time.time() - start:.2f}s)')
                    return False
            send()
            self.key_press_at = time.time()
            n += 1
            if hud:
                live += 1
            step_end = time.time() + self.E_RESEND_STEP
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - now))
        self.macro_mark(f'{tag}_CD?(x{n},seen{int(seen_ready)},{cap:.1f}s)')
        self.logger.info(f'{tag}: never read as on-cooldown after {n} presses (seen ready={seen_ready})')
        return False

    def read_con(self, want, settle=None, click=False, tag=None):
        """读协奏: 自己刷帧、给结算留时间, 连着几帧达到 want 就立刻返回。返回期间读到的最大值。"""
        settle = 0.50 if settle is None else settle
        start = time.time()
        best = 0.0
        hits = 0
        next_click = 0.0
        while True:
            now = time.time()
            if click and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= want else 0
            if hits >= self.SK_CON_FULL_CONFIRM or time.time() - start >= settle:
                break
            self.macro_wait(self.SK_CON_POLL)
        if tag:
            self.macro_mark(f'{tag}(con{best:.2f},{time.time() - start:.2f}s)')
        return best

    def wait_con_full(self, who, cap, tag, poke=True, cur=None, strict=False):
        """边打边等协奏真满(连着几帧; 给了 cur 就光圈也算; strict 且光圈认得出过就只认光圈)。返回满没满。"""
        strict = strict and cur is not None and self.glow_trusted()
        start = time.time()
        samples = []
        hits = 0
        next_click = 0.0
        while time.time() - start < cap:
            now = time.time()
            if poke and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            if cur is not None and self.con_glow(cur):
                self.macro_mark(f'{tag}(满,光圈,{time.time() - start:.2f}s)')
                return True
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM and not strict:
                self.macro_mark(f'{tag}(满,{time.time() - start:.2f}s)')
                return True
            self.macro_wait(self.SK_CON_POLL)
        self.macro_mark(f'{tag}(没满,{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}: {who} 协奏 {" ".join(samples)}')
        return False

    def glow_slot(self, frame, slot):
        """队伍栏第 slot 格(0 起)头像外面那圈细白环 -> 落在同一半径上的上半圈角度占比(0~1)。"""
        H, W = frame.shape[:2]
        s = H / 2160
        cx = W - (3840 - 3586.5) * s
        cy = (500 + 264 * slot) * s
        R = int(round(125 * s)) + 3
        xa, ya = int(cx) - R, int(cy) - R
        if xa < 0 or ya < 0 or xa + 2 * R + 1 > W or ya + 2 * R + 1 > H:
            return 0.0
        crop = frame[ya:ya + 2 * R + 1, xa:xa + 2 * R + 1, :3]
        white = np.ascontiguousarray(crop.min(axis=2)).astype(np.float32)
        polar = cv2.warpPolar(white, (R, 360), (cx - xa, cy - ya), R, cv2.WARP_POLAR_LINEAR)
        rows = polar[225:331]
        d = max(1, int(round(4.5 * s)))
        r = np.arange(int(80 * s), int(118 * s))
        ridge = rows[:, r] - np.maximum(rows[:, r - d], rows[:, r + d])
        k = ridge.argmax(axis=1)
        hit = ridge[np.arange(len(rows)), k] > 40
        if not hit.any():
            return 0.0
        hist = np.bincount(r[k][hit], minlength=int(r[-1]) + 2)
        win = max(2, int(round(4 * s)))
        return float(np.convolve(hist, np.ones(win), 'valid').max()) / len(rows)

    def glow_now(self, cur):
        """当前帧 cur 以外两格的光圈够不够"协奏满"。同一帧只算一次, 顺手数连着几帧(换人从头数)。"""
        frame = self.task.frame
        if frame is None:
            return False
        same_cur = getattr(self, 'glow_cur', None) == cur.index
        if same_cur and getattr(self, 'glow_frame', None) is frame:
            return self.glow_last
        others = [self.glow_slot(frame, i) for i in range(3) if i != cur.index]
        lo, hi = min(others), max(others)
        on = (lo >= self.GLOW_BOTH and hi >= self.GLOW_ONE) or hi >= self.GLOW_SOLO
        self.glow_hits = ((getattr(self, 'glow_hits', 0) if same_cur else 0) + 1) if on else 0
        self.glow_frame, self.glow_cur, self.glow_last = frame, cur.index, on
        return on

    def con_glow(self, cur):
        """光圈连着 GLOW_CONFIRM 帧都在 = 协奏真满。读当前帧(调用方负责 next_frame)。
        认出过一次就记下"这台机器光圈认得出"(glow_trusted), 之后 strict 的地方不再信读数。"""
        self.glow_now(cur)
        ok = getattr(self, 'glow_hits', 0) >= self.GLOW_CONFIRM
        if ok:
            self.glow_ok = True
        return ok

    def glow_trusted(self):
        return getattr(self, 'glow_ok', False)

    def con_ready(self, cur, settle, tag=None, strict=False):
        """交棒前读协奏: 光圈连 GLOW_CONFIRM 帧 = 满; 认不出光圈时读数连 SK_CON_FULL_CONFIRM 帧 1.00 也算满
        (strict 且光圈认得出过: 读数不算, 只认光圈)。settle 内都没有 = 不满。读数最大值留在 self.con_last(给日志)。"""
        strict = strict and self.glow_trusted()
        start = time.time()
        best = 0.0
        hits = 0
        why = None
        while True:
            self.task.next_frame()
            self.hud_ok()
            if self.con_glow(cur):
                why = '光圈'
                break
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM and not strict:
                why = f'con{best:.2f}'
                break
            if time.time() - start >= settle:
                break
            self.macro_wait(self.SK_CON_POLL)
        self.con_last = best
        self.con_why = why
        if tag:
            self.macro_mark(f'{tag}({why or f"没满con{best:.2f}"},{time.time() - start:.2f}s)')
        return why is not None

    def so_forte_full(self, so):
        """妄执满: E 位是无舍念(框架的 suoming_forte_e 模板)或锁妄念(自己的字形 SO_WN_GLYPH)。读当前帧。"""
        try:
            if so.forte_available():
                return True
        except Exception:
            pass
        return self.so_wangnian_score() >= self.SO_WN_MIN

    def so_wangnian_score(self):
        """E 位跟锁妄念字形的相似度(16x16 灰度, 只比圆内, 去均值归一化点积)。读当前帧, 读不到 0。"""
        try:
            ref = getattr(self, '_wn_ref', None)
            if ref is None:
                yy, xx = np.mgrid[:96, :96]
                disk = ((((xx - 48) ** 2 + (yy - 44) ** 2) <= 25 ** 2)).astype(np.float32)
                mask = cv2.resize(disk[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA) > 0.99
                raw = np.frombuffer(base64.b64decode(''.join(self.SO_WN_GLYPH)), np.uint8)
                tpl = raw.reshape(16, 16).astype(np.float32)[mask]
                ref = self._wn_ref = (disk, mask, self.unit_vec(tpl))
            disk, mask, tpl = ref
            x, y, w, h = self.SO_E_SLOT
            box = self.task.box_of_screen_scaled(3840, 2160, x, y, x + w, y + h, hcenter=True)
            frame = self.task.frame
            cx, cy = int(box.x + box.width / 2), int(box.y + box.height / 2)
            half = int(box.height * 1.1)
            icon = frame[max(0, cy - half):cy + half, max(0, cx - half):cx + half, :3]
            g = cv2.cvtColor(cv2.resize(icon, (96, 96), interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY).astype(np.float32) * disk
            v = cv2.resize(g[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA)[mask]
            return float(self.unit_vec(v) @ tpl)
        except Exception:
            return 0.0

    @staticmethod
    def unit_vec(a):
        v = a.astype(np.float32).flatten()
        v = v - v.mean()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def so_on_field(self, so):
        """队伍 HUD 在场而且当前是 so(锁暝/谁都行; 刚切人那几帧技能栏还是上一个人的)。读当前帧。"""
        try:
            in_team, index, _count = self.task.in_team()
        except Exception:
            return False
        return bool(in_team) and index == so.index

    def so_r_asap(self, so, tag, cap, allow_e):
        """锁暝进场就放 R, R 没好(CD / 能量不够)就一直点 A, 好了马上出, 不站着等。
        每下 A 之后顺手按一下 R; 读到 R 亮: 入场动作里一直按, 入场之后连按 SO_R_BURST, HUD 没消失就回去点 A。
        allow_e: 没变奏入场(念起, R 锁着)时妄执满了点 E 进念深。放出去 = 按过 R 之后 HUD 连续 LIB_GONE_CONFIRM 帧不在。"""
        sw = self.switch_press_at
        start = time.time()
        taps = bursts = r_n = e_taps = 0
        next_tap = 0.0
        burst_until = backoff_until = 0.0
        last_r = -9.0
        next_e = sw + self.SO_R_E_FROM
        lit_hits = forte_hits = gone = 0
        fired = False
        while time.time() - start < cap:
            self.task.next_frame()
            now = time.time()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM and now - last_r < 0.6:
                    fired = True
                    break
                self.macro_wait(0.01)
                continue
            gone = 0
            on = self.so_on_field(so)
            try:
                lit = on and bool(so.liberation_available())
            except Exception:
                lit = False
            lit_hits = lit_hits + 1 if lit else 0
            forte_hits = forte_hits + 1 if on and self.so_forte_full(so) else 0
            if now < burst_until:
                so.send_liberation_key()
                r_n += 1
                last_r = now
            elif lit_hits >= 2 and now >= backoff_until:
                bursts += 1
                burst_until = max(now + self.SO_R_BURST, sw + self.SO_R_ENTRY)
                # 一直读成亮却按不出 = 能量差一点: 连按越来越稀, 主要靠每下 A 后的那一下 R
                backoff_until = burst_until + self.SO_R_BACKOFF * bursts
                so.send_liberation_key()
                r_n += 1
                last_r = now
            elif allow_e and forte_hits >= 2 and not lit and now >= next_e:
                self.so_tap_e(so.get_resonance_key())
                so.record_resonance_use()
                e_taps += 1
                next_e = time.time() + 1.0
            elif now >= next_tap:
                self.tap()
                so.send_liberation_key()
                r_n += 1
                taps += 1
                last_r = time.time()
                next_tap = last_r + max(0.0, self.SO_A_EVERY - self.A_TAP)
            self.macro_wait(self.LIB_KEY_INTERVAL if time.time() < burst_until else 0.01)
        self.task.skip_combat_check = True
        el = time.time() - start
        e_note = f',E x{e_taps}' if e_taps else ''
        if fired:
            so.record_liberation_use()
            self.macro_mark(f'{tag}R_fired(A x{taps},R x{r_n},连按{bursts}次{e_note},{el:.2f}s)')
        else:
            self.macro_mark(f'{tag}R_NOFIRE(cap,A x{taps},R x{r_n},连按{bursts}次{e_note},{el:.2f}s)')
        return fired

    def so_echo(self, so, tag, cap=None):
        """一直按 Q 到声骸 CD 涨上来(比按之前高 SO_ECHO_RISE, 连 2 帧)。返回放出去没有。so 可以是任何人。"""
        cap = self.SO_ECHO_CAP if cap is None else cap
        cd0 = self.read_cd('echo', so)
        start = time.time()
        n = hits = 0
        fired = False
        while time.time() - start < cap:
            so.send_echo_key()
            n += 1
            self.task.next_frame()
            up = self.so_on_field(so) and self.read_cd('echo', so) > cd0 + self.SO_ECHO_RISE
            hits = hits + 1 if up else 0
            if hits >= 2:
                fired = True
                break
            self.macro_wait(0.06)
        if fired:
            so.record_echo_use()
        else:
            self.logger.info(f'{tag}: 按了 {n} 下声骸 CD 没涨(基线 {cd0:.1f}s) — 被收招吞了, 还是读不到数?')
        self.macro_mark(f'{tag}({"出" if fired else "cap"},x{n},{time.time() - start:.2f}s)')
        return fired

    def echo_once(self, ch, tag):
        """声骸只放一下: 按一下 Q, ECHO_ONCE_GAP 内读到 CD 涨了就停, 没涨再按, 最多 ECHO_ONCE_PRESSES 下。
        暗鸟是变身声骸, 放完不切人会一直变身(这期间点的 A 都是鸟在打, 协奏也不涨), 所以调用方放完就切人。
        按之前 CD 就在走(> ECHO_SKIP_CD)不按。返回最后一下按下的时刻, 没按返回 None。"""
        cd0 = self.read_cd('echo', ch)
        if cd0 > self.ECHO_SKIP_CD:
            self.macro_mark(f'{tag}(冷却{cd0:.1f}s,不按)')
            return None
        start = time.time()
        t_press = start
        n = 0
        fired = False
        while n < self.ECHO_ONCE_PRESSES and not fired:
            ch.send_echo_key()
            t_press = time.time()
            n += 1
            while time.time() - t_press < self.ECHO_ONCE_GAP:
                self.task.next_frame()
                if self.so_on_field(ch) and self.read_cd('echo', ch) > cd0 + self.SO_ECHO_RISE:
                    fired = True
                    break
                self.macro_wait(0.02)
        if fired:
            ch.record_echo_use()
        self.macro_mark(f'{tag}({"出" if fired else "没见CD"},x{n},{time.time() - start:.2f}s)')
        return t_press

    def echo_and_switch(self, cur, nxt, full, tag, label, **kw):
        """交棒前: 声骸只放一下 -> 什么都不按, 按下后 ECHO_TO_SWITCH 切 nxt。
        协奏还没满就在这段等里读光圈(鸟那一下命中也给协奏, 实机 0.39 -> 0.54)。"""
        t_q = self.echo_once(cur, f'{tag}Q')
        if t_q is not None:
            if not full:
                full = self.con_ready(cur, self.ECHO_TO_SWITCH, tag=f'{tag}声骸后协奏', strict=True)
            self.wait_until_time(t_q + self.ECHO_TO_SWITCH)
        return self.switch_retry(nxt, f'{tag}{label}', assume_con=full, **kw)

    def so_tap_e(self, key):
        """点一下 E, 真的按住 SO_KX_E_TAP 秒。"""
        self.task.send_key_down(key)
        try:
            self.macro_wait(self.SO_KX_E_TAP)
        finally:
            self.task.send_key_up(key)

    def so_wuse(self, so, tag, label='无舍念'):
        """点 E 出无舍念: E 位是无舍念就每 SO_KX_E_EVERY 点一下, 读到出了(连 2 帧不是无舍念)就停, 上限 SO_KX_WUSE_CAP。
        返回 (E 键, 停下的时刻)。"""
        key = so.get_resonance_key()
        start = time.time()
        e_taps = gone = 0
        next_e = 0.0
        out = False
        while time.time() - start < self.SO_KX_WUSE_CAP:
            self.task.next_frame()
            if self.so_on_field(so):
                if self.so_forte_full(so):
                    gone = 0
                    if time.time() >= next_e:
                        self.so_tap_e(key)
                        e_taps += 1
                        next_e = time.time() + max(0.0, self.SO_KX_E_EVERY - self.SO_KX_E_TAP)
                else:
                    gone += 1
                    if gone >= 2 and e_taps:
                        out = True
                        break
            self.macro_wait(0.01)
        so.record_resonance_use()
        t_w = time.time()
        self.so_wuse_out = out
        self.macro_mark(f'{tag}{label}({"出" if out else "cap"},E x{e_taps},{t_w - start:.2f}s)')
        return key, t_w

    def so_attack_down(self):
        return self.attack_down(self.SO_HOLD_SENDINPUT and self.game_foreground())

    # ==================== 处决 ====================

    # ==================== 丢目标 ====================

    def target_visible(self):
        """锁定目标在不在。照 CombatCheck.has_target() 的读框, 去掉它最后那个兜底分支 ——
        那个分支会 sleep(1) 再连按两次 ESC, 不能在轴里调(同心锁守)。"""
        t = self.task
        try:
            has_name, no_name = t.get_target_names()
            scale = 1.2 if t.is_browser() else 1.1
            for box in (t.get_box_by_name(has_name).scale(scale), t.get_box_by_name('box_target_enemy_long'),
                        t.get_box_by_name('target_box_long2')):
                best = t.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
                if best:
                    return best.name == has_name
        except Exception as e:
            # 别的 ok-ww 版本这几个读框/方法可能改名了: 当成有怪, 不等新怪
            if not getattr(self, '_target_visible_warned', False):
                self._target_visible_warned = True
                self.logger.warning(f'ssw target_visible unsupported in this ok-ww version ({e!r}), assuming a target is there')
            return True
        return False

    def so_enemy_here(self):
        """场上有没有怪: Boss 破盾/锁图标、怪物血条、锁定目标, 任一个读到就算有(照心锁守 xn_enemy_here)。"""
        t = self.task
        try:
            if t.is_boss() or t.has_health_bar():
                return True
        except Exception as e:
            if not getattr(self, '_health_bar_warned', False):
                self._health_bar_warned = True
                self.logger.warning(f'ssw is_boss/has_health_bar unsupported in this ok-ww version ({e!r})')
        return self.target_visible()

    def so_wait_enemy(self, tag):
        """没怪: 不按普攻, 每 WAVE_LOCK_INTERVAL 按中键, 连 ENEMY_CONFIRM 次读到怪就返回 True, 上限 SO_WAVE_WAIT。"""
        start = time.time()
        next_mid = start + self.WAVE_LOCK_INTERVAL   # 先看 0.3s 再按中键: 怪物血条一闪一闪误判没怪时, 别一上来就把锁定按掉
        hits = 0
        while time.time() - start < self.SO_WAVE_WAIT:
            self.task.next_frame()
            hud = self.hud_ok()
            hits = hits + 1 if self.so_enemy_here() else 0
            if hits >= self.ENEMY_CONFIRM:
                self.macro_mark(f'{tag}没怪等到新怪({time.time() - start:.2f}s)')
                return True
            if hud and time.time() >= next_mid:      # 结算/过场画面上不按
                self.task.middle_click()
                next_mid = time.time() + self.WAVE_LOCK_INTERVAL
            self.macro_wait(0.05)
        self.macro_mark(f'{tag}没怪{self.SO_WAVE_WAIT:.0f}s没等到新怪')
        return False

    def so_enemy_tick(self, state, tag, before=None):
        """攒妄执/念起点 A 每帧调: 隔 SO_ENEMY_EVERY 读一次场上有没有怪, 连 TARGET_GONE_CONFIRM 次没有就 before()(松手),
        按中键等新怪。返回等了多少秒(没等 = 0), 调用方加回上限。state = {} 调用方持有。"""
        now = time.time()
        if now < state.get('next', 0.0):
            return 0.0
        state['next'] = now + self.SO_ENEMY_EVERY
        if self.so_enemy_here():
            state['gone'] = 0
            return 0.0
        state['gone'] = state.get('gone', 0) + 1
        if state['gone'] < self.TARGET_GONE_CONFIRM:
            return 0.0
        state['gone'] = 0
        if before is not None:
            before()
        self.so_wait_enemy(tag)
        return time.time() - now

    def so_con_now(self):
        try:
            return float(self.task.get_current_con())
        except Exception:
            return None

    def so_stall_tick(self, state):
        """隔 SO_STALL_EVERY 读一次协奏, 比上次一点没涨(< SO_STALL_GAIN, 已满不算) = 没打中东西, 返回 True。
        state = {'at': 第一次读的时刻}(调用方持有; 重按后去掉 'ref' 重新起算)。"""
        now = time.time()
        if now < state.get('at', 0.0):
            return False
        state['at'] = now + self.SO_STALL_EVERY
        con = self.so_con_now()
        ref = state.get('ref')
        state['ref'] = con
        return con is not None and ref is not None and con < 0.97 and con - ref < self.SO_STALL_GAIN

    def so_break_tick(self, tag, state, before=None):
        """攒妄执/念起点 A 那段每帧调一次: 怪身上的 F 提示连 BREAK_PROMPT_CONFIRM 帧就按 F 到起手, 等完动画。
        state = {'hits': n}(调用方持有); before = 按 F 之前调一下(松开按住的普攻)。返回这一下处决了没有。"""
        if not self.SO_BREAK:
            return False
        if not self.break_prompt():
            state['hits'] = 0
            return False
        state['hits'] = state.get('hits', 0) + 1
        if state['hits'] < self.BREAK_PROMPT_CONFIRM:
            return False
        state['hits'] = 0
        if before is not None:
            before()
        hit_at, n = self.break_press(tag)
        try:
            self.task.can_break = False
        except Exception:
            pass
        if hit_at is None:
            self.macro_mark(f'{tag}处决?(F x{n},提示一直在)')
            return False
        self.wait_until_time(hit_at + self.BREAK_ANIM)
        self.wait_hud_up(cap=1.0, tag=f'{tag}处决后HUD')
        self.macro_mark(f'{tag}处决(F x{n})')
        return True

    # ==================== 散华 ====================

    def sh_bar_read(self):
        """散华特殊能量条(屏幕底部正中): 返回 (光标位置, 冰痕区左, 右), 都是条宽的占比, 读不到是 None。读当前帧。
        冰痕区 = 刻度那几行里的青色(0/1/2 层透视 = 0.66~0.77 / 0.59~0.84 / 0.45~0.95)。
        光标 = 星形往下伸到条底线下面的那根竖尖: 那几行里只有它比左右两边亮一截(背景/白雾/刻度是平的)。
        v0.1.2 数整条的白像素列, HUD 刚回来那帧暗场里亮刻度被当成光标(10-25 22:44 实机 0.44s 就松手, 没出爆裂)。
        这版在视频和 10-25 实机录屏上 2K/1080 读数一致, 起点 0.04 也认得出来。"""
        frame = self.task.frame
        if frame is None:
            return None, None, None
        H, W = frame.shape[:2]
        s = H / 2160
        x1 = int(round(W / 2 + (1600 - 1920) * s))
        x2 = int(round(W / 2 + (2200 - 1920) * s))
        y1, y2 = int(round(1956 * s)), int(round(2028 * s))
        c = frame[y1:y2, x1:x2, :3].astype(np.int32)
        if c.size == 0:
            return None, None, None
        b, g, r = c[..., 0], c[..., 1], c[..., 2]
        mn, mx = c.min(axis=2), c.max(axis=2)
        h, n = c.shape[:2]
        t1, t2 = int(h * 0.55), int(h * 0.78)
        cyan_col = ((g - r > 45) & (b - r > 45) & (g > 150))[t1:t2].sum(axis=0) >= max(2, int(h * 0.12))
        # 区最宽(2 层)也是从 0.45 开始; 左边冒出来的青色是别的(10-25 23:31 实机读成 0.03~0.95, 光标 0.12 就松了)
        cyan_col[:int(n * 0.40)] = False
        cols = np.where(cyan_col)[0]
        zl = zr = None
        if len(cols) >= max(3, int(n * 0.03)):
            zl, zr = cols.min() / n, cols.max() / n
        s1, s2 = int(h * 0.85), int(h * 0.97)
        d = max(2, int(round(6 * s)))
        m = mn[s1:s2]
        side = np.maximum(np.pad(m, ((0, 0), (d, 0)), mode='edge')[:, :-d],
                          np.pad(m, ((0, 0), (0, d)), mode='edge')[:, d:])
        ridge = ((m - side) > 35) & (m > 160) & ((mx - mn)[s1:s2] < 55)
        score = ridge.sum(axis=0)
        score[:int(n * 0.025)] = 0           # 条两头的端帽也是亮竖条
        score[int(n * 0.975):] = 0
        k = int(score.argmax())
        thr = max(2, int(np.ceil((s2 - s1) * 0.5)))
        cur = None
        if score[k] >= thr:
            peaks = np.where(score >= max(thr, score[k] - 1))[0]
            if peaks.max() - peaks.min() <= n * 0.04:     # 两处都像 = 认不准, 不认
                cur = k / n
        return cur, zl, zr

    def sh_burst(self, sh, tag, r_at=None):
        """重击·爆裂: 按住普攻, 光标走进冰痕区就松手 -> 爆裂(+15 协奏)引爆场上的冰棱/冰川/冰棘(各 +15)。
        松手要两次读数对得上: 这次在区里, 上一次读到的光标在它后面不远(SH_Z_BACK 内), 或者连着两次在区里。
        冰痕区某帧没读到就用上一次读到的。
        r_at: 按着的时候到这一刻放 R(启动轴), R 演出里光标停住, 演完接着按。
        认不出光标就按满 SH_Z_CAP 松手(放了 R 从 HUD 回来算)。返回 R 放出去没有(没要求放 R 返回 None)。"""
        si = self.so_attack_down()
        t_down = time.time()
        fired = None
        why = 'cap'
        cur = prev = zone = None
        in_prev = False
        reads = 0
        try:
            if r_at is not None:
                self.wait_until_time(r_at)
                fired = self.lib_ready_reads(sh, 2) and self.fire_r(sh, f'{tag}R', self.SH_R_WINDOW)
                if fired:
                    self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SH_R_ANIM_MIN, time_out=self.SH_R_ANIM_CAP)
            t_go = time.time() if fired else t_down
            while time.time() - t_go < self.SH_Z_CAP:
                self.task.next_frame()
                if self.hud_ok() and time.time() - t_go >= self.SH_Z_MIN:
                    cur, zl, zr = self.sh_bar_read()
                    if zl is not None:
                        zone = (zl, zr)
                    if cur is not None:
                        reads += 1
                        inside = zone is not None and zone[0] <= cur <= zone[1]
                        if inside and (in_prev or (prev is not None and cur - self.SH_Z_BACK <= prev <= cur + 0.02)):
                            why = '进区'
                            break
                        in_prev = inside
                        prev = cur
                self.macro_wait(0.01)
        finally:
            self.release_attack()
        held = time.time() - t_down
        last = cur if why == '进区' else prev
        where = f',光标{last:.2f}' if last is not None else ',光标没读到'
        where += f'@{zone[0]:.2f}~{zone[1]:.2f}' if zone else ',区没读到'
        self.macro_mark(f'{tag}爆裂({why},{"SI" if si else "PM"},按{held:.2f}s{where},读{reads})')
        return fired

    def seg_sh_a(self, sh, ve, tag):
        """散华·第一次上场。每次都要打出重击·爆裂把冰引爆(冰棱/冰川/冰棘各 +15 协奏), 不然第二次上场协奏差一大截:
        启动轴(普通入场)和循环轴(变奏入场)一样: E -> 按住普攻 -> 按着放 R -> 演完接着按到光标进冰痕区松手
        (冰棱/冰川/冰棘一起引爆) -> 切维里奈, 引爆的伤害在切走后才打出来, 散华第二次上场协奏就满了。
        v0.1.4 循环是 E -> 爆裂 -> R, 冰川没被引爆: 10-25 23:31 实机切走时 0.45、第二次上场 0.57;
        视频循环也是按着放 R(39.8 光标已在区里不松, 演完 41.3 才松), 切走 0.45 -> 第二次上场 0.93"""
        sw = self.switch_press_at
        intro = sh.has_intro
        self.wait_until_time(sw + (self.SH_INTRO_LOCK if intro else self.SH_E_LEAD))
        e_ok = self.press_res_until_cd(sh, self.SH_E_CAP, f'{tag}E', ready_grace=self.SH_E_GRACE)
        t_e = self.key_press_at if e_ok else time.time()
        self.wait_until_time(t_e + (self.SH_E_TO_Z_INTRO if intro else self.SH_E_TO_Z))
        fired = self.sh_burst(sh, tag, r_at=t_e + self.SH_E_TO_R)
        if fired:
            self.macro_wait(self.SH_Z_TO_SW)
        if not e_ok:
            self.press_res_until_cd(sh, self.SH_E_RETRY_CAP, f'{tag}补E', ready_grace=self.SH_E_RETRY_GRACE)
        if not fired:
            fired = self.ensure_r(sh, tag, self.SH_R_WINDOW, self.SH_R_CAP,
                                  lambda rounds: self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL,
                                                                 tag=f'{tag}攒R:A#{rounds}'))
            if fired:
                self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SH_R_ANIM_MIN, time_out=self.SH_R_ANIM_CAP)
            else:
                self.logger.warning(f'{tag}: 散华 R 没放出去(能量不够?), 照样往下走')
        self.need(self.switch_retry(ve, f'{tag}切维', read_con=True), 'sanhua -> verina')

    def seg_sh_b(self, sh, so, tag):
        """散华·第二次上场(锁暝切过来, 普通入场): 点 A -> [协奏不满接着点 A] -> 声骸只放一下 -> 变奏锁暝。"""
        sw = self.switch_press_at
        self.wait_until_time(sw + self.SH_B_A_LEAD)
        taps = 0
        next_tap = 0.0
        while time.time() < sw + self.SH_B_A_UNTIL:
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.SH_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}A(x{taps})')
        full = self.con_ready(sh, 0.10, tag=f'{tag}协奏') or \
            self.wait_con_full('散华', self.SH_CON_CAP, tag=f'{tag}补协奏', cur=sh, strict=True)
        if not full and self.press_res_until_cd(sh, self.SH_E_RETRY_CAP, f'{tag}补E',
                                                 ready_grace=self.SH_E_RETRY_GRACE):
            full = self.wait_con_full('散华', self.SH_B_E_CON_CAP, tag=f'{tag}补E后协奏', cur=sh, strict=True)
        if not full:
            self.logger.warning(f'{tag}: 散华点 A {self.SH_CON_CAP:.1f}s(+补E) 协奏没满(打不到怪?), 放完声骸再看一眼')
        self.need(self.echo_and_switch(sh, so, full, tag, '切锁'), 'sanhua -> suoming')

    # ==================== 维里奈 ====================

    def ve_jump_a(self, ve, tag):
        """跳A 到协奏真满(光圈; 光圈认不出时读数连 3 帧 1.00)。返回满没满。"""
        start = time.time()
        rounds = 0
        full = False
        while True:
            if self.con_ready(ve, 0.05):
                full = True
                break
            if time.time() - start >= self.VE_JUMP_A_CAP:
                break
            t_round = time.time()
            self.task.jump(after_sleep=0.0)
            self.macro_wait(self.VE_JUMP_TO_A)
            for i in range(self.VE_JUMP_A_TAPS):
                if i:
                    self.macro_wait(max(0.0, self.SH_A_EVERY - self.A_TAP))
                self.tap()
            rounds += 1
            self.wait_until_time(t_round + self.VE_JUMP_EVERY)
        self.macro_mark(f'{tag}跳A({"满," + (self.con_why or "") if full else "cap"},x{rounds},{time.time() - start:.2f}s)')
        if not full:
            self.logger.warning(f'{tag}: 维里奈跳A {self.VE_JUMP_A_CAP:.0f}s 协奏没满(打空/R 没放?), 锁暝普通入场')
        return full

    def seg_ve(self, ve, so, tag):
        """维里奈: E -> 声骸 -> R -> 演完 -> 跳A 到协奏满 -> 变奏锁暝。"""
        sw = self.switch_press_at
        self.wait_until_time(sw + self.VE_E_LEAD)
        intro = ve.has_intro
        if intro:
            self.macro_mark(f'{tag}变奏入场(空中, 等落地)')
        self.press_res_until_cd(ve, self.VE_E_CAP_INTRO if intro else self.VE_E_CAP, f'{tag}E',
                                ready_grace=self.VE_E_GRACE)
        self.so_echo(ve, f'{tag}Q', cap=self.VE_ECHO_CAP)
        fired = self.ensure_r(ve, tag, self.VE_R_WINDOW, self.VE_R_CAP,
                              lambda rounds: self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL,
                                                             tag=f'{tag}攒R:A#{rounds}'))
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.VE_R_ANIM_MIN, time_out=self.VE_R_ANIM_CAP)
        else:
            self.logger.warning(f'{tag}: 维里奈 R 没放出去(能量不够?), 照样往下走')
        full = self.ve_jump_a(ve, tag)
        self.need(self.switch_retry(so, f'{tag}切锁', assume_con=full), 'verina -> suoming')

    # ==================== 锁暝 ====================

    def seg_so_c(self, so, sh, tag):
        """锁暝·维里奈变奏进来(启动轴闪裂进念深 / 循环碎霆 +200 妄执): 什么都不按, 站 SO_C_STAY 切散华。"""
        self.wait_until_time(self.switch_press_at + self.SO_C_STAY)
        self.need(self.switch_retry(sh, f'{tag}切散华'), 'suoming -> sanhua')

    def so_hold_until_forte(self, so, tag, cap):
        """按住普攻(SendInput)到妄执满(E 位是无舍念/锁妄念, 连 2 帧)。按住期间只读屏不发键,
        只有血条掉了(挨打, 连段断了)才在 SO_HIT_REPRESS 那几下松开重按; 看到处决提示就松开按 F, 处决完重新按住。返回满没满。
        每帧都读 HUD: 不读的话看门狗以为 HUD 10s 没出现, 松手那一下就把整条轴判断掉(10-25 22:44 实机)。"""
        start = time.time()
        hits = 0
        full = False
        hp_prev = None
        repress = []
        n_hit = n_re = breaks = waits = stalls = 0
        brk = {}
        ene = {}
        stall = {'at': start + 0.50}       # 破隙刚扣完 20 协奏, 0.5s 后读第一次当基准
        deadline = start + cap
        si = self.so_attack_down()
        try:
            while time.time() < deadline:
                self.task.next_frame()
                if self.hud_ok() and self.so_on_field(so):
                    hits = hits + 1 if self.so_forte_full(so) else 0
                    if hits >= 2:
                        full = True
                        break
                    t_b = time.time()
                    if self.so_break_tick(tag, brk, before=self.release_attack):
                        breaks += 1
                        deadline += time.time() - t_b
                        hits = 0
                        hp_prev = None
                        repress = []
                        si = self.so_attack_down()
                        continue
                    if not self.holding_attack:      # 按了 F 没起手(提示一直在): 左键已经松了, 重新按住
                        deadline += time.time() - t_b
                        si = self.so_attack_down()
                    waited = self.so_enemy_tick(ene, tag, before=self.release_attack)
                    if waited:
                        waits += 1
                        deadline += waited
                        hits = 0
                        hp_prev = None
                        repress = []
                        stall = {'at': time.time() + 0.50}
                        si = self.so_attack_down()
                        continue
                    if self.so_stall_tick(stall):
                        stalls += 1
                        self.release_attack()
                        self.task.middle_click()
                        self.macro_wait(self.A_TAP)
                        si = self.so_attack_down()
                        stall.pop('ref', None)
                    hp = self.so_hp_fill()
                    if hp is not None:
                        if hp_prev is not None and hp_prev - hp >= self.SO_HIT_DROP:
                            n_hit += 1
                            now = time.time()
                            repress = [now + d for d in self.SO_HIT_REPRESS]
                        hp_prev = hp
                if repress and time.time() >= repress[0]:
                    repress.pop(0)
                    self.release_attack()
                    self.macro_wait(self.A_TAP)
                    si = self.so_attack_down()
                    n_re += 1
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        hit_s = (f',挨打x{n_hit}重按x{n_re}' if n_hit else '') + (f',处决x{breaks}' if breaks else '') + \
            (f',等怪x{waits}' if waits else '') + (f',打空重按x{stalls}' if stalls else '')
        self.macro_mark(f'{tag}({"满" if full else "cap"},{"SI" if si else "PM"}{hit_s},{time.time() - start:.2f}s)')
        return full

    def dodge(self):
        """闪避(右键, 真的按住 SO_DODGE_HOLD 秒)。"""
        self.task.mouse_down(key='right')
        try:
            self.macro_wait(self.SO_DODGE_HOLD)
        finally:
            self.task.mouse_up(key='right')

    def so_fill_a2dodge(self, so, tag, cap):
        """点 A1A2 -> 闪避 -> 点 A1A2 ... 到妄执满(E 位是无舍念, 连 2 帧)。每帧都读 HUD(看门狗)和妄执, 满了立刻停。
        只点不按住: A2 之后按住是旋雷破。破隙后第一下短按接的是解伞形第 3 段, 照样攒妄执, 不用管。返回满没满。"""
        start = time.time()
        hits = taps = dodges = breaks = 0
        full = False
        group = start                # 这一组第一下 A 的时刻
        next_tap = 0.0
        brk = {}
        deadline = start + cap
        while time.time() < deadline:
            self.task.next_frame()
            if self.hud_ok() and self.so_on_field(so):
                hits = hits + 1 if self.so_forte_full(so) else 0
                if hits >= 2:
                    full = True
                    break
                t_b = time.time()
                if self.so_break_tick(tag, brk):
                    breaks += 1
                    deadline += time.time() - t_b
                    hits = 0
                    group = next_tap = time.time()
                    continue
            t = time.time() - group
            if 0 <= t < self.SO_A2_TAP_UNTIL:
                if time.time() >= next_tap:
                    self.tap()
                    taps += 1
                    next_tap = time.time() + max(0.0, self.SO_A_EVERY - self.A_TAP)
            elif t >= self.SO_A2_DODGE_AT:
                self.dodge()
                dodges += 1
                group = time.time() + self.SO_A2_AFTER_DODGE
                next_tap = group
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}({"满" if full else "cap"},A x{taps},闪x{dodges}'
                        f'{f",处决x{breaks}" if breaks else ""},{time.time() - start:.2f}s)')
        return full

    def so_hp_fill(self):
        """在场角色血条(技能栏下面那条白的)的填充占比: 白色像素占 1/4 以上的列 / 总列数。读当前帧, 读不到 None。
        数字盖在条上(满血那段是黑字), 按列数不按整块面积, 字的笔画不影响。"""
        frame = self.task.frame
        if frame is None:
            return None
        H, W = frame.shape[:2]
        s = H / 2160
        x1 = int(round(W / 2 + (1600 - 1920) * s))
        x2 = int(round(W / 2 + (2200 - 1920) * s))
        y1, y2 = int(round(2058 * s)), int(round(2083 * s))
        c = frame[y1:y2, x1:x2, :3].astype(np.int32)
        if c.size == 0:
            return None
        mn, mx = c.min(axis=2), c.max(axis=2)
        fill = float((((mn > 200) & (mx - mn < 35)).mean(axis=0) >= 0.25).mean())
        return fill if fill > 0.02 else None

    def so_kx_spin(self, so, key, t_w, tag):
        """无舍念出了之后按住普攻出刻心念, 按满 SO_KX_SPIN 松手(转圈是时停, 松手才收招)。
        按住期间 E 位又是无舍念 = 没放出来(被打断 / 前面读错): 松开, 连点 E 到放出来, 再按住(最多 SO_KX_E_MAX 轮)。"""
        self.wait_until_time(t_w + self.SO_KX_A_FROM)
        si = self.so_attack_down()
        first_held = held_at = time.time()
        e_more = e_hits = 0
        echo_done = False
        try:
            while time.time() - held_at < self.SO_KX_SPIN:
                if not echo_done and time.time() - held_at >= self.SO_KX_ECHO_AT:
                    echo_done = True
                    self.echo_once(so, f'{tag}Q')
                self.task.next_frame()
                if not self.hud_ok():
                    self.macro_wait(0.02)
                    continue
                on = self.so_on_field(so)
                e_hits = e_hits + 1 if on and self.so_forte_full(so) else 0
                if (e_hits >= 2 and e_more < self.SO_KX_E_MAX
                        and time.time() - first_held < self.SO_KX_E_RESCUE_CAP):
                    self.release_attack()
                    e_more += 1
                    key, t_w = self.so_wuse(so, f'{tag}补#{e_more}')
                    self.wait_until_time(t_w + self.SO_KX_A_FROM)
                    si = self.so_attack_down()
                    held_at = time.time()
                    e_hits = 0
                    continue
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}刻心念按住({"SI" if si else "PM"},{time.time() - held_at:.2f}s'
                        f'{f",补E x{e_more}" if e_more else ""})')

    def so_awake_taps(self, so, tag, cap):
        """念起点 A 到妄执满(E 位是锁妄念, 连 2 帧)。只点不按住。中间看到处决提示就处决。返回满没满。"""
        start = time.time()
        taps = hits = breaks = waits = stalls = 0
        next_tap = 0.0
        full = False
        brk = {}
        ene = {}
        stall = {'at': start + 0.50}
        deadline = start + cap
        while time.time() < deadline:
            self.task.next_frame()
            if self.hud_ok():
                if self.so_on_field(so):
                    hits = hits + 1 if self.so_forte_full(so) else 0
                    if hits >= 2:
                        full = True
                        break
                if self.so_break_tick(f'{tag}', brk):
                    breaks += 1
                    hits = 0
                    continue
                waited = self.so_enemy_tick(ene, tag)
                if waited:
                    waits += 1
                    deadline += waited
                    self.so_waited += waited
                    hits = 0
                    stall = {'at': time.time() + 0.50}
                    continue
                if self.so_stall_tick(stall):
                    stalls += 1
                    self.task.middle_click()
                    stall.pop('ref', None)
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.SO_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}念起A({"满" if full else "cap"},A x{taps}'
                        f'{f",处决x{breaks}" if breaks else ""}{f",等怪x{waits}" if waits else ""}'
                        f'{f",打空中键x{stalls}" if stalls else ""},{time.time() - start:.2f}s)')
        return full

    def so_awake_lock(self, so, tag):
        """念起点 A 到妄执满 -> E 锁妄念。锁妄念点到上限还没出(还是锁妄念图标)就回去接着点 A、满了再点 E,
        总上限 SO_AWAKE_CAP。出了等 SO_LOCK_TO_SWITCH(那几段伤害打完)再返回 True。"""
        deadline = time.time() + self.SO_AWAKE_CAP
        self.so_waited = 0.0
        tries = 0
        while time.time() < deadline + self.so_waited:
            sub = f'{tag}补#{tries}' if tries else tag
            if not self.so_awake_taps(so, sub, deadline + self.so_waited - time.time()):
                return False
            _key, t_l = self.so_wuse(so, sub, label='锁妄念')
            if self.so_wuse_out:
                self.wait_until_time(t_l + self.SO_LOCK_TO_SWITCH)
                return True
            tries += 1
        return False

    def seg_so_main(self, so, sh, tag):
        """锁暝·散华变奏进来(碎霆): R -> E 破隙(封印之主) -> A1A2闪避循环/按住普攻到妄执满 -> 无舍念 -> 按住刻心念(时停里放声骸)
        -> 念起点 A 到妄执满 -> 锁妄念(回念深) -> 协奏满 -> [声骸没放出去才补] -> 变奏散华。"""
        self.wait_until_time(self.switch_press_at + self.SO_R_LEAD)
        fired = self.so_r_asap(so, f'{tag}', self.SO_R_CAP, allow_e=not so.has_intro)
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SO_R_ANIM_MIN, time_out=self.SO_R_ANIM_CAP)
        else:
            self.logger.warning(f'{tag}: 锁暝 R 没放出去(冷却/能量/不在念深) — 没有同奏, 破隙换不到封印之主')
        # 妄执已经满了(碎霆/R 各 +200, 等 R 时点的 A 也攒): E 位是无舍念, 按下去不是破隙, 封印之主就没了 -> 直接走无舍念
        self.task.next_frame()
        pre_full = self.so_on_field(so) and self.so_forte_full(so)
        if fired and not pre_full:
            if not self.press_res_until_cd(so, self.SO_SEAL_E_CAP, f'{tag}破隙', ready_grace=self.SO_SEAL_E_GRACE):
                self.press_res_until_cd(so, self.SO_SEAL_E_RETRY_CAP, f'{tag}补破隙', ready_grace=self.SO_SEAL_E_GRACE)
            self.macro_wait(self.SO_FILL_HOLD_LEAD)
        elif pre_full:
            self.macro_mark(f'{tag}妄执已满(跳过破隙)')
        if pre_full:
            full = True
        elif self.SO_FILL_MODE == 'A2DODGE':
            full = self.so_fill_a2dodge(so, f'{tag}A2闪A2到妄执满', self.SO_FILL_CAP)
        else:
            full = self.so_hold_until_forte(so, f'{tag}按住A到妄执满', self.SO_FILL_CAP)
        locked = False
        if full:
            key, t_w = self.so_wuse(so, tag)
            self.so_kx_spin(so, key, t_w, tag)
            self.macro_wait(self.SO_KX_TAIL)
            locked = self.so_awake_lock(so, tag)
            if not locked:
                self.logger.warning(f'{tag}: 念起 {self.SO_AWAKE_CAP:.0f}s 锁妄念没打出来, 停在念起切走(下次变奏是闪裂进念深)')
        else:
            self.logger.warning(f'{tag}: 攒妄执({self.SO_FILL_MODE}) {self.SO_FILL_CAP:.0f}s 没满, 没有无舍念/刻心念, 停在念深切走')
        con = self.con_ready(so, 0.15, tag=f'{tag}协奏') or \
            self.wait_con_full('锁暝', self.SO_TO_SH_CON_CAP, tag=f'{tag}补协奏', cur=so, strict=True)
        self.need(self.echo_and_switch(so, sh, con, tag, '切散华', time_out=self.SO_TO_SH_TIME_OUT),
                  'suoming -> sanhua')
        return locked

    # ==================== 一圈 ====================

    def run_cycle(self, sh, ve, opener, tag):
        """散华 E 重击爆裂 R -> 维里奈 E Q R 跳A -> 锁暝(变奏, 站一下) -> 散华 A 声骸 -> 锁暝(变奏) 主段 -> 变奏散华。
        返回时散华刚变奏入场(循环轴从她的 E 接上)。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        self.seg_sh_a(sh, ve, tag=f'{tag}散a')
        self.seg_ve(ve, self, tag=f'{tag}维')
        self.seg_so_c(self, sh, tag=f'{tag}锁c')
        self.seg_sh_b(sh, self, tag=f'{tag}散b')
        self.seg_so_main(self, sh, tag=f'{tag}锁')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        sh = self.teammate('Sanhua')
        ve = self.teammate('Verina')
        if sh is None or ve is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in (self, sh, ve):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return sh, ve, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'ssw timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 切散华(普通入场) -> 一圈。返回时散华刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        sh, ve, previous = got
        self.logger.info(f'ssw opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            if self.cur is not sh:
                self.need(self.switch_retry(sh, 'op'), 'opener: switch to sanhua')
            # 开局散华是普通入场(或本来就在场), 别吃上一局留下的 has_intro 去等变奏锁
            sh.has_intro = False
            self.run_cycle(sh, ve, opener=True, tag='op')
            self.logger.info(
                f'ssw opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'ssw opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是散华(宏散过)就先把场子交给她(普通入场, 不等变奏锁)。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        sh, ve, previous = got
        try:
            if self.cur is not sh:
                self.need(self.switch_retry(sh, 'L接散华'), 'loop: hand the field to sanhua')
                sh.has_intro = False
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(sh, ve, opener=False, tag=f'L{n}')
            self.logger.info(
                f'ssw loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'ssw loop broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def drive(self, entry):
        """三个人的 do_perform 都走这里。entry = 框架这次调的是谁(= 场上是谁)。"""
        # 【HUD 不在就什么都不做】: 关卡结束/结算/过场时框架还会接着调进来(F 开始战斗那边撑着不脱战)
        if not self.wait_hud_up(cap=self.DRIVE_HUD_CAP, tag='drive'):
            if time.time() - self.drive_log_at > 10:
                self.drive_log_at = time.time()
                self.logger.info('ssw: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'ssw: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_ssw:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让散华先上。"""
        if self.is_in_ssw and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
