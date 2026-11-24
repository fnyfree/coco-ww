import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.Labels import Labels
from src.char.Hsin import Hsin as NativeHsin
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


class Hsin(MacroKit, NativeHsin):
    """心。心卜穗(心/卜灵/穗穗)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    长风ei 的「心卜穗 25s 轮椅轴」教学视频(E:/轴视频/【电磁】心卜穗-25s轮椅轴.mp4, 1080p, 1.0x + 字幕定格),
    键盘浮层 + 步骤条 + 游戏倒计时逐帧扒的。作者编队 1 心 / 2 卜灵 / 3 穗穗; 代码【按角色切人】, 编队随意。
    【心要用共鸣模态·电磁】: 卜灵大招给怪挂电磁效应, 心照世相的 E 引爆; 同奏模态下变奏·照世相直接进统御众机, 这条轴不是那么走的。

    ================= 轴的形状 =================
    启动轴: 穗穗 3A(水云息满) -> 醒春潮(强化E, 进濯雨时 15s) -> [马上]切卜灵
    循环轴: 穗穗(心变奏进来, 变奏直接给濯雨时) E -> 切卜灵
    之后两条一样:
      卜灵 2A(艮) -> 跳A(下落, 震) -> E(震) -> A4(震) -> 两段强化重击(少阳+少阴 = 阴阳相生) -> R(开阵 + 电磁效应) -> 切穗穗
      穗穗 A 到芳菲信 600(三段回路满) -> Q -> R -> 演完 -> 变奏心
      心   (变奏·应世相, +60 应世心) Q -> A 到应世心满 -> 按住出步红尘 -> 转相 -> 演完 -> 切卜灵(普通切人)
      卜灵 2A -> Q -> 协奏满 -> 变奏心
      心   (变奏·照世相) A(普攻第1段生成活字心机) -> E(引爆电磁效应, 照世心 +150 ...) -> 照世心满 300 E 换成万阙连衡
           -> 连按 E 进统御众机(13s) -> A -> 闪避 -> 2A -> 按住到临寰宇(照世心打空) -> 万阙垂天 -> 协奏满 -> 变奏穗穗
    宏在【穗穗变奏入场、HUD 在场】时返回, 下一次框架调的就是她(循环轴从她的 E 开始)。

    ================= 判据 =================
    - 心: 应世心满 = hsin_h1(花图标); 转相解锁 = 步红尘清空 + 2.06s; 万阙垂天解锁 = hsin_lib2 —— 这三条都照心汐守定稿。
    - 心: 【进统御众机 = 重击位变成雕像图标】(xn_tongyu)。16x16 灰度模板只比圆内像素, 取自 10-05 两份 2K 录屏:
      统御众机里 0.71~1.00(p5 0.93), 其余所有帧(应世相花/普通重击位/今汐/守岸人/演出)最高 0.38, 缩到 1080 读数一样;
      教学视频(1080p 非全屏)里两次强化E 都在出手当帧认出来(0.83~0.86)。
    - 心: 【统御众机里回路条 8 颗雕像 = 照世心, 被吃掉的从右往左翻面】(xn_statue_count): 翻面那一刻雕像上半身先变亮白,
      0.3~0.4s 后中间才出红菱形; 两样任一连读 2 帧就记住这颗翻了。翻满 4 颗就闪避/处决(心雷穗 10-06 标定, 同一套 HUD)。
    - 卜灵: 重击图标外圈 黄=少阳 / 蓝=少阴(bl_ring), 两段重击一段一段按, 照露丽卜定稿; 流程一不对就直接开 R(不补卦象)。
    - 穗穗: suisui_e1 = 醒春潮可放, suisui_forte3 = 芳菲信满 600, 照秧千穗定稿。

    ================= 改这个文件之前先读 =================
    - 引擎(macro_* / quick_switch / fire_r / wait_hud_back ...)和心第一段的判据逐字抄自 Hsin__Jinhsi__ShoreKeeper/Hsin.py,
      卜灵那几个 bl_* 抄自 Douling__Lucy__Rebecca/Lucy.py, 穗穗的 sui_e1/forte3 抄自 Chisa__Suisui__YangYangSp/YangYangSp.py。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 普攻一律走 tap()(按住 A_TAP 秒): task.click() 只按 10ms, 打不出来。
    - 长按期间不调 click()/mouse_down()/send_key() —— 它们先投 WM_ACTIVATE, 会把按住打断。
    """

    # ==================== 全局节奏(引擎用) ====================
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
    HUD_BACK_CONFIRM = 2         # 演出中途 HUD 会闪一两帧, 回来要连读这么多帧

    # ---- 杂项 ----
    CON_SAMPLE_MAX = 24
    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上
    M_E_KEY_DOWN = 0.05
    # 协奏: 0.99 是环没闭合的哨兵值, 要真满; 连着几帧才算
    CON_ENOUGH = 1.0
    CON_FULL_CONFIRM = 3
    # 【面积已经满了但环没认成闭合】: 框架只在协奏环轮廓闭合时报 1.0, 面积 >= 满环面积但轮廓没闭合(特效/背景压在环上)
    # 报 0.99 哨兵, 等 1.0 就一直补 A。10-09 日志 141 次: 卜灵第二格 84 次面积比 1.00~1.11(录屏里环是满的, 白点 A 2~14 下),
    # 卜灵第一格 4 次 1.28~2.01(大招/重击的紫色特效混进来, 协奏其实没满)。
    # -> 哨兵 + 面积比在 CON_SOFT_RATIO 之间连 CON_SOFT_FRAMES 帧就当满(调用方还要再连读 CON_FULL_CONFIRM 帧)
    CON_SOFT_RATIO = (1.01, 1.15)
    CON_SOFT_FRAMES = 3
    CON_POLL = 0.03

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 保底 ====================
    # 只花时间的保底默认开(切人重试 / 等 HUD / 判据没成立就再按【那一个本该出去的键】);
    # 会改状态的只在判据明确说"这一下没出去"时才补
    SWITCH_RETRIES = 2           # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)
    SWITCH_RETRY_TIME_OUT = 3.00
    HUD_LOST_ABORT = 9.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃(最长的演出是万阙垂天)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    R_TRIES = 3                  # R 亮着但没放出去(被吞/被打断)就再按, 最多这么多次
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长

    # ==================== 穗穗 ====================
    # ---- 启动轴: 3A 攒满水云息 -> 醒春潮(强化E) -> 马上切卜灵 ----
    # 醒春潮第1F 就清水云息、给 15s 濯雨时, 后面的伤害可脱手; 视频里按完 E 0.1s 就切走了
    SUI_E1_THRESHOLD = 0.80      # suisui_e1 = E 图标的醒春潮图案, 亮着 = 水云息满
    SUI_E1_FILL = 2.50           # 没亮就点普攻攒, 上限(循环轴兜底那种, 穗穗可能还在濯雨时); 3A 正好 123 水云息(上限 120), 视频 3A 用了 1.9s
    # 启动轴醒春潮必须开出来: 上限放到这么久。10-06 实机 10 局 4 局 2.5s 没亮(开局普攻打不中怪), 按了普通 E 切走, 穗穗第二格整段白打
    SU_E1_MUST_CAP = 12.0
    SUI_E1_FILL_INTERVAL = 0.12
    SUI_E1_CAST_CAP = 1.50       # 按到图标消失的上限
    SUI_E1_KEY_INTERVAL = 0.05
    # ---- 循环轴第一格: 心变奏进来(变奏直接给濯雨时) -> E -> 切卜灵 ----
    # 视频(游戏倒计时): E 出手到切人 0.42s; 变奏入场第60F前不能切人
    SU_L_E_CAP = 2.00            # E 按到进 CD 的上限(变奏入场里按的会被丢, 一直按到出去)
    # 按完 E 多站一会儿: 濯雨时 E 第36F 还在场才给 100 芳菲信(帧表), E 判到进 CD 比真出手晚 ~0.1s
    SU_L_E_TO_SWITCH = 0.55
    SU_L_MIN_FIELD = 1.00
    SU_L_INTRO_FRESH = 4.00      # 到场超过这么久才轮到这一格 = 中间断过(濯雨时还在不在说不准), 走启动格
    # ---- 第二格: A 到芳菲信满 -> Q -> R -> 演完 -> 变奏心 ----
    # 强化A1~A4 一轮只给 570 芳菲信, 不到 600 —— 所以不按段数按判据: 延奏清掉 >599 才有三次翾舞 + 烟岚前置
    FORTE3_GATE = True
    FORTE3_WAIT = 6.0            # 等的上限(秒), 超了照放不误
    FORTE3_THRESHOLD = 0.75
    FORTE3_CONFIRM = 2
    FORTE3_SUSPECT = 0.30
    # 【濯雨时只有 ~15s】(醒春潮 / 变奏入场给的), 过了芳菲信就清掉、普攻再也攒不满。循环轴是变奏那一刻起算,
    # 穗穗第二次上场时常常只剩 4s 左右: 10-06 实机循环轴 5 圈 4 圈撞满 6s 上限(启动轴醒春潮晚 2s, 大多 3.95~5.43s 就满)。
    # 唯一一次循环轴满的是变奏后 15.4s。所以等满的上限 = 濯雨时还剩多久(SU_QIANG_DUR), 过期了就直接 Q R
    SU_QIANG_DUR = 16.0
    SU2_A_MIN = 3.60             # 至少打完 4A(强化A1~A4 ≈ 3.4s)
    # 【三段回路不满不 QR】(用户 10-06): 14:17~14:18 三局启动轴撞 6s 上限没满就开了大, 另两局 5.95/5.99s 才满(贴着上限)。
    # 上限放到 SU2_MUST_CAP; 只有濯雨时估计已经过了 SU_QIANG_GRACE 还没满才放弃(过了普攻攒不出芳菲信)
    SU2_MUST_CAP = 10.0
    SU_QIANG_GRACE = 2.50
    # 打完 4A(~3.4s)还没满、E 好了: 补一个 E。濯雨时 E 第36F 还在场就给 100 芳菲信(帧表), 正好补上 4A 只有 570 的那一截。
    # 不在 4A 中间插 E(10-09 用户: 试过 1.2s 起 E 好了就按, 去掉)
    SU2_E_RESCUE_AT = 4.20
    # 【大招前协奏要够】(用户 10-06, 心雷穗那边发现的): 穗穗大招只回 20 协奏(帧表「大招-前置」协奏回收 20, nanoka 同;
    # 声骸封庭械囿 0), 大招前不到 80, 放完协奏不满, 切心就不是变奏(心少 60 应世心, 穗穗不跳延奏、翾舞全丢)。
    # 不够就点普攻补(强化普攻一段 4~8 协奏), 上限 SU_R_CON_CAP
    SU_R_CON_MIN = 0.82          # 100 - 20 = 80, 留 2 点读数误差
    SU_R_CON_CAP = 4.00
    # 【补协奏不再因为濯雨时快结束就停】(10-08 实机 20:08 / 20:09 / 21:31 三圈: 穗穗变奏打空(R2 把这一波打死了),
    # 大招前协奏 0.60/0.61, 濯雨时估计已过、一下没补就开大, 放完 0.86~0.91, 心没有变奏)。
    # 协奏不到线时不补 = 这一圈本来就没有延奏(翾舞也没了), 补上至少心第一段还是变奏·应世相; 所以到线之前一直补, 只受 SU_R_CON_CAP 限制
    SU_Q_WINDOW = 0.15           # Q 连发两下的窗口(视频 Q 后 0.07s 就按 R)
    SU_R_WINDOW = 1.50
    SU_R_CAP = 5.00              # R 不亮就攒能量的上限
    SU_R_ANIM_MIN = 3.60         # 穗穗大招藏 HUD 258F = 4.3s
    SU_R_ANIM_CAP = 7.00
    # 变奏心之前穗穗协奏要真满: 心没吃到变奏就没有 +60 应世心, 穗穗也不会跳延奏(三次翾舞全丢)
    SU_CON_CAP = 3.00

    # ==================== 卜灵 ====================
    # 第一格(两条轴都是普通切人进来): 2A(A2 命中给艮) -> 跳A(下落落地命中给震) -> E(第1F 给震) -> A4(由E派生, 命中给震)
    # -> 两段强化重击(一段少阳一段少阴 = 阴阳相生) -> R(阴阳相生下才开阵 24s; 大招命中给怪 6 层电磁效应)。
    # 视频(倒计时): 到场 -> 跳 0.59s -> E 1.64s -> 按住 2.63s(一口气 2.07s 两段) -> R 4.74s
    BL1_2A_WINDOW = 0.55         # 到场就连点, A1 派生 25F 后接 A2, A2 命中脱手后 6F 给艮
    BL1_A_EVERY = 0.15
    BL_JUMP_TO_A = 0.05
    BL_A_PRESSES = 2             # 空中 A 补按一下: 单发可能落在起跳那几帧里被吞
    BL_A_PRESS_GAP = 0.08
    # 起跳到按 E: 空中攻击-下落 38F + 落地派生 8F ≈ 0.77s(从按 A 算), 视频起跳 -> E 1.05s。
    # 落地之前按的 E 被丢(卜灵 E 只能地面放), press_res_until_cd 会一直按到出去
    BL1_JUMP_TO_E = 0.90
    BL_E_CAP = 1.50              # E 按到进 CD 的上限
    BL_E_TO_A = 0.35             # E-前置第38F 才让普攻打断
    # A4: 隔 BL_A4_CLICK_GAP 点一下(最多 BL_A4_MAX_CLICKS 下), 外圈读到蓝那一帧起不再点(多点的会排出 A1->A2, A2 的艮把蓝顶成黄)。
    # 这条轴跳A 的震在 E 之前, E 一出手外圈就已经是蓝的 —— BL_A4_MIN_BLUE 之前读到的蓝不算, 保证 A4 真的点出去
    BL_A4_CLICK_GAP = 0.20
    BL_A4_MAX_CLICKS = 4
    BL_BLUE_CAP = 1.30
    BL_A4_MIN_BLUE = 0.70
    # 重击图标外圈(露丽卜 08:26 实机连拍 2560x1440 标定): 黄 = 少阳(艮震/震艮), 蓝 = 少阴(艮艮/震震)
    BL_RING_CX = 0.7453          # 占画面宽
    BL_RING_CY = 0.8944          # 占画面高
    BL_RING_R0 = 0.0306          # 占画面高
    BL_RING_R1 = 0.0389
    BL_RING_BLUE = 0.20
    BL_RING_YELLOW = 0.15
    BL_RING_CONFIRM = 2
    # 两段重击一段一段按(露丽卜定稿): 按住到这一段结算(协奏连 2 帧涨 BL_HEAVY_CON_JUMP)就松, 读圈确认出去了, 还差的颜色再按;
    # 圈不亮/颜色不对/一段没按出去 = 流程被打断 -> 不补了, 直接攒 R 开 R(见 bl1_heavies)。按住上限都压在 60F + 鬼门占卦 30F 之前
    BL_HEAVY_FIRST_CAP = 1.40
    BL_HEAVY_CAP = 0.95
    BL_HEAVY_NEXT_GAP = 0.30
    BL_HEAVY_VERIFY_CAP = 0.35
    BL_HEAVY_CON_JUMP = 0.105    # 一段重击协奏 +15; A4 打中晚到的 +7.5 会骗过 0.08
    BL_HEAVY_MIN_SETTLE = 0.50   # 按下后这么久内的涨并进基准(第40F 之前不可能结算)
    # ---- 卦象栏(10-08 DVR 23.11.17.09 标定): 角色栏正上方一排字, 艮 = 橙字/金光圈, 震 = 蓝底/蓝光圈, 最多 4 个, 再来一个挤掉最左的。
    # 个数一变整排重新居中, 被重击吃掉两个后剩下的先靠左停 ~0.45s 再居中 -> 7 个候选中心(相邻差半格, 不会同时有字)。
    # 笔画不管有没有光圈都是白的: 取 RGB 最小通道、减掉模糊底色(去光圈渐变)后缩 16x16 比相关, 每个位置在 ±2 像素里找最高分
    # (换分辨率取整会差一两个像素)。2K 原图和缩到 1080/900/720: 真字 >= 0.59(2K >= 0.77), 整段录屏的非字 <= 0.52,
    # 艮/震 415 个样本 0 认错; 跟 2K 读数不一致的只有新字浮现的第一帧
    BL_GUA_DX = (-212.6, -148.1, -83.6, -19.1, 45.4, 109.9, 174.4)   # 4K, 相对画面中线
    BL_GUA_Y = 1822.5
    BL_GUA_HALF_W = 31.5
    BL_GUA_HALF_H = 34.5
    BL_GUA_SHIFT = 2
    BL_GUA_MIN = 0.55
    BL_GUA_CONFIRM = 2
    BL_GUA_REFS = (              # 16x16 uint8, 128 = 0
        ('艮', 'gXVyb2xsZGVmaGtrcnp9foF4cHB/2NbU0dTbkWJ1fH6Cf3RprIg4OzxAV+5QcHt+foB5XLPSs6+ztLT7RW57gH2BeliPjzEyMjRV5EZven98gXFZZvqvoqWs3LhPcXp+f4NzY0yHmDk/eNVZW3R7fX+BdGNCcNLK071pUGp2eHyCgG9jh+3FiSs5TWjFdXd9gn9pbfRKMq50MUexz2x2fYB4Xa+vTT6FsWau/21veH59dVfdcVtRhPKhf09gb3p/gHJa+1dgW0jwMkxpcHN7f391ZeRjbGpS0a9mW3h0eoB/fHh4e3t8dnKk4ej0gnqBf3x7e35/f39yaWdeY3J6fg=='),
        ('艮', 'jHpyeH5yU1hkWVpegXmAiHh3lGpJvbSvrazNnWp5gYl5nXZZfc8uPkFBTM1zZ3uGmohqV5L1qsDBvKvtcGFpd5t1bFl92F5VW15u4GJmZ2x3cG9lTu6vmJedv9xgaHFtaGtxdEZ8ujw6UdeEW217d4Bxc3NTatbY0N98Wmt1enqBdmxgeObQqz5OT27MbHJ3dnBfYe+HRJ+1R02I/1xmanVxXHTeV0xc31yf65VgZmmAcVWXtGJiT/PVjHdvbHJ2i35cyZBtbWDrSklgaW5wl3qAcreGamZXr7pRVm5md4l8e3hxZGBiXV6Fwt/MhId2iIN9eXpzd4SUZ2hibnJ1ew=='),
        ('艮', 'k4KAf4NoWE5vW2RhjoVyeHp7gHJjvrGzurrPjUuPnnN2h31ofdJHPD8/TtVwXoSmhIN5WYPkq66xr7bcf19oh3p6dmJ31ltUU1ZtyXVkcGZ4fmtnTuqfjpSIvcBhZ3R0d3loZ1CWpDktUr6BaXJ1eJCAamVPW9DRwNOHYm92eYqJg21cdMDOrjg/U2zDdm5zdndpV+KIR6+ZO0t+7HNmZnFpYHzEQUtW1mie3odlbXOPcFywrWdkUPXPiWZaanV7m4Jb0olvZ07kUUphZXBxfXWmbs+Db2parbRxcW9tbnp7b4d0Y3VwZGKQ1v/thm19iXxwhJaEdHN8WVdmW2GLpw=='),
        ('震', 'hIOEe29jYlZaZ3Fra3iGh4eHgXRpasORcJzSb3J3gouDhXxrXU9R0//HY1FkbnWAgHxobai3qGPDYKi1m29mdYF2b8d4mlojuCdynnzRbnWDeZWToneSacVxhnmhpI5uf3qWj0ubrWG+cKiFQ5OMc317ZUxEaIaGmYeLlpxOYnh+e2ZavYV5aD9fhY6LXGx6f3ljlYyqklprpnWAc15wfIN5XK1c34uQ052GjYpkdYB+eF2xUopmYF2Gp5NvYX2FdW1Wwk3PSDp6nHKjl2KAgXN3pcRNmsSW1ZxxasRneYBxu69sZ2Fog4iKgnODbnmCfHNzdnl2dXNra3d7fnyBhQ=='),
        ('震', 'eG9sjcONXUKCSzxfg4J3eHFrlp9YWMNybJPEU3F7lXZzlm89RUNT4//hRTBPYICRpm9GVKjIvoXYmL7LsmBXcYNNZPuhplMpuDJ9tIXgZmlnT5urrZqRc8Vxgm+zzHxfZlqamh6OplW9XJl4Nrt5aoFsWUIocpWpuqOxwK9cXXl9aF9J1I9+V0NyiI55XG6BYVpPha3HomaEpmp7a2VwcmleQrhh/6OrzqiXo5hldXOJXz7ATrQ0JjSZ1rJ2ZHVkkGxG3VblUSh3pl2kvFdhaWyMsdVFieWf5LJ0UcZSdolvwLRlWlhef4FvXmZhdpJ1dmlkdX5vYVaSXHWbuohqdg=='),
        ('震', 'i4+RlI6Ge2hscHyQl5aPk42SjH1sUr98YnmpZnmMi4mKh3hsSjVCyvzAPUFbboKSiIFmXai3qmfZaK66lmFngH5pZ9hlokoByBhhpHjZWmiCbJOWtXqiWd50e2ecno1ie2eThDOKr1XeW558NJOTaId5YEU7YIaIpYSNm5tVZXeJemhM2IxqVDFgjp2SSmJ3hXRfhZSulVtltHd8cF55eYt5WLVa7JCV6Z96g5RfcHiIdU6+SZBPQ0SDtJ1nUmt8gmRCyVDVMC17rGu3jFl9hI5rmsdJnNef5pdzXrZmhId6xp1pdHdpc3R5dGKDgIeGeXqDhH5+fWt0goGKhICHiQ=='),
        ('震', 'gn58eXVtdmFbZ3hud319foB9fHhwZduZZpXJa2h4e319enFqXUhRx//BXEpTbHd8e3FccKe+oWHLbbO5nXBncX9ucdyMlk8UwCtooH3fd26AbZaZr36SYtN1f3OcpZtxgG2Wk0qUo1LGYq+NPpyTd4BxXEdAbXiCpoSPlZ9VZHx/cl9U04uKYUVgh5WNWW58fXBeoaWiilNcqHiHfmFueXttWr9m4oyJ1JqLlpZkcn14a1bHTJdiYV2LsJhvZXh/c2RM2VPcQCp2q2ixqWl2dnF2sdhEm9OW6K9rac1nb3dy1r5mXlhykoyLe3CKb3d8d29pZG1tamhmandwcHd8fw=='),
    )
    # 第一格一律先打 2A(视频循环轴带着艮进场也打, 10-09 对过录屏: 艮 -> 2A 艮艮 -> 跳A/E 震震 -> A4 = 艮震震震)。
    # 10-08 试过"带着艮进场就不打 2A", 卦象一样但少 A1A2 那 ~5~9 协奏, 卜灵第二格要多补 9~10 下 A 才满, 10-09 用户发现后改回
    # 2A 边点边读卦象栏: 最新一个是艮就照走; 是震 = 空中进场, 普攻成了下落(落地给震), 人已经落地 -> 再打一轮 2A 补艮(只补一次),
    # 然后不跳A(下落已经给过那个震): 震艮 + E 震 + A4 震 = 震艮震震, 先蓝(震震)后黄(震艮), 协奏跟正常流程一样多。
    # 10-08 23:10 / 10-09 00:02 两次 L2 都是穗穗 E 完马上切过来, 卜灵在怪头顶出现
    BL1_2A_VERIFY = 0.25         # 2A 点完再读这么久
    BL1_2A_REDO_DELAY = 0.12     # 补打那轮先等落地收招(落地 派生 8F), 第二下才落得在 A1 派生 25F 之后
    # 两段重击前读全排卦象, 凑不出一阴一阳就补: G = 普攻 A1A2 补艮, Z = 跳A 下落补震, 最多补 BL1_FIX_MAX 次, 每步做完重读重排。
    # 补之前算协奏账: 现在 + 每段重击 15 + 补的 + 大招 20 > BL1_CON_BUDGET 就不补, 直接开 R(R 完满协奏切穗穗会变奏, 用户 10-06)
    BL1_FIX_MAX = 2
    BL1_FIX_CON = {'G': 0.05, 'Z': 0.06}   # A1 1.67 + A2 2.7 / 落地 4.96, 留点余量
    BL1_CON_BUDGET = 0.97
    BL1_CON_PENDING = 0.08       # 刚打完 A4 那一刻 A4 的 7.5 协奏还没到账(实机 重击前 0.36~0.40 -> R 完 0.93~0.98, 比 +50 多 ~0.06)
    BL1_FIX_CAP = {'G': 1.30, 'Z': 1.60}
    BL1_G_CLICK_WINDOW = 0.50    # 补艮只点出 A1、A2(A1 派生 25F), 再点会排出 A3
    # 按住重击: 卦象栏少两个(第40F结算)连 2 帧就松。实机第一段 0.94~1.19s 结算, 协奏还要再晚 0.2~0.4s 才涨。
    # 上限再长就会连到下一段(60F)甚至鬼门占卦(没卦象时的重击)
    BL_HEAVY_GUA_CAP = (1.45, 1.00)   # 前面是别的动作 / 紧接着上一段
    BL_R_KEY_INTERVAL = 0.12     # R 和 A 错开帧一起连发到 HUD 消失
    BL_R_CAP = 10.0
    BL_R_ANIM_MIN = 3.00         # 卜灵大招藏 HUD 216F = 3.6s
    BL_R_ANIM_CAP = 6.00
    BL_INTRO_ANIM = 1.17         # 万一是变奏入场: QTE 第70F前不响应输入
    # 第二格(心转相演完, 普通切人进来): 2A -> 协奏满 -> [等翾舞] -> Q -> 马上变奏心。2A 顺手给一个艮, 带进下一圈的第一格
    BL2_A_MIN = 0.75
    BL2_A_EVERY = 0.15
    BL_Q_WINDOW = 0.25
    BL_Q_PRESSES = 2
    # 心第二段要变奏·照世相: 协奏不满就补(外圈亮按重击 +15, 不亮 A)。第一格流程被打断时协奏留在这一格补, 上限放宽
    BL2_CON_CAP = 5.00
    # 差不到 10 协奏就只点 A 补: 一段重击 +15 要按 0.7~1.4s, 还会把带进下一圈第一格的艮吃掉。
    # 读到 1.00 只差连读确认的那几帧什么都不按(10-09 4K L1: 协奏 1.00 时外圈先亮, 又按了一发 1.4s 的重击)
    BL2_HEAVY_BELOW = 0.90
    # 心第一段到场后(游戏时间)不到这么久不变奏心: 翾舞1 那一刻卜灵要在场拿烟岚前置。翾舞1 = 帧表 5.87s / 秧千穗实测切人键后 6.02~6.11s;
    # 这里从切人确认(键后 ~0.1s)起算, 6.12 = 键后 ~6.22s。原来 6.30 协奏满了还要白点 ~0.7s A(10-09 用户嫌 Q 切人慢), 视频是 5.99
    BL2_LEAVE_MIN = 6.12

    # ==================== 心 ====================
    # ---- 应世心条(4K 坐标, hcenter): 回路条中间徽记左右两半的紫色格子(心汐守定稿) ----
    XN_GAUGE_L = (1627, 1999, 1837, 2011)
    XN_GAUGE_R = (1965, 1999, 2175, 2011)
    XN_GAUGE_VALID = 0.50        # 左半 >= 这个才算"在应世相、读数可信"(变奏入场之后左半就是满的)
    XN_YING_READY = 0.70         # 右半到这么多就算到线
    XN_CONSUMED = 0.15           # 按住之后左半掉到这个以下 = 整条清空 = 步红尘出手
    XN_FULL_THRESHOLD = 0.70     # hsin_h1(应世心满的花图标)
    XN_HOLD_SENDINPUT = True     # 心的长按走 SendInput(游戏在前台时): PostMessage 的按下沿落在普攻收招里会被丢掉

    # ---- 第一段(穗穗变奏进来, 应世相): Q -> A 到应世心满 -> 按住出步红尘 -> 转相 ----
    # 视频(倒计时, 从到场起): Q 0.30~0.53s; 一直点普攻(打 a4); 按住 1.94s(循环轴, 应世心 1.64s 满) / 2.38s(启动轴, 上场就满)
    XN1_Q_AT = 0.30
    XN1_Q_WINDOW = 0.15
    # 上场先认一下是不是应世相(转相图标 / 应世心花 / 应世心条, 任一读到就是): 上一圈万阙垂天没放出来的话心还在照世相,
    # 这时按 R 放的是借相。连读 XN_FORM_READS 帧(HUD 在场)三样都没有才算照世相
    XN_FORM_CAP = 0.40
    XN_FORM_READS = 4
    XN1_ZHAO_A = 1.00
    XN1_A_FROM = 0.45
    XN1_A_EVERY = 0.17
    XN1_A_HOLD = 0.08
    XN1_GATE_CONFIRM = 2         # 连读这么多帧满/到线才算(特效扫过会闪)
    XN1_HOLD_AT_MIN = 1.90       # 满了也至少打到这一刻再按住(视频循环轴 1.94s 按住)。原来 2.40 是为了凑"心到场->切卜灵 >= 5s", 10-09 放开了
    XN1_A_CAP = 4.50             # 一直不满就打到这一刻照按
    XN1_LAST_A_GAP = 0.12        # 最后一下普攻和按住之间至少隔这么久(按下沿别跟点击粘在一起)
    XN1_HOLD = 1.80              # 按住上限(XN1_R1_SPAM 时读到清空就松开)。步红尘免疫打断, 转相解锁从清空那一刻算
    XN1_RETRIES = 2              # 按满 XN1_HOLD 条都没清空(= 出的是普通重击) -> 补两下 A 再按
    XN1_RETRY_A = 2
    XN1_FILL_EVERY = 0.40
    # 转相(R1)解锁 = 应世心条清空(步红尘出手) + 2.06s(心汐守三个样本一帧不差; 这条视频 清空->转相 也对得上)
    # 【心这一段的时长是穗穗大招定的】(用户 10-06): 穗穗延奏(变奏心那一刻)后约 6s 第一次翾舞, 那一刻在场的人拿烟岚前置,
    # 它再变奏下一个人, 下一个人才拿烟岚(攻击 +50%)。要的是卜灵在场拿前置、变奏给心第二段 —— 所以
    # 翾舞1 那一刻卜灵要在场、之后才变奏心(帧表: 延奏 79F 后生成飞仙, 飞仙 273F 翾舞1 = 5.87s; 秧千穗实测切人键后 6.02~6.11s)。
    # 转相演出整段停游戏时钟(10-06 录屏: 演出 4.25s 倒计时只走了演出外那 1.22s), 心这段的游戏时间 = 到场到转相出手 + 演完到切人。
    # 【只有上限没有下限】(10-09 用户同意): 视频心到场 -> 切卜灵 4.44s(倒计时)、卜灵 5.99s 变奏心; 切早了卜灵就多站一会儿,
    # 不早于翾舞走由 BL2_LEAVE_MIN 管。原来卡的 5~6s 下限每圈多花 ~0.4s
    # 做法: 转相不早于到场后 XN1_R1_AT 按(之前接着打应世相普攻), 演完立刻切卜灵; 卜灵变奏心不早于 BL2_LEAVE_MIN
    XN1_R1_AT = 5.25
    XN1_R1_TAP_GAP = 0.12        # 最后一下普攻和 R 之间隔这么久
    # 步红尘出手(应世心条清空)就松开重击、连按 R 到转相出手: 没解锁时按 R 什么都不发生, 解锁那一刻就放出去,
    # 不再等图标亮、也不再卡 XN1_R1_AT。
    # False = 旧做法(读到图标亮再按, 不早于 XN1_R1_AT)
    XN1_R1_SPAM = True
    XN1_BL_MIN = 0.0             # 演完之后游戏时间还不到这一刻就站着等。0 = 演完直接切卜灵
    XN1_BL_MAX = 5.70            # 超过这个记一笔(切人 ~0.1s, 翾舞1 最早 5.87s, 卜灵赶不上)
    XN1_R1_UNLOCK = 2.06
    XN1_R1_POLL_FROM = 1.85
    XN1_R1_MARGIN = 0.08
    XN1_R1_LIT_CAP = 3.00
    XN1_R1_READS = 2
    XN1_R1_BLIND_AT = 2.60
    XN_R_WINDOW = 1.50           # R 连按到 HUD 消失的窗口
    XN1_R1_RESCUE_CAP = 8.00     # 转相没出就补到出为止(照世相、万阙垂天、整个第二段都靠它)
    XN1_R1_RESCUE_A = 0.80
    XN1_R1_LOST_ROUNDS = 3
    XN_R_ANIM_MIN = 3.90         # 转相 / 万阙垂天演出藏 HUD 4.3s 以上, 中途 HUD 会闪一帧
    XN_R_ANIM_CAP = 8.00

    # ---- 第二段(卜灵变奏进来, 照世相): A -> E -> 连按 E 到统御众机 -> A -> 闪 -> 2A -> 按住到临寰宇 -> 万阙垂天 ----
    # 视频(倒计时, 从到场起): 一直点普攻, E 1.87~2.03s; E 之后连按 E, 1.57~1.72s 后进统御众机(照世心满 300, E 换万阙连衡);
    # 统御众机里 A 3.5s -> 闪(启动轴是处决) -> A 0.8s -> 按住 0.5s 临寰宇出手(照世心打空)
    XN2_A_FROM = 0.05
    XN2_A_EVERY = 0.15
    XN2_E_AT = 1.85              # 普攻第1段要先打出来(电磁模态下生成活字心机, E 把它引爆才给照世心)
    XN2_E1_CAP = 1.20            # 照世相 E(CD 20s)按到进 CD 的上限
    XN2_E_MASH = 0.15            # 之后连按 E 的间隔: 照世心满的那一刻 E 变万阙连衡, 按下去进统御众机; 没满按 E 是空操作(还在 CD)
    XN2_LIANHENG_CAP = 3.00
    XN2_RESCUE_CAP = 3.00        # 没进统御众机: 普攻攒照世心 + 按 E 交替, 上限
    XN2_RESCUE_A = 0.80
    XN2_RESCUE_E = 0.80
    XN2_TY_CONFIRM = 2           # 雕像图标连读几帧才认
    XN_TY_MIN = 0.60             # 统御众机 0.71~1.00 / 其他 <= 0.38
    XN_TY_GLYPH = (
        'AAAAAAAMJCEsJBYBAAAAAAAAABiR1cJ40tzbpyYAAAAAABK+4+Pt2unk48BaJAAAAAhQg6aMnN1/l6RzUVEbAAAzV3Dw9crHyvngXlFQRQIJ'
        'VFqs8dbtovLV8JdRUFAdIlmK7ZPT7IT7v6Dtbk9bOzdhj5en/O2E/PmHoXdelTpIsV1Xrv3thPz7iE9XwJuUJsCvW6797YX9+4dTsfjxriWy'
        '9KGQ++2E/fqGnvfU33sBpvjsmt/thPzfmeylk9cPADOnpLzM7IT4tc54Y+FhAAAASGKt+eyD5uiKUbyiBQAAAAAkjf3tg/j6h3aCCQAAAAAA'
        'AAZbpFvGrUwKAAAAAA=='
    )
    XN_TY_SLOT = (2826, 1893, 82, 79)   # 重击位(4K, 跟 hsin_h1 的标注框一样)
    # 【照世心打空 = 重击位从雕像换成人像】, 这时候按住 ~0.45s 出临寰宇。雕像还在时按住是白按:
    # 统御众机里照世心是普攻/闪避反击打掉的, 按住重击不消耗(10-06 实机 09:36 / 09:39 两次按满 3s 没出, 补几下 A 人像一出来就出了)。
    # 人像模板取自 10-06 两份 2K 录屏三次临寰宇: 人像帧 0.93~1.00(按住时外面多一圈金光也算), 雕像帧 <= 0.16, 其余所有帧 <= 0.64, 缩 1080 一样
    XN_READY_MIN = 0.80
    XN_READY_GLYPH = (
        'AAAAAAAGGDxjHgwAAAAAAAAAABQoWcH31Vg3Jg0AAAAAACGmh+r9+X82NzlcMgAAAAZ47tD9/c06R2NGZ2cWAAA32P38/f2+N0xtaDY3MQIa'
        '3/v9/v3x4DhNbFk2OEcVYf39/f34yMM4RGJWQjmCKmH9+vW6sa2FPT09WGFLyjZ5/c7E4fWaQD0/UHOX0POeYfmXotjqXz09PkBvmojMUTfc'
        'vZnUhD08PDw9mcfbvzMBauG+pJZJREA9P9L05XAFACb1+/38refMZD3D+oQjAAAAbfn9/tDY/LQ7tOMxAgAAAABU2f7is/y+QMd0AgAAAAAA'
        'AAhcpG3GgygPAAAAAA=='
    )
    XN2_READY_CONFIRM = 2
    # 统御众机里: 一直普攻到人像出来, 中间闪避一次(有处决提示就用处决代替)。
    # 【闪避/处决的时机 = 照世心雕像从右往左翻满 4 颗的那一刻】(用户 10-06 "前面就是 4 层, 后面每下都是 2"):
    # 万阙连衡出招动画 ~2.5s 里按的普攻全被吃掉, 之后每一下万阙连衡普攻翻 2 颗。作者第 4 颗一翻就闪避/处决, 把第二下的收招打断,
    # 之后每下普攻都是 2 颗(5、6 一起, 7、8 一起), 两下就能按住出临寰宇。视频三次: 第 4 颗翻面 -> 出手 0.03 / 0.17 / 0.17s。
    # 10-06 实机旧版按固定时间: 第 2 颗(循环轴)/第 4 颗前 0.11s(启动轴)就闪了, 第二下的消耗被打断, 后面要多打几下(用户: "还是快了一点")
    XN2_TY_A_EVERY = 0.18
    XN2_TY_DODGE_COUNT = 4
    XN2_TY_DODGE_MIN = 2.50      # 进统御众机这么久之前不出手(万阙连衡出招动画里不会翻面, 防误读)
    XN2_TY_DODGE_CAP = 5.00      # 一直没数到 4 颗(读不准)也到这一刻出手。实机第 4 颗在进统御众机后 3.75~3.95s
    XN2_TY_PROMPT_KEEP = 3.00    # 处决提示在出手前这么久内见过, 出手那一刻就再确认一下, 还在就处决代替闪避
    # 照世心雕像(4K, hcenter): 8 颗的中心 x, 红菱形那一行 y, 上半身(亮白)那一行 y。取自 10-06 实机录屏(2560x1440 缩 720p)
    XN_STATUE_XS = (1647, 1719, 1794, 1866, 1938, 2013, 2085, 2160)
    XN_STATUE_Y = 1980
    XN_STATUE_UP_Y = 1953
    XN_STATUE_HW = 9
    XN_STATUE_RED = 0.08         # 红菱形占比(没翻 0.00, 翻了 0.10~0.55)
    XN_STATUE_GLOW = 0.30        # 上半身亮白占比(没翻 <= 0.12, 翻面那一刻 0.45~0.61, 翻完常亮 0.14~0.5)
    XN_STATUE_CONFIRM = 2
    XN2_DODGE_HOLD = 0.07        # 闪避 = 右键
    XN2_DODGE_TO_A = 0.12
    XN2_TY_CAP = 10.0            # 统御众机 13s; 一直没看到人像就到这一刻照按(实机打空要 5.1~5.3s 的普攻)
    XN2_HOLD_CAP = 1.20          # 人像出来后按住到 HUD 消失(临寰宇出手, 实测 0.40~0.45s)
    XN2_BLIND_HOLD = 2.00        # 没看到人像(撞上限/没认出统御众机)时按住试一次的上限
    XN2_RELEASE_AFTER_GONE = 0.30
    XN2_RETRIES = 2              # 按住没出(被打断) -> 普攻到人像再按, 最多这么多次
    XN2_RETRY_CAP = 3.00         # 补的那一轮普攻到人像的上限
    XN2_ANIM_MIN = 1.40          # 临寰宇藏 HUD ~2.0s
    XN2_ANIM_CAP = 4.00
    # 临寰宇演完后读 hsin_lib2(万阙垂天解锁)的上限。【认不出就不按 R】: 10-06 09:31 那圈照按, R 放成了借相(CD 1.5s, 心切回应世相)
    XN2_LIB2_CAP = 1.50
    XN2_LIB2_READS = 2
    # 万阙垂天之前协奏低于这个就照世相普攻补(人还贴着怪; 开完大离怪远了打不到 —— 心汐守实机 10-05 11:56)
    XN2_PRE_R2_CON = 0.80
    XN2_PRE_R2_CAP = 2.00
    # 变奏穗穗之前心协奏要真满。作者: 打得快的话等 0.5s 左右再变奏穗穗, 等穗穗的被动(翾舞)给协奏
    XN2_CON_CAP = 3.00
    # 变奏穗穗之前没怪(这一波被万阙垂天打死)就等新怪再切, 见 wait_next_wave。数值同绯洛穗
    WAVE_WAIT = 4.0
    TARGET_GONE_CONFIRM = 2      # 连着这么多次读不到锁定目标才算没怪, 单帧被特效挡住不算
    WAVE_LOCK_INTERVAL = 0.3     # 等新怪时按中键锁目标的间隔
    # 处决: 只按 F, 从第一下起等满 XN_BREAK_ANIM。
    # 【判据只认怪身上的 F 提示(屏幕中间 f_break >= 0.90, 不是拾取 F), 不认顶部谐度满(f_break_full)】:
    # 10-06 09:39 录屏顶部谐度满比 F 提示早 2.8s, 宏在只有顶部那个时按 F 是空按(倒计时没停), 白站 1.4s;
    # 真提示出来时正在按住重击, 处决没打。两份录屏 F 提示帧 0.91~0.98, 其余 <= 0.81
    XN_BREAK = True
    XN_BREAK_FROM = 0.80         # 进统御众机这么久之后才看(万阙连衡那一下的演出)
    XN_BREAK_CONFIRM = 2
    XN_BREAK_PRESSES = 3
    XN_BREAK_ANIM = 1.40         # 视频: 按 F 到恢复普攻 ~1.2s, 倒计时停了 ~1.0s
    XN_BREAK_CENTER_MIN = 0.90

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
        self.su_qiang_at = -99.0       # 穗穗进濯雨时的时刻(醒春潮 / 变奏入场)
        self.su_no_qiang = False       # 醒春潮确定没开出来
        self.bl1_fallback = False      # 卜灵第一格流程被打断过(直接开的 R)
        self.bl_gua_left = None        # 卜灵第二格走之前的卦象(下一圈第一格进场带着它)
        self._con_soft_n = 0
        self._con_soft_frame = None
        self.xn1_t0 = 0.0              # 心第一段到场时刻 / 转相演出时长(算游戏时间用)
        self.xn1_pause = 0.0
        self.xn_broke = False
        self.hud_seen_at = 0.0         # 最后一次读到队伍 HUD 在场的时刻(HUD_LOST_ABORT 用)
        self.hud_watch = False         # 宏跑着的时候才查 HUD 丢没丢
        self.drive_log_at = 0.0
        self.timeline_logged = True
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
    def is_in_xbs(self) -> bool:
        """本队 = 心 + 卜灵 + 穗穗。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Douling', 'Suisui'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_xbs:
            return 'team is not hsin/douling/suisui'
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


    # ==================== 宏原语(引擎) ====================

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

    def spam_key(self, send, window, interval=None, tag='key', poke=None, presses=None,
                 click_interval=None, stop_when=None):
        """在一个窗口里反复发同一个技能键, 用来盖住动画那一段。

        动画期间的按键是被丢掉的、不排队, 所以单发必丢 —— 这个形状在爱琳莫那条轴上
        踩到过六次, 症状统一是"那一步慢了"(要等整个动画演完才有第二次机会)。

        【必须在这个角色自己的回合里跑完, 不要跟切人并行】: 切人期间发出去的键会被
        游戏带过换人边界落在新角色身上。点击可以并行(quick_switch 的 click_during),
        技能键不行。唯一的例外是 switch_into_lib()。

        Args:
            poke: 发键期间要不要照常点普攻(默认不点)。
            click_interval: 点普攻的间隔, 默认 A_INTERVAL。给 A_INTERVAL_FAST
                就是"技能键按自己的节奏、普攻按加密节奏", 两条流互不影响。
            stop_when: 每发一次键之后调一次, 返回 True 就停。用在【多按一下会
                烧掉后面要用的充能】的地方 —— 一旦读到技能已经不可用就立刻收手。
                【会读屏, 别配太密的 interval】。
            presses: 只发这么多次, 均匀铺在窗口里。用在"连续点按两次E"那种
                【次数本身有意义】的地方 —— 连发会把两发挤成一发。
                【给了 presses 就由它说了算, window 只用来推间隔】: 否则
                window=0(要求两发贴着发)会在第一发之后就把循环停掉, 变成单发。
        """
        interval = self.KEY_INTERVAL if interval is None else interval
        poke = bool(poke)
        click_interval = self.A_INTERVAL if click_interval is None else click_interval
        if presses is not None and presses > 0:
            interval = max(interval, window / presses)
        start = time.time()
        n = 0
        clicks = 0
        next_click = 0.0
        while True:
            send()
            n += 1
            if stop_when is not None and stop_when():
                break
            if presses is not None:
                if n >= presses:
                    break
                left = interval
            else:
                left = window - (time.time() - start)
                if left <= 0:
                    break
            step_end = time.time() + min(interval, left)
            # 发键的节奏和点击的节奏各走各的 —— 挂在一起的话点击会被技能键的
            # 间隔卡住(爱琳莫那边 R 被 E2_POLL 卡住就是这个)
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + click_interval
                self.macro_wait(min(0.02, step_end - time.time()))
        # 【点击数也要打进去】: x{n} 只数了技能键, 看不出这段时间有没有在持续普攻。
        # 用户 2026-08-16 问过一次"E的期间也要一直普攻" —— 实际一直在点,
        # 只是日志里看不见。A x0 就是真的没点(poke=False 的那几格)
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
        if not self.is_in_xbs:
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
                con_full = self.con_now() >= 1.0
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

    def wait_hud_back(self, tag='anim', time_out=None, min_gone=0.0, tail=0.0):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。先确认消失过。

        min_gone: 从第一次读到消失起, 这么久之内读到的"回来"不认(演出中途 HUD 会闪一两帧)。
        回来要连读 HUD_BACK_CONFIRM 帧。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_at = None
        back_hits = 0
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
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

    def watch_hud_gone(self, window, tag, poke_after=None):
        """在 window 里盯队伍 HUD 有没有连续消失(= 演出开始), 消失了立刻返回 True。

        poke_after: 过了这么久 HUD 还在就开始点普攻(这一下技能没出手, A 在攒资源)。None = 不点。
        """
        start = time.time()
        gone = clicks = 0
        next_click = 0.0
        while time.time() - start < window:
            self.task.next_frame()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    self.macro_mark(f'{tag}(出手,{time.time() - start:.2f}s,A x{clicks})')
                    return True
            else:
                gone = 0
                now = time.time()
                if poke_after is not None and now - start >= poke_after and now >= next_click:
                    self.tap()
                    clicks += 1
                    next_click = time.time() + self.A_INTERVAL
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(没出,A x{clicks})')
        return False

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


    def dodge(self, tag):
        """闪避(右键)。"""
        self.task.mouse_down(key='right')
        try:
            self.macro_wait(self.XN2_DODGE_HOLD)
        finally:
            self.task.mouse_up(key='right')
        self.macro_mark(f'{tag}闪避')

    def break_prompt_reads(self, frames):
        """最多读 frames 帧, 有一帧读到怪身上的处决提示就返回 True。"""
        for _ in range(frames):
            self.task.next_frame()
            if self.break_prompt():
                return True
        return False

    def break_prompt(self):
        """怪身上有处决提示(屏幕中间那个 F, 排除拾取 F)。读当前帧。不看顶部的谐度满(f_break_full): 那个比提示早好几秒, 那时按 F 是空按。"""
        task = self.task
        try:
            hit = task.find_one('f_break', box=task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True),
                                target_height=720, threshold=self.XN_BREAK_CENTER_MIN)
            return bool(hit) and not task.is_pick_f()
        except Exception as e:
            self.logger.info(f'处决提示读失败 {e}')
            return False

    # ==================== 穗穗 ====================

    def sui_e1_lit(self):
        """穗穗的强化E(醒春潮)就绪没有 —— 读的是 E 图标那张强化态模板。"""
        return bool(self.task.find_one(Labels.suisui_e1, threshold=self.SUI_E1_THRESHOLD))

    def fill_sui_e1(self, char, tag, cap=None):
        """强化E 图标不亮(= 水云息没满)就接着点普攻攒, 亮了立刻停手。返回亮了没有。"""
        cap = self.SUI_E1_FILL if cap is None else cap
        start = time.time()
        n = 0
        next_click = 0.0
        while time.time() - start < cap:
            self.task.next_frame()
            self.hud_ok()                   # 喂看门狗: 这一格最长 SU_E1_MUST_CAP, 比 HUD_LOST_ABORT 长
            if self.sui_e1_lit():
                if n:
                    self.macro_mark(f'{tag}攒回路({time.time() - start:.2f}s,A x{n})')
                return True
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.SUI_E1_FILL_INTERVAL
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}攒回路(CAP,A x{n},{cap:.1f}s)')
        return False

    def cast_sui_e1(self, char, tag):
        """按到 suisui_e1 这张图消失 —— 消失才等于强化E(醒春潮)真的放出去了。

        【为什么要连按】: 这一格紧跟在切人后面, 入场动画会把单发吞掉(跟 fire_r 一个道理)。
        【连按在这里是安全的】: 图标一消失就停手, 多按的那几下落不到第二次 E 上。
        """
        start = time.time()
        n = 0
        while time.time() - start < self.SUI_E1_CAST_CAP:
            char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
            n += 1
            self.macro_wait(self.SUI_E1_KEY_INTERVAL)
            self.task.next_frame()
            if not self.sui_e1_lit():
                self.macro_mark(f'{tag}强化E出手(x{n},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}强化E没出手(x{n},CAP)')
        self.logger.warning(f'{tag}: 按了 {n} 下 suisui_e1 还亮着 — 这几下多半被吞了'
                            f'(入场动画/硬直?), 这一轮穗穗进不了濯雨时')
        return False

    def forte3_full(self):
        """穗穗的三段回路(芳菲信 600)满没满。

        【这几帧连着读完, 中间不做别的】—— 靠外层循环去凑命中次数会白白多等一轮。
        HUD 被演出盖住时读不到, 返回 False 落在"还没满"这一侧, 方向是安全的
        (顶多多点几下普攻, 不会误判成满就去放大招)。
        """
        for i in range(self.FORTE3_CONFIRM):
            if not self.task.find_one(Labels.suisui_forte3, threshold=self.FORTE3_THRESHOLD):
                return False
            if i + 1 < self.FORTE3_CONFIRM:
                self.task.next_frame()   # 换一帧再读, 否则两次读的是同一张图
        return True

    def wait_forte3(self, tag, cap=None, su=None):
        """穗穗大招前的保底: 三段回路没满就接着点普攻, 满了立刻停手。返回满了没有。
        su: 打了 SU2_E_RESCUE_AT 秒还没满、E 好了就补一个 E(濯雨时的 E 第36F 给 100 芳菲信), 只补一次。
        上限 cap 到了【照放不误】。
        """
        cap = self.FORTE3_WAIT if cap is None else cap
        if not self.FORTE3_GATE:
            return True
        start = time.time()
        n = 0
        next_click = 0.0
        e_used = su is None
        while time.time() - start < cap:
            self.hud_ok()                   # 喂看门狗: 上限可能比 HUD_LOST_ABORT 长
            if not e_used and time.time() - start >= self.SU2_E_RESCUE_AT:
                e_used = True
                try:
                    ready = bool(su.resonance_available())
                except Exception:
                    ready = False
                if ready:
                    self.spam_key(su.send_resonance_key, 0.20, tag=f'{tag}补E(+100芳菲信)', presses=2)
                    su.record_resonance_use()
                    continue
            # 【先点再读】(2026-09-22 用户: "穗穗最后回路没满要补的时候, 经常卡顿一下才A")。
            # forte3_full() 要连读 FORTE3_CONFIRM 帧, 一帧 next_frame 就几十毫秒 ——
            # 把读屏放在点击前面, 进这一格的第一下 A 就被硬生生压后, 手感上就是"卡一下"。
            # 判据晚一轮(0.02s)成立没有任何代价, 第一下 A 晚 60ms 是看得出来的
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            if self.forte3_full():
                waited = time.time() - start
                self.macro_mark(f'{tag}满({waited:.2f}s,A x{n})')
                if n <= 1 and waited < self.FORTE3_SUSPECT:
                    # 【一下没点就"满"了, 十有八九是误判】。穗穗有两个形态两条【不同】的
                    # 资源条: 浣尘时显示【水云息】, 濯雨时才是【芳菲信】; 而 suisui_forte3
                    # 匹配的是芳菲信满档那张图 —— 她要是没进濯雨时(E 被吞, 醒春潮没放出来),
                    # 读的就是水云息那条条, 会误命中。
                    # 实机对照: 启动轮 满(1.44s,A x11) / (1.88s,A x14), 循环轮 满(0.03s,A x0)
                    self.logger.info(
                        f'{tag}: {waited:.2f}s 一下没点就读到"回路满"(循环轮常见: 前面上场已经攒满); '
                        f'要是这一轮秧秧没吃到烟岚, 再怀疑穗穗没进濯雨时、读到的是水云息那条')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}没满(CAP,A x{n},{cap:.1f}s)')
        self.logger.warning(f'{tag}: 三段回路 {cap:.1f}s 没攒满(点了 {n} 下 A), '
                            f'照放(濯雨时过了 / 打不到怪)')
        return False


    def seg_su_open(self, su, bl, tag, must=True):
        """穗穗·启动格: 3A 攒满水云息 -> 醒春潮(进濯雨时 15s) -> 切卜灵。
        【醒春潮必须开出来才切卜灵】(用户 10-06): 没进濯雨时, 穗穗第二格的芳菲信根本攒不起来。
        图标不亮就一直 A(开局怪还没站稳时普攻打不中), 按了图标还亮(被吞/被打断)就回去接着 A 再按, 上限 SU_E1_MUST_CAP。
        must=False(循环轴兜底, 穗穗可能还在濯雨时 —— 那样图标永远不亮): 短上限, 不亮照按 E 走人。"""
        self.su_qiang_at = -99.0
        self.su_no_qiang = False
        if self.cur is not su:
            self.need(self.switch_retry(su, f'{tag}切穗穗'), 'opener: switch to suisui')
        cap = self.SU_E1_MUST_CAP if must else self.SUI_E1_FILL
        start = time.time()
        tries = 0
        while time.time() - start < cap:
            if not self.fill_sui_e1(su, tag, cap=cap - (time.time() - start)):
                break
            tries += 1
            if self.cast_sui_e1(su, tag):
                self.su_qiang_at = time.time()
                break
            self.macro_mark(f'{tag}醒春潮按了没出(#{tries}, 接着A再按)')
        if self.su_qiang_at < 0:
            if must:
                self.su_no_qiang = True
                self.logger.warning(f'{tag}: 醒春潮 {cap:.0f}s 都没开出来(普攻打不到怪?), 照常切卜灵 —— 穗穗第二格芳菲信攒不满')
            else:
                self.press_res_until_cd(su, self.SU_L_E_CAP, tag=f'{tag}E(醒春潮没亮)')
        su.record_resonance_use()
        self.need(self.switch_retry(bl, f'{tag}切卜灵'), 'suisui -> buling')

    def seg_su_loop(self, su, bl, tag):
        """穗穗·循环格(心变奏进来, 变奏直接给 15s 濯雨时): E -> 切卜灵。"""
        arrived = getattr(su, 'last_switch_in_time', 0.0) or 0.0
        # has_intro 是上一圈交棒时记的; 宏散过/框架隔了很久才调进来的话不作数
        if self.cur is not su or not su.has_intro or time.time() - arrived > self.SU_L_INTRO_FRESH:
            # 变奏进来但隔久了: 濯雨时多半还在(醒春潮图标不会亮), 短上限; 不是变奏进来的: 醒春潮必须开出来
            still = self.cur is su and su.has_intro and time.time() - arrived < self.SU_QIANG_DUR - 4.0
            self.macro_mark(f'{tag}穗穗不是刚变奏进来的(走启动格{",濯雨时多半还在" if still else ""})')
            self.seg_su_open(su, bl, tag, must=not still)
            if still and self.su_qiang_at < 0:
                self.su_qiang_at = arrived
            return
        self.su_qiang_at = arrived          # 变奏入场第1F 给 15s 濯雨时
        self.su_no_qiang = False
        # 变奏入场里按的 E 被丢, 一直按到进 CD
        self.press_res_until_cd(su, self.SU_L_E_CAP, tag=f'{tag}E', ready_grace=0.6)
        su.record_resonance_use()
        self.wait_until_time(max(time.time() + self.SU_L_E_TO_SWITCH, arrived + self.SU_L_MIN_FIELD))
        self.need(self.switch_retry(bl, f'{tag}切卜灵'), 'suisui -> buling')

    def seg_su2(self, tag):
        """穗穗·第二格(卜灵大招演完切进来): A 到芳菲信满 -> Q -> R -> 演完 -> 协奏满 -> 变奏心。"""
        su = self.cur
        if self.su_no_qiang:
            cap = 0.0                  # 醒春潮没开出来, 不在濯雨时: 普攻攒不出芳菲信, 不白打
            self.macro_mark(f'{tag}不在濯雨时(醒春潮没开出来)')
        elif self.su_qiang_at > 0:
            left = self.su_qiang_at + self.SU_QIANG_DUR - time.time()
            cap = max(self.SU2_A_MIN, min(self.SU2_MUST_CAP, left + self.SU_QIANG_GRACE))
            self.macro_mark(f'{tag}濯雨时还剩{left:.1f}s(最多等{cap:.1f}s)')
        else:
            cap = self.SU2_MUST_CAP    # 醒春潮没认出来按的, 不知道濯雨时从哪一刻算
        if cap > 0:
            self.wait_forte3(f'{tag}芳菲信', cap=cap, su=su)
        self.su_con_before_r(tag)
        self.spam_key(su.send_echo_key, self.SU_Q_WINDOW, tag=f'{tag}Q', presses=2)
        su.record_echo_use()
        if self.ensure_r(su, tag, self.SU_R_WINDOW, self.SU_R_CAP,
                         lambda rounds: self.macro_spam(self.R_BUILD_SLICE, tag=f'{tag}攒能量#{rounds}')):
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SU_R_ANIM_MIN, time_out=self.SU_R_ANIM_CAP)
        full = self.read_con(1.0, settle=0.15) >= 1.0 or self.wait_con_full('穗穗', self.SU_CON_CAP, tag=f'{tag}协奏')
        if not full:
            self.logger.warning(f'{tag}: 穗穗协奏没满就切心 —— 心没有变奏(少 60 应世心), 穗穗不跳延奏(这一圈没有翾舞)')
        self.need(self.switch_retry(self, f'{tag}切心', assume_con=full), 'suisui -> hsin')

    def su_con_before_r(self, tag):
        """穗穗大招前协奏不到 SU_R_CON_MIN 就点普攻补到够(大招只回 20)。上限 SU_R_CON_CAP。"""
        con = self.read_con(self.SU_R_CON_MIN, settle=0.15)
        if con >= self.SU_R_CON_MIN:
            self.macro_mark(f'{tag}大招前协奏{con:.2f}')
            return
        start = time.time()
        deadline = start + self.SU_R_CON_CAP
        why = 'cap'
        taps = 0
        samples = [f'0:{con:.2f}']
        while time.time() < deadline:
            self.tap()
            taps += 1
            self.macro_wait(max(0.0, self.A_INTERVAL - self.A_TAP))
            con = self.read_con(self.SU_R_CON_MIN, settle=0.03)
            if len(samples) < 20:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= self.SU_R_CON_MIN:
                why = 'ok'
                break
        self.macro_mark(f'{tag}大招前补协奏({con:.2f},A x{taps},{time.time() - start:.2f}s)')
        if why != 'ok':
            self.logger.warning(f'{tag}: 大招前协奏只补到 {con:.2f}({why}), 放完多半不满(心没有变奏), 后面再补 — {" ".join(samples)}')

    # ==================== 卜灵 ====================

    def bl_ring(self):
        """卜灵重击图标外圈: 返回 ('blue' | 'yellow' | None, 黄占比, 蓝占比)。拿不到画面返回 (None, -1, -1)。

        手上不到两个卦象(或刚加卦象那半秒白光过渡)外圈不亮, 读成 None。
        按 HSV 判不按 RGB: 圈会呼吸, 亮度起伏大, 色相稳。只截圈附近一小块, 每帧读不花时间。
        """
        try:
            import cv2
            import numpy as np
            frame = self.task.frame
            h, w = frame.shape[:2]
            cx, cy = int(w * self.BL_RING_CX), int(h * self.BL_RING_CY)
            r0, r1 = h * self.BL_RING_R0, h * self.BL_RING_R1
            m = int(r1) + 2
            crop = frame[cy - m:cy + m + 1, cx - m:cx + m + 1]
            yy, xx = np.mgrid[-m:m + 1, -m:m + 1]
            d = np.sqrt(xx * xx + yy * yy)
            sel = (d >= r0) & (d <= r1)
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            hh, ss, vv = hsv[..., 0][sel], hsv[..., 1][sel], hsv[..., 2][sel]
            blu = float(((hh >= 85) & (hh <= 115) & (ss > 110) & (vv > 140)).mean())
            yel = float(((hh >= 20) & (hh <= 35) & (ss > 60) & (vv > 150)).mean())
        except Exception:
            return None, -1.0, -1.0
        if blu >= self.BL_RING_BLUE:
            return 'blue', yel, blu
        if yel >= self.BL_RING_YELLOW:
            return 'yellow', yel, blu
        return None, yel, blu

    def bl_a4_until_blue(self, tag, max_clicks, cap, min_blue=0.0, gua_before=None):
        """隔 BL_A4_CLICK_GAP 点一下 A(最多 max_clicks 下), 外圈读到蓝的那一帧起不再点(min_blue 秒之前读到的蓝不算);
        连 BL_RING_CONFIRM 帧蓝返回 True, cap 到点返回 False(交给两段重击那段换卦象)。HUD 不在的帧不读。
        gua_before(E 之后读的卦象栏): 卦象栏变了、最新一个是震(A4 命中)连 BL_GUA_CONFIRM 帧就算到, 不再等 min_blue。"""
        start = time.time()
        gua_hits = 0
        gua_got = None
        n = streak = 0
        next_click = 0.0
        blue_seen = False
        last = None
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if not blue_seen and n < max_clicks and now >= next_click:
                self.click()
                n += 1
                next_click = now + self.BL_A4_CLICK_GAP
            self.task.next_frame()
            if self.task.in_team()[0]:
                if gua_before is not None:
                    g = self.bl_gua()
                    if g and g != gua_before and g.endswith('震'):
                        gua_hits += 1
                        blue_seen = True            # A4 已经出了, 不再点(多点会排出 A1->A2, A2 的艮)
                        if gua_hits >= self.BL_GUA_CONFIRM:
                            gua_got = g
                            break
                    else:
                        gua_hits = 0
                color, yel, blu = self.bl_ring()
                last = color
                if len(trace) < 40:
                    # 带上协奏: A4 打中协奏 +7.5, 能分清是 A4 没出(协奏不动)还是符的顺序错了(协奏涨了圈还黄)
                    trace.append(f'{time.time() - start:.2f}:{(color or "-")[0]}{yel:.2f}/{blu:.2f}c{self.bl_con():.2f}')
                if color == 'blue' and time.time() - start >= min_blue:
                    blue_seen = True
                    streak += 1
                    if streak >= self.BL_RING_CONFIRM:
                        break
                else:
                    streak = 0
            self.macro_wait(0.02)
        if gua_got:
            self.macro_mark(f'{tag}A4震到({gua_before or "空"}->{gua_got},A x{n},{time.time() - start:.2f}s)')
            return True
        got = streak >= self.BL_RING_CONFIRM
        self.macro_mark(f'{tag}A4到蓝圈({"蓝" if got else "CAP:" + (last or "不亮")},A x{n},{time.time() - start:.2f}s)')
        if not got:
            self.logger.info(f'{tag}A4到蓝圈: 没等到蓝圈 — {" ".join(trace)}')
        return got

    def bl_ring_settled(self, cap):
        """不按键读圈, 连 BL_RING_CONFIRM 帧读数相同就返回它('blue' / 'yellow' / None=不亮); 到点没读稳返回 'unknown'。"""
        start = time.time()
        last, streak = 'unknown', 0
        while time.time() - start < cap:
            self.task.next_frame()
            if self.task.in_team()[0]:
                c = self.bl_ring()[0]
                streak = streak + 1 if c == last else 1
                last = c
                if streak >= self.BL_RING_CONFIRM:
                    return c
            self.macro_wait(0.02)
        return 'unknown'

    def bl_con(self):
        try:
            return self.con_now()
        except Exception:
            return -1.0

    def bl_gua_refs(self):
        refs = getattr(self, '_gua_refs', None)
        if refs is None:
            kinds, vecs = [], []
            for kind, b64 in self.BL_GUA_REFS:
                v = np.frombuffer(base64.b64decode(b64), np.uint8).astype(np.float32) - 128.0
                v -= v.mean()
                vecs.append(v / max(float(np.linalg.norm(v)), 1e-6))
                kinds.append(kind)
            refs = self._gua_refs = (np.array(vecs), kinds)
        return refs

    def bl_gua(self):
        """卦象栏从左到右, 如 '艮震震震', 一个都没有返回 ''(卜灵不在场也是 '')。HUD 不在 / 读不了返回 None。读当前帧。"""
        if not self.task.in_team()[0]:
            return None
        try:
            frame = self.task.frame
            H, W = frame.shape[:2]
            sc = H / 2160.0
            refs, kinds = self.bl_gua_refs()
            sh = self.BL_GUA_SHIFT if H >= 1000 else 1
            hw, hh = self.BL_GUA_HALF_W * sc, self.BL_GUA_HALF_H * sc
            cy = self.BL_GUA_Y * sc
            x0 = int(W / 2 + self.BL_GUA_DX[0] * sc - hw) - sh - 2
            x1 = int(W / 2 + self.BL_GUA_DX[-1] * sc + hw) + sh + 3
            y0, y1 = int(cy - hh) - sh - 2, int(cy + hh) + sh + 3
            m = frame[y0:y1, x0:x1, :3].min(axis=2).astype(np.float32)
            m -= cv2.GaussianBlur(m, (0, 0), max(1.0, 6.0 * sc))
            best = np.full(len(self.BL_GUA_DX), -1.0, np.float32)
            cls = [''] * len(self.BL_GUA_DX)
            for p, dx in enumerate(self.BL_GUA_DX):
                cx = W / 2 + dx * sc
                bx, by = int(round(cx - hw)) - x0, int(round(cy - hh)) - y0
                bw, bh = int(round(cx + hw)) - x0 - bx, int(round(cy + hh)) - y0 - by
                for sy in range(-sh, sh + 1):
                    for sx in range(-sh, sh + 1):
                        c = m[by + sy:by + sy + bh, bx + sx:bx + sx + bw]
                        v = cv2.resize(c, (16, 16), interpolation=cv2.INTER_AREA).ravel()
                        v -= v.mean()
                        n = float(np.linalg.norm(v))
                        if n < 1e-6:
                            continue
                        r = refs @ (v / n)
                        j = int(r.argmax())
                        if r[j] > best[p]:
                            best[p], cls[p] = r[j], kinds[j]
        except Exception as e:
            if not getattr(self, '_gua_err_logged', False):
                self._gua_err_logged = True
                self.logger.warning(f'bl_gua failed ({e}) — 卜灵卦象栏读不了, 改看外圈')
            return None
        taken = set()
        for p in np.argsort(-best):
            if best[p] < self.BL_GUA_MIN:
                break
            if p - 1 in taken or p + 1 in taken:
                continue
            taken.add(int(p))
        return ''.join(cls[p] for p in sorted(taken))

    def bl_gua_settled(self, cap, nonempty=False):
        """连 BL_GUA_CONFIRM 帧读数相同就返回它(nonempty: '' 不算); 到点没读稳返回最后一个读数(一次都没读到 None)。"""
        start = time.time()
        last, streak, seen = None, 0, None
        while time.time() - start < cap:
            self.task.next_frame()
            g = self.bl_gua()
            if g is not None and (g or not nonempty):
                seen = g
                streak = streak + 1 if g == last else 1
                last = g
                if streak >= self.BL_GUA_CONFIRM:
                    return g
            self.macro_wait(0.02)
        return seen

    @staticmethod
    def bl_gua_plan(gua, got, heavies_left, fixes_left):
        """手上卦象 gua -> 凑出一阴一阳(连同已出的 got)最省的步骤: 'H' = 一段重击(吃最新两个, 同字少阴/异字少阳),
        'G' = 补艮, 'Z' = 补震。凑不出返回 None。"""
        best = None

        def search(state, have, seq, hl, fl):
            nonlocal best
            if {'阴', '阳'} <= have:
                cost = sum(1.0 if x == 'G' else 1.2 for x in seq if x != 'H')
                if best is None or cost < best[0]:
                    best = (cost, seq)
                return
            if hl and len(state) >= 2:
                kind = '阳' if state[-1] != state[-2] else '阴'
                search(state[:-2], have | {kind}, seq + ['H'], hl - 1, fl)
            if fl:
                for step, g in (('G', '艮'), ('Z', '震')):
                    search((state + g)[-4:], have, seq + [step], hl, fl - 1)

        search(gua, set(got), [], heavies_left, fixes_left)
        return None if best is None else best[1]

    def bl_heavy_gua(self, tag, before, cap):
        """按住左键, 卦象栏比按下时少两个(这一段第40F结算)连 BL_GUA_CONFIRM 帧就松手。返回 (出没出, 松手后的卦象)。
        cap 到点还没少: 松手后再读一会儿, 少了也算出(结算正好卡在上限那几帧)。按住期间只读画面, 不发任何键。"""
        con0 = self.bl_con()
        self.task.mouse_down()
        self.holding_attack = True
        down = time.time()
        self.macro_mark(f'{tag}_down')
        hits = 0
        cur = before
        trace = []
        try:
            while time.time() - down < cap:
                self.task.next_frame()
                g = self.bl_gua()
                if len(trace) < 45:
                    trace.append(f'{time.time() - down:.2f}:{g if g is not None else "?"}')
                if g is not None and len(g) <= len(before) - 2:
                    hits += 1
                    cur = g
                    if hits >= self.BL_GUA_CONFIRM:
                        break
                else:
                    hits = 0
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        out = hits >= self.BL_GUA_CONFIRM
        if not out:
            g = self.bl_gua_settled(self.BL_HEAVY_VERIFY_CAP)
            if g is not None and len(g) <= len(before) - 2:
                out, cur = True, g
        self.macro_mark(f'{tag}_up({"出" if out else "没出"},{before}->{cur or "空"},协奏{con0:.2f}->{self.bl_con():.2f},'
                        f'{time.time() - down:.2f}s)')
        self.logger.info(f'{tag} 按住卦象 — {" ".join(trace)}')
        return out, cur

    def bl_gua_add(self, tag, step, before, cap):
        """补一个卦象: G = 点出 A1A2(A2 命中给艮), Z = 跳起来 A(下落落地命中给震)。
        卦象栏变了而且最新那个是要的字, 连 BL_GUA_CONFIRM 帧才算。返回 (成没成, 卦象)。"""
        want = '艮' if step == 'G' else '震'
        start = time.time()
        n = 0
        if step == 'Z':
            self.task.jump(after_sleep=0)
            self.macro_wait(self.BL_JUMP_TO_A)
            for i in range(self.BL_A_PRESSES):
                self.click()
                n += 1
                if i + 1 < self.BL_A_PRESSES:
                    self.macro_wait(self.BL_A_PRESS_GAP)
        next_click = 0.0
        hits = 0
        cur = before
        while time.time() - start < cap:
            now = time.time()
            if step == 'G' and now - start < self.BL1_G_CLICK_WINDOW and now >= next_click:
                self.click()
                n += 1
                next_click = now + self.BL1_A_EVERY
            self.task.next_frame()
            g = self.bl_gua()
            if g is not None and g != before and g.endswith(want):
                hits += 1
                cur = g
                if hits >= self.BL_GUA_CONFIRM:
                    break
            else:
                hits = 0
            self.macro_wait(0.02)
        ok = hits >= self.BL_GUA_CONFIRM
        self.macro_mark(f'{tag}({"成" if ok else "没成"},{before}->{cur or "空"},A x{n},{time.time() - start:.2f}s)')
        return ok, cur

    def bl1_heavies_gua(self, tag):
        """卜灵第一格两段强化重击, 按卦象栏走: 读全排 -> 凑一阴一阳最省的步骤 -> 做一步重读一次重排。
        返回 True = 一阴一阳都出了; False = 凑不出 / 协奏账不够 / 哪一步没出(调用方直接开 R);
        None = 卦象栏读不到(调用方改用外圈那套)。"""
        start = time.time()
        gua = self.bl_gua_settled(self.BL_HEAVY_VERIFY_CAP, nonempty=True)
        if not gua:
            self.macro_mark(f'{tag}卦象栏读不到({gua!r})->看外圈')
            return None
        got = []
        heavies = fixes = 0
        last_heavy_up = 0.0
        while True:
            if {'阴', '阳'} <= set(got):
                self.macro_mark(f'{tag}两段重击(齐,{"+".join(got)},补{fixes}次,{time.time() - start:.2f}s)')
                return True
            plan = self.bl_gua_plan(gua, got, 2 - heavies, self.BL1_FIX_MAX - fixes)
            con = self.bl_con()
            if plan is None:
                self.macro_mark(f'{tag}卦象{gua or "空"}凑不出(已出{"+".join(got) or "无"},协奏{con:.2f})')
                return False
            fix_con = sum(self.BL1_FIX_CON[x] for x in plan if x != 'H')
            pending = self.BL1_CON_PENDING if not (heavies or fixes) else 0.0
            total = con + pending + 0.15 * plan.count('H') + fix_con + 0.20
            self.macro_mark(f'{tag}卦象{gua}->{"".join(plan)}(协奏{con:.2f},R完约{total:.2f})')
            if fix_con > 0 and total > self.BL1_CON_BUDGET:
                self.logger.warning(f'{tag}: 卦象 {gua} 要走 {"".join(plan)}, 协奏 {con:.2f}, 补完 R 完约 {total:.2f} 会满 -> 不补, 直接开 R')
                return False
            step = plan[0]
            chained = bool(last_heavy_up)
            if chained:
                self.wait_until_time(last_heavy_up + self.BL_HEAVY_NEXT_GAP)
            if step == 'H':
                kind = '阳' if gua[-1] != gua[-2] else '阴'
                out, after = self.bl_heavy_gua(f'{tag}重击#{heavies + 1}({gua[-2:]}少{kind})', gua,
                                               self.BL_HEAVY_GUA_CAP[1 if chained else 0])
                heavies += 1
                if not out:
                    return False
                got.append(kind)
                last_heavy_up = time.time()
                gua = after or ''
            else:
                ok, after = self.bl_gua_add(f'{tag}补{"艮" if step == "G" else "震"}', step, gua, self.BL1_FIX_CAP[step])
                fixes += 1
                last_heavy_up = 0.0
                if not ok:
                    return False
                gua = after

    def bl_heavy_once(self, tag, cap, jump, min_settle):
        """按住左键, 协奏连 2 帧比基准涨 jump(这一段第40F结算)就松手; 没读到涨按满 cap。返回读到涨没有。
        按下后 min_settle 秒内读到的涨是前一个动作晚到的协奏, 不算结算, 并进基准。按住期间不发任何键, 只读协奏。"""
        con0 = self.bl_con()
        base = con0
        self.task.mouse_down()
        self.holding_attack = True
        down = time.time()
        self.macro_mark(f'{tag}_down')
        hits = 0
        con = con0
        trace = []
        try:
            while time.time() - down < cap:
                self.task.next_frame()
                con = self.bl_con()
                t = time.time() - down
                if len(trace) < 45:
                    # 排查用(纯文字): 按住期间每帧协奏 + 外圈, 对得出哪一帧结算、外圈按住时读成什么
                    trace.append(f'{t:.2f}:c{con:.2f}{(self.bl_ring()[0] or "-")[0]}')
                if t < min_settle:
                    base = max(base, con)
                    hits = 0
                else:
                    hits = hits + 1 if (base >= 0 and con - base >= jump) else 0
                if hits >= 2:
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        jumped = hits >= 2
        self.macro_mark(f'{tag}_up({"涨" if jumped else "CAP"},协奏{con0:.2f}->{con:.2f},{time.time() - down:.2f}s)')
        self.logger.info(f'{tag} 按住读数(基准{base:.2f}) — {" ".join(trace)}')
        return jumped

    def bl_ensure_r(self, bl, tag):
        """卜灵 R: R 和 A 错开帧一起连发, 队伍 HUD 连续消失 LIB_GONE_CONFIRM 帧 = 大招出来了。返回放出去没有。

        R 没转好的时候这段就在打怪攒能量, 转好的那一刻 R 打断普攻放出来; BL_R_CAP 封顶(打不到怪时防卡死)。
        这几下 A 攒出来的卦象由后面补协奏那段的长按重击清掉。
        """
        self.wait_hud_up(tag=f'{tag}R前HUD')
        start = time.time()
        self.lib_press_at = start
        nr = na = gone = 0
        next_r = next_a = 0.0
        fired = False
        while time.time() - start < self.BL_R_CAP:
            now = time.time()
            if now >= next_r:
                bl.send_liberation_key()
                nr += 1
                next_r = now + self.BL_R_KEY_INTERVAL
            elif now >= next_a:
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    fired = True
                    break
            else:
                gone = 0
            self.macro_wait(0.02)
        self.task.skip_combat_check = True
        if fired:
            bl.record_liberation_use()
            self.macro_mark(f'{tag}R_fired(R x{nr},A x{na},{time.time() - start:.2f}s)')
            return True
        self.macro_mark(f'{tag}R_NOFIRE(R x{nr},A x{na},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 卜灵 R 边 A 边按了 {time.time() - start:.1f}s 没放出来, 不等了')
        return False


    def bl_intro_wait(self, bl, tag):
        """万一卜灵是变奏进来的(上一个人协奏刚好满): QTE 第70F前不响应输入, 从到场起等满。"""
        if not bl.has_intro:
            return
        arrived = getattr(bl, 'last_switch_in_time', 0.0) or 0.0
        left = arrived + self.BL_INTRO_ANIM - time.time()
        if 0 < left <= self.BL_INTRO_ANIM:
            self.macro_mark(f'{tag}变奏入场(等{left:.2f}s)')
            self.macro_wait(left)

    def bl1_heavies(self, tag):
        """卜灵第一格两段强化重击, 只走正常流程: 外圈亮一种颜色 -> 按出去 -> 外圈换成另一种 -> 按出去(少阴 + 少阳)。
        返回 True = 两段都按出去了; False = 流程不对(外圈不亮/不是还差的颜色/一段没按出去), 调用方直接攒 R 开 R。
        【不换卦象、不跳A补震】(用户 10-06): 那些补救的普攻会把协奏攒满, R 完卜灵满协奏, 切穗穗就成了变奏。
        10-06 实机 10 局 7 局第一发蓝之后外圈直接灭(只剩一个卦象), 换卦象点了 18~58 下 A、2.3~7.7s。"""
        start = time.time()
        got = []
        chained = False
        next_down = 0.0
        color = self.bl_ring_settled(self.BL_HEAVY_VERIFY_CAP)
        for k in range(2):
            if color not in ('blue', 'yellow') or color in got:
                self.macro_mark(f'{tag}卦象不对(外圈{color or "不亮"},已出{"+".join(got) or "无"},{time.time() - start:.2f}s)')
                return False
            left = next_down - time.time()
            if left > 0:
                self.macro_wait(left)
            jumped = self.bl_heavy_once(f'{tag}重击#{k + 1}({color})',
                                        self.BL_HEAVY_CAP if chained else self.BL_HEAVY_FIRST_CAP,
                                        self.BL_HEAVY_CON_JUMP, self.BL_HEAVY_MIN_SETTLE)
            up = time.time()
            after = self.bl_ring_settled(self.BL_HEAVY_VERIFY_CAP)
            if not (jumped or after not in (color, 'unknown')):
                self.macro_mark(f'{tag}重击#{k + 1}({color})没出(圈->{after})')
                return False
            got.append(color)
            self.macro_mark(f'{tag}出{color}(圈->{after})')
            color = after
            chained = jumped
            next_down = up + self.BL_HEAVY_NEXT_GAP if jumped else 0.0
        self.macro_mark(f'{tag}两段重击(齐,{"+".join(got)},{time.time() - start:.2f}s)')
        return True

    def bl1_2a(self, tag, base):
        """第一格进场 2A。返回 True = 第一轮成了下落(已经给过震, 调用方不再跳A)。base = 进场时的卦象(没读过传 None, 用第一次读到的)。"""
        cur = base
        for attempt in range(2):
            if attempt:
                self.macro_wait(self.BL1_2A_REDO_DELAY)
            start = time.time()
            n = hits = 0
            next_click = 0.0
            last = got = None
            while time.time() - start < self.BL1_2A_WINDOW + self.BL1_2A_VERIFY:
                now = time.time()
                if now - start < self.BL1_2A_WINDOW and now >= next_click:
                    self.click()
                    n += 1
                    next_click = now + self.BL1_A_EVERY
                self.task.next_frame()
                g = self.bl_gua()
                if g is not None and cur is None:
                    cur = g
                if g and g != cur:
                    hits = hits + 1 if g == last else 1
                    last = g
                    if hits >= self.BL_GUA_CONFIRM:
                        got = g
                        if time.time() - start >= self.BL1_2A_WINDOW:
                            break
                self.macro_wait(0.02)
            if got and got.endswith('震') and attempt == 0:
                self.macro_mark(f'{tag}2A成了下落({cur or "空"}->{got},A x{n})->再打2A')
                cur = got
                continue
            self.macro_mark(f'{tag}2A({cur or "空"}->{got or "没变"},A x{n},{time.time() - start:.2f}s)')
            return attempt == 1
        return True

    def seg_bl1(self, su, tag, opener):
        """卜灵·第一格: 2A -> 跳A -> E -> A4 -> 两段强化重击(凑阴阳相生) -> R -> 演完 -> 切穗穗(协奏不满, 普通切人)。
        空中进场 2A 成了下落就再打一轮 2A、不跳A。两段重击按卦象栏凑一阴一阳, 补得起就补(协奏账), 补不起/哪一步没出就不管卦象了:
        直接攒 R 开 R 切穗穗, 协奏留到第二格补满变奏心。卦象栏读不到时退回看外圈那套。"""
        bl = self.cur
        self.bl_intro_wait(bl, tag)
        plunged = self.bl1_2a(tag, None)
        if plunged:
            self.macro_mark(f'{tag}下落给过震了(不跳A)')
        else:
            self.task.jump(after_sleep=0)
            jump_at = time.time()
            self.macro_mark(f'{tag}跳')
            self.macro_wait(self.BL_JUMP_TO_A)
            for i in range(self.BL_A_PRESSES):
                self.click()
                if i + 1 < self.BL_A_PRESSES:
                    self.macro_wait(self.BL_A_PRESS_GAP)
            self.macro_mark(f'{tag}跳A')
            self.wait_until_time(jump_at + self.BL1_JUMP_TO_E)
        if not self.press_res_until_cd(bl, self.BL_E_CAP, tag=f'{tag}E'):
            self.logger.info(f'{tag}: 卜灵 E 没判到进 CD')
        bl.record_resonance_use()
        self.macro_wait(self.BL_E_TO_A)
        self.task.next_frame()
        a4_blue = self.bl_a4_until_blue(tag, self.BL_A4_MAX_CLICKS, self.BL_BLUE_CAP, self.BL_A4_MIN_BLUE,
                                        gua_before=self.bl_gua())
        normal = self.bl1_heavies_gua(tag)
        if normal is None:
            normal = a4_blue and self.bl1_heavies(tag)
        self.bl1_fallback = not normal
        if not normal:
            self.macro_mark(f'{tag}流程被打断->直接开R(不管卦象)')
            self.logger.warning(f'{tag}: 卜灵卦象流程不对, 这一发 R 不凑阴阳相生(不开阵), 协奏 {self.bl_con():.2f}, 第二格再补满变奏心')
        if self.bl_ensure_r(bl, tag):
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.BL_R_ANIM_MIN, time_out=self.BL_R_ANIM_CAP)
        con = self.read_con(1.0, settle=0.10)
        self.macro_mark(f'{tag}切穗穗前协奏{con:.2f}')
        if con >= 1.0:
            self.logger.warning(f'{tag}: 卜灵 R 完协奏满了, 切穗穗会变奏(第二格要从头攒协奏)')
        self.need(self.switch_retry(su, f'{tag}切穗穗'), 'buling -> suisui')

    def bl2_fill_con(self, bl, tag, cap):
        """卜灵第二格协奏不满就补: 外圈亮(手上两个卦象)就按一段强化重击(协奏 +15, 顺手清卦象), 不亮平 A。
        连读几帧到满返回 True; cap 到点返回 False。"""
        start = time.time()
        hits = lit = na = nh = 0
        next_click = 0.0
        samples = []
        while time.time() - start < cap:
            self.task.next_frame()
            con = self.bl_con()
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            hits = hits + 1 if con >= self.CON_ENOUGH else 0
            if hits >= self.CON_FULL_CONFIRM:
                self.macro_mark(f'{tag}补协奏(满,A x{na},重击x{nh},{time.time() - start:.2f}s)')
                return True
            if hits:
                self.macro_wait(0.02)
                continue
            lit = lit + 1 if (self.hud_ok() and self.bl_ring()[0] and con < self.BL2_HEAVY_BELOW) else 0
            if lit >= self.BL_RING_CONFIRM:
                lit = 0
                nh += 1
                self.bl_heavy_once(f'{tag}补协奏重击#{nh}', self.BL_HEAVY_FIRST_CAP,
                                   self.BL_HEAVY_CON_JUMP, self.BL_HEAVY_MIN_SETTLE)
                continue
            now = time.time()
            if lit == 0 and now >= next_click:
                self.click()
                na += 1
                next_click = now + self.BL2_A_EVERY
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}补协奏(没满,A x{na},重击x{nh},{cap:.1f}s)')
        self.logger.info(f'{tag}: 卜灵协奏 — {" ".join(samples)}')
        return False

    def seg_bl2(self, tag, opener):
        """卜灵·第二格(心转相演完切进来): 2A -> 协奏满(外圈亮按重击, 不亮 A) -> [等翾舞] -> Q -> 马上变奏心。
        Q 放在最后紧跟着变奏: 补协奏、等翾舞都在 Q 之前(10-09 心第一段放开 5s 下限后, 启动轴原来的 2A -> Q 之后还要等翾舞 ~0.9s)。"""
        bl = self.cur
        self.bl_intro_wait(bl, tag)
        self.macro_spam(self.BL2_A_MIN, self.BL2_A_EVERY, tag=f'{tag}2A')
        q_last = True
        if not q_last:
            self.spam_key(bl.send_echo_key, self.BL_Q_WINDOW, tag=f'{tag}Q', presses=self.BL_Q_PRESSES)
            bl.record_echo_use()
        full = self.read_con(1.0, settle=0.15) >= 1.0 or self.bl2_fill_con(bl, f'{tag}', self.BL2_CON_CAP)
        if not full:
            self.logger.warning(f'{tag}: 卜灵协奏没满就切心 —— 心第二段不是变奏·照世相')
        # 第一次翾舞(心第一段到场后 ~6s)那一刻要卜灵在场拿烟岚前置, 之后变奏心才把烟岚给心: 不到点接着 A。
        # Q 算在等待里(Q 连发 BL_Q_WINDOW 秒), 按完正好到点切人
        q_win = self.BL_Q_WINDOW if q_last else 0.0
        game = self.xn_game_since_xn1()
        if self.xn1_t0 > 0 and game < self.BL2_LEAVE_MIN - q_win:
            self.macro_spam(self.BL2_LEAVE_MIN - q_win - game, self.BL2_A_EVERY, tag=f'{tag}等翾舞A')
        if q_last:
            self.spam_key(bl.send_echo_key, self.BL_Q_WINDOW, tag=f'{tag}Q(协奏满后)', presses=self.BL_Q_PRESSES)
            bl.record_echo_use()
        if self.xn1_t0 > 0:
            left = self.BL2_LEAVE_MIN - self.xn_game_since_xn1()
            if left > 0:
                self.macro_wait(left)
            self.macro_mark(f'{tag}变奏心 游戏时间{self.xn_game_since_xn1():.2f}s')
        self.need(self.switch_retry(self, f'{tag}切心', assume_con=full), 'buling -> hsin')

    # ==================== 心 ====================

    def xn_gauge(self):
        """应世心条 -> (左半, 右半) 有紫色的列占比。读当前帧, 调用方负责 next_frame。"""
        frame = self.task.frame
        out = []
        for x1, y1, x2, y2 in (self.XN_GAUGE_L, self.XN_GAUGE_R):
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, hcenter=True)
            crop = frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.size == 0:
                return 0.0, 0.0
            hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2HSV)
            h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            purple = (h >= 125) & (h <= 160) & (sat >= 70) & (v >= 90)
            out.append(float(purple.any(axis=0).mean()))
        return out[0], out[1]

    def xn_ying(self):
        """应世心 -> (满没满, 左半紫, 右半紫)。满 = 重击位的花图标 hsin_h1; 没满时条是紫色格子。读当前帧。"""
        try:
            full = bool(self.task.find_one('hsin_h1', threshold=self.XN_FULL_THRESHOLD))
        except Exception:
            full = False
        L, R = self.xn_gauge()
        return full, L, R

    def xn_icon(self, name):
        """心技能栏的图标模板(hsin_lib1 = 转相 / hsin_lib2 = 万阙垂天解锁)。读当前帧。"""
        try:
            return bool(self.task.find_one(name, threshold=0.7))
        except Exception:
            return False

    def xn_attack_down(self):
        return self.attack_down(self.XN_HOLD_SENDINPUT and self.game_foreground())

    def xn_hold_burn(self, tag):
        """按住重击 XN1_HOLD 秒, 期间看应世心条有没有被清空(= 步红尘出手)。

        返回 (状态, 清空时刻, 按下时刻)。状态:
          'burned'  清空了(时刻可能是 None: 按住期间没读到那一帧, 松手后读到是空的)
          'full'    松手后条还在 = 出的是普通重击(条差太多/被打断) -> 调用方补 A 再按
          'unknown' 条一次都没读出来, 不知道 -> 不重按(重按落在步红尘演出里会再起一个重击)
        """
        si = self.xn_attack_down()
        start = time.time()
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        seen_full = False
        burned_at = None
        low_at = None
        trace = []
        try:
            while time.time() - start < self.XN1_HOLD:
                self.task.next_frame()
                if self.hud_ok() and burned_at is None:
                    now = time.time()
                    full, L, R = self.xn_ying()
                    if len(trace) < 30:
                        trace.append(f'{now - start:.2f}:{"满" if full else ""}{L:.2f}/{R:.2f}')
                    if full or L >= self.XN_GAUGE_VALID:
                        seen_full = True
                        low_at = None
                    elif seen_full and L < self.XN_CONSUMED:
                        # 连着两帧空才算(特效闪一下会压暗一帧); 时刻记第一帧 —— 转相解锁从这一刻算
                        if low_at is None:
                            low_at = now
                        else:
                            burned_at = low_at
                            self.macro_mark(f'{tag}步红尘出手({burned_at - start:.2f}s)')
                            if self.XN1_R1_SPAM:
                                break           # 步红尘免疫打断, 出手了就松开去连按 R
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        if burned_at is not None:
            self.macro_mark(f'{tag}松开')
            return 'burned', burned_at, start
        # 按住期间没读到清空: 松手后再读两帧, 条还在 = 真没出步红尘
        full = empty = 0
        for _ in range(6):
            self.task.next_frame()
            if self.hud_ok():
                is_full, L, R = self.xn_ying()
                if is_full or L >= self.XN_GAUGE_VALID:
                    full += 1
                    seen_full = True
                elif seen_full and L < self.XN_CONSUMED:
                    empty += 1
                if full >= 2 or empty >= 2:
                    break
            self.macro_wait(0.02)
        state = 'full' if full >= 2 else ('burned' if empty >= 2 else 'unknown')
        self.macro_mark(f'{tag}松开({state})')
        self.logger.info(f'{tag}: 按住 {self.XN1_HOLD:.1f}s 没读到应世心清空 -> {state} — {" ".join(trace)}')
        return state, None, start

    def xn_r1_ready(self):
        """转相能不能按: R 图标是转相(hsin_lib1)而且亮着。读当前帧。

        不查 CD(check_cd=False): 那一路每换一帧就 OCR 一次技能栏, 解锁那 0.3s 的窗口里读不了几次; 转相也没有 CD 数字。
        """
        try:
            lit = bool(self.available('liberation', check_color=True, check_cd=False))
        except Exception:
            lit = False
        return lit and self.xn_icon('hsin_lib1')

    def xn_r1_rescue(self, xn, tag):
        """转相第一次没放出去: 补到放出去为止(上限 XN1_R1_RESCUE_CAP)。返回放出去没有。

        每一轮看现在卡在哪:
          转相图标亮着(连两帧)       -> 按 R(被吞了就再来)
          应世心是满的(花图标 / 右半到线) -> 再按住出步红尘, 清空后等解锁
          都不是                     -> 普攻攒应世心
          HUD 不在(被击飞/演出)       -> 等它回来
        一直既看不到转相图标也读不到应世心条 = 已经进照世相了(转相其实出了只是没看到演出), 不再按 R ——
        照世相里按 R 放的是借相。
        """
        start = time.time()
        rounds = 0
        lost = 0
        self.macro_mark(f'{tag}转相补救开始')
        while time.time() - start < self.XN1_R1_RESCUE_CAP:
            rounds += 1
            self.task.next_frame()
            if not self.hud_ok():
                self.wait_hud_up(cap=1.0, tag=f'{tag}转相补救等HUD')
                continue
            lit = 0
            for _ in range(self.XN1_R1_READS):
                if self.xn_r1_ready():
                    lit += 1
                self.task.next_frame()
            if lit >= self.XN1_R1_READS:
                lost = 0
                if self.fire_r(xn, f'{tag}转相补#{rounds}', self.XN_R_WINDOW):
                    self.macro_mark(f'{tag}转相补救成功({time.time() - start:.2f}s)')
                    return True
                continue
            full, L, R = self.xn_ying()
            if full or L >= self.XN_GAUGE_VALID or self.xn_icon('hsin_lib1'):
                lost = 0
            else:
                lost += 1
                if lost >= self.XN1_R1_LOST_ROUNDS:
                    self.macro_mark(f'{tag}转相补救: 已不在应世相(当转相出过了)')
                    return True
            if full or (L >= self.XN_GAUGE_VALID and R >= self.XN_YING_READY):
                state, burned_at, _hold_at = self.xn_hold_burn(f'{tag}补步红尘#{rounds}')
                if state == 'burned':
                    # 清空后 2.06s 解锁: 等到点(期间亮了就提前走), 下一轮去按
                    anchor = burned_at if burned_at is not None else time.time() - 0.3
                    due = anchor + self.XN1_R1_UNLOCK + self.XN1_R1_MARGIN
                    while time.time() < due:
                        self.task.next_frame()
                        if self.hud_ok() and self.xn_r1_ready():
                            break
                        self.macro_wait(0.01)
                continue
            self.macro_spam(self.XN1_R1_RESCUE_A, self.XN1_FILL_EVERY, tag=f'{tag}补应世心A#{rounds}')
        self.macro_mark(f'{tag}转相补救(cap,{rounds}轮)')
        return False

    def xn_hold_until_gone(self, cap, tag):
        """按住左键到队伍 HUD 消失(临寰宇出手)。消失后再按 XN2_RELEASE_AFTER_GONE 秒松手。返回出手没有。"""
        si = self.xn_attack_down()
        start = time.time()
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        gone = 0
        fired = False
        try:
            while time.time() - start < cap:
                self.task.next_frame()
                if not self.hud_ok():
                    gone += 1
                    if gone >= self.LIB_GONE_CONFIRM:
                        fired = True
                        break
                else:
                    gone = 0
                self.macro_wait(0.02)
            if fired:
                self.macro_mark(f'{tag}临寰宇出手({time.time() - start:.2f}s)')
                self.macro_wait(self.XN2_RELEASE_AFTER_GONE)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}松开({"出手" if fired else "没出"})')
        return fired

    def xn_pre_r2_con(self, tag):
        """万阙垂天之前: 协奏低于 XN2_PRE_R2_CON 就照世相普攻补(人还贴着怪), 每下之后读一次。"""
        start = time.time()
        con = self.read_con(1.0, settle=0.12)
        if con >= self.XN2_PRE_R2_CON:
            self.macro_mark(f'{tag}开大前协奏{con:.2f}')
            return
        samples = [f'0:{con:.2f}']
        taps = 0
        while time.time() - start < self.XN2_PRE_R2_CAP:
            self.tap()
            taps += 1
            self.macro_wait(max(0.0, self.A_INTERVAL - self.A_TAP))
            con = self.read_con(1.0, settle=0.03)
            if len(samples) < 20:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= self.XN2_PRE_R2_CON:
                break
        self.macro_mark(f'{tag}开大前补协奏({con:.2f},A x{taps},{time.time() - start:.2f}s)')
        if con < self.XN2_PRE_R2_CON:
            self.logger.info(f'{tag}: 开大前协奏补不到 {self.XN2_PRE_R2_CON:.2f} — {" ".join(samples)}')


    def xn_slot_glyph(self):
        """重击位 16x16 灰度(只取圆内像素), 已归一化。心不在场/HUD 不在返回 None。读当前帧。"""
        in_team, index, _count = self.task.in_team()
        if not in_team or index != self.index:
            return None
        self.hud_seen_at = time.time()
        ref = getattr(self, '_slot_ref', None)
        if ref is None:
            yy, xx = np.mgrid[:96, :96]
            disk = ((((xx - 48) ** 2 + (yy - 44) ** 2) <= 25 ** 2)).astype(np.float32)
            mask = cv2.resize(disk[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA) > 0.99

            def tpl(chunks):
                raw = base64.b64decode(''.join(chunks))
                return self.glyph_vec(np.frombuffer(raw, np.uint8).reshape(16, 16).astype(np.float32)[mask])

            ref = self._slot_ref = (mask, disk, tpl(self.XN_TY_GLYPH), tpl(self.XN_READY_GLYPH))
        mask, disk, _ty, _rd = ref
        x, y, w, h = self.XN_TY_SLOT
        box = self.task.box_of_screen_scaled(3840, 2160, x, y, x + w, y + h, hcenter=True)
        frame = self.task.frame
        cx, cy = int(box.x + box.width / 2), int(box.y + box.height / 2)
        half = int(box.height * 1.1)
        icon = frame[max(0, cy - half):cy + half, max(0, cx - half):cx + half, :3]
        g = cv2.cvtColor(cv2.resize(icon, (96, 96), interpolation=cv2.INTER_AREA),
                         cv2.COLOR_BGR2GRAY).astype(np.float32) * disk
        v = cv2.resize(g[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA)
        return self.glyph_vec(v[mask])

    def xn_tongyu(self):
        """在不在统御众机: 重击位是雕像图标。读当前帧; 心不在场/HUD 不在返回 None。"""
        try:
            v = self.xn_slot_glyph()
            if v is None:
                return None
            self._ty_score = float(v @ self._slot_ref[2])
            return self._ty_score >= self.XN_TY_MIN
        except Exception as e:
            self.logger.info(f'xn_tongyu probe failed ({e})')
            return None

    def xn_slot_state(self):
        """统御众机里重击位: 'statue'(照世心还没打空) / 'ready'(人像, 临寰宇可按) / '?' / None(HUD 不在)。读当前帧。"""
        try:
            v = self.xn_slot_glyph()
            if v is None:
                return None
            _mask, _disk, ty, rd = self._slot_ref
            self._ty_score = float(v @ ty)
            self._rd_score = float(v @ rd)
            if self._rd_score >= self.XN_READY_MIN:
                return 'ready'
            if self._ty_score >= self.XN_TY_MIN:
                return 'statue'
            return '?'
        except Exception as e:
            self.logger.info(f'xn_slot_state probe failed ({e})')
            return '?'

    def xn_form(self, tag):
        """心现在是哪个相: 'ying'(转相图标 hsin_lib1 / 应世心花 / 应世心条读得出) / 'zhao'(HUD 在场读够帧数, 三样都不是) /
        '?'(HUD 一直不在, 当应世相处理)。最多读 XN_FORM_CAP 秒。"""
        start = time.time()
        live = 0
        while time.time() - start < self.XN_FORM_CAP:
            self.task.next_frame()
            if self.hud_ok():
                live += 1
                full, L, _R = self.xn_ying()
                if full or L >= self.XN_GAUGE_VALID or self.xn_icon('hsin_lib1'):
                    return 'ying'
                if live >= self.XN_FORM_READS:
                    self.macro_mark(f'{tag}在照世相({time.time() - start:.2f}s)')
                    return 'zhao'
            self.macro_wait(0.02)
        return '?'

    def xn1_fill(self, arrived, tag):
        """心第一段: 一直点普攻, 应世心满(花图标 / 右半到线, 连读几帧)并且过了 XN1_HOLD_AT_MIN 就返回。
        返回原因: 'ready' / 'cap'(到上限还没满, 照按) / 'blind'(条一次都没读出来, 照按)。"""
        self.wait_until_time(arrived + self.XN1_A_FROM)
        hits = taps = 0
        next_tap = last_tap = 0.0
        ready_at = None
        valid_seen = False
        trace = []
        why = 'cap'
        while True:
            now = time.time()
            if now >= next_tap:
                self.tap(self.XN1_A_HOLD)
                taps += 1
                last_tap = time.time()
                next_tap = last_tap + max(0.0, self.XN1_A_EVERY - self.XN1_A_HOLD)
            self.task.next_frame()
            now = time.time()
            if self.hud_ok():
                full, L, R = self.xn_ying()
                if len(trace) < 30:
                    trace.append(f'{now - arrived:.2f}:{"满" if full else ""}{L:.2f}/{R:.2f}')
                if full or L >= self.XN_GAUGE_VALID:
                    valid_seen = True
                hits = hits + 1 if (full or (L >= self.XN_GAUGE_VALID and R >= self.XN_YING_READY)) else 0
                if hits >= self.XN1_GATE_CONFIRM and ready_at is None:
                    ready_at = now
                    self.macro_mark(f'{tag}应世心满({"花" if full else f"{R:.2f}"},A x{taps},{now - arrived:.2f}s)')
            if ready_at is not None and now >= arrived + self.XN1_HOLD_AT_MIN:
                why = 'ready'
                break
            if now >= arrived + self.XN1_A_CAP:
                why = 'cap' if valid_seen else 'blind'
                break
            self.macro_wait(0.02)
        if why != 'ready':
            self.macro_mark(f'{tag}应世心{"不够" if why == "cap" else "读不到"}(照按,A x{taps})')
            self.logger.info(f'{tag}: 应世心 {why} — {" ".join(trace)}')
        left = last_tap + self.XN1_LAST_A_GAP - time.time()
        if left > 0:
            self.macro_wait(left)
        return why

    def seg_xn1(self, bl, tag):
        """心·第一段(穗穗变奏进来, 应世相): Q -> A 到应世心满 -> 按住出步红尘 -> 转相解锁 -> 转相 -> 演完 -> 切卜灵。"""
        arrived = getattr(self, 'last_switch_in_time', 0.0) or time.time()
        self.xn1_t0 = arrived
        self.xn1_pause = 0.0
        if not self.has_intro:
            self.macro_mark(f'{tag}心不是变奏进来的(应世心少 60)')
        self.wait_until_time(arrived + self.XN1_Q_AT)
        self.spam_key(self.send_echo_key, self.XN1_Q_WINDOW, tag=f'{tag}Q', presses=2)
        self.record_echo_use()
        if self.xn_form(tag) == 'zhao':
            # 上一圈第二段没放出万阙垂天, 心还在照世相: 这一段没有步红尘/转相可打, 按 R 是借相(切回应世相)。
            # 普攻一会儿就切卜灵, 第二段照常走照世相那套(会把万阙垂天补上)
            self.macro_spam(self.XN1_ZHAO_A, self.XN1_A_EVERY, tag=f'{tag}照世相A')
            self.logger.warning(f'{tag}: 心上场还在照世相(上一圈万阙垂天没放出来), 第一段跳过步红尘/转相')
            self.need(self.switch_retry(bl, f'{tag}切卜灵'), 'hsin -> buling')
            return
        self.xn1_fill(arrived, tag)

        state = 'unknown'
        hold_at = burned_at = None
        for attempt in range(1 + self.XN1_RETRIES):
            if attempt > 0:
                # 上一次松手后条还在 = 出的是普通重击: 补两下 A 再按
                for _ in range(self.XN1_RETRY_A):
                    self.tap(self.XN1_A_HOLD)
                    self.macro_wait(self.XN1_FILL_EVERY)
                self.macro_mark(f'{tag}重击重按#{attempt}')
            state, burned_at, hold_at = self.xn_hold_burn(f'{tag}重击' if attempt == 0 else f'{tag}重击#{attempt}')
            if state != 'full':
                break
        burned = state == 'burned'
        if state == 'full':
            self.logger.warning(f'{tag}: 重按 {self.XN1_RETRIES} 次应世心都没清空(步红尘没出), 转相多半放不出来')

        if burned_at is not None and self.XN1_R1_SPAM:
            # 清空那一刻起连按 R, 窗口盖住解锁(清空后 2.06s)再加一个正常的 R 窗口
            window = burned_at + self.XN1_R1_UNLOCK + self.XN1_R1_MARGIN + self.XN_R_WINDOW - time.time()
            fired = self.fire_r(self, f'{tag}转相连按', max(window, self.XN_R_WINDOW))
            if fired:
                self.macro_mark(f'{tag}转相出手(清空后{time.time() - burned_at:.2f}s, 含确认 {self.LIB_GONE_CONFIRM} 帧)')
        else:
            if burned_at is not None:
                # 锚点 + 固定时长: 清空后 2.06s 解锁。期间读到一帧亮就提前走
                self.wait_until_time(burned_at + self.XN1_R1_POLL_FROM)
                due = burned_at + self.XN1_R1_UNLOCK + self.XN1_R1_MARGIN
                why = '到点'
                while time.time() < due:
                    self.task.next_frame()
                    if self.hud_ok() and self.xn_r1_ready():
                        why = '亮'
                        break
                    self.macro_wait(0.01)
                self.macro_mark(f'{tag}转相解锁({why},清空后{time.time() - burned_at:.2f}s)')
            else:
                # 没看到清空: 读图标, 连着亮几帧才算; 读不到就到这一刻照按(没解锁时按 R 什么都不发生)
                cap_at = hold_at + self.XN1_R1_LIT_CAP
                blind_at = hold_at + self.XN1_R1_BLIND_AT
                hits = 0
                lit = False
                while time.time() < cap_at:
                    self.task.next_frame()
                    if self.hud_ok():
                        hits = hits + 1 if self.xn_r1_ready() else 0
                        if hits >= self.XN1_R1_READS:
                            lit = True
                            break
                    if not burned and time.time() >= blind_at:
                        break
                    self.macro_wait(0.02)
                self.macro_mark(f'{tag}转相{"亮" if lit else "没读到亮"}(没看到清空)')
            # 转相按早了卜灵会赶在翾舞之前就走: 不到 XN1_R1_AT 接着打普攻
            r1_at = arrived + self.XN1_R1_AT
            if not self.XN1_R1_SPAM and time.time() < r1_at - self.XN1_R1_TAP_GAP:
                self.macro_spam(r1_at - self.XN1_R1_TAP_GAP - time.time(), self.XN1_A_EVERY, tag=f'{tag}等转相时刻A')
                self.wait_until_time(r1_at)
            fired = self.fire_r(self, f'{tag}转相', self.XN_R_WINDOW)
        if not fired:
            fired = self.xn_r1_rescue(self, tag)
        fire_t = back_t = time.time()
        if not fired:
            self.logger.warning(f'{tag}: 补了 {self.XN1_R1_RESCUE_CAP:.0f}s 转相还是没放出去, 照常切卜灵(第二段不在照世相)')
        else:
            self.wait_hud_back(tag=f'{tag}转相演出', min_gone=self.XN_R_ANIM_MIN, time_out=self.XN_R_ANIM_CAP)
            back_t = time.time()
        # 游戏时间(转相演出那段时钟是停的)
        self.xn1_pause = back_t - fire_t
        game = self.xn_game_since_xn1()
        if game < self.XN1_BL_MIN:
            # 站着等, 不点普攻: 照世相普攻会离地, 切人落在离地那一下卜灵就是空中进场, 第二格的 2A 成了下落(给震不给艮)
            # (10-09 4K DVR L1: 演完补一下普攻 -> 卜灵在怪头顶出来, 落地给震)
            wait = self.XN1_BL_MIN - game
            self.macro_wait(wait)
            self.macro_mark(f'{tag}等切卜灵时刻(站着{wait:.2f}s)')
        game = self.xn_game_since_xn1()
        self.macro_mark(f'{tag}心到场->切卜灵 游戏时间{game:.2f}s')
        if game > self.XN1_BL_MAX:
            self.logger.warning(f'{tag}: 心到场到切卜灵游戏时间 {game:.2f}s > {self.XN1_BL_MAX:.1f}s, 卜灵多半赶不上第一次翾舞(烟岚给不到心第二段)')
        self.need(self.switch_retry(bl, f'{tag}切卜灵'), 'hsin -> buling')

    def xn_game_since_xn1(self):
        """从心第一段到场起的游戏时间(扣掉转相演出, 那段游戏时钟是停的)。"""
        return time.time() - self.xn1_t0 - self.xn1_pause

    def xn_break(self, tag):
        """统御众机里处决: 只按 F(最多 XN_BREAK_PRESSES 下, 提示没了就停), 从第一下起等满 XN_BREAK_ANIM。返回花了多久。
        不走框架的 task.f_break(): 那个会连点左键、至少卡 0.5s。"""
        start = time.time()
        presses = 0
        while presses < self.XN_BREAK_PRESSES:
            self.task.send_key('f', down_time=0.05)
            presses += 1
            self.macro_wait(0.10)
            self.task.next_frame()
            if not self.break_prompt():
                break
        try:
            self.task.can_break = False
        except Exception:
            pass
        self.macro_wait(max(0.0, start + self.XN_BREAK_ANIM - time.time()))
        self.macro_mark(f'{tag}处决(F x{presses},{time.time() - start:.2f}s)')
        return time.time() - start

    def xn_lianheng(self, tag):
        """E(照世相)之后连按 E: 照世心满 300 的那一刻 E 是万阙连衡, 按出去进统御众机。返回进没进。
        没满时 E 在 CD 上, 按了是空操作。没进去就 普攻攒照世心 + 按 E 交替, 上限 XN2_RESCUE_CAP。"""
        start = time.time()
        n = hits = 0
        next_e = 0.0
        scores = []
        while time.time() - start < self.XN2_LIANHENG_CAP:
            now = time.time()
            if now >= next_e:
                self.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                n += 1
                next_e = time.time() + self.XN2_E_MASH
            self.task.next_frame()
            ty = self.xn_tongyu()
            if len(scores) < 30:
                scores.append(f'{time.time() - start:.2f}:{getattr(self, "_ty_score", -1):.2f}')
            hits = hits + 1 if ty else 0
            if hits >= self.XN2_TY_CONFIRM:
                self.macro_mark(f'{tag}进统御众机(E x{n},{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}强化E没进统御众机(E x{n},{self.XN2_LIANHENG_CAP:.1f}s)')
        self.logger.info(f'{tag}: 雕像图标读数 — {" ".join(scores)}')
        r0 = time.time()
        rounds = 0
        while time.time() - r0 < self.XN2_RESCUE_CAP:
            rounds += 1
            self.macro_spam(self.XN2_RESCUE_A, self.XN2_A_EVERY, tag=f'{tag}攒照世心A#{rounds}')
            end = time.time() + self.XN2_RESCUE_E
            hits = 0
            while time.time() < end:
                self.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                self.macro_wait(0.05)
                self.task.next_frame()
                hits = hits + 1 if self.xn_tongyu() else 0
                if hits >= self.XN2_TY_CONFIRM:
                    self.macro_mark(f'{tag}补进统御众机(#{rounds},{time.time() - start:.2f}s)')
                    return True
                self.macro_wait(max(0.0, self.XN2_E_MASH - 0.05))
        self.macro_mark(f'{tag}统御众机补救(cap,{rounds}轮)')
        return False

    def xn_statue_reset(self):
        self._statue_streak = [0] * len(self.XN_STATUE_XS)
        self._statue_latch = [False] * len(self.XN_STATUE_XS)

    def xn_statue_count(self):
        """照世心雕像从右往左连续翻了几颗(翻过的记住, 每次进统御众机先 xn_statue_reset)。读当前帧。"""
        frame = self.task.frame
        hw = self.XN_STATUE_HW
        for k, x in enumerate(self.XN_STATUE_XS):
            if self._statue_latch[k]:
                continue
            red = glow = 0.0
            for y, kind in ((self.XN_STATUE_Y, 'red'), (self.XN_STATUE_UP_Y, 'glow')):
                box = self.task.box_of_screen_scaled(3840, 2160, x - hw, y - hw, x + hw, y + hw, hcenter=True)
                crop = frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
                if crop.size == 0:
                    continue
                hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2HSV)
                h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
                if kind == 'red':
                    red = float((((h < 12) | (h > 160)) & (sat > 90) & (v > 110)).mean())
                else:
                    glow = float(((v > 235) & (sat < 60)).mean())
            hit = red >= self.XN_STATUE_RED or glow >= self.XN_STATUE_GLOW
            self._statue_streak[k] = self._statue_streak[k] + 1 if hit else 0
            if self._statue_streak[k] >= self.XN_STATUE_CONFIRM:
                self._statue_latch[k] = True
        n = 0
        for latched in reversed(self._statue_latch):
            if not latched:
                break
            n += 1
        return n

    def xn_tongyu_phase(self, tag, cap, dodge_at=None):
        """统御众机里一直普攻到重击位换成人像(照世心打空)。dodge_at: 给了就闪避一次(None = 不闪)。
        【闪避的时机 = 照世心雕像翻满 XN2_TY_DODGE_COUNT 颗】(不早于 XN2_TY_DODGE_MIN, 数不到也不晚于 XN2_TY_DODGE_CAP)。
        怪身上出了处决提示就处决: 出在闪避之前就等到闪避那一刻再处决(代替闪避), 之后出的马上处决(只处决一次)。
        返回 'ready'(人像连读几帧) / 'cap'(到上限没看到人像)。"""
        start = time.time()
        n = 0
        next_click = 0.0
        dodged = dodge_at is None
        ready_hits = brk_hits = 0
        prompt_at = None
        count = last_count = 0
        counts = []
        self.xn_statue_reset()
        why = 'cap'
        last = None
        while time.time() - start < cap:
            now = time.time()
            if now >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + max(0.0, self.XN2_TY_A_EVERY - self.A_TAP)
            self.task.next_frame()
            st = self.xn_slot_state()
            last = st
            ready_hits = ready_hits + 1 if st == 'ready' else 0
            if ready_hits >= self.XN2_READY_CONFIRM:
                why = 'ready'
                break
            el = time.time() - start
            if st is not None:
                try:
                    count = self.xn_statue_count()
                except Exception as e:
                    self.logger.info(f'xn_statue_count failed ({e})')
                if count != last_count:
                    last_count = count
                    if len(counts) < 12:
                        counts.append(f'{count}@{el:.2f}')
            prompt = False
            if self.XN_BREAK and not self.xn_broke and st is not None and el >= self.XN_BREAK_FROM:
                brk_hits = brk_hits + 1 if self.break_prompt() else 0
                prompt = brk_hits >= self.XN_BREAK_CONFIRM
                if prompt and prompt_at is None:
                    prompt_at = time.time()
                    self.macro_mark(f'{tag}处决提示({el:.2f}s,翻{count})')
            if dodged and prompt:
                # 闪避之后才出的提示: 马上处决
                self.xn_broke = True
                self.macro_mark(f'{tag}统御众机A(x{n},{el:.2f}s,翻{count})')
                self.xn_break(tag)
                continue
            if not dodged and el >= self.XN2_TY_DODGE_MIN and (count >= self.XN2_TY_DODGE_COUNT or el >= self.XN2_TY_DODGE_CAP):
                dodged = True
                self.macro_mark(f'{tag}统御众机A(x{n},{el:.2f}s,翻{count}{"" if count >= self.XN2_TY_DODGE_COUNT else ",数不到撞上限"})')
                # 之前见过处决提示: 这一刻再确认一下(提示读数会闪), 还在就处决代替闪避
                use_break = False
                if self.XN_BREAK and not self.xn_broke and prompt_at is not None and \
                        time.time() - prompt_at <= self.XN2_TY_PROMPT_KEEP:
                    use_break = prompt or self.break_prompt_reads(3)
                if use_break:
                    self.xn_broke = True
                    self.xn_break(tag)
                else:
                    self.dodge(tag)
                    self.macro_wait(self.XN2_DODGE_TO_A)
                continue
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}照世心{"打空" if why == "ready" else "没看到打空(cap)"}(A x{n},{time.time() - start:.2f}s)')
        if counts:
            self.logger.info(f'{tag}: 照世心雕像翻面(颗@秒) — {" ".join(counts)}')
        if why != 'ready':
            self.logger.info(f'{tag}: {cap:.1f}s 没看到重击位换人像(最后读成 {last}, '
                             f'雕像{getattr(self, "_ty_score", -1):.2f} 人像{getattr(self, "_rd_score", -1):.2f})')
        return why

    def seg_xn2(self, su, tag):
        """心·第二段(卜灵变奏进来, 照世相): A -> E -> 连按 E 进统御众机 -> A -> 闪 -> 2A -> 按住到临寰宇
        -> 万阙垂天(认 hsin_lib2) -> 演完 -> 协奏满 -> 变奏穗穗。"""
        arrived = getattr(self, 'last_switch_in_time', 0.0) or time.time()
        if not self.has_intro:
            self.macro_mark(f'{tag}心不是变奏进来的')
        self.wait_until_time(arrived + self.XN2_A_FROM)
        self.macro_spam(max(0.0, arrived + self.XN2_E_AT - time.time()), self.XN2_A_EVERY, tag=f'{tag}A')
        if not self.press_res_until_cd(self, self.XN2_E1_CAP, tag=f'{tag}E'):
            self.logger.info(f'{tag}: 照世相 E 没判到进 CD')
        self.record_resonance_use()
        in_ty = self.xn_lianheng(tag)
        self.xn_broke = False
        if in_ty:
            why = self.xn_tongyu_phase(tag, self.XN2_TY_CAP, dodge_at=True)
            fired = self.xn_hold_until_gone(self.XN2_HOLD_CAP if why == 'ready' else self.XN2_BLIND_HOLD, f'{tag}按住')
            for attempt in range(1, 1 + self.XN2_RETRIES):
                if fired:
                    break
                # 按住没出(被打断 / 人像是误读): 普攻到人像再按
                why = self.xn_tongyu_phase(f'{tag}补#{attempt}', self.XN2_RETRY_CAP)
                fired = self.xn_hold_until_gone(self.XN2_HOLD_CAP if why == 'ready' else self.XN2_BLIND_HOLD,
                                                f'{tag}重按#{attempt}')
        else:
            # 没认出统御众机: 不在统御众机里临寰宇出不来, 只按一次试探(万一是雕像图标没认出来), 不重按
            fired = self.xn_hold_until_gone(self.XN2_BLIND_HOLD, f'{tag}按住(试探)')
        if not fired:
            self.logger.warning(f'{tag}: 临寰宇一直没出手(统御众机{"进了" if in_ty else "没进"}), '
                                f'这一轮不放万阙垂天(按 R 会放成借相)')
        else:
            self.wait_hud_back(tag=f'{tag}临寰宇演出', min_gone=self.XN2_ANIM_MIN, time_out=self.XN2_ANIM_CAP)
            # 【R 只认 hsin_lib2】: 照世相里 R 一直亮(借相·应世), 只有临寰宇之后才换成万阙垂天的图标; 认不出就不按
            start = time.time()
            hits = 0
            unlocked = False
            while time.time() - start < self.XN2_LIB2_CAP:
                self.task.next_frame()
                if self.hud_ok():
                    hits = hits + 1 if self.xn_icon('hsin_lib2') else 0
                    if hits >= self.XN2_LIB2_READS:
                        unlocked = True
                        break
                self.macro_wait(0.02)
            self.macro_mark(f'{tag}万阙垂天{"解锁" if unlocked else "图标没出来(不按, 免得放成借相)"}'
                            f'({time.time() - start:.2f}s)')
            if unlocked:
                self.xn_pre_r2_con(tag)
                if self.fire_r(self, f'{tag}万阙垂天', self.XN_R_WINDOW):
                    self.wait_hud_back(tag=f'{tag}万阙垂天演出', min_gone=self.XN_R_ANIM_MIN,
                                       time_out=self.XN_R_ANIM_CAP)
                else:
                    self.logger.warning(f'{tag}: 万阙垂天没放出去(能量不够?)')
            else:
                self.logger.warning(f'{tag}: 临寰宇之后 {self.XN2_LIB2_CAP:.1f}s 没认出万阙垂天图标, 这一轮不放'
                                    f'(心留在照世相, 下一圈第一段会跳过转相)')
        full = self.read_con(1.0, settle=0.20) >= 1.0 or self.wait_con_full('心', self.XN2_CON_CAP, tag=f'{tag}协奏')
        if not full:
            self.logger.warning(f'{tag}: 心协奏没满就切穗穗 —— 穗穗不是变奏进来的, 下一圈第一格走启动格(3A 醒春潮)')
        self.wait_next_wave(tag)
        self.need(self.switch_retry(su, f'{tag}切穗穗', assume_con=full), 'hsin -> suisui')

    def target_visible(self):
        """锁定目标在不在。照 CombatCheck.has_target() 的前三个读框, 去掉它最后那个兜底分支 ——
        那个分支会 sleep(1) 再连按两次 ESC, 不能在轴里调(同绯洛穗 Hiyuki.target_visible)。"""
        t = self.task
        try:
            has_name, no_name = t.get_target_names()
            for box in (t.get_box_by_name(has_name).scale(1.1), t.get_box_by_name('box_target_enemy_long'),
                        t.get_box_by_name('target_box_long2')):
                best = t.find_best_match_in_box(box, [has_name, no_name], threshold=0.6)
                if best:
                    return best.name == has_name
        except Exception as e:
            # 别的 ok-ww 版本这几个读框/方法可能改名了: 当成有怪, 退回"不等下一波、直接切"
            if not getattr(self, '_target_visible_warned', False):
                self._target_visible_warned = True
                self.logger.warning(f'xbs target_visible unsupported in this ok-ww version ({e!r}), assuming a target is there')
            return True
        return False

    def wait_next_wave(self, tag):
        """【没怪先别切】: 心变奏穗穗之前读锁定目标, 连 TARGET_GONE_CONFIRM 次读不到 = 这一波被万阙垂天打死了。
        这时变奏进去穗穗的变奏技能打空, 协奏一点不回(~47), 后面回路满了开大协奏也不满, 心没有变奏(10-08 用户; 同绯洛穗)。
        等新怪刷出来再切: 不点普攻(没怪空挥), 每 WAVE_LOCK_INTERVAL 按中键锁目标, 上限 WAVE_WAIT(整场打完了就照切)。"""
        start = time.time()
        gone = 0
        next_mid = 0.0
        waited = False
        while True:
            self.task.next_frame()
            self.hud_ok()
            if self.target_visible():
                if waited:
                    self.macro_mark(f'{tag}等到新一波({time.time() - start:.2f}s)')
                return True
            gone += 1
            if gone >= self.TARGET_GONE_CONFIRM:
                if not waited:
                    waited = True
                    self.macro_mark(f'{tag}没怪(等新一波再变奏穗穗)')
                if time.time() - start >= self.WAVE_WAIT:
                    self.macro_mark(f'{tag}{self.WAVE_WAIT:.0f}s没等到新怪(照切)')
                    return False
                if time.time() >= next_mid:
                    self.task.middle_click()
                    next_mid = time.time() + self.WAVE_LOCK_INTERVAL
            self.macro_wait(0.05)

    # ==================== 一圈 ====================

    def run_cycle(self, bl, su, opener, tag):
        """穗穗(HUD 在场) -> 卜灵 -> 穗穗 -> 心 -> 卜灵 -> 心 -> 穗穗变奏入场、HUD 在场。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if opener:
            self.seg_su_open(su, bl, tag=f'{tag}穗0')
        else:
            self.seg_su_loop(su, bl, tag=f'{tag}穗0')
        self.seg_bl1(su, tag=f'{tag}卜1', opener=opener)
        self.seg_su2(tag=f'{tag}穗2')
        self.seg_xn1(bl, tag=f'{tag}心1')
        self.seg_bl2(tag=f'{tag}卜2', opener=opener)
        self.seg_xn2(su, tag=f'{tag}心2')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        bl = self.teammate('Douling')
        su = self.teammate('Suisui')
        if bl is None or su is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in (self, bl, su):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return bl, su, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'xbs timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 切穗穗 -> 一圈(穗穗启动格)。返回时穗穗刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        bl, su, previous = got
        self.logger.info(f'xbs opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            self.run_cycle(bl, su, opener=True, tag='op')
            self.logger.info(
                f'xbs opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xbs opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是穗穗(宏散过)就先切给她, 第一格走启动格(3A 醒春潮)。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        bl, su, previous = got
        try:
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(bl, su, opener=False, tag=f'L{n}')
            self.logger.info(
                f'xbs loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xbs loop broke ({e}) — timeline: {self.macro_timeline()}')
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
                self.logger.info('xbs: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'xbs: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_xbs:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时不让框架把心切上来(宏自己切)。"""
        if self.is_in_xbs and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
