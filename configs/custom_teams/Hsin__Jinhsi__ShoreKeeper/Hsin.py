import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
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
    """心。心汐守(心/今汐/守岸人)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    作者给了两份录像(E:/轴视频/宏.mp4 按宏跑的、手打.mp4 手打的, 都是 2560x1440 实机直录, 视频时间 = 墙钟)
    和一张启动宏面板截图。作者的编队是 1 心 / 2 今汐 / 3 守, 从守岸人起手; 代码【按角色切人】, 编队怎么排都行。

    ================= 轴的形状 =================
    启动轴: 守 3A-E-2A-重击-闪-A到满-重击-Q-R(自闭启动, 照搬日月守) -> 变奏心   (见 seg_sk_open)
    循环轴: 守(强化变奏入场) A到回路满-强化重击-E-Q-R(协奏连两次 >= 0.80 就直接 Q-R) -> 变奏心
    之后两条一样:
      心   连续 A 到应世心到线(至少两下)-按住(步红尘, 清空后 2.06s 解锁转相) -> Q-R(转相, 没出就补到出) -> 【同奏】切今汐
      今汐 Q-A-R(按住1s, 没亮留到第二段)-[演完立刻]E(神霓飞芒, 进凌霄)-凌霄普攻到第4段-E(惊龙破空, 拿同奏) -> 变奏心
      心   (变奏·照世相 直接进统御众机 + 300照世心) 4A-按住(打空照世心 -> 临寰宇; 按住断了重按)-R(万阙垂天; 能量不够先普攻攒)
      今汐 [E 冷却中先普攻]-E(神霓飞芒)-凌霄3A -> [守 A到3格-E-强化重击(+撑到同奏 25s)] -> 凌霄第4段-E(惊龙破空) -> 变奏守
      今汐在场就随时看处决(怪身上 F 提示 / 顶部谐度满连着 0.5s), 能处决就按 F
    【这条轴靠同奏串起来】: 心转相给同奏 -> 今汐变奏(才有神霓飞芒); 惊龙破空给同奏 -> 心/守变奏;
    心变奏·照世相直接进统御众机拿 300 照世心。心给同奏、和变奏直接进统御众机【只有心在共鸣模态·同奏下才有】。
    宏在【守岸人变奏入场、HUD 回来】时返回, 下一次框架调的就是她。返回点不落在任何演出里。

    ================= 宏表 vs 手打, 改了哪些地方 =================
    宏表在宏录像里是原样跑出来的(10 次切人对表, 每步宏工具多 ~11ms, 别的没差)。跟手打对下来,
    【慢的全是"演出完了还在等固定时间"】—— 手打都是 HUD 一回来就动:
      守R 演完 -> 切心        宏 +0.4s
      心转相演完 -> 切今汐     宏 +0.6s(同奏只有 5 秒, 宏表这一格离过期只差 0.07s)
      今汐惊龙破空演完 -> 切人  宏 +0.5 / +0.7s
      临寰宇演完 -> 万阙垂天    宏 +0.9s(手打 +0.3s)
    这几格都改成"读到 HUD 回来就走"。心那两格 A 的时机也按手打往前挪了一点(见 XN1_A1_AT / XN2_LEAD)。
    宏表里另外两个守岸人小插段(今汐中间切守 A 两下): 手打第一段没打, 默认关(SK_HOP1); 第二段保留(SK_HOP2)。
    第一次实机(2026-10-05 08:37 录屏)之后又改了: 守岸人启动段按手打删减、Q 挪到 R 后; 心上场应世心就满时
    不打 A 直接重击(省 3.4s); 今汐大招演完立刻按神霓飞芒(晚 0.2s 就过了 5s 窗口, 整条链断掉)。

    ================= 心的判据(2026-10-05 拿两份录像在 2K/1080p 上各验过) =================
    - 应世心条: 回路条中间徽记两边的紫色格子, 左半 0~50、右半 50~100。变奏入场给 60(左半满)。
      两份录像里按住重击的那一刻右半都是 0.68~0.72, 按下去 0.3s 内整条清空 = 步红尘出手 ——
      条读到满之前就按也打得出步红尘(普通重击的伤害把最后那截补满, 按住的键接着出步红尘)。
      判据用紫色占比, 2560 和缩到 1920 的同一帧读数一样(差 <0.04)。
    - R 图标: 【应世相】转相没解锁时白色 0, 解锁后 ~0.17(box_liberation 的白色占比, 就是 liberation_available);
      hsin_lib1 模板在应世相全程都认得(0.9+)。
      【照世相里 R 一直是亮的】(借相·应世, CD 1.5s), 所以万阙垂天解锁【只能认 hsin_lib2 模板】
      (临寰宇之后才出现, 0.95+, 之前一帧都不中) —— 拿"R 亮了"去按会放出借相, 把心切回应世相。
    - 【应世心满 = hsin_h1(重击位的花图标)】: 满了条变成粉红高柱, 紫色判据读成 0.4 上下。用户实机心变奏入场
      应世心就是满的(作者的从没满过), 录屏里满的那几秒 0.96~0.99、步红尘出手当帧 0。
    - 临寰宇出手 = 队伍 HUD 消失(~2.0s)。框架的 mouse_forte / hsin_h2 在录像里一帧都没认出来, 不用。

    ================= 改这个文件之前先读 =================
    - 引擎那一半(macro_* / quick_switch / press_key_until_cd / fire_r / switch_to_sk ...)抄自
      Iuno__JingRan__ShoreKeeper/JingRan.py, 守岸人那几个数是那边实机标定过的。
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
    # 宏表里的切人格: 在这一格自己的 gap 里盯到场(正常 0.1s 就到, 剩下的照等, 时序不变);
    # 没到就每 SWITCH_REPRESS 补按, 最多再多等 SWITCH_EXTRA(游戏的换人 CD 约 1 秒)
    SWITCH_EXTRA = 1.50
    SWITCH_REPRESS = 0.05
    SWITCH_CONFIRM = 2

    # ---- 大招 / 演出 ----
    LIB_KEY_INTERVAL = 0.03
    LIB_ANIM_TIME_OUT = 8.0
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防假成功)
    # 【演出中途 HUD 会闪一两帧】(录像: 心转相 0.55s / 3.6s 处、守R 1.9s 处各闪一次)。等"HUD 回来"时
    # 要连读到这么多帧才算, 而且演出开始后不到 min_gone 秒的"回来"一律不认(见 wait_hud_back)
    HUD_BACK_CONFIRM = 2

    # ---- 杂项 ----
    CON_SAMPLE_MAX = 24
    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 保底 ====================
    # 原则(秧千穗那次定下的): 只花时间的保底默认开(切人重试 / 等 HUD / 判据没成立就再按【那一个本该出去的键】);
    # 会改状态的只在判据明确说"这一下没出去"时才补, 不凭猜测多按
    SWITCH_RETRIES = 2           # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)
    SWITCH_RETRY_TIME_OUT = 3.00
    HUD_LOST_ABORT = 8.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃(最长的演出 4.3s)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    R_TRIES = 3                  # R 亮着但没放出去(被吞/被打断)就再按, 最多这么多次
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长
    ROWS_HUD_READ_EVERY = 2.0    # 宏表回放时至少这么久读一次 HUD(喂看门狗), 只在格子空档里读, 不推后下一格

    # ==================== 守岸人 ====================
    # 启动段 = 守岸人自闭启动, 流程和数值照搬日月守 / 露丽守(实机一格一格标定过, 景月守也是这套):
    # 3A -> E -> 2A -> 重击1 -> 闪避 -> A 到回路满 -> 重击2 -> Q -> [协奏不到 0.70 补一发强化重击] -> R -> 演完 -> 交棒。
    # 用户 10-05 16:36: 照手打改的几版(两次清回路 / A 到 2 格 / 提前按住)都不如 15:06 那次, 启动轴要守岸人自闭启动。
    # 闪避是往前闪(按住 W + 右键, 作者宏表也是这么按的): 守岸人打中才回回路, 往后闪 A 不到怪
    SHORE_A3_WINDOW = 1.38       # 第一组三段普攻。A3 被 E 掐掉就加, E 前有空转/多出 A4 就减
    SHORE_E_TO_HEAVY = 1.43      # 从按下 E 起算的第一发重击时刻 —— 落在 A3 抬手, 把 A3 那一两格能量卡到下一次回路
    SHORE_HEAVY_HOLD = 0.85      # 读不到鼠标指示时的兜底按住时长
    SHORE_HEAVY2_HOLD = 0.45
    SHORE_HEAVY_CAP = 1.60       # 长按硬上限。指示暗了 = 长按已被识别, 立刻松手
    SHORE_DODGE_TO_A = 0.25      # 闪避 -> 接普攻, 要跨过闪避的派生帧
    SHORE_FORTE_WAIT_CAP = 5.0   # 边打边等回路满的上限, 超时也照按重击
    SHORE_HEAVY_TO_QR = 0.30     # 重击松手 -> Q
    SHORE_LIB_MIN_CON = 0.70     # 放 R 前协奏至少到这个值, 不够补一发强化重击(R 自己给 20)
    SHORE_LIB_CON_SETTLE = 0.60
    SHORE_LIB_RESCUE_CAP = 3.0
    # 两发重击都只在回路满时按: 不满按出来是普通重击(化蝶), 回路不清还白飞一下(日月守那边读不到指示会兜底按满,
    # 这里不兜: 开局 A 打空/怪还没站稳时宁可不按)。重击1 到点没满就再 A 这么久
    SHORE_HEAVY1_WAIT = 1.50
    SK_OPEN_R_CAP = 5.00         # 启动段 R 的保底上限: 不亮就攒能量(普攻 / 回路满打强化重击)、亮了没出就重按
    WD_W_LEAD = 0.05             # 往前闪: 按住 W -> 右键 -> 松 W
    WD_DODGE_HOLD = 0.07
    WD_W_TAIL = 0.05
    SK_FORTE_CONFIRM = 2         # 回路满连着几帧才算(单帧会被特效骗)
    SK_FILL_HEAVY_MIN = 0.90     # 垫刀剩下不到这么久就不插强化重击了(插段后面马上要切回今汐打凌霄第 4 段)
    SK_E_CAP = 0.60              # E 按到进 CD 的上限(被打断时那一下会被吞)
    SK_Q_PRESSES = 2             # R 演完之后连发 Q 的次数 / 窗口
    SK_Q_WINDOW = 0.15
    SK_R_HOLD = 0.07
    SK_R_WATCH = 1.00            # 按 R 之后盯 HUD 消失的时长, 没消失就当没放出去
    SK_R_RESCUE = 1.80           # 补按 R 的窗口(要盖过重击/普攻的收招)
    SK_R_ANIM_MIN = 2.40         # 守 R 演出藏 HUD ~3.0s, 第 1.9s 处 HUD 会闪一下
    SK_R_ANIM_CAP = 6.00
    # 交棒(变奏心)前协奏要真满。读一下(SK_CON_READ)不满就直接补: E > 强化重击(R 之后回路是满的) > 普攻。
    # 不再边 A 边干等 —— 实机 R 后读数 0.95 一动不动, 等 1s 什么都没变
    SK_CON_READ = 0.25
    SK_CON_RESCUE_CAP = 4.0
    SK_E_CD = 16.0               # 守岸人 E 冷却。补协奏时 E 好没好按上次放 E 的时间算 —— 实机 11:22 技能栏 OCR 读漏,
                                 # 把 11.6s CD 的 E 当成好了, 白按 0.9s
    SK_CON_ENOUGH = 1.0          # 0.99 是环没闭合的哨兵值, 要真满
    SK_CON_FULL_CONFIRM = 3
    SK_CON_POLL = 0.03
    SK_E_DERIVE = 0.67           # E-地面 派生 40F
    # 循环段: 强化变奏入场演完(HUD 回来)之后 A 这么久再重击。手打录像 HUD 回来 -> 重击 1.3s;
    # 帧表 QTE 第86F(1.43s)前不响应输入, 普通变奏入场没演出时这一段正好盖过去
    SK_L_A = 1.20
    SK_L_FULL_CAP = 4.00         # 循环段 A 到回路满的上限(从空攒满实机 2.3~3.2s)
    # 【协奏够了就收手】循环段边打边读协奏, 连两次 >= SK_L_CON_QR 就不再攒回路/重击/E, 直接 Q R —— R 自己给 20 协奏, 放完正好满
    # (用户 10-07: 协奏已经够开 R 满协奏切人了还老老实实打完)。读协奏不便宜(能量环连通域), 隔 SK_L_CON_EVERY 读一次。
    # 守这段缩了, 循环轴今汐第一段惊龙破空离上一次同奏就近了(最近两局 27.6s, 要 >= 25s) —— 那边惊龙破空前不够 25s 会等够
    SK_L_CON_QR = 0.80
    SK_L_CON_EVERY = 0.15
    SK_L_E_CAP = 1.00            # 重击之后按 E 到进 CD 的上限(E 在 CD 上就跳过)
    SK_L_Q_WINDOW = 0.25         # E 之后连发 Q 的窗口(两下)
    SK_R_ENSURE_CAP = 6.00       # 循环段 R 的保底上限(R 不亮就攒能量)
    HEAVY_MIN_HOLD = 0.10        # 长按之后先按这么久再盯指示(按下那一瞬指示还没变)
    SK_HEAVY_HOLD = 0.85         # 读不到鼠标指示时的兜底按住时长
    SK_HEAVY_CAP = 1.60          # 长按硬上限。指示暗了 = 长按已被识别, 立刻松手
    HEAVY_RETRIES = 1            # 指示一直亮(没打出去)就再按, 最多这么多次
    # 切给守岸人: 带演出的强化变奏入场藏 HUD ~3.2s
    SK_INTRO_ANIM_CAP = 6.0
    SK_INTRO_NOANIM = 0.35
    SK_SWITCH_ANIM_HINT = 1.00   # quick_switch 超过这么久才判到到场 = 强化变奏的演出已经在里面演完
    SK_TO_TIME_OUT = 4.00        # 今汐 -> 守岸人: 惊龙破空收招没完游戏不放人

    # ==================== 心 ====================
    # ---- 应世心条(4K 坐标, hcenter): 回路条中间徽记左右两半的紫色格子 ----
    # 读数 = 有紫色的列占比(HSV 色相 125~160、饱和度>=70、亮度>=90)。第一版用 RGB 阈值, 实机 11:24 那局
    # 背景一暗紫格子跟着暗, 左半满格读成 0.58、右半 0.53 读成 0.31, "到线"一直不成立; HSV 两种场景左半都是 0.99
    XN_GAUGE_L = (1627, 1999, 1837, 2011)
    XN_GAUGE_R = (1965, 1999, 2175, 2011)
    XN_GAUGE_VALID = 0.50        # 左半 >= 这个才算"在应世相、读数可信"(变奏入场之后左半就是满的)
    XN_YING_READY = 0.70         # 右半到这么多就按重击(作者宏/手打按下去那一刻都是 0.73, 0.3s 内出步红尘)
    XN_YING_LATE = 0.58          # 到了 XN1_LATE_AT 右半 >= 这个也按 —— 差的那截普通重击能补上
    XN_CONSUMED = 0.15           # 按住之后左半掉到这个以下 = 整条清空 = 步红尘出手
    # 【应世心满 = 重击位出现 hsin_h1(花图标)】。满的时候条变成粉红高柱(紫色判据读成 0.4 上下, 不能用),
    # 实机录屏满的那几秒模板 0.96~0.99、步红尘一出手当帧 0; 作者两份录像从没打满过, 一帧都不中。2K/1080p 一样
    XN_FULL_THRESHOLD = 0.70
    # 心两段长按走 SendInput(游戏在前台时): PostMessage 的按下沿落在普攻收招里会被丢掉, 后面一直按着也不算数
    XN_HOLD_SENDINPUT = True

    # ---- 第一段(变奏·应世相): A -> A -> 重击 -> Q -> 转相 ----
    # 【上场就满(6 链二链: 进战斗 +100 应世心, 只有启动轴那一次)】可以不打那两下 A 直接按住出步红尘(省 ~1.8s),
    # 开关 XN1_SKIP_A_WHEN_FULL。【默认关】: 实机 10-05 两局启动轴跳过 A 之后, 心第一次上场协奏从 0 起又少了
    # A3/A4 那十来段伤害的协奏, 万阙垂天演完协奏环还差一截, 今汐普通入场、第二段惊龙破空出不来。
    # 打开时从切人键起 XN1_FULL_FROM 开始看花图标, 到 XN1_A1_AT 之前看到就在 XN1_FULL_HOLD_AT 按住
    XN1_SKIP_A_WHEN_FULL = False
    XN1_FULL_FROM = 0.20
    XN1_FULL_HOLD_AT = 0.50
    # 没满就照宏表: 切人键后 1.05s / 2.13s 两下 A(接变奏入场的普攻第 3/4 段)、3.21s 按住。
    # 第一版按手打估计提前到 0.70 / 1.70, 实机 11:24 那局第一下之后条一秒多一格没涨(多半落在入场动作里了),
    # 上场 4.2s 才到线(作者 2.6~3.1s) —— 改回宏表
    XN1_A1_AT = 1.05             # 从按切人键起算
    XN1_A_HOLD = 0.08
    # 【从第一下 A 起连续点普攻, 每帧读应世心条, 到线就按住】原来照宏表 A1 -> 隔 1.08s -> A2 -> 再等 ~1.5s 才补 A:
    # 10-08 11:02 循环轴心上场应世心不满, 录屏 160.6 一下 A 之后站了 1.2s 才有下一下(用户: A 得很卡顿)。
    # 点得比出招快没关系(动作里的点击被丢掉), 连段也接得上(补 A 间隔 0.45 断过)。至少打满 XN1_MIN_A 下(启动轴靠这两下攒协奏)。
    # 下面三个时刻都从第一下 A 起算, 跟原来"第二下 A + 1.08s / + 3.0s"对齐
    XN1_TAP_EVERY = 0.25
    XN1_MIN_A = 2
    XN1_LATE_AT = 2.15           # 到这一刻还没到 READY: 右半 >= LATE 也按(差的那截普通重击能补上)
    XN1_BLIND_AT = 2.15          # 到这一刻条一次都没读出来: 不猜, 照按
    XN1_FILL_CAP = 4.00          # 条一直不够的上限, 到了照按
    XN1_FILL_EVERY = 0.40        # 补 A 的间隔(要接得上连段, 0.45 那版断过)
    XN1_GAUGE_CONFIRM = 2        # 连读这么多帧到线才算(特效扫过会闪)
    XN1_HOLD = 1.80              # 按住时长(宏表)。步红尘免疫打断, 演出里一直按着没有坏处
    XN1_RETRIES = 2              # 按满 XN1_HOLD 条都没清空(= 出的是普通重击) -> 补两下 A 再按, 最多这么多次
    XN1_RETRY_A = 2
    # 转相(R1)解锁 = 应世心条清空(步红尘出手) + 2.06s, 三个样本(手打两轮 + 宏)一帧不差。
    # 亮了之后图标会闪(亮 0.13s / 暗 0.17s / 再亮), 读"连着亮两帧"会错过 —— 改成锚点 + 固定时长:
    # 从 XN1_R1_POLL_FROM 起读, 读到一帧亮或者到了 XN1_R1_UNLOCK + XN1_R1_MARGIN 就 Q + 连按 R。
    # 应世相里转相没解锁时按 R 什么都不发生, 早按一点没事; 但 Q 不能早 —— 落在步红尘演出里可能把它取消掉、转相就不解锁了
    XN1_R1_UNLOCK = 2.06
    XN1_R1_POLL_FROM = 1.85
    XN1_R1_MARGIN = 0.08
    # 没看到清空(条读不出来)时退回读图标: 连读 XN1_R1_READS 帧亮才算(清空之前有过一帧火花被读成亮),
    # 读不到就到 XN1_R1_BLIND_AT(从按住起, 宏表 2.6s)直接按
    XN1_R1_LIT_CAP = 3.00
    XN1_R1_READS = 2
    XN1_R1_BLIND_AT = 2.60
    XN_R_WINDOW = 1.50           # R 连按到 HUD 消失的窗口
    # 【转相一定要放出来】(用户 10-05: 今汐的同奏、心第二段的变奏·照世相全靠它, 没放出来就不切今汐)。
    # 第一次没出就补到出为止: 转相亮 -> 按; 应世心满 -> 再按住出步红尘、等解锁; 都不是 -> 普攻攒应世心
    XN1_R1_RESCUE_CAP = 8.00
    XN1_R1_RESCUE_A = 0.80       # 攒应世心一轮的普攻时长
    XN1_R1_LOST_ROUNDS = 3       # 连着这么多轮既看不到转相图标也读不到应世心条 = 已经不在应世相(转相其实出了)
    XN_R_ANIM_MIN = 3.90         # 转相/万阙垂天演出藏 HUD 4.3s, 中途 0.55s / 3.6s 处 HUD 会闪一帧
    XN_R_ANIM_CAP = 7.00

    # ---- 第二段(变奏·照世相, 直接进统御众机 + 300照世心): 4A -> 按住到临寰宇 -> 万阙垂天 ----
    # 宏表是切人键后 3.05s 才第一下 A。手打同一段从上场到临寰宇出手比宏快 0.6s, 这里提前 0.55s
    XN2_LEAD = 2.50
    XN2_ROWS = (
        ('A', 100, 400, ''),
        ('A', 100, 400, ''),
        ('A', 80, 400, ''),
        ('A', 80, 400, ''),
    )
    # 4A 之后按住到 HUD 消失(临寰宇出手)。同奏模态下照世心被普攻打空的那一刻, 按着的这一下马上出临寰宇:
    # 10-05 两份录屏 4 圈都是进统御众机 5.87~5.90s 出手(按住后 ~1.6s), 重击位一直是雕像、人像来不及显示。
    # 【按住被打断就要重按】10-07 11:35 那圈按满 4.4s 没出, 补 7 下 A 再按 0.42s 就出 —— 照世心早空了, 是那一下按住没生效
    # (金狐召唤楼阁容易被肘)。所以按住期间每帧看重击位: 人像出来了却 XN2_READY_REPRESS 还没出手 = 按住断了, 松开重按。
    # 人像判据照搬心卜穗/心雷穗定稿(重击位 16x16 灰度只比圆内像素): 人像帧 0.93~1.00、雕像 <= 0.16、其余 <= 0.64,
    # 2K 和缩到 1080 都量过; 同奏录屏里雕像帧 0.76~0.99、人像读数 <= 0.28
    XN2_HOLD_CAP = 3.00
    XN2_READY_REPRESS = 0.60     # 按住时人像出来这么久还没出手就松开重按(人像出来再按住 0.40~0.45s 出手)
    XN2_REPRESS_MAX = 2
    XN2_TY_A_EVERY = 0.18
    XN2_READY_CONFIRM = 2
    XN_READY_MIN = 0.80
    XN_READY_GLYPH = (
        'AAAAAAAGGDxjHgwAAAAAAAAAABQoWcH31Vg3Jg0AAAAAACGmh+r9+X82NzlcMgAAAAZ47tD9/c06R2NGZ2cWAAA32P38/f2+N0xtaDY3MQIa'
        '3/v9/v3x4DhNbFk2OEcVYf39/f34yMM4RGJWQjmCKmH9+vW6sa2FPT09WGFLyjZ5/c7E4fWaQD0/UHOX0POeYfmXotjqXz09PkBvmojMUTfc'
        'vZnUhD08PDw9mcfbvzMBauG+pJZJREA9P9L05XAFACb1+/38refMZD3D+oQjAAAAbfn9/tDY/LQ7tOMxAgAAAABU2f7is/y+QMd0AgAAAAAA'
        'AAhcpG3GgygPAAAAAA=='
    )
    XN_TY_MIN = 0.60             # 统御众机雕像 0.71~1.00 / 其他 <= 0.38
    XN_TY_GLYPH = (
        'AAAAAAAMJCEsJBYBAAAAAAAAABiR1cJ40tzbpyYAAAAAABK+4+Pt2unk48BaJAAAAAhQg6aMnN1/l6RzUVEbAAAzV3Dw9crHyvngXlFQRQIJ'
        'VFqs8dbtovLV8JdRUFAdIlmK7ZPT7IT7v6Dtbk9bOzdhj5en/O2E/PmHoXdelTpIsV1Xrv3thPz7iE9XwJuUJsCvW6797YX9+4dTsfjxriWy'
        '9KGQ++2E/fqGnvfU33sBpvjsmt/thPzfmeylk9cPADOnpLzM7IT4tc54Y+FhAAAASGKt+eyD5uiKUbyiBQAAAAAkjf3tg/j6h3aCCQAAAAAA'
        'AAZbpFvGrUwKAAAAAA=='
    )
    XN_TY_SLOT = (2826, 1893, 82, 79)   # 重击位(4K, 跟 hsin_h1 的标注框一样)
    XN2_READY_HOLD = 1.20        # 补救时人像出来后按住到 HUD 消失的上限(实测 0.40~0.45s 出手)
    XN2_BLIND_HOLD = 2.00        # 补救时没看到人像(撞上限/没进统御众机)按住试一次的上限
    XN2_RELEASE_AFTER_GONE = 0.30
    XN2_ANIM_MIN = 1.40          # 临寰宇藏 HUD ~2.0s
    XN2_ANIM_CAP = 4.00
    # 按住没出(被打断 / 人像误读) -> 普攻到人像再按, 最多这么多次。
    # 心没拿到变奏(今汐协奏没满)时没进统御众机, 第一次补救前先按一下 E(满 300 照世心是万阙连衡, 不满也给照世心)
    XN2_RETRIES = 2
    XN2_RETRY_CAP = 1.50         # 补的那一轮普攻到人像的上限(同奏下人像不一定认得到, 到点照按)
    XN2_LIB2_CAP = 1.20          # 临寰宇演完后读 hsin_lib2(万阙垂天解锁)的上限
    # 【万阙垂天图标没认出 = 大招能量不够, 先攒能量, 不照按】统御众机那段普攻没打中怪, 能量不满, R 位不是万阙垂天:
    # 10-07 14:16 L1 照按 R 连发 30 下没放出来; 心卜穗 10-06 09:31 照按放成了借相(R 进 1.5s 冷却, 心切回应世相)。
    # 现在照世相普攻攒, 每帧看 hsin_lib2, 认出来再放; 撞上限就这一轮不放
    XN2_R2_BUILD_CAP = 5.00
    XN2_LIB2_READS = 2
    # 万阙垂天演完 -> 切今汐之前协奏要真满(今汐要变奏入场才有神霓飞芒)。实机 11:23 启动轴: 心第一次上场协奏从 0 起,
    # 第一段又是满条直接重击、没打 A, 万阙垂天演完协奏环还差一截 -> 今汐普通入场, 第二段惊龙破空没出。
    # 不满就补: 心的 E(这条轴里没用过, 回协奏多) -> 普攻, 每下之后读一次
    # 【开大之前就把协奏打够】: 实机 10-05 11:56 万阙垂天演完补了 E + 14 下普攻(3.1s)协奏一点没涨 ——
    # 开完大心多半离怪远了, 打不到。所以开大前先读: 低于 XN2_PRE_R2_CON(万阙垂天自己回 20)就照世相普攻补,
    # 这时候人还贴着怪。开大后那道闸只留短上限兜底
    XN2_PRE_R2_CON = 0.80
    XN2_PRE_R2_CAP = 2.00
    XN2_CON_READ = 0.20
    XN2_CON_CAP = 1.50
    XN_E_CD = 12.0               # 共鸣技能·应世相冷却

    # ==================== 今汐 ====================
    JX1_ROWS = (
        ('Q', 80, 500, '声骸'),
        ('A', 200, 80, ''),
    )
    JX1_SWITCH_GAP = 0.10        # 切人键 -> Q(宏表 80+100)
    JX_R_HOLD = 1.00             # 宏表按住 1s: R 落在变奏入场的不响应输入里, 按住到能接受的那一帧
    JX_R_READS = 2
    JX_R_WATCH = 0.80            # 松开 R 之后盯 HUD 消失
    JX_R_RESCUE = 1.50
    JX_R_ANIM_MIN = 2.60         # 今汐大招藏 HUD ~3.45s
    JX_R_ANIM_CAP = 6.00
    # 【今汐的链】变奏入场 -> E 换成神霓飞芒, 只有 5 秒 -> 按了进乘岁凌霄(10s, 凌霄普攻段数切人不重置)
    #   -> 凌霄普攻第 4 段 -> 游龙廻光(5s) -> E = 惊龙破空 -> 今汐拿同奏(25s 一次), 切人下一个人必变奏。
    # 实机 2026-10-05: 大招 HUD 回来又等 0.35s 才按 E, 是切人后 5.1s —— 窗口刚过, 按出来的是普通 E(流光夕影,
    # CD 3s), 没进凌霄, 后面惊龙破空一下都按不出来。宏录像里作者是 HUD 回来 0.15s 就按了(切人后 4.9s)。
    # 所以大招一演完就开始按。
    # 【所有判断都看 E 位的图标】(实机 12:18/12:20 两局: 神霓飞芒按住 0.4s 落在变奏入场里没出, 读 E 框 CD 区分不了
    # "神霓飞芒还没按"和"已经在凌霄", 误判进去了 -> 守岸人插段把 5s 窗口耗光 -> 惊龙破空按 3s 都出不来)。
    # E 位四种图标: 神霓飞芒(Δ人形+金环) / 逐天取月(白龙, 凌霄里) / 惊龙破空(龙+人+金环) / 流光夕影(剑, 普通E)。
    # 16x16 灰度模板, 取自宏录像 + 用户两份录屏, 2K/1080p 逐帧跑过: 逐天取月 0.98~1.00、流光夕影 0.95~1.00、
    # 神霓飞芒 0.85~1.00, 五段录像这三类一帧没错。"惊龙破空"那类会被切换瞬间的闪光骗(0.93~0.98, 比第二名高 0.2),
    # 只在凌霄里点普攻那一段用(那时 E 位只会从逐天取月变成惊龙破空), 而且要过严格阈值 JX_JINGLONG_*。
    #   进凌霄 = 只在 E 位是神霓飞芒(或认不出)时按 E, 看到逐天取月就停 —— 第一版 0.26s 连按 5 下, 第一下进了凌霄,
    #            后面几下把逐天取月也放了, 逐天取月的动作又吞掉一下凌霄普攻, 第一下惊龙破空总是没出;
    #   凌霄第 4 段 = 一直点普攻到 E 位换成惊龙破空(凌霄结束), 然后按 E。认不出的帧不算数 —— 逐天取月转 CD 时
    #            图标是灰龙加跳动的数字, 读数一阵一阵的(实机 12:20 那下没出的 E 放出了逐天取月, 见过)。
    JX_NO_R_E_AT = 1.00          # R 没亮跳过时, 从切人键后这么久开始按神霓飞芒(变奏入场的锁里按的会被吞, 会补按)
    JX_ICON_MIN = 0.85
    JX_ICON_MARGIN = 0.05
    JX_JINGLONG_MIN = 0.92
    JX_JINGLONG_MARGIN = 0.12
    JX_LX_CAP = 1.80             # 按神霓飞芒到看见逐天取月的上限(E 冷却那段不算)
    # 今汐第二段切进来 E 位转 ~1.8s 冷却, 冷却数字盖在图标上会被认成惊龙破空/认不出 —— 10-07 L2 那圈一直认成惊龙破空不按,
    # 冷却转完认出神霓飞芒时 1.8s 上限已到, 冷却里按的两下被当成"按过神霓飞芒"判成进了凌霄, 后面整段落空。
    # 现在 E 位不是那三种明确的图标时读技能栏冷却数字, 在冷却就等(不按、不计时、不算按过)
    JX_LX_LOCK_CAP = 3.00        # 等 E 冷却的上限
    # 等 E 冷却期间点普攻(手打这段也是 A 三下): 10-07 14:25 三圈干等了 1.7 / 2.5 / 3.5s。动作里的点击被丢掉, 不会多出一段
    JX_LX_CD_A_EVERY = 0.20
    JX_LX_CD_EVERY = 0.10        # 读冷却数字(OCR)的间隔
    JX_LX_REPRESS = 0.40         # 图标还是神霓飞芒就隔这么久再按(按出去到图标换掉要 0.2~0.3s, 太密会连出逐天取月)
    JX_LX_REPRESS_UNKNOWN = 0.80 # 图标认不出时补按的间隔
    JX_LX_A4_CAP = 2.80          # 流光夕影(没吃到变奏 / 窗口过了): 普通普攻打到第 4 段、E 位出神霓飞芒的上限
    JX_A4_TAP_EVERY = 0.20       # 凌霄普攻点按间隔(动作里的点击被丢掉, 不会多出一段)
    JX_A4_CAP = 4.20             # 点到凌霄第 4 段的上限。第一段凌霄 4 下全在这里打, 实机进凌霄 -> E 位换惊龙破空 3.4~3.65s
    JX_A4_SEE_GRACE = 0.80       # 这么久都没见过逐天取月(认不出/切人后技能栏还没换过来): 不再等, 直接按 E 走补救
    # 惊龙破空给的同奏 25 秒一次: 作者两份录像两次惊龙破空都正好隔 25.1 / 25.4s。
    # 提速之后第二次会早于 25s、拿不到同奏, 守岸人就吃不到变奏 —— 第二段守岸人小插段多打几下把间隔撑到 25s
    JX_TONGZOU_CD = 25.0
    JX_TONGZOU_MARGIN = 0.15
    JX_BIG_E_LAG = 0.20          # 按惊龙破空到出手
    JX_YOULONG_WAIT_CAP = 4.00   # 游龙廻光 5s 里最多等这么久(认出第 4 段晚 ~0.3s + 按下到出手 0.2s)
    JX_TONGZOU_GRACE = 8.0       # 惊龙破空出手后这么久之内切人都算吃到同奏(同奏 5s + 演出 3s)
    JX_HOP_EXTRA_CAP = 5.0       # 最多多打这么久
    # 第二段切守岸人之前处决(谐度破坏): 凌霄里今汐会升空打, 处决把人拉到怪跟前、落在地上, 守岸人切进来才在地面、
    # 打得到怪; 在空中切进来她的普攻是下落攻击, 离怪还远(10-05 13:55 录屏: 守岸人切进来在 Boss 右边老远)。
    # 手打两次都是凌霄 3A 之后按 F, 按完 1.58s 切守岸人; 帧表凌霄谐度破坏: 全局时停 / 无敌 94F, 无敌期间不能切人。
    # 【今汐在场就随时看处决】凌霄普攻、第二段等 E 冷却、3A 期间每帧都看, 能处决就按 F。
    # 10-07 11:57 录屏: 处决提示出在开局插段(163.3~167.7)、L1 今汐第一段(190.4~195.2)、L2 守/心1/今汐第一段 ——
    # 怪谐度满的时刻跟手打不一样, 只在今汐第二段 3A 那一下看, 三圈全错过。
    # 判据: 怪身上 F 提示连两帧(>= 0.86, 排除拾取 F) 或 顶部谐度满(f_break_full >= 0.92)连着 0.5s。
    # 这关多波怪/换 Boss, 怪身上 F 提示模板经常认不出(录屏 175.5 / 251.0 画面上有 F 圈, 只认出顶部谐度满);
    # 顶部谐度满又比 F 提示早出 ~0.5s(心卜穗那边早 2.8s, 那时按 F 是空按), 所以它要连着亮 0.5s 才算。
    # 真提示 0.89~0.97, 误报 0.80~0.83
    JX_BREAK_CENTER_MIN = 0.86
    JX_BREAK_FULL_MIN = 0.92
    JX_BREAK_FULL_HOLD = 0.50
    JX_BREAK_CONFIRM = 2
    JX_BREAK_COOLDOWN = 3.00     # 处决之后这么久不再判(提示还要 ~1s 才消失)
    JX_BREAK_WAIT = 0.40         # 第二段 3A 打完还没处决, 切守岸人之前再看这么久
    JX_BREAK = True
    JX_BREAK_PRESSES = 3
    JX_BREAK_ANIM = 1.60
    SK_HOP_AIR_WAIT = 0.50       # 没处决可用时切守岸人先不点普攻, 等她从空中落地(不然是下落攻击)。估的, 没实测
    JX_LX_DURATION = 10.0        # 乘岁凌霄时长: 插段再长也要在凌霄结束前把第 4 段打出去
    JX_TAIL_EST = 1.00           # 切回今汐 -> 惊龙破空 的估计(算插段要多打多久用)
    # 守岸人小插段(宏表有)。【第一段默认关】: 手打录像这一段没切守, 实机 12:20 启动轴今汐第一段比手打慢 2.9s
    # (游戏时间), 这一趟 + 惊龙破空补救占了大头。第二段保留, 而且它要负责把两次惊龙破空撑开 25s
    SK_HOP1 = False
    SK_HOP2 = True
    JX1_ROWS_BEFORE_HOP = (      # SK_HOP1 打开时: 神霓飞芒之后先打这三下凌霄普攻再切守
        ('A', 80, 500, ''),
        ('A', 100, 500, ''),
        ('A', 80, 200, ''),
    )
    SK_HOP1_ROWS = (
        ('>SK', 80, 50, '切守'),
        ('A', 100, 1000, '守A'),
        ('A', 80, 100, '守A'),
        ('>JX', 80, 80, '切今汐'),
    )
    JX2_LEAD = 1.00              # 第二段: 切人键 -> 开始按神霓飞芒(宏表 50+1000, 变奏入场的动作)
    JX2_R_AT = 0.60              # 第二段补开大(第一段 R 没亮的话): 切人键后这么久按住 R
    JX2_E_HOLD = 0.40            # 第二段那下神霓飞芒宏表按住 0.4s
    JX2_ROWS = (
        ('A', 150, 500, ''),
        ('A', 80, 500, ''),
        ('A', 100, 300, ''),
    )
    SK_HOP2_ROWS = (             # 中间那段 = A 到回路满 -> 强化重击(sk_clear), 再按需垫到同奏 25s
        ('>SK', 50, 50, '切守'),
        ('>JX', 50, 100, '切今汐'),
    )
    SK_HOP_FULL_CAP = 4.00       # 插段 A 到回路满的上限(还要再扣掉凌霄剩下的时间)
    # 插段 A 到 3 格就按 E: 守岸人的 E 给 ~2 格回路, 正好补满, 接着强化重击(用户 10-05; 手打插段也按了 E)。
    # 录屏量的: 手打插段 E 前 3.5 格 -> E 出手那一帧满; 我们启动段 E 后 0.35s 内 2 -> 4 格。
    # 但强化重击刚清空时紧接着按的 E 不回回路(手打启动段 / 我们循环段, 之后近 1s 一格不涨) —— E 要在攒着回路的时候按
    SK_HOP_E_AT_SEGS = 3
    SK_HOP_E_WAIT = 0.60         # 按完 E 等回路满的时间(E 出手那一下就给), 不满就接着 A 到满
    # 回路条 5 格 x 7 点, 亮的点是黄色。4K 坐标, 每格算有黄色的列占比: 满格 0.49~0.87, 半格 0.04~0.40
    # (手打 + 10-05 13:55 录屏, 2560 和缩到 1920 都量过)。满了整条换成白色高柱, 那时读数是 0, 满不满看鼠标指示
    SK_SEG_BOXES = (
        (1627, 1995, 1717, 2016),
        (1743, 1995, 1833, 2016),
        (1857, 1995, 1947, 2016),
        (1972, 1995, 2062, 2016),
        (2088, 1995, 2178, 2016),
    )
    SK_SEG_ON = 0.45
    SK_HOP_HEAVY_EST = 0.50      # 插段强化重击 + 切回今汐的估计, 从凌霄剩余时间里先扣掉
    JX_GLYPH_LABELS = ('shenni', 'zhutian', 'jinglong', 'liuguang', 'cd')   # cd = 逐天取月转着 CD, 跟逐天取月算一类
    JX_GLYPHS = (
        'AAAAAAATR0dfUTEBAAAAAAAAAA9gk5GWmZ+0iTQAAAAAAB2Bd2xlbF1pfZe3XQAAAA15Y0w9VENIPU9pi7FIAABLYD8xRU+S'
        'NygxQmGUpgYPaUAtNGZvwU1XJzNwpbNCNk4xKWo+trWvrC1NiJmOeCY8KU1vKdR7ZcszSIhrcm0sNTWATTTYSVbQVX9iPWd4'
        'HDdmhz0/tTI+fNRVKTtuZhNNhYdYZsU2aJKoVEp+gk0AWIlnKKfaVUiyr9C9lpcKACJrPlLXiaW+n25iQItNAAAAKH+to3hS'
        'Pzw7PExSBAAAAAArPkBCRFFriYhEAwAAAAAAAAEUJiQtLjoMAAAAAAAAAAAABRQVGxcNAAAAAAAAAAAFHjM3NTc5PCsPAAAA'
        'AAAKMTtXgEI8Y0g1OhsAAAAFTG6CpNbq28JATTI1EgAAHTI/mtz7/exWVkYyMC0BBi45dbb9/ftwRHE2MzIzFB5FNEvm/N3E'
        'NXNZNTU1NCcVWkJw/cN3RlCLQjZNNTUmHD9ucfvaaTyDgUdaWDY9KxhEa1mz7vmEbYRVpjk4UScNQ3JGQWCiuc9xgZM4PmIe'
        'ADN9STMzOqD6c9pdO1tUBAAUoGxjPThh463LSkZvJgAAAFqrZoBndKHynnVcMgIAAAAAUniJfXDI7mF7QwIAAAAAAAAEKWhk'
        'xopXDgAAAAAAAAAAABJERFhILAEAAAAAAAAAD16anp2gp65+LQAAAAAAH4+UjZeNjKSYoaxSAAAAD42UlYuompuulZSdrT8A'
        'AFiSpJuqzcTIwbC5mp+YBRCNna6p28zH4sm2yLeYpkE4nraxydTU3t/dvsDFmZ95N5jJzdzU7+bV7OHBwZScdUWdzc24zfHx'
        '6fHpyKuVo4U5s8vAqbXr6e/w6uW4lqd1JZuwsrWY2ujv7dG84pmxWABkoKm9psTv7ungxtiaqwwAIpOfsMzNyeLnvd27ul8A'
        'AAA/n8PGtM/SwKylso8GAAAAAD+rpqq+vqijt3oHAAAAAAAABTp1iZeMeBIAAAAAAAAAAAAGFxYdGA4AAAAAAAAAAAcoPT07'
        'OTk9KxAAAAAAAAtARkdHR0ZZW0hOJwAAAAY4PD9FOTc4izYzNDkUAAAiPFFSOzpEfIc3ODk5NgEFNFNbNTU9etlHMzMyMTIU'
        'FEV5ODVNSeLBMTAvMUxHIxRtWDRZPGz8hDZel65lMCA0cEFEWjug/ZPv434vKikiR3V0rNTKvuihxJttOCgoHDfq5Z5iRJXB'
        'MC0wKiktJxQBhGZDLi2PlTJFLCg0NCQDABddRi4ttXtMKigvUyYTAAAAO3ltaOZOKSk+Zy0ZAQAAAAAeOZfOQU1tay4TAQAA'
        'AAAAAAFYjERWLxYCAAAAAAAAAAAACiQmMCcXAAAAAAAAAAAJOl5lZ2hob1MfAAAAAAASWmZ7m3Bti3ludzoAAAAIaoucuN7t'
        '5NFyfmx1LAAALVZfreL7/O+Bgnhra2cDBj1PhsH9/vuUcZRpaGlsKxxKQFnr/eHOX5B9YmVkZk0SWUVx/MN8UmGUWFNmV1tC'
        'FTltb/rZaT6Eg0xfXkhXQRI4Zlez7vmBbYRVpT9BXTMLN3BFQWChts5wgZI7Q2kkACx5SDU2PKL6c9pWO19aBQASn2hlQT1l'
        '5K/JREdyKAAAAFqsaoNse6PxmndgNQIAAAAAUneOhnfF7F98RgIAAAAAAAAELGtlxolXDgAAAAA='
    )
    JX1_BIG_E_HOLD = 0.30        # 惊龙破空那一下 E 的按住时长(宏表 300 / 100)
    JX2_BIG_E_HOLD = 0.10
    JX_BIG_E_WATCH = 1.20        # 按惊龙破空到 HUD 消失的上限(录像里按下当帧就藏了)
    JX_BIG_E_REPRESS = 0.15
    JX_BIG_E_RESCUE = 3.00       # 还没出手 -> 边 A 边按 E 的补救上限
    JX_BIG_ANIM_MIN = 2.20       # 惊龙破空藏 HUD ~3.0s
    JX_BIG_ANIM_CAP = 6.00
    JX_CON_CAP = 1.20            # 惊龙破空之后变奏心/守, 协奏没满就边 A 边等这么久

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
        self.sk_intro_done_at = 0.0
        self.jx_r_pending = False      # 今汐第一段大招没放出去(能量没满), 第二段补
        self.tongzou_at = -99.0        # 上一次惊龙破空拿到同奏的时刻(25s 一次)
        self.lx_at = 0.0               # 上一次按出神霓飞芒(进乘岁凌霄)的时刻
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
    def is_in_xxs(self) -> bool:
        """本队 = 心 + 今汐 + 守岸人。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Jinhsi', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_xxs:
            return 'team is not hsin/jinhsi/shorekeeper'
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
        if not self.is_in_xxs:
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

    def wait_con_full(self, who, cap, tag, poke=True):
        """边打边等协奏真满(连着几帧)。返回满没满。"""
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
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            hits = hits + 1 if con >= self.SK_CON_ENOUGH else 0
            if hits >= self.SK_CON_FULL_CONFIRM:
                self.macro_mark(f'{tag}(满,{time.time() - start:.2f}s)')
                return True
            self.macro_wait(self.SK_CON_POLL)
        self.macro_mark(f'{tag}(没满,{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}: {who} 协奏 {" ".join(samples)}')
        return False

    # ==================== 宏表回放 ====================

    def macro_switch(self, target, hold, gap, tag, required=True):
        """宏表里的数字键: 在这一格自己的 gap 里盯到场; 没到就补按, 最多再多等 SWITCH_EXTRA。返回到没到。

        required: 还没到就先等 HUD 回来再切(switch_retry 的那一套), 再不行整圈放弃 —— 切人错一格, 后面每一格都发在错的人身上。
        不 required(插段这种可有可无的): 再认一次场上是谁, 不是目标就算了, 调用方跳过这一段。
        """
        if target is self.cur:
            self.macro_mark(f'{tag}(already)')
            return True
        source = self.cur if self.cur is not None else self
        slot = target.index + 1
        entry = time.time()
        self.switch_press_at = entry
        self.task.send_key(slot, down_time=hold)
        soft = entry + hold + gap
        hard = soft + self.SWITCH_EXTRA
        last = time.time()
        hits = 0
        arrived = False
        while time.time() < hard:
            self.task.next_frame()
            in_team, index, _count = self.task.in_team()
            if in_team:
                self.hud_seen_at = time.time()
            hits = hits + 1 if (in_team and index == target.index) else 0
            if hits >= self.SWITCH_CONFIRM:
                arrived = True
                break
            if time.time() - last >= self.SWITCH_REPRESS:
                self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
                last = time.time()
            self.macro_wait(0.02)
        cost = time.time() - entry
        late = time.time() - soft
        if arrived:
            self.handoff(source, target, False)
            self.macro_mark(f'{tag}({cost:.2f}{f",晚{late:.2f}" if late > 0.05 else ""})')
        elif required:
            self.macro_mark(f'{tag}超时({cost:.2f})')
            self.need(self.switch_retry(target, tag), f'{tag}: switch to {target.name} never confirmed')
        else:
            # 超时那一刻可能刚好切过去了: 认一下场上是谁, 键要发给真正在场的人。认不出来先等 HUD 再认
            who = self.on_field_char(time_out=0.6)
            if who is None:
                self.wait_hud_up(cap=1.0, tag=f'{tag}认人前HUD')
                who = self.on_field_char(time_out=1.0)
            if who is target:
                self.handoff(source, target, False)
                self.macro_mark(f'{tag}(晚到,{time.time() - entry:.2f})')
            elif who is not None and who is source:
                self.macro_mark(f'{tag}切不过去({cost:.2f},还是{who.name[:3]})')
                return False
            else:
                # 场上是谁说不清: 不能赌, 按必须切过去处理
                self.macro_mark(f'{tag}超时({cost:.2f},场上{who.name[:3] if who else "?"})')
                self.need(self.switch_retry(target, tag), f'{tag}: switch to {target.name} never confirmed')
        self.wait_until_time(soft)
        return True

    def play_rows(self, rows, tag, chars):
        """按宏表逐格回放: (动作, 按住ms, 松开后ms, 注释)。每一格按【绝对时刻】排, 读屏/发键的开销不往后累计。

        动作: 'A' 普攻按住 / 'E' 'Q' 'R' 技能键按住 / 'E#' 按到进 CD(格子时间照走完) /
              '>SK' '>JX' '>XN' 切人(在这一格的 gap 里确认到场)。技能键【发给当时在场的那个人】。
        """
        t = time.time()
        for row in rows:
            act, hold_ms, gap_ms = row[0], row[1], row[2]
            note = row[3] if len(row) > 3 else ''
            hold = hold_ms / 1000.0
            gap = gap_ms / 1000.0
            self.wait_until_time(t)
            row_start = time.time()
            nxt = row_start + hold + gap
            if act.startswith('>'):
                self.macro_switch(chars[act[1:]], hold, gap, f'{tag}{note or act}')
                t = max(nxt, time.time())
                continue
            char = self.cur if self.cur is not None else self
            if act == 'A':
                self.tap(hold)
            elif act == 'E':
                char.send_resonance_key(down_time=hold)
                char.record_resonance_use()
            elif act == 'Q':
                char.send_echo_key(down_time=hold)
                char.record_echo_use()
            elif act == 'R':
                char.send_liberation_key(down_time=hold)
            elif act == 'E#':
                self.press_res_until_cd(char, max(self.SK_E_CAP, hold + gap), tag=f'{tag}{note}',
                                        ready_grace=0.15)
                char.record_resonance_use()
            else:
                raise ValueError('unknown macro action ' + repr(act))
            # 看门狗要有 HUD 读数: 宏表几秒不读屏的话会误判"HUD 丢了"。只在这一格的空档够读一帧时读
            if time.time() - self.hud_seen_at > self.ROWS_HUD_READ_EVERY and nxt - time.time() > 0.12:
                self.task.next_frame()
                self.hud_ok()
            if note:
                self.macro_mark(f'{tag}{note}')
            t = max(nxt, time.time())
        self.wait_until_time(t)

    # ==================== 守岸人 ====================

    def switch_to_sk(self, sk, tag):
        """切给守岸人并等她的变奏入场演完。返回时 HUD 在场、场上是她。"""
        for attempt in range(1 + self.SWITCH_RETRIES):
            got = self.switch_to_sk_once(sk, tag if attempt == 0 else f'{tag}重切#{attempt}')
            if got is not None:
                return got
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
        return False

    def switch_to_sk_once(self, sk, tag):
        """切一次守岸人。返回 True/False(到场了, HUD 回没回来), None = 守岸人没上场。

        带演出的强化变奏入场一切进来 HUD 就藏了, quick_switch 读不到她到场会超时 ——
        所以不管切人判没判到, 都等 HUD 回来再认一次场上是谁。演出里一下都不点(马上要接循环段)。
        """
        src = self.cur
        sw_at = time.time()
        full = False
        try:
            full = float(self.task.get_current_con()) >= 1.0
        except Exception:
            pass
        # 惊龙破空刚给了同奏(5s, 演出 3s 也算在里面): 协奏读数不满守岸人也是变奏入场
        full = full or time.time() - self.tongzou_at < self.JX_TONGZOU_GRACE
        arrived = self.quick_switch(sk, assume_con=full, time_out=self.SK_TO_TIME_OUT)
        anim_in_switch = arrived and time.time() - sw_at >= self.SK_SWITCH_ANIM_HINT
        start = time.time()
        gone_at = None
        hits = 0
        back = False
        while time.time() - start < self.SK_INTRO_ANIM_CAP:
            self.task.next_frame()
            if not self.hud_ok():
                if gone_at is None:
                    gone_at = time.time()
                hits = 0
            elif gone_at is not None or anim_in_switch:
                hits += 1
                if hits >= self.HUD_BACK_CONFIRM:
                    back = True
                    break
            elif time.time() - start >= self.SK_INTRO_NOANIM:
                back = True
                break
            self.macro_wait(0.01)
        if gone_at is not None:
            self.add_freeze_duration(gone_at, time.time() - gone_at)
        self.macro_mark(f'{tag}守入场({"anim" if gone_at is not None or anim_in_switch else "noanim"},'
                        f'{"ok" if back else "timeout"},{time.time() - start:.2f}s)')
        if not arrived:
            who = self.on_field_char(time_out=1.0)
            if who is not sk:
                self.macro_mark(f'{tag}守没上场({who.name if who else "?"})')
                return None
            self.handoff(src if src is not None else self, sk, full)
            self.macro_mark(f'{tag}>Sho(演出里到场)')
        self.sk_intro_done_at = time.time()
        return back

    def hold_heavy(self, char, hold, cap, tag):
        """守岸人长按重击: 鼠标指示【见过亮之后一暗】就松手; 没亮过按满 hold 兜底; cap 是硬上限。"""
        cap = max(cap, hold)
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}_down')
        start = time.time()
        why = None
        seen_lit = False
        try:
            self.macro_wait(min(self.HEAVY_MIN_HOLD, hold))
            while time.time() - start < cap:
                self.task.next_frame()
                self.hud_ok()
                try:
                    lit = bool(char.is_mouse_forte_full())
                except Exception:
                    lit = False
                if lit:
                    seen_lit = True
                elif seen_lit:
                    why = '指示暗'
                    break
                if not seen_lit and time.time() - start >= hold:
                    why = '一直暗'
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        if why is None:
            why = 'cap'
        self.macro_mark(f'{tag}_up({why},{time.time() - start:.2f}s)')
        return why != 'cap'

    def sk_heavy(self, sk, tag):
        """指示一直亮着没暗(= 没打出去: 被打断/按在别的动作里)就再按, 最多 HEAVY_RETRIES 次。"""
        for i in range(1 + self.HEAVY_RETRIES):
            if self.hold_heavy(sk, self.SK_HEAVY_HOLD, self.SK_HEAVY_CAP,
                               tag=tag if i == 0 else f'{tag}重按#{i}'):
                return True
        return False

    def sk_forte_full_once(self, sk):
        try:
            return bool(sk.is_mouse_forte_full())
        except Exception:
            return False

    def sk_forte_full(self, sk):
        """守岸人回路满(鼠标指示亮)连着 SK_FORTE_CONFIRM 帧。读当前帧起。"""
        for i in range(self.SK_FORTE_CONFIRM):
            if i:
                self.task.next_frame()
            try:
                if not sk.is_mouse_forte_full():
                    return False
            except Exception:
                return False
        return True

    def sk_fill(self, sk, window, tag):
        """守岸人垫刀 window 秒: 点普攻, 回路满了就插一下强化重击(满着再 A 只是白攒)。总时长不变。"""
        start = time.time()
        end = start + window
        taps = 0
        heavies = 0
        while time.time() < end:
            self.task.next_frame()
            left = end - time.time()
            if left >= self.SK_FILL_HEAVY_MIN and self.sk_forte_full(sk):
                self.hold_heavy(sk, self.SK_HEAVY_HOLD, min(self.SK_HEAVY_CAP, end - time.time()),
                                tag=f'{tag}回路满重击')
                heavies += 1
                continue
            self.tap()
            taps += 1
            self.macro_wait(min(max(0.0, self.A_INTERVAL - self.A_TAP), max(0.0, end - time.time())))
        self.macro_mark(f'{tag}(A x{taps},重击 x{heavies})')
        return heavies

    def sk_a_until(self, cap, done, tag):
        """守岸人连点普攻, 每下之后读屏, done() 连着两次成立就停。返回成立没有。"""
        start = time.time()
        taps = 0
        hits = 0
        while time.time() - start < cap:
            self.tap()
            taps += 1
            until = time.time() + max(0.0, self.A_INTERVAL - self.A_TAP)
            first = True
            while True:
                self.task.next_frame()
                if first:
                    self.hud_ok()
                    first = False
                hits = hits + 1 if done() else 0
                if hits >= 2:
                    self.macro_mark(f'{tag}(A x{taps},{time.time() - start:.2f}s)')
                    return True
                if time.time() >= until:
                    break
                self.macro_wait(0.01)
        self.macro_mark(f'{tag}(cap,A x{taps})')
        return False

    def sk_clear(self, sk, cap, tag):
        """清空回路: 满了才重击(满 = 强化重击, 清回路 + 协奏 6), 没满先 A 到满。返回清没清。

        cap 内没满就不按重击 —— 没满时重击出的是普通重击(化蝶, 小蝴蝶飞起来), 回路不清还白花一秒多。
        实机 10-05 14:16 循环段: 插段被跳过、回路没满, 照旧 A 1.2s 就重击 -> 化蝶。
        """
        def full():
            try:
                return bool(sk.is_mouse_forte_full())
            except Exception:
                return False
        self.task.next_frame()
        if self.sk_forte_full(sk):
            self.macro_mark(f'{tag}回路已满')
        elif cap < 0.3 or not self.sk_a_until(cap, full, tag=f'{tag}A到回路满'):
            self.macro_mark(f'{tag}回路没满不重击')
            self.logger.warning(f'{tag}: 守岸人 {max(cap, 0):.1f}s 内回路没攒满(打不到怪/时间不够), 不重击(不满会化蝶)')
            return False
        self.sk_heavy(sk, tag=f'{tag}强化重击')
        return True

    def sk_forte_segs(self):
        """守岸人回路亮了几格(从左数连着亮的)。读当前帧。满了(白色高柱)读成 0。"""
        frame = self.task.frame
        n = 0
        fracs = []
        for x1, y1, x2, y2 in self.SK_SEG_BOXES:
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, hcenter=True)
            crop = frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.size == 0:
                return 0
            hsv = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2HSV)
            h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            yellow = (h >= 18) & (h <= 38) & (sat >= 110) & (v >= 150)
            fracs.append(float(yellow.any(axis=0).mean()))
        for f in fracs:
            if f < self.SK_SEG_ON:
                break
            n += 1
        return n

    def sk_e_ready(self, sk):
        """守岸人 E 好没好: 上次放 E 的时间 + 技能栏 CD 都要说好了(OCR 读漏过一次, 把 CD 中的 E 当成好了)。"""
        return time.time() - getattr(sk, 'last_res', 0) >= self.SK_E_CD - 0.3 and self.read_cd('resonance') <= 0

    def sk_hop_clear(self, sk, cap, tag):
        """插段清回路: A 到 3 格 -> E(补 ~2 格到满) -> 强化重击。E 没好就照旧 A 到满再重击。返回清没清。"""
        if cap < 0.3:
            return self.sk_clear(sk, cap, tag)
        start = time.time()
        self.task.next_frame()
        if self.sk_forte_full(sk) or not self.sk_e_ready(sk):
            return self.sk_clear(sk, cap, tag)

        def full():
            try:
                return bool(sk.is_mouse_forte_full())
            except Exception:
                return False
        if not self.sk_a_until(cap, lambda: full() or self.sk_forte_segs() >= self.SK_HOP_E_AT_SEGS,
                               tag=f'{tag}A到{self.SK_HOP_E_AT_SEGS}格'):
            self.macro_mark(f'{tag}回路没满不重击')
            self.logger.warning(f'{tag}: 守岸人 {cap:.1f}s 内没攒到 {self.SK_HOP_E_AT_SEGS} 格(打不到怪?), 不重击')
            return False
        self.task.next_frame()
        if not self.sk_forte_full(sk):
            if self.press_res_until_cd(sk, self.SK_E_CAP, f'{tag}E补回路', ready_grace=0.15):
                sk.record_resonance_use()
            left = cap - (time.time() - start)
            hits = 0
            wait_end = time.time() + min(self.SK_HOP_E_WAIT, max(0.0, left))
            while time.time() < wait_end:
                self.task.next_frame()
                hits = hits + 1 if full() else 0
                if hits >= self.SK_FORTE_CONFIRM:
                    break
                self.macro_wait(0.02)
            if hits < self.SK_FORTE_CONFIRM:
                left = cap - (time.time() - start)
                if left < 0.3 or not self.sk_a_until(left, full, tag=f'{tag}E后A到回路满'):
                    self.macro_mark(f'{tag}回路没满不重击')
                    return False
            self.macro_mark(f'{tag}回路满(E后,{time.time() - start:.2f}s)')
        return self.sk_heavy(sk, tag=f'{tag}强化重击')

    def sk_build_energy(self, sk, tag, rounds):
        """守岸人攒能量的一轮: 回路满了就打强化重击(能量多), 否则普攻一小段。"""
        self.task.next_frame()
        if self.sk_forte_full(sk):
            self.sk_heavy(sk, tag=f'{tag}攒能量重击#{rounds}')
        else:
            self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL, tag=f'{tag}攒能量A#{rounds}')

    def sk_ensure_con(self, sk, tag):
        """守岸人协奏没满就别交棒 —— 用能给协奏的技能补: E(20) > 强化重击(6) > 普攻(1~4)。"""
        start = time.time()
        taps = 0
        while time.time() - start < self.SK_CON_RESCUE_CAP:
            if self.read_con(self.SK_CON_ENOUGH, settle=self.SK_CON_POLL) >= self.SK_CON_ENOUGH:
                self.macro_mark(f'{tag}补协奏(满,A x{taps},{time.time() - start:.2f}s)')
                return True
            # E 好没好: 上次放 E 的时间 + OCR 都要说好了才按(OCR 读漏过一次, 把 CD 中的 E 当成好了)
            e_ready = time.time() - getattr(sk, 'last_res', 0) >= self.SK_E_CD - 0.3 and self.read_cd('resonance') <= 0
            if e_ready:
                self.spam_key(sk.send_resonance_key, 0.26, interval=0.06, tag=f'{tag}补协奏E')
                sk.record_resonance_use()
                self.macro_wait(self.SK_E_DERIVE)
                continue
            if self.sk_forte_full(sk):
                self.sk_heavy(sk, tag=f'{tag}补协奏重击')
                continue
            # 一下一下打, 每下之后看回路/协奏 —— 回路一满立刻强化重击(实机最后那截协奏就是它补上的)
            self.tap()
            taps += 1
            self.macro_wait(max(0.0, self.A_INTERVAL - self.A_TAP))
        self.macro_mark(f'{tag}补协奏(cap,A x{taps},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 补了 {time.time() - start:.1f}s 协奏还是没满, 认赔交棒(打不到怪?)')
        return False

    def sk_r_after_press(self, sk, tag):
        """守 R 按过一下之后: 盯 HUD 消失, 没消失就补按; 放出去了就等演完。返回放没放出去。"""
        if self.watch_hud_gone(self.SK_R_WATCH, tag=f'{tag}R'):
            sk.record_liberation_use()
            fired = True
        else:
            self.logger.info(f'{tag}: 守岸人 R 按了没出手(收招吞了/能量不够) — 补按')
            fired = self.fire_r(sk, f'{tag}R补按', self.SK_R_RESCUE)
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SK_R_ANIM_MIN, time_out=self.SK_R_ANIM_CAP)
        return fired

    def sk_hand_to_xin(self, sk, xn, tag):
        """守岸人 -> 心: 协奏满了才交棒(心的变奏入场给 60 应世心), 读一下不满就直接补(不干等)。"""
        full = self.read_con(self.SK_CON_ENOUGH, settle=self.SK_CON_READ, tag=f'{tag}协奏') >= self.SK_CON_ENOUGH
        if not full:
            full = self.sk_ensure_con(sk, tag)
        if not full:
            self.logger.warning('【协奏没满就交棒】守 -> 心: 心拿不到变奏入场, 应世心要多 A 几下')
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full), 'shorekeeper -> hsin')
        return full

    def sk_dodge_forward(self, tag):
        """往前闪避(按住 W + 右键): 断重击后摇, 顺便贴近怪。"""
        task = self.task
        task.send_key_down('w')
        try:
            self.macro_wait(self.WD_W_LEAD)
            task.mouse_down(key='right')
            self.macro_wait(self.WD_DODGE_HOLD)
            task.mouse_up(key='right')
            self.macro_wait(self.WD_W_TAIL)
        finally:
            task.send_key_up('w')
        self.macro_mark(f'{tag}闪避')

    def sk_ready_for_lib(self, sk, tag):
        """放 R 之前: 协奏不到 SHORE_LIB_MIN_CON 就补一发强化重击(第二发多半被打断了)。只补一次。"""
        con = self.read_con(self.SHORE_LIB_MIN_CON, settle=self.SHORE_LIB_CON_SETTLE, click=True)
        if con >= self.SHORE_LIB_MIN_CON:
            self.macro_mark(f'{tag}R保底(con{con:.2f},够)')
            return True
        self.macro_mark(f'{tag}R保底(con{con:.2f},补)')
        if self.sk_a_until(self.SHORE_LIB_RESCUE_CAP, lambda: self.sk_forte_full(sk), tag=f'{tag}补等回路'):
            self.sk_heavy(sk, tag=f'{tag}补重击')
        return False

    def seg_sk_open(self, sk, xn, chars, tag):
        """守岸人·启动段(自闭启动, 照搬日月守/露丽守): 3A -> E -> 2A -> 重击1 -> 闪避 -> A 到回路满 -> 重击2 -> Q
        -> [协奏不到 0.70 补一发强化重击] -> R -> 演完 -> 协奏满了变奏心。

        第一发重击锚在按下 E 的那一刻(用相对间隔往下串误差会累加)。
        """
        self.macro_spam(self.SHORE_A3_WINDOW, self.A_INTERVAL, tag=f'{tag}3A')
        e_at = time.time()
        if self.press_res_until_cd(sk, 0.80, f'{tag}E', ready_grace=0.15):
            sk.record_resonance_use()
        left = e_at + self.SHORE_E_TO_HEAVY - time.time()
        if left > 0:
            self.macro_spam(left, self.A_INTERVAL, tag=f'{tag}2A')
        else:
            self.macro_mark(f'{tag}2A(late{-left:.2f}s)')
        self.task.next_frame()
        if self.sk_forte_full(sk) or self.sk_a_until(self.SHORE_HEAVY1_WAIT, lambda: self.sk_forte_full_once(sk),
                                                     tag=f'{tag}重击1前等回路满'):
            self.hold_heavy(sk, self.SHORE_HEAVY_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}重击1')
        else:
            self.macro_mark(f'{tag}重击1不按(回路没满)')
        self.sk_dodge_forward(tag)
        self.macro_wait(self.SHORE_DODGE_TO_A)
        if self.sk_a_until(self.SHORE_FORTE_WAIT_CAP, lambda: self.sk_forte_full_once(sk), tag=f'{tag}等回路满'):
            self.hold_heavy(sk, self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}重击2')
            self.macro_wait(self.SHORE_HEAVY_TO_QR)
        else:
            self.macro_mark(f'{tag}重击2不按(回路没满)')
            self.logger.warning(f'{tag}: 守岸人 A 了 {self.SHORE_FORTE_WAIT_CAP:.0f}s 回路没满(打不到怪?), 第二发重击不按')
        sk.send_echo_key()
        sk.record_echo_use()
        self.macro_mark(f'{tag}Q')
        self.sk_ready_for_lib(sk, tag)
        # R 一定要放出来(领域 + 协奏 20): 跟循环段一样, 不亮就攒能量, 亮了没出就重按
        if self.ensure_r(sk, tag, self.SK_R_RESCUE, self.SK_OPEN_R_CAP,
                         lambda rounds: self.sk_build_energy(sk, tag, rounds)):
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SK_R_ANIM_MIN, time_out=self.SK_R_ANIM_CAP)
        else:
            self.logger.warning(f'{tag}: 守岸人启动段 R 没放出去, 照常往下走')
        return self.sk_hand_to_xin(sk, xn, tag)

    def sk_con_watch(self):
        """返回 (enough, state): enough() 每 SK_L_CON_EVERY 读一次协奏, 连两次 >= SK_L_CON_QR 返回 True。"""
        st = {'next': 0.0, 'hits': 0, 'val': 0.0}

        def enough():
            now = time.time()
            if now >= st['next']:
                st['next'] = now + self.SK_L_CON_EVERY
                try:
                    v = float(self.task.get_current_con())
                except Exception:
                    v = 0.0
                st['val'] = v
                st['hits'] = st['hits'] + 1 if v >= self.SK_L_CON_QR else 0
            return st['hits'] >= 2
        return enough, st

    def seg_sk_loop(self, sk, xn, tag):
        """守岸人·循环段(强化变奏入场, HUD 回来之后): A 到回路满 -> 强化重击 -> E -> Q -> R -> 变奏心。
        中途协奏连两次读到 >= SK_L_CON_QR 就跳过没打完的, 直接 Q R。"""
        if not sk.has_intro:
            self.macro_mark(f'{tag}无变奏')
        enough, cst = self.sk_con_watch()

        def full():
            try:
                return bool(sk.is_mouse_forte_full())
            except Exception:
                return False
        # 回路满了才重击: 进场就满直接重击(0.3s); 没满 A 到满(不满按重击会化蝶), 期间协奏够了就不攒了
        self.task.next_frame()
        con_ok = False
        if self.sk_forte_full(sk):
            self.macro_mark(f'{tag}回路已满')
            self.sk_heavy(sk, tag=f'{tag}强化重击')
            con_ok = enough()
        elif self.sk_a_until(self.SK_L_FULL_CAP, lambda: full() or enough(), tag=f'{tag}A到回路满'):
            if cst['hits'] >= 2:
                con_ok = True
            else:
                self.sk_heavy(sk, tag=f'{tag}强化重击')
                con_ok = enough()
        else:
            self.macro_mark(f'{tag}回路没满不重击')
            self.logger.warning(f'{tag}: 守岸人 {self.SK_L_FULL_CAP:.1f}s 内回路没攒满(打不到怪?), 不重击(不满会化蝶)')
        if con_ok:
            self.macro_mark(f'{tag}协奏够了(con{cst["val"]:.2f}), 直接QR')
        # 插段按过 E 的话这里还在冷却(16s), 不空按; E 的协奏插段已经拿了(守岸人协奏切走不清)
        if con_ok:
            pass
        elif self.sk_e_ready(sk):
            if self.press_res_until_cd(sk, self.SK_L_E_CAP, f'{tag}E', ready_grace=0.15):
                sk.record_resonance_use()
        else:
            self.macro_mark(f'{tag}E冷却中(插段用过)')
        self.spam_key(sk.send_echo_key, self.SK_L_Q_WINDOW, tag=f'{tag}Q', presses=2)
        sk.record_echo_use()
        if self.ensure_r(sk, tag, self.SK_R_RESCUE, self.SK_R_ENSURE_CAP,
                         lambda rounds: self.sk_build_energy(sk, tag, rounds)):
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.SK_R_ANIM_MIN, time_out=self.SK_R_ANIM_CAP)
        return self.sk_hand_to_xin(sk, xn, tag)

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

    def xn_fill_until_ready(self, tag):
        """连续点普攻(XN1_TAP_EVERY 一下), 每帧读应世心条, 打满 XN1_MIN_A 下且到线就返回。
        返回 'ready' 到线 / 'late' 到点差一点 / 'cap' 补到上限 / 'blind' 条读不出来。"""
        start = time.time()
        taps = 0
        next_tap = 0.0
        hits = 0
        valid_seen = False
        last_r = 0.0
        trace = []
        while True:
            now = time.time()
            if now >= next_tap:
                self.tap(self.XN1_A_HOLD)
                taps += 1
                next_tap = time.time() + max(0.0, self.XN1_TAP_EVERY - self.XN1_A_HOLD)
            self.task.next_frame()
            el = time.time() - start
            if self.hud_ok():
                full, L, R = self.xn_ying()
                if len(trace) < 30:
                    trace.append(f'{el:.2f}:{"满" if full else ""}{L:.2f}/{R:.2f}')
                if full or L >= self.XN_GAUGE_VALID:
                    valid_seen = True
                    last_r = 1.0 if full else R
                    hits = hits + 1 if (full or R >= self.XN_YING_READY) else 0
                    if taps >= self.XN1_MIN_A and hits >= self.XN1_GAUGE_CONFIRM:
                        self.macro_mark(f'{tag}应世心到线({R:.2f},A x{taps},{el:.2f}s)')
                        return 'ready'
                    if el >= self.XN1_LATE_AT and R >= self.XN_YING_LATE:
                        self.macro_mark(f'{tag}应世心将满({R:.2f},A x{taps},{el:.2f}s)')
                        return 'late'
            if el >= self.XN1_BLIND_AT and not valid_seen:
                self.macro_mark(f'{tag}应世心读不到(A x{taps}, 照按)')
                self.logger.info(f'{tag}: 应世心条读不出来 — {" ".join(trace)}')
                return 'blind'
            if el >= self.XN1_FILL_CAP:
                self.macro_mark(f'{tag}应世心不够(cap,{last_r:.2f},A x{taps})')
                self.logger.info(f'{tag}: 应世心补到上限还没到线 — {" ".join(trace)}')
                return 'cap'
            self.macro_wait(0.02)

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

    def seg_xn1(self, xn, jx, tag):
        """心·第一段(变奏·应世相): [上场就满 -> 直接按住] / 连续 A 到应世心到线(至少两下) -> 按住出步红尘
        -> 转相解锁 -> Q -> 转相 -> 演完 -> 同奏切今汐。"""
        sw = self.switch_press_at
        full_on_entry = False
        if self.XN1_SKIP_A_WHEN_FULL:
            self.wait_until_time(sw + self.XN1_FULL_FROM)
            hits = 0
            while time.time() < sw + self.XN1_A1_AT:
                self.task.next_frame()
                if self.hud_ok() and self.xn_ying()[0]:
                    hits += 1
                    if hits >= 2:
                        full_on_entry = True
                        break
                else:
                    hits = 0
                self.macro_wait(0.02)
        if full_on_entry:
            self.macro_mark(f'{tag}上场应世心就满({time.time() - sw:.2f}s)')
            self.wait_until_time(sw + self.XN1_FULL_HOLD_AT)
        else:
            self.wait_until_time(sw + self.XN1_A1_AT)
            self.xn_fill_until_ready(tag)

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
            # 没看到清空: 读图标, 连着亮几帧才算; 读不到就到宏表那一刻照按(没解锁时按 R 什么都不发生)
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
        xn.send_echo_key(down_time=0.05)
        xn.record_echo_use()
        self.macro_wait(0.05)
        fired = self.fire_r(xn, f'{tag}转相', self.XN_R_WINDOW)
        if not fired:
            fired = self.xn_r1_rescue(xn, tag)
        if not fired:
            self.logger.warning(f'{tag}: 补了 {self.XN1_R1_RESCUE_CAP:.0f}s 转相还是没放出去 — 今汐拿不到同奏, 照常往下走')
        else:
            self.wait_hud_back(tag=f'{tag}转相演出', min_gone=self.XN_R_ANIM_MIN, time_out=self.XN_R_ANIM_CAP)
        # 同奏 5 秒: 转相一演完立刻切, 今汐不用满协奏也是变奏入场
        self.need(self.switch_retry(jx, f'{tag}切今汐', assume_con=fired), 'hsin -> jinhsi')

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

    def xn_hold_until_gone(self, cap, tag, watch_ready=False):
        """按住左键到队伍 HUD 消失(临寰宇出手)。消失后再按 XN2_RELEASE_AFTER_GONE 秒松手。返回出手没有。

        watch_ready: 按住期间看重击位, 人像出来 XN2_READY_REPRESS 还没出手 = 这一下按住断了(被打断), 松开重按。
        """
        si = self.xn_attack_down()
        start = time.time()
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        gone = 0
        fired = False
        ready_since = None
        repress = 0
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
                    if watch_ready and repress < self.XN2_REPRESS_MAX:
                        if self.xn_slot_state() == 'ready':
                            ready_since = ready_since or time.time()
                            if time.time() - ready_since >= self.XN2_READY_REPRESS:
                                repress += 1
                                ready_since = None
                                self.release_attack()
                                self.macro_wait(0.05)
                                self.xn_attack_down()
                                self.macro_mark(f'{tag}人像出来没出手,重按#{repress}({time.time() - start:.2f}s)')
                        else:
                            ready_since = None
                self.macro_wait(0.02)
            if fired:
                self.macro_mark(f'{tag}临寰宇出手({time.time() - start:.2f}s)')
                self.macro_wait(self.XN2_RELEASE_AFTER_GONE)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}松开({"出手" if fired else "没出"})')
        return fired

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

    def xn_tongyu_phase(self, tag, cap):
        """统御众机里一直普攻到重击位换成人像(照世心打空)。返回 'ready'(人像连读几帧) / 'cap'(到上限没看到人像)。"""
        start = time.time()
        n = 0
        next_click = 0.0
        hits = 0
        why = 'cap'
        last = None
        statue_seen = False
        while time.time() - start < cap:
            now = time.time()
            if now >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + max(0.0, self.XN2_TY_A_EVERY - self.A_TAP)
            self.task.next_frame()
            st = self.xn_slot_state()
            last = st
            statue_seen = statue_seen or st == 'statue'
            hits = hits + 1 if st == 'ready' else 0
            if hits >= self.XN2_READY_CONFIRM:
                why = 'ready'
                break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}照世心{"打空" if why == "ready" else "没看到打空(cap)"}'
                        f'(A x{n},{time.time() - start:.2f}s{"" if statue_seen else ",没见过雕像"})')
        if why != 'ready':
            self.logger.info(f'{tag}: {cap:.1f}s 没看到重击位换人像(最后读成 {last}, '
                             f'雕像{getattr(self, "_ty_score", -1):.2f} 人像{getattr(self, "_rd_score", -1):.2f})')
        return why

    def seg_xn2(self, xn, jx, intro, tag):
        """心·第二段(变奏·照世相, 直接进统御众机): 4A -> 按住到临寰宇(按住断了就重按) -> 演完
        -> 万阙垂天(认 hsin_lib2) -> 演完 -> 切今汐。"""
        self.wait_until_time(self.switch_press_at + self.XN2_LEAD)
        chars = {'XN': xn, 'JX': jx}
        self.play_rows(self.XN2_ROWS, f'{tag}A', chars)
        fired = self.xn_hold_until_gone(self.XN2_HOLD_CAP, f'{tag}按住', watch_ready=True)
        for attempt in range(1, 1 + self.XN2_RETRIES):
            if fired:
                break
            # 按满上限没出(照世心没打空 / 被打断): 普攻到人像再按。没拿到变奏(没进统御众机)时先按一下 E
            if attempt == 1 and not intro:
                self.spam_key(xn.send_resonance_key, 0.2, interval=0.06, tag=f'{tag}补E(没变奏)')
                xn.record_resonance_use()
            why = self.xn_tongyu_phase(f'{tag}补#{attempt}', self.XN2_RETRY_CAP)
            fired = self.xn_hold_until_gone(self.XN2_READY_HOLD if why == 'ready' else self.XN2_BLIND_HOLD,
                                            f'{tag}重按#{attempt}', watch_ready=True)
        if not fired:
            self.logger.warning(f'{tag}: 临寰宇一直没出手, 这一轮不放万阙垂天(按 R 会放成借相)')
        else:
            self.wait_hud_back(tag=f'{tag}临寰宇演出', min_gone=self.XN2_ANIM_MIN, time_out=self.XN2_ANIM_CAP)
            # 【R 只认 hsin_lib2】: 照世相里 R 一直亮(借相·应世), 只有临寰宇之后、能量够了才换成万阙垂天的图标
            unlocked, t = self.xn_wait_lib2(self.XN2_LIB2_CAP)
            self.macro_mark(f'{tag}万阙垂天{"解锁" if unlocked else "图标没认出(能量不够?)"}({t:.2f}s)')
            if not unlocked:
                unlocked, t = self.xn_wait_lib2(self.XN2_R2_BUILD_CAP, build=True)
                self.macro_mark(f'{tag}普攻攒大招能量({"认出万阙垂天" if unlocked else "cap"},{t:.2f}s)')
            fired = False
            for attempt in range(2):
                if not unlocked:
                    break
                if attempt == 0:
                    self.xn_pre_r2_con(tag)
                fired = self.fire_r(xn, f'{tag}万阙垂天' if attempt == 0 else f'{tag}万阙垂天重按', self.XN_R_WINDOW)
                if fired:
                    self.wait_hud_back(tag=f'{tag}万阙垂天演出', min_gone=self.XN_R_ANIM_MIN,
                                       time_out=self.XN_R_ANIM_CAP)
                    break
                # 认出来了却没放出去(收招吞了 / 能量其实差一点): 再攒一下, 还认得出才重按
                unlocked, t = self.xn_wait_lib2(1.50, build=True)
            if not fired:
                self.logger.warning(f'{tag}: 万阙垂天没放出去({"能量攒不满/图标没认出, 不照按(会放成借相)" if not unlocked else "按了没出"})')
        full = self.xn_ensure_con(xn, tag)
        self.need(self.switch_retry(jx, f'{tag}切今汐', assume_con=full), 'hsin -> jinhsi')

    def xn_wait_lib2(self, cap, build=False):
        """等 R 位变成万阙垂天(hsin_lib2 连读几帧)。build: 期间点普攻攒大招能量。返回 (认出没有, 用时)。"""
        start = time.time()
        hits = 0
        next_tap = 0.0
        while time.time() - start < cap:
            if build and time.time() >= next_tap:
                self.tap()
                next_tap = time.time() + max(0.0, self.A_INTERVAL - self.A_TAP)
            self.task.next_frame()
            if self.hud_ok():
                hits = hits + 1 if self.xn_icon('hsin_lib2') else 0
                if hits >= self.XN2_LIB2_READS:
                    return True, time.time() - start
            self.macro_wait(0.02)
        return False, time.time() - start

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

    def xn_ensure_con(self, xn, tag):
        """心切今汐之前协奏要真满: 读一下, 不满就 E(这条轴里心的 E 没用过) -> 普攻, 每下之后读一次。返回满没满。"""
        start = time.time()
        con = self.read_con(1.0, settle=self.XN2_CON_READ)
        if con >= 1.0:
            return True
        samples = [f'0:{con:.2f}']
        taps = 0
        used_e = False
        while time.time() - start < self.XN2_CON_CAP:
            if not used_e and time.time() - getattr(xn, 'last_res', 0) >= self.XN_E_CD:
                used_e = True
                self.spam_key(xn.send_resonance_key, 0.26, interval=0.06, tag=f'{tag}补协奏E')
                xn.record_resonance_use()
            else:
                self.tap()
                taps += 1
                self.macro_wait(max(0.0, self.A_INTERVAL - self.A_TAP))
            con = self.read_con(1.0, settle=0.03)
            if len(samples) < 20:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= 1.0:
                self.macro_mark(f'{tag}补协奏(满,E{int(used_e)},A x{taps},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}补协奏(cap,E{int(used_e)},A x{taps},{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 心补了 {time.time() - start:.1f}s 协奏还是没满, 今汐拿不到变奏(第二段走普攻第4段补神霓飞芒)'
                            f' — {" ".join(samples)}')
        return False

    # ==================== 今汐 ====================

    def jx_r_block(self, jx, tag):
        """今汐大招: 按住 JX_R_HOLD(落在入场不响应输入里也能被接住) -> 盯 HUD 消失 -> 没出手补按 -> 演完(调用方立刻按神霓飞芒)。

        返回放出去没有。R 不亮(能量没满)就不按, 记下来下一段再补。
        """
        if not self.lib_ready_reads(jx, self.JX_R_READS):
            self.jx_r_pending = True
            self.macro_mark(f'{tag}R没亮(下一段补)')
            return False
        jx.send_liberation_key(down_time=self.JX_R_HOLD)
        self.macro_mark(f'{tag}R')
        if self.watch_hud_gone(self.JX_R_WATCH, tag=f'{tag}R'):
            jx.record_liberation_use()
            fired = True
        elif not self.lib_ready_reads(jx, self.JX_R_READS):
            # 实机 10-05 16:36 循环轴: 读成亮按了没出, 补按连发 1.5s(x30)图标一直是暗的 —— 能量其实没满, 人愣在那
            self.macro_mark(f'{tag}R没出(其实没亮,下一段补)')
            fired = False
        else:
            fired = self.fire_r(jx, f'{tag}R补按', self.JX_R_RESCUE)
        if fired:
            self.jx_r_pending = False
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.JX_R_ANIM_MIN, time_out=self.JX_R_ANIM_CAP)
        else:
            self.jx_r_pending = True
        return fired

    def jx_e_icon(self, jx):
        """今汐 E 位现在是哪个图标: 'shenni' / 'zhutian'(含转 CD) / 'liuguang' / 'jinglong'(严格阈值) /
        '?'(认不出) / None(HUD 不在或今汐不在场)。读当前帧, 调用方负责 next_frame。
        """
        try:
            in_team, index, _count = self.task.in_team()
            if not in_team or index != jx.index:
                return None
            self.hud_seen_at = time.time()
            tv = getattr(self, '_jx_glyph_vecs', None)
            if tv is None:
                raw = base64.b64decode(''.join(self.JX_GLYPHS))
                arr = np.frombuffer(raw, np.uint8).reshape(len(self.JX_GLYPH_LABELS), 16, 16).astype(np.float32)
                tv = self._jx_glyph_vecs = [(lab, self.glyph_vec(a)) for lab, a in zip(self.JX_GLYPH_LABELS, arr)]
            box = self.task.get_box_by_name('box_resonance')
            frame = self.task.frame
            cx, cy = int(box.x + box.width / 2), int(box.y + box.height / 2)
            h = int(box.height * 1.1)
            icon = frame[max(0, cy - h):cy + h, max(0, cx - h):cx + h, :3]
            g = cv2.cvtColor(cv2.resize(icon, (96, 96), interpolation=cv2.INTER_AREA),
                             cv2.COLOR_BGR2GRAY).astype(np.float32)
            yy, xx = np.mgrid[:96, :96]
            g = g * (((xx - 48) ** 2 + (yy - 44) ** 2) <= 25 ** 2)
            v = self.glyph_vec(cv2.resize(g[18:70, 22:74], (16, 16), interpolation=cv2.INTER_AREA))
            score = {}
            for lab, t in tv:
                lab = 'zhutian' if lab == 'cd' else lab
                score[lab] = max(score.get(lab, -1.0), float(v @ t))
            order = sorted(score.items(), key=lambda kv: -kv[1])
            (l1, s1), (_l2, s2) = order[0], order[1]
            if l1 == 'jinglong':
                ok = s1 >= self.JX_JINGLONG_MIN and s1 - s2 >= self.JX_JINGLONG_MARGIN
                return 'jinglong' if ok else '?'
            if s1 < self.JX_ICON_MIN or s1 - s2 < self.JX_ICON_MARGIN:
                return '?'
            return l1
        except Exception as e:
            self.logger.info(f'jx_e_icon probe failed ({e})')
            return '?'

    def jx_enter_lingxiao(self, jx, tag, hold=0.08):
        """按神霓飞芒进乘岁凌霄: E 位是神霓飞芒才按, 看到逐天取月(连两帧)就停。返回进没进。

        E 位是流光夕影(没吃到变奏 / 5s 窗口过了) -> 普攻打到第 4 段、E 位出神霓飞芒再按。
        """
        start = time.time()
        first = start
        last = -99.0
        presses = zt = lg = 0
        e_at = None
        fallback_done = False
        trace = []
        next_cd_read = 0.0
        in_cd = False
        cd_marked = False
        cd_taps = 0
        next_cd_tap = 0.0
        while time.time() - start < self.JX_LX_CAP:
            self.task.next_frame()
            if self.hud_ok() and self.break_tick():
                start += self.jx_do_break(tag)
                continue
            lab = self.jx_e_icon(jx)
            now = time.time()
            if len(trace) < 30:
                trace.append(f'{now - start:.2f}:{lab or "-"}')
            if lab not in ('zhutian', 'shenni', 'liuguang'):
                if now >= next_cd_read:
                    next_cd_read = now + self.JX_LX_CD_EVERY
                    in_cd = self.read_cd('resonance') > 0
                if in_cd and now - first < self.JX_LX_LOCK_CAP:
                    # E 在冷却: 不按 E、不计时、不算按过; 点普攻
                    if not cd_marked:
                        cd_marked = True
                        self.macro_mark(f'{tag}E冷却中(A)')
                    if now >= next_cd_tap:
                        self.tap()
                        cd_taps += 1
                        next_cd_tap = time.time() + self.JX_LX_CD_A_EVERY
                    start = time.time()
                    self.macro_wait(0.02)
                    continue
            else:
                if cd_marked and in_cd:
                    self.macro_mark(f'{tag}E冷却完(A x{cd_taps},{time.time() - first:.2f}s)')
                in_cd = False
            zt = zt + 1 if lab == 'zhutian' else 0
            lg = lg + 1 if lab == 'liuguang' else 0
            if zt >= 2:
                self.lx_at = e_at or now
                self.macro_mark(f'{tag}进凌霄(E x{presses},{now - start:.2f}s)')
                return True
            if lg >= 2 and not fallback_done:
                fallback_done = True
                self.macro_mark(f'{tag}E位是流光夕影(没神霓飞芒)')
                self.jx_fill_shenni(jx, tag)
                start = time.time()
                last = -99.0
                lg = 0
                continue
            gap = self.JX_LX_REPRESS if lab == 'shenni' else self.JX_LX_REPRESS_UNKNOWN
            # 'jinglong' 在这里只可能是按出神霓飞芒那一下的闪光, 不按
            if lab in ('shenni', '?') and now - last >= gap:
                jx.send_resonance_key(down_time=hold if presses == 0 else 0.05)
                jx.record_resonance_use()
                presses += 1
                last = time.time()
                e_at = e_at or last
            self.macro_wait(0.02)
        if e_at is not None and lg == 0:
            # E 位一直认不出来但按过神霓飞芒: 当进了 —— 实机 10-05 14:16 第二段 1.8s 全是 '?', 其实进了
            # (凌霄第 4 段 1.6s 就打出来了); 判成没进的话插段时间预算是 0, 守岸人只 A 两下就切回去
            self.lx_at = e_at
            self.macro_mark(f'{tag}E位认不出(按过E x{presses}, 当进了凌霄)')
            self.logger.warning(f'{tag}: 今汐 E 位认不出来, 按过神霓飞芒当进了凌霄 — {" ".join(trace)}')
            return True
        self.macro_mark(f'{tag}没进凌霄(E x{presses})')
        self.logger.warning(f'{tag}: 今汐没进乘岁凌霄, 惊龙破空多半出不来 — {" ".join(trace)}')
        return False

    def jx_fill_shenni(self, jx, tag):
        """普通普攻打到第 4 段(第 4 段给 5s 神霓飞芒), E 位出神霓飞芒就停。"""
        start = time.time()
        hits = taps = 0
        next_tap = 0.0
        while time.time() - start < self.JX_LX_A4_CAP:
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + self.JX_A4_TAP_EVERY
            self.task.next_frame()
            hits = hits + 1 if self.jx_e_icon(jx) == 'shenni' else 0
            if hits >= 2:
                self.macro_mark(f'{tag}普攻到第4段(出神霓飞芒,A x{taps},{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}普攻到第4段(cap,A x{taps})')
        return False

    def jx_to_youlong(self, jx, tag):
        """凌霄里一直点普攻到第 4 段打出来(E 位换成惊龙破空, 连两帧)。返回判到没有。

        刚切回今汐的 SKILL_BAR_SETTLE 秒里不读(技能栏还是守岸人的, 守岸人的 E 被认成过惊龙破空)。
        认到惊龙破空(严格阈值)就算; 见过逐天取月之后换成流光夕影/神霓飞芒也算; 认不出('?')的帧不算。
        读了 JX_A4_SEE_GRACE 秒既没见过逐天取月也没认到惊龙破空就不等了, 交给惊龙破空那一步的补救。
        """
        start = time.time()
        read_from = max(start, getattr(jx, 'last_switch_in_time', 0.0) + self.SKILL_BAR_SETTLE)
        seen = False
        gone = taps = 0
        next_tap = 0.0
        while time.time() - start < self.JX_A4_CAP:
            now = time.time()
            if now >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + self.JX_A4_TAP_EVERY
            self.task.next_frame()
            if time.time() < read_from:
                self.macro_wait(0.02)
                continue
            if self.hud_ok() and self.break_tick():
                dur = self.jx_do_break(tag)
                start += dur
                read_from = time.time()
                continue
            lab = self.jx_e_icon(jx)
            if lab == 'zhutian':
                seen = True
                gone = 0
            elif lab == 'jinglong' or (lab in ('liuguang', 'shenni') and seen):
                gone += 1
                if gone >= 2:
                    self.macro_mark(f'{tag}凌霄第4段(A x{taps},{time.time() - start:.2f}s)')
                    return True
            elif lab is not None and lab != '?':
                gone = 0
            if not seen and time.time() - read_from >= self.JX_A4_SEE_GRACE:
                self.macro_mark(f'{tag}没见过逐天取月(A x{taps})')
                return False
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}凌霄第4段(cap,A x{taps})')
        return False

    def jx_big_e(self, jx, hold, tag):
        """惊龙破空: 按一下 -> 盯 HUD 消失; 没出手就边 A 边按 E 补 -> 演完。

        返回 (出手没有, 拿没拿到同奏)。同奏 25 秒才给一次, 拿到了切人下一个人必变奏, 不用等协奏。
        """
        # 第 4 段刚打出来时动作还没收完, 单按一下会被吞: E 位不是逐天取月就隔 JX_BIG_E_REPRESS 按一次到 HUD 消失;
        # 还是逐天取月(第 4 段没打出来)就只点普攻 —— 这时按 E 放出来的是逐天取月
        start = time.time()
        presses = taps = gone = 0
        last = -99.0
        fired = False
        while time.time() - start < self.JX_BIG_E_WATCH:
            self.task.next_frame()
            if not self.hud_ok():
                gone += 1
                if presses and gone >= self.LIB_GONE_CONFIRM:
                    fired = True
                    break
                self.macro_wait(0.02)
                continue
            gone = 0
            lab = self.jx_e_icon(jx)
            now = time.time()
            if lab == 'zhutian':
                self.tap()
                taps += 1
            elif now - last >= self.JX_BIG_E_REPRESS:
                jx.send_resonance_key(down_time=hold if presses == 0 else 0.05)
                presses += 1
                last = time.time()
            self.macro_wait(0.02)
        jx.record_resonance_use()
        self.macro_mark(f'{tag}惊龙破空({"出手" if fired else "没出"},E x{presses},A x{taps},{time.time() - start:.2f}s)')
        if not fired:
            self.logger.info(f'{tag}: 惊龙破空没出手(韶光没满 / 没进凌霄 / 被吞) — 边 A 边按 E 补')
            fired = self.press_until_hud_gone(jx, self.JX_BIG_E_RESCUE, tag=f'{tag}惊龙破空补救')
        granted = False
        if fired:
            cast_at = time.time()
            since = cast_at - self.tongzou_at
            granted = since >= self.JX_TONGZOU_CD
            if granted:
                self.tongzou_at = cast_at
            self.macro_mark(f'{tag}同奏{"有" if granted else f"没有(距上次{since:.1f}s)"}')
            self.wait_hud_back(tag=f'{tag}惊龙破空演出', min_gone=self.JX_BIG_ANIM_MIN, time_out=self.JX_BIG_ANIM_CAP)
        return fired, granted

    def press_until_hud_gone(self, char, cap, tag):
        """一直按 E(间隙点普攻)到队伍 HUD 连续消失。只有 HUD 在场时发的 E 才算按过。"""
        start = time.time()
        ne = live = gone = clicks = 0
        next_click = 0.0
        while time.time() - start < cap:
            self.task.next_frame()
            if not self.hud_ok():
                if live > 0:
                    gone += 1
                    if gone >= self.LIB_GONE_CONFIRM:
                        self.macro_mark(f'{tag}_ok(E x{ne},A x{clicks},{time.time() - start:.2f}s)')
                        return True
                self.macro_wait(0.02)
                continue
            gone = 0
            char.send_resonance_key()
            ne += 1
            live += 1
            step_end = time.time() + self.E_RESEND_STEP
            while True:
                now = time.time()
                if now >= step_end:
                    break
                if now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - now))
        self.macro_mark(f'{tag}_NOHUDGONE(E x{ne},A x{clicks},{cap:.1f}s)')
        return False

    def jx_con_gate(self, tag):
        """惊龙破空演完, 交棒前协奏没满就边 A 边等一会儿(心/守要的是变奏入场)。返回满没满。"""
        if self.read_con(1.0, settle=0.15) >= 1.0:
            return True
        return self.wait_con_full('今汐', self.JX_CON_CAP, tag=f'{tag}协奏')

    def seg_jx1(self, jx, sk, xn, tag):
        """今汐·第一段(同奏变奏入场): Q -> A -> R -> 神霓飞芒 -> 凌霄3A -> [守 2A] -> 凌霄A4 + A -> 惊龙破空 -> 变奏心。
        返回心带没带变奏。"""
        chars = {'SK': sk, 'JX': jx, 'XN': xn}
        self.wait_until_time(self.switch_press_at + self.JX1_SWITCH_GAP)
        self.play_rows(self.JX1_ROWS, tag, chars)
        if not self.jx_r_block(jx, tag):
            self.wait_until_time(self.switch_press_at + self.JX_NO_R_E_AT)
        self.jx_enter_lingxiao(jx, tag)
        if self.SK_HOP1:
            self.play_rows(self.JX1_ROWS_BEFORE_HOP, tag, chars)
            self.play_rows(self.SK_HOP1_ROWS, tag, chars)
        self.jx_to_youlong(jx, tag)
        self.jx_tongzou_wait(tag)
        fired, granted = self.jx_big_e(jx, self.JX1_BIG_E_HOLD, tag)
        full = granted or self.jx_con_gate(tag)
        self.need(self.switch_retry(xn, f'{tag}切心', assume_con=full), 'jinhsi -> hsin')
        return full

    def break_signals(self):
        """(怪身上 F 提示, 顶部谐度满)。读当前帧。F 也是拾取/交互键, 中间那个提示要排除拾取。"""
        task = self.task
        center = full = False
        try:
            full = bool(task.find_one('f_break_full', threshold=self.JX_BREAK_FULL_MIN))
            hit = task.find_one('f_break', box=task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True),
                                target_height=720, threshold=self.JX_BREAK_CENTER_MIN)
            center = bool(hit) and not task.is_pick_f()
        except Exception as e:
            self.logger.info(f'处决判据读失败 {e}')
        return center, full

    def break_reset(self):
        self._brk_center = 0
        self._brk_full_since = None

    def break_tick(self):
        """每帧调一次(调用方负责 next_frame、HUD 在场)。返回现在能不能处决。"""
        if not self.JX_BREAK or time.time() - getattr(self, 'jx_broke_at', -99.0) < self.JX_BREAK_COOLDOWN:
            return False
        center, full = self.break_signals()
        self._brk_center = getattr(self, '_brk_center', 0) + 1 if center else 0
        since = getattr(self, '_brk_full_since', None)
        self._brk_full_since = (since or time.time()) if full else None
        return self._brk_center >= self.JX_BREAK_CONFIRM or (
            self._brk_full_since is not None and time.time() - self._brk_full_since >= self.JX_BREAK_FULL_HOLD)

    def jx_do_break(self, tag):
        """按 F 处决(最多 JX_BREAK_PRESSES 下, 提示都没了就停), 从第一下起等满 JX_BREAK_ANIM(无敌期间切不了人)。
        返回花了多久。不走框架的 task.f_break(): 那个会连点左键、至少卡 0.5s(见秧千穗那条轴)。"""
        start = time.time()
        presses = 0
        while presses < self.JX_BREAK_PRESSES:
            self.task.send_key('f', down_time=0.05)
            presses += 1
            self.macro_wait(0.10)
            self.task.next_frame()
            if not any(self.break_signals()):
                break
        try:
            self.task.can_break = False
        except Exception:
            pass
        self.jx_broke_at = time.time()
        self.break_reset()
        # 帧表说全局时停里 buff 冻结, 但乘岁凌霄算不算没实证 —— 插段预算不顺延(宁可插段短一点, 别让凌霄过期)
        self.macro_wait(max(0.0, start + self.JX_BREAK_ANIM - time.time()))
        self.macro_mark(f'{tag}处决(F x{presses},{time.time() - start:.2f}s)')
        return time.time() - start

    def jx_rows_watch_break(self, rows, tag):
        """宏表普攻照打(按绝对时刻排), 每一格的空档里每帧看处决, 能处决马上处决。返回处决了没有。"""
        broke = False
        t = time.time()
        for _act, hold_ms, gap_ms, *_ in rows:
            self.wait_until_time(t)
            self.tap(hold_ms / 1000.0)
            nxt = t + (hold_ms + gap_ms) / 1000.0
            while time.time() < nxt - 0.02:
                self.task.next_frame()
                if not broke and self.hud_ok() and self.break_tick():
                    self.jx_do_break(tag)
                    broke = True
                    break
                self.macro_wait(0.01)
            t = max(nxt, time.time())
        self.wait_until_time(t)
        return broke

    def jx_break(self, tag, broke=False):
        """第二段切守岸人之前: 3A 里没处决的话再看 JX_BREAK_WAIT, 能处决就处决。返回处决了没有。"""
        if broke or not self.JX_BREAK:
            return broke
        end = time.time() + self.JX_BREAK_WAIT
        while time.time() < end:
            self.task.next_frame()
            if self.hud_ok() and self.break_tick():
                self.jx_do_break(tag)
                return True
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}没处决可用')
        return False

    def jx_ensure_lingxiao(self, jx, tag):
        """插段切回今汐: E 位还是神霓飞芒/流光夕影(连两帧) = 没在凌霄里, 先进凌霄再打第 4 段。"""
        read_from = getattr(jx, 'last_switch_in_time', 0.0) + self.SKILL_BAR_SETTLE
        end = max(time.time(), read_from) + 0.50
        hits = 0
        prev = None
        while time.time() < end:
            self.task.next_frame()
            if time.time() < read_from:
                self.macro_wait(0.02)
                continue
            lab = self.jx_e_icon(jx)
            hits = hits + 1 if (lab is not None and lab == prev) else 0
            prev = lab
            if hits >= 1 and lab == 'zhutian':
                return True
            if hits >= 1 and lab in ('shenni', 'liuguang'):
                self.macro_mark(f'{tag}切回来不在凌霄({lab}), 补进凌霄')
                return self.jx_enter_lingxiao(jx, f'{tag}回来')
            self.macro_wait(0.02)
        return True

    def jx_tongzou_wait(self, tag):
        """惊龙破空要离上一次拿同奏 >= 25s: 不够就在游龙廻光里等(不点, 别把惊龙破空的窗口打乱), 最多 JX_YOULONG_WAIT_CAP。"""
        if self.tongzou_at < 0:
            return
        extra = (self.tongzou_at + self.JX_TONGZOU_CD + self.JX_TONGZOU_MARGIN) - (time.time() + self.JX_BIG_E_LAG)
        if extra > 0.05:
            extra = min(extra, self.JX_YOULONG_WAIT_CAP)
            self.macro_wait(extra)
            self.macro_mark(f'{tag}等同奏{self.JX_TONGZOU_CD:.0f}s({extra:.2f}s)')

    def jx_lx_left(self):
        """乘岁凌霄还剩多久可以花在插段上(留出切回今汐 + 打凌霄第 4 段)。"""
        return (self.lx_at + self.JX_LX_DURATION - 0.6) - (time.time() + 0.15)

    def jx_hop_extra(self, tag):
        """第二段守岸人插段要多打多久: 让第二次惊龙破空离上一次拿同奏 >= 25s(不然守岸人吃不到变奏)。
        插段再长也要在乘岁凌霄结束前把凌霄第 4 段打出去。"""
        if self.tongzou_at < 0:
            return 0.0
        tail = self.JX_TAIL_EST
        need = (self.tongzou_at + self.JX_TONGZOU_CD + self.JX_TONGZOU_MARGIN) - (time.time() + tail)
        return max(0.0, min(need, self.JX_HOP_EXTRA_CAP, self.jx_lx_left()))

    def seg_jx2(self, jx, sk, xn, tag):
        """今汐·第二段: [第一段没开大就在这开] -> 神霓飞芒 -> 凌霄3A -> 处决 -> [守 A到回路满-强化重击(+补到同奏 25s)]
        -> 凌霄A4 + 2A -> 惊龙破空 -> 变奏守。"""
        chars = {'SK': sk, 'JX': jx, 'XN': xn}
        t_seg = time.time()
        if self.jx_r_pending:
            self.wait_until_time(self.switch_press_at + self.JX2_R_AT)
            self.jx_r_block(jx, f'{tag}补')
            self.jx_enter_lingxiao(jx, tag)
        else:
            self.wait_until_time(self.switch_press_at + self.JX2_LEAD)
            self.jx_enter_lingxiao(jx, tag, hold=self.JX2_E_HOLD)
        if self.SK_HOP2:
            broke = self.jx_rows_watch_break(self.JX2_ROWS, tag)
            if not broke and getattr(self, 'jx_broke_at', -99.0) >= t_seg:
                # 这一段前面(等 E 冷却 / 进凌霄)已经处决过, 同一次谐度破坏不会再有, 不再等
                self.macro_mark(f'{tag}这段已处决过')
            else:
                broke = self.jx_break(tag, broke)
            _act, hold_ms, gap_ms, note = self.SK_HOP2_ROWS[0]
            # 插段切不过去(被打断/硬直)就跳过, 今汐接着打凌霄第 4 段 —— 不为了插段放弃整圈
            if self.macro_switch(sk, hold_ms / 1000.0, gap_ms / 1000.0, f'{tag}{note}', required=False):
                if not broke:
                    self.macro_wait(self.SK_HOP_AIR_WAIT)
                # 插段把回路清掉, 循环段守岸人进场再从空攒满清一次(用户给的流程); 凌霄剩的时间不够就不清
                budget = min(self.SK_HOP_FULL_CAP, self.jx_lx_left() - self.SK_HOP_HEAVY_EST)
                self.sk_hop_clear(sk, budget, f'{tag}守')
                extra = self.jx_hop_extra(tag)
                if extra > 0.05:
                    self.sk_fill(sk, extra, tag=f'{tag}守多打(撑到同奏{self.JX_TONGZOU_CD:.0f}s)')
                self.play_rows(self.SK_HOP2_ROWS[-1:], tag, chars)
                self.jx_ensure_lingxiao(jx, tag)
        self.jx_to_youlong(jx, tag)
        self.jx_tongzou_wait(tag)
        fired, granted = self.jx_big_e(jx, self.JX2_BIG_E_HOLD, tag)
        if not granted:
            self.jx_con_gate(tag)

    # ==================== 一圈 ====================

    def run_cycle(self, jx, sk, opener, tag):
        """守岸人(HUD 在场) -> 心 -> 今汐 -> 心 -> 今汐 -> 守岸人变奏入场、HUD 回来。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        chars = {'SK': sk, 'JX': jx, 'XN': self}
        if opener:
            self.seg_sk_open(sk, self, chars, tag=f'{tag}守')
        else:
            self.seg_sk_loop(sk, self, tag=f'{tag}守')
        self.seg_xn1(self, jx, tag=f'{tag}心1')
        intro = self.seg_jx1(jx, sk, self, tag=f'{tag}汐1')
        self.seg_xn2(self, jx, intro, tag=f'{tag}心2')
        self.seg_jx2(jx, sk, self, tag=f'{tag}汐2')
        self.need(self.switch_to_sk(sk, tag=f'{tag}切守'), 'jinhsi -> shorekeeper')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        jx = self.teammate('Jinhsi')
        sk = self.teammate('ShoreKeeper')
        if jx is None or sk is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in (self, jx, sk):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return jx, sk, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'xxs timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 切守岸人 -> 一圈(守岸人启动段)。返回时守岸人刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        jx, sk, previous = got
        self.logger.info(f'xxs opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            self.jx_r_pending = False
            self.tongzou_at = -99.0
            if self.cur is not sk:
                self.need(self.switch_retry(sk, 'op'), 'opener: switch to shorekeeper')
            self.run_cycle(jx, sk, opener=True, tag='op')
            self.logger.info(
                f'xxs opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xxs opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是守岸人(宏散过)就先把场子交给她。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        jx, sk, previous = got
        try:
            if self.cur is not sk:
                self.need(self.switch_to_sk(sk, tag='L接守'), 'loop: hand the field to shorekeeper')
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(jx, sk, opener=False, tag=f'L{n}')
            self.logger.info(
                f'xxs loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'xxs loop broke ({e}) — timeline: {self.macro_timeline()}')
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
                self.logger.info('xxs: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'xxs: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_xxs:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让守岸人先上。"""
        if self.is_in_xxs and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
