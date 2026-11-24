import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.JingRan import JingRan as NativeJingRan
from src.combat.MacroKit import MacroKit, MacroBroken, send_input_left

# ==================== 物理 F 键监视 ====================
# 跟 Chisa__Suisui__YangYangSp/YangYangSp.py 里那份逐字相同, 说明见那边。
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
# False, 而角色对象常被粘性复用、reset_state() 不跑。do_perform 里看不到"刚才关过", 只有独立线程
# 能观察到 enabled 的 True->False->True。判据跟物理 F 键那条路一致: 记时间戳, 调用方比 > last_opener。
ENABLE_POLL_INTERVAL = 0.2


class JingRan(MacroKit, NativeJingRan):
    """景燃。景月守(景燃/尤诺/守岸人)里他兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    B 站「景月守 25 秒轮椅轴」(长风ei), 本地 E:/轴视频/景月守-25s轮椅轴.mp4。启动轴 19~87s、循环轴 89~136s。
    教学段有大量整帧定格和慢放, 下面的秒数都按画面顶部的游戏倒计时量(键盘浮层 + 头像数字牌 + 技能栏 CD 逐帧扫)。

    ================= 轴的形状 =================
    启动轴: 守 R(开局先放, 让它提前进 CD) -> 3A -> E -> 2A -> 重击(A3 抬手时) -> 闪 -> 3A -> 重击 -> Q -> 变奏尤诺
            尤诺(同循环) -> 变奏景燃 -> 景燃(同循环) -> 变奏守岸人
    循环轴(每圈从守岸人变奏入场开始):
      守  3A -> E -> [轻云: 切尤诺点 Q(无常凶鹭) -> 切回] -> 2A -> 重击 -> Q -> R -> 变奏尤诺
      月  E(告终喧响) -> [Q] -> 闪 -> 跳(换形态) -> R(打断跳跃攻击) -> 2A -> E -> 2A -> E -> 重击(至臻完满) -> 变奏景燃
      景  Q -> R -> 【从这里起左键一直按住到切人】重击 -> E -> (黄泉渡) -> 重击 -> E -> (往生送) -> 重击
          -> [处决] -> A2 A3 A4 -> 闪避断 A4 -> 重击 -> 变奏守岸人
    宏在【守岸人变奏入场、HUD 回来】时返回, 下一次框架调的就是她; 返回点不落在任何演出里。

    ================= 景燃这一段为什么是"一直按住左键" =================
    视频里作者从大招演出里按下左键, 一直按到切人, 期间只点 E / 闪避 / F。景燃没有普通重击:
    【炁】不满 300 时按住 = 连续普攻(空中带破秽时就是黄泉渡/往生送), 满 300 就自动出重击(劫魄/踏罡),
    所以资源怎么走由游戏决定, 宏只管把 E 和闪避按在对的时刻:
      - 【炁】满时准星旁/回路条右端的鼠标提示会亮(视频: 提示亮着要等黄泉渡/往生送演完才出重击, 1s 多);
        【提示见过亮之后变暗 = 重击出手了】, 那之后开始连点 E —— E 在重击演完前按不出来, 会接在重击后面;
      - 阴/阳两个 E 各自 15s CD, 重击换形态后 E 框换成另一个 E。【E 框的 CD 数字往上跳到 ~15 = E 放出去了】;
      - 第三发重击之后按住会打 A2 A3 A4, A4 给完炁提示就亮, 但 A4 的动作挡着重击 —— 【提示一亮就闪避】
        把 A4 断掉, 重击立刻出手(视频说能省 ~0.7s);
      - 回路条中间的徽记颜色就是形态(金 = 抱阳, 青 = 负阴), 跟 E 框 CD 数字的跳变一起判"重击打完换形态了"。

    ================= 改这个文件之前先读 =================
    - 引擎那一半(macro_* / quick_switch / press_key_until_cd / fire_r …)抄自 Lucy__Rebecca__ShoreKeeper/Lucy.py,
      守岸人那几个 shore_* 的数值是日月守实机标定过的(露丽守也在用)。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 普攻一律走 tap()(按住 A_TAP 秒): task.click() 只按 10ms, 打不出来。
    - 景燃按住左键期间【不调 click()/mouse_down()/send_key()】—— 它们先投 WM_ACTIVATE, 会把按住打断。
      那一段的 E / 闪避 / F 走 key_na(): PostMessage 不带 activate。
    """

    # ==================== 全局节奏(引擎用) ====================
    A_INTERVAL = 0.12
    KEY_INTERVAL = 0.10
    A_TAP = 0.06                 # 普攻按住这么久才算一下; task.click() 的 10ms 会被吞

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    SWITCH_SETTLE = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True

    # ---- 大招 ----
    LIB_KEY_INTERVAL = 0.03
    LIB_ANIM_TIME_OUT = 8.0
    LIB_ANIM_TAIL = 0.15
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防假成功)

    # ---- 协奏 ----
    CON_SAMPLE_MAX = 24
    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 保底(用户 2026-09-28: 三个人被打断也要保证流程走完, 时间很充裕) ====================
    # 原则: 只花时间的保底默认开(重试切人 / 等 HUD / 判据没成立就再按【那一个本该出去的键】);
    # 会改状态的只在判据明确说"这一下没出去"时才补(比如重击指示松手后还亮着), 不凭猜测多按
    SWITCH_RETRIES = 2           # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)
    SWITCH_RETRY_TIME_OUT = 3.00
    HUD_LOST_ABORT = 8.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃, 不再乱按
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(实机 19:17 结算画面上尾段反复重跑)
    TAIL_INTRO_TTL = 4.00        # "带着变奏上场"只认刚切进来这么久之内的(旧标记会让尾段一直重跑)
    HEAVY_RETRIES = 2            # 守岸人重击按住期间指示一直亮(没打出去)就再按, 最多这么多次
    R_TRIES = 3                  # R 亮着但没放出去(被吞/被打断)就再按, 最多这么多次
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长
    SK_R_ENSURE_CAP = 8.00       # 守岸人循环段 R 的保底上限

    # ==================== 守岸人 ====================
    # 启动段这套(3A-E-2A-重击-闪-A到回路满-重击-Q)跟日月守/露丽守的守岸人启动段一模一样,
    # 数值原样搬过来; 视频量出来的也对得上(E 在 3A 起手后 1.40s, 重击在 E 后 1.44s)。
    E_PRESS_WINDOW = 0.26        # 发一发 E 的连发窗口, 比任何派生帧都短, 不会连出第二发
    E_PRESS_INTERVAL = 0.06
    HEAVY_MIN_HOLD = 0.10        # 长按之后先按这么久再盯指示(按下那一瞬指示还没变)
    SHORE_A3_WINDOW = 1.38       # 启动段第一组三段普攻。A3 被 E 掐掉就加, E 前有空转/多出 A4 就减
    SHORE_E_TO_HEAVY = 1.43      # 从按下 E 起算的第一发重击时刻 —— 落在 A3 抬手, 把 A3 那一两格能量卡到下一次回路
    SHORE_HEAVY_HOLD = 0.85      # 读不到鼠标指示时的兜底按住时长
    SHORE_HEAVY2_HOLD = 0.45
    SHORE_HEAVY_CAP = 1.60       # 长按硬上限。指示暗了 = 长按已被识别, 立刻松手
    SHORE_DODGE_TO_A = 0.25      # 闪避 -> 接普攻, 要跨过闪避的派生帧
    SHORE_FORTE_WAIT_CAP = 5.0   # 边打边等回路满的上限, 超时也照按重击
    SHORE_FORTE_CONFIRM = 2      # 连着几帧读到满才算, 单帧会被特效骗
    SHORE_HEAVY_TO_QR = 0.30     # 重击松手 -> Q
    SHORE_E_DERIVE = 0.67        # E-地面 派生 40F
    SHORE_LIB_WINDOW = 1.80      # R 的重试窗口, 要盖过强化重击的收招(86F)
    # 交棒前边打边等协奏满的上限(正常一读就满)。不满就去补(E 一发 20 协奏, 比普攻快得多):
    # 实机 2026-09-28 循环轴 R 后协奏 0.86~0.93, 普攻打 2s 才涨到 0.93, 补一发 E 当场满
    SHORE_CON_CAP = 1.00
    SHORE_CON_RESCUE_CAP = 6.0   # 还不满就按 E > 强化重击 > 普攻 补, 这是补救上限(打不到怪时防卡死)
    SHORE_CON_RESCUE_SLICE = 0.80
    SHORE_CON_ENOUGH = 1.0       # 0.99 是环没闭合的哨兵值, 要真满
    SHORE_CON_FULL_CONFIRM = 3   # 协奏满要连着几帧读到, 一个假的满会把交棒提前
    SHORE_CON_SETTLE = 0.50
    SHORE_CON_POLL = 0.03
    # 循环段: 变奏入场演完(HUD 回来)之后 3A 打这么久再按 E。视频: 强化变奏入场演出(HUD 藏 ~3.2s)完了作者 A 了 ~1.6s。
    # 实机 2026-09-28 第一版锚在按切人键的时刻(+1.9s), 演出就吃掉 3.2s, 回来直接放 E、一下 A 都没打, 协奏跟着不够。
    # 普通变奏入场(没演出)时 HUD 回来 = 到场 +0.35s, 再 +1.6s 正好盖过 QTE 第86F前不响应输入
    SK_L_A_BEFORE_E = 1.60
    SK_L_ANCHOR_TTL = 3.00       # 入场演完的时刻离现在超过这么久就不作数(宏散过、从别处接进来的), 从现在起算
    SK_L_E_CAP = 1.00            # 按 E 到进 CD 的上限
    SK_L_FORTE_CAP = 2.50        # E 之后 A 到重击指示亮(3A)的上限, 超时也照按。实机 1.6s 没满过一次, 出的是普通重击
    # 切给守岸人之后: 带演出的强化QTE 等 HUD 回来; 没演出就只看一小段它会不会藏 HUD
    SK_INTRO_ANIM_CAP = 6.0
    SK_INTRO_NOANIM = 0.35
    SK_SWITCH_ANIM_HINT = 1.00   # 切给她的 quick_switch 超过这么久才判到到场 = 强化QTE 已经在里面演完
    SK_TO_TIME_OUT = 4.00        # 景燃 -> 守岸人: 重击没演完游戏不放人

    # ==================== 尤诺 ====================
    # 【首位声骸是无常凶鹭(轻云出月)才开】: 循环轴守岸人 E 之后切尤诺点一下 Q 再切回, 尤诺自己那段不再按 Q。
    # 关着(海之女, 用户 2026-09-28 确认带的是这个)就不切, 尤诺变奏入场 E 之后按 Q。启动轴两种都是尤诺 E 之后按 Q。
    IU_QINGYUN = False
    IU_HERON_Q_WINDOW = 0.12     # 在尤诺身上连发 Q 的窗口(两下)
    IU_HERON_STAY = 0.55         # 按完 Q 之后多久开始连按切回守岸人(视频: Q 后 0.6s 开始按, 1.05s 切回)
    IU_HERON_BACK_TIME_OUT = 2.50
    # 变奏入场: QTE 第78F前不响应输入, 一直按 E 到告终喧响进 CD(8s)。视频 1.23~1.30s 出手
    IU_E_CAP = 2.40
    IU_E_BAR_SETTLE = 0.80       # 刚切进来技能栏还是守岸人的, 这么久都没见过尤诺的 E 亮才当它在 CD 上
    IU_Q_WINDOW = 0.10
    IU_Q_TO_DODGE = 0.03
    # 跳(月相里 = 换形态攻击): 闪避之后连按跳, 到左下那格"可跳"图标(内置模板 iuno_jump)灭为止。
    # 实机 2026-09-28 第一版只在闪避后 0.15/0.30s 各按一下, 全被闪避/告终喧响的动作吞了, 尤诺没跳 ->
    # 没换成弓形态 -> 大招后弓 E 攒不出协奏, 景燃没变奏。跳是这一段的前提, 所以按到图标灭为止;
    # 上限要盖过告终喧响的派生(98F=1.63s): 闪避也被吞的话要等 E 自己演完才跳得出来
    IU_DODGE_TO_JUMP = 0.05
    IU_JUMP_INTERVAL = 0.08
    IU_JUMP_CAP = 1.80
    IU_JUMP_BLIND = 0.60         # 图标一次都没读到(模板不认/月相没拿到): 按满这么久就停, 不再多按
    IU_JUMP_MIN = 2
    IU_JUMP_TO_R = 0.03          # 视频第一下跳到 R: 0.33~0.47s; fire_r 自己还要先等 HUD 在场
    IU_R_WINDOW = 2.00
    # R 能量保底(用户 2026-09-28: 尤诺共鸣效率不够, 能量不满 R 就没放)。先读 R 亮不亮, 亮了才按;
    # 不亮就弓 A 攒一小段再读, 每 IU_R_BLIND_EVERY 轮盲按一次防读数不准。上限只防打不到怪时永远攒不满
    IU_R_BUILD_CAP = 8.00
    IU_R_BUILD_SLICE = 0.60
    IU_R_BLIND_EVERY = 3
    IU_R_BLIND_WINDOW = 0.40
    # 大招 HUD 回来 -> 弓 A1 A2 -> E(越限弦引, 第一格充能)。视频 0.98 / 1.33s
    IU_A_BEFORE_E1 = 1.00
    IU_E1_CAP = 0.60             # 连按 E 等它出手的上限; 判据 = E 框出现充能 CD 数字
    IU_E1_TO_E2 = 2.20           # 第一发 E 之后 2A 再按第二发。视频 2.29 / 2.32s
    IU_E2_CAP = 1.00             # 连按第二发 E 等特殊重击图标(iuno_heavy)亮的上限
    IU_E2_MIN_PRESSES = 2        # 至少按过这么多下才认图标(防进来时读到残留)
    IU_E2_TO_HEAVY = 0.75        # 第二发 E 出手后停手等箭矢出伤再按重击(视频 0.80 / 0.87s)
    IU_HEAVY_MIN = 0.20
    IU_HEAVY_CAP = 0.60          # 重击(至臻完满)按住上限; 视频按 ~0.4s 就切景燃了
    IU_CON_CAP = 3.00            # 切景燃之前协奏还没满就边 A 边补 E 等这么久(E 落在 CD 上是空操作)
    IU_CON_E_EVERY = 0.50
    IU_E_RETRY_CAP = 2.50        # 告终喧响没判到、"可跳"图标也没亮(月相没拿到) -> 再连按 E 这么久, 到图标亮为止
    IU_E2_RESCUE_CAP = 2.50      # 第二发弓 E 按完特殊重击图标一直不亮 -> E 和 A 交替补这么久(没充能时按 E 是空操作)

    # ==================== 景燃 ====================
    JR_Q_WINDOW = 0.20           # 变奏入场先连发 Q(视频入场 0.16s 就按了, 生效了)
    JR_R_WINDOW = 2.50           # 入场锁里 R 也按不出来, 视频入场后 ~1.0s 才起演出
    JR_R_READ_SETTLE = 0.40      # 到场这么久之内技能栏还是尤诺的, 读 R 之前先等到这一刻
    # R 能量保底(用户 2026-09-28: 能量不够 R 就没放): R 没亮就先按住左键打(普攻/重击都攒能量),
    # R 一亮就在按住的状态下按 R(不投 activate), 然后照常走; 读数不准时每 JR_R_BLIND_EVERY 秒盲按一下(没能量是空操作)
    JR_R_BUILD_CAP = 10.0
    JR_R_BLIND_EVERY = 1.00
    # 【左键按住走 SendInput】: PostMessage 的按下沿要是落在大招演出/收招里会被丢掉, 后面一直按着也不算数;
    # 系统级按下跟手按一样, 游戏恢复输入时读得到"现在按着"。只在游戏窗口在前台时走, 否则退回 PostMessage
    # (那样就等 HUD 回来再按下去)
    JR_HOLD_SENDINPUT = True
    JR_KEY_DOWN = 0.03           # key_na 按键按住时长
    # 重击1: 大招演出一完按住的左键就出重击(提示亮 -> 暗)。读不到提示的话 HUD 回来这么久之后就开始点 E
    JR_H1_CAP = 3.50
    JR_H1_BLIND = 0.60
    JR_H1_REGRIP = 1.20          # 大招刚完提示就亮着、这么久都没变暗 = 长按没生效, 松开重按一次
    JR_E_INTERVAL = 0.10         # 连点 E 的间隔(视频手速 ~0.2s, E 在重击演完前按不出来)
    JR_E_CAP = 5.00              # 连点 E 等它放出去的上限(视频从开始点到出手 1.5~2.2s; 被打断要多等一会儿)
    JR_E_CAST_MIN = 12.5         # E 框 CD 读到 >= 这个并且比上一次读数往上跳 = 刚放出去(CD 15s)
    JR_E_FRESH = 14.5            # 还没有基线时读到 >= 这个也算刚放出去
    JR_CD_MAX = 15.3             # CD 读数超过这个当 OCR 误读(视频里见过把 7.2 读成 72)
    # 重击2/3: 前一个 E 放出去之后, 按住的左键会打黄泉渡/往生送, 【炁】满了提示亮, 演完出重击(提示暗)
    JR_HEAVY_CAP = 5.00
    JR_HEAVY_BLIND = 2.20        # 提示一次都没亮过: 从 E 放出去起这么久就当重击已经出手(视频 E->下一个 E 2.9~3.2s)
    # 提示要连续亮 JR_PROMPT_CONFIRM 帧才算"见过亮", 再连续暗 JR_HEAVY_DARK 帧才算出手; E 放出去之后
    # JR_HEAVY_NOT_BEFORE 秒内的亮暗不算(黄泉渡/往生送还没打, 不可能已经出重击)。
    # 实机 2026-09-28 L1: 往生送之前提示闪了一下, 0.24s 就判成重击3 出手, 后面整段提早
    JR_HEAVY_DARK = 3
    JR_HEAVY_NOT_BEFORE = 0.50
    # 重击3 打完 = 形态换过去(徽记变色, 或 E 框换成另一个 E 的 CD)。读不到就从重击出手起按这么久
    JR_FLIP_CAP = 2.60
    JR_FLIP_BLIND = 1.90
    JR_FLIP_CONFIRM = 3          # 特效会把徽记染色, 少于 3 帧的不认(翻早了处决键会按进重击里)
    JR_CD_JUMP = 1.20            # E 框 CD 跟上一帧推算值差这么多 = 换成了另一个 E 的 CD(形态翻了)
    # 处决: 重击3 打完、普攻之前有处决就先处决(没有处决时按 F 什么都不触发)
    JR_F_BREAK = True
    JR_F_WINDOW = 0.35
    JR_F_INTERVAL = 0.10
    JR_EXEC_ANIM_CAP = 5.00
    # A4 给满炁提示就亮 -> 闪避断 A4 -> 重击4。视频提示亮到闪避 0.26s
    JR_A4_CAP = 3.50             # 实机 2026-09-28: 重击3打完到 A4 提示亮 2.4~2.6s(视频 1.8s), 2.6 刚好在亮之前放弃了
    JR_PROMPT_CONFIRM = 2
    JR_PROMPT_TO_DODGE = 0.20
    # 重击4 打完(形态换过去)之后这么久切人; 视频闪避到切人 1.60 / 1.62s, 形态翻到切人 0.2s
    JR_H4_CAP_DODGED = 1.80
    JR_H4_CAP_NODODGE = 3.00
    JR_H4_TO_SWITCH = 0.15
    JR_CON_CAP = 4.00            # 切守岸人之前协奏还没满就接着按住等(按住就是在打)
    # 四发重击里有没判到出手的(被打断/读漏), 趁荧惑还没完接着按住等补上; 从大招演出结束起算(荧惑 15s)
    JR_YINGHUO = 13.5
    JR_EXTRA_HEAVY_CAP = 4.00
    # 形态徽记: 回路条正中那个圆, 4K 坐标(hcenter)。金 = 抱阳, 青 = 负阴; 演出/重击里常常发暗, 读成 None
    JR_STANCE_BOX = (1890, 1949, 1950, 2009)
    JR_STANCE_MIN = 0.06
    # 二链(启动轴): 尤诺第一发弓 E 之后切景燃, 用进战斗送的 300 炁打一发重击再切回。作者视频里没打, 要二链才开
    JR_CHAIN2_OPENER = False
    JR_C2_HEAVY_CAP = 1.20
    JR_C2_HOLD_BLIND = 0.60
    JR_C2_BACK_TIME_OUT = 3.00
    IU_C2_BACK_A = 1.20          # 切回尤诺后 2A 再按第二发 E

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.holding_attack = False
        self.si_holding = False
        self.poking = False
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.loop_count = 0
        self.jr_last_stance = None     # 景燃最近一次读到的形态('yang'/'yin'), 判"重击打完翻过去了"用
        self.hud_seen_at = 0.0         # 最后一次读到队伍 HUD 在场的时刻(HUD_LOST_ABORT 用)
        self.hud_watch = False         # 宏跑着的时候才查 HUD 丢没丢
        self.drive_log_at = 0.0
        # reset_state() 只在 load_chars() 时跑, 而角色对象会被粘性复用 —— 这里也要启一次
        try:
            start_enable_watch(self.task, self.logger)
        except Exception:
            pass

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(处决只在宏里指定的位置按)
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_jys(self) -> bool:
        """本队 = 景燃 + 尤诺 + 守岸人。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Iuno', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_jys:
            return 'team is not jingran/iuno/shorekeeper'
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
        # 再按下去就是在结算画面上乱按(实机 2026-09-28 19:17 见过)。不读屏, 只比时间戳
        if self.hud_watch and time.time() - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {time.time() - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')

    def macro_spam(self, window, interval=None, tag='A', max_clicks=None):
        """连点一个窗口。打出几段由 window 决定(动画期间的点击被丢掉, 连点保证总有一下落在动画刚结束时)。"""
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        cap = int(window / max(interval, 0.01)) + 2
        max_clicks = cap if max_clicks is None else min(cap, max_clicks)
        self.macro_mark(f'{tag}_start')
        while time.time() - start < window and n < max_clicks:
            self.click()
            n += 1
            remaining = window - (time.time() - start)
            if remaining <= 0:
                break
            # click 自己按住 A_TAP 秒, 不扣的话连点周期从 interval 变成 interval + A_TAP
            self.macro_wait(min(max(0.0, interval - self.A_TAP), remaining))
        self.macro_mark(f'{tag}_end(x{n})')
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
        if not self.is_in_jys:
            return super().click(*args, **kwargs)
        self.tap()

    def key_na(self, key, hold=None):
        """发一个键, 【不投 WM_ACTIVATE】—— 景燃按住左键期间用, activate 会把按住打断。

        只有 PostMessage 后端的 send_key_down 带 activate 参数; 别的后端退回 task.send_key。
        """
        hold = self.JR_KEY_DOWN if hold is None else hold
        inter = getattr(getattr(self.task, 'executor', None), 'interaction', None)
        try:
            inter.send_key_down(key, activate=False)
        except Exception:
            self.task.send_key(key, down_time=hold)
            return False
        time.sleep(hold)
        try:
            inter.send_key_up(key)
        except Exception:
            pass
        return True

    def quick_switch(self, target, click=False, click_during=None, settle=None, time_out=None,
                     read_con=False, assume_con=False, click_interval=None):
        """按数字键直切给指定角色(不走 switch_next_char 选人)。

        click_during: 等换人生效的这段时间里继续点(默认跟随持续普攻开关)。
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
        settle = self.SWITCH_SETTLE if settle is None else settle
        click_during = self.poking if click_during is None else click_during
        click_interval = self.A_INTERVAL if click_interval is None else click_interval
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
                next_click = now + click_interval
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
                if trace:
                    self.logger.info(f'switch trace -> {target.name}: {" ".join(trace)}')
                if click:
                    self.click()
                self.macro_gap(settle)
                return True
        # 一直是旧角色的 index = 游戏在拒绝切人; 带 ? 的都是目标 = count 判据挡住了; 全是 x = HUD 不在
        self.logger.warning(f'switch to {target.name} slot {slot} timed out '
                            f'after {time_out:.1f}s (stuck in an animation?)'
                            + (f' trace: {" ".join(trace)}' if trace else ''))
        self.macro_mark(f'>{target.name[:3]}FAIL')
        return False

    def poke_until_hud_back(self, tag='anim', time_out=None, tail=None):
        """演出期间一直点普攻, 等队伍 HUD 回来(先确认消失过)再往下走。"""
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        tail = self.LIB_ANIM_TAIL if tail is None else tail
        start = time.time()
        n = 0
        gone_seen = False
        back = False
        next_click = 0.0
        while time.time() - start < time_out:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            if not self.hud_ok():
                gone_seen = True
            elif gone_seen or time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},x{n},'
                        f'{time.time() - start:.2f}s)')
        if tail > 0:
            self.macro_spam(tail, self.A_INTERVAL, tag=f'{tag}Tail')
        return back

    def wait_hud_back(self, tag='anim', time_out=None, tail=0.0):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。先确认消失过。"""
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_seen = False
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
            if not self.hud_ok():
                gone_seen = True
            elif gone_seen or time.time() - start >= self.HUD_NO_ANIM_GRACE:
                back = True
                break
            self.macro_wait(0.01)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},{time.time() - start:.2f}s)')
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

    def lit_now(self, read, frames):
        """HUD 在场时连续 frames 帧读到 read() 为真。HUD 不在的帧不算, 最多读 frames*3 帧。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.hud_ok():
                try:
                    lit = bool(read())
                except Exception:
                    lit = False
                if not lit:
                    return False
                hits += 1
                if hits >= frames:
                    return True
            self.macro_wait(0.02)
        return False

    def spam_until(self, deadline, tag='A', interval=None):
        """连点到某个绝对时刻为止。已经过点就立刻返回并记一笔 late —— 看到 late 去查前一格。"""
        interval = self.A_INTERVAL if interval is None else interval
        left = deadline - time.time()
        if left <= 0:
            self.macro_mark(f'{tag}(late{-left:.2f}s)')
            return 0
        return self.macro_spam(left, interval, tag=tag)

    def press_e(self, char, tag='E', window=None, interval=None):
        """把一发 E 递进去: 0.26 秒连发, 比任何派生帧都短, 不会把下一发提前按出来。"""
        window = self.E_PRESS_WINDOW if window is None else window
        interval = self.E_PRESS_INTERVAL if interval is None else interval
        return self.spam_key(char.send_resonance_key, window, interval=interval, tag=tag, poke=False)

    def fire_echo(self, char, tag='Q', window=0.0, presses=1):
        """发声骸键, 不读屏不等(落在 CD 上是空操作, 读屏是实打实的延迟)。"""
        if presses <= 1 and window <= 0:
            char.send_echo_key()
            self.macro_mark(tag)
        else:
            self.spam_key(char.send_echo_key, window, interval=0.0, tag=tag, presses=presses, poke=False)
        char.record_echo_use()
        return True

    def hold_heavy(self, hold, cap=None, min_hold=None, tag='重击'):
        """长按左键打重击, 鼠标指示【见过亮之后一暗】就松手; 没亮过按满 hold 兜底; cap 是硬上限。"""
        cap = hold if cap is None else max(cap, hold)
        min_hold = self.HEAVY_MIN_HOLD if min_hold is None else min_hold
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}_down')
        start = time.time()
        why = None
        seen_lit = False
        try:
            self.macro_wait(min(min_hold, hold))
            while time.time() - start < cap:
                self.task.next_frame()
                self.hud_ok()
                try:
                    lit = bool(self.cur.is_mouse_forte_full()) if self.cur is not None \
                        else bool(self.is_mouse_forte_full())
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

    def wait_forte_full(self, cap=None, click=True, tag='等回路满'):
        """边打边等鼠标指示亮(回路满), 连着 SHORE_FORTE_CONFIRM 帧才算。先点再读。超时也返回。"""
        cap = self.SHORE_FORTE_WAIT_CAP if cap is None else cap
        start = time.time()
        hits = 0
        n = 0
        next_click = 0.0
        while time.time() - start < cap:
            now = time.time()
            if click and now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            try:
                full = bool(self.cur.is_mouse_forte_full()) if self.cur is not None \
                    else bool(self.is_mouse_forte_full())
            except Exception:
                full = False
            hits = hits + 1 if full else 0
            if hits >= self.SHORE_FORTE_CONFIRM:
                self.macro_mark(f'{tag}(满,A x{n},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}(cap,A x{n},{time.time() - start:.2f}s)')
        return False

    def read_cd(self, name='resonance', char=None):
        """读技能框里的 CD 数字(秒)。不在 CD / 读不到都返回 0。"""
        try:
            index = None if char is None else char.index
            return float(self.task.get_cd(name, index))
        except Exception:
            return 0.0

    def read_con(self, want, settle=None, click=False, tag=None):
        """读协奏: 自己刷帧、给结算留时间, 连着几帧达到 want 就立刻返回。返回期间读到的最大值。"""
        settle = self.SHORE_CON_SETTLE if settle is None else settle
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
            if hits >= self.SHORE_CON_FULL_CONFIRM or time.time() - start >= settle:
                break
            self.macro_wait(self.SHORE_CON_POLL)
        if tag:
            self.macro_mark(f'{tag}(con{best:.2f},{time.time() - start:.2f}s)')
        return best

    def wait_con_for_intro(self, char, next_name, poke=True, time_out=None, tag='con'):
        """边打边等协奏满(连着几帧读到)再交棒。"""
        time_out = self.SHORE_CON_CAP if time_out is None else time_out
        start = time.time()
        samples = []
        full = False
        full_hits = 0
        next_click = 0.0
        while time.time() - start < time_out:
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
            full_hits = full_hits + 1 if con >= self.SHORE_CON_ENOUGH else 0
            if full_hits >= self.SHORE_CON_FULL_CONFIRM:
                full = True
                break
            self.macro_wait(self.SHORE_CON_POLL)
        self.macro_mark(f'{tag}({"full" if full else "timeout"},{time.time() - start:.2f}s)')
        if not full:
            self.logger.info(f'{char.name} con {" ".join(samples)} (never filled, {next_name} gets no intro)')
        return full

    # ==================== 守岸人 ====================

    def switch_to_sk(self, sk, tag):
        """切给守岸人并等她的变奏入场演完。返回时 HUD 在场、场上是她。

        带演出的强化QTE 一切进来 HUD 就藏了, quick_switch 读不到她到场会超时 ——
        所以不管切人判没判到, 都等 HUD 回来再认一次场上是谁; 真没切过去就再切(最多 SWITCH_RETRIES 次)。
        """
        for attempt in range(1 + self.SWITCH_RETRIES):
            got = self.switch_to_sk_once(sk, tag if attempt == 0 else f'{tag}重切#{attempt}')
            if got is not None:
                return got
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
        return False

    def switch_to_sk_once(self, sk, tag):
        """切一次守岸人。返回 True/False(到场了, HUD 回没回来), None = 守岸人没上场。"""
        src = self.cur
        sw_at = time.time()
        full = False
        try:
            full = float(self.task.get_current_con()) >= 1.0
        except Exception:
            pass
        arrived = self.quick_switch(sk, click_during=False, assume_con=full,
                                    time_out=self.SK_TO_TIME_OUT)
        anim_in_switch = arrived and time.time() - sw_at >= self.SK_SWITCH_ANIM_HINT
        start = time.time()
        gone_seen = False
        back = False
        n = 0
        next_click = 0.0
        while time.time() - start < self.SK_INTRO_ANIM_CAP:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            if not self.hud_ok():
                gone_seen = True
            elif gone_seen or anim_in_switch or time.time() - start >= self.SK_INTRO_NOANIM:
                back = True
                break
            self.macro_wait(0.01)
        if gone_seen:
            self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}守入场({"anim" if gone_seen or anim_in_switch else "noanim"},'
                        f'{"ok" if back else "timeout"},A x{n},{time.time() - start:.2f}s)')
        if not arrived:
            who = self.on_field_char(time_out=1.0)
            if who is not sk:
                self.macro_mark(f'{tag}守没上场({who.name if who else "?"})')
                return None
            self.handoff(src if src is not None else self, sk, full)
            self.macro_mark(f'{tag}>Sho(演出里到场)')
        # 循环段的 3A 从这一刻起算(强化变奏入场的演出吃掉的时间不算)
        self.sk_intro_done_at = time.time()
        return back

    def shore_ensure_con(self, sk, tag, cap=None):
        """协奏没满就别交棒 —— 用能给协奏的技能补: E(20) > 强化重击(6) > 普攻(1~4)。"""
        cap = self.SHORE_CON_RESCUE_CAP if cap is None else cap
        start = time.time()
        rounds = 0
        while time.time() - start < cap:
            rounds += 1
            if self.read_con(self.SHORE_CON_ENOUGH, settle=self.SHORE_CON_POLL) >= self.SHORE_CON_ENOUGH:
                self.macro_mark(f'{tag}补协奏(满,{rounds}轮,{time.time() - start:.2f}s)')
                return True
            if self.read_cd('resonance') <= 0:
                self.press_e(sk, tag=f'{tag}补协奏E')
                self.macro_gap(self.SHORE_E_DERIVE)
                continue
            try:
                forte = bool(sk.is_mouse_forte_full())
            except Exception:
                forte = False
            if forte:
                self.hold_heavy(self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}补协奏重击')
                continue
            self.macro_spam(self.SHORE_CON_RESCUE_SLICE, self.A_INTERVAL, tag=f'{tag}补协奏A')
        self.macro_mark(f'{tag}补协奏(cap,{rounds}轮,{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 补了 {time.time() - start:.1f}s 协奏还是没满, 认赔交棒(打不到怪?)')
        return False

    def shore_hand_to_iuno(self, sk, iu, tag):
        """守岸人 -> 尤诺: 协奏满了(连着几帧)才交棒, 不满先补。返回切过去没有。"""
        full = self.wait_con_for_intro(sk, 'Iuno', time_out=self.SHORE_CON_CAP, tag=f'{tag}协奏')
        if not full:
            full = self.shore_ensure_con(sk, tag)
        self.warn_no_con(full, '守', '月')
        return self.switch_retry(iu, tag, assume_con=full, click_during=False)

    def shore_open(self, sk, iu, tag):
        """守岸人·启动段: R -> 3A -> E -> 2A -> 重击 -> 闪 -> A 到回路满 -> 重击 -> Q -> 变奏尤诺。

        R 放在最前面是为了让它提前进 CD, 循环轴守岸人要放 R 的时候正好转好。能量不够就跳过, 不等。
        两次长按都锚在按下 E 的那一刻: 第一发重击是"指示一暗就松手", 用相对间隔往下串误差会累加。
        """
        self.poking = False
        if self.lib_ready_reads(sk, 2):
            if self.fire_r(sk, f'{tag}R', self.SHORE_LIB_WINDOW):
                self.poke_until_hud_back(tag=f'{tag}R演出', tail=0.0)
        else:
            self.macro_mark(f'{tag}R没好(跳过)')
        self.macro_spam(self.SHORE_A3_WINDOW, self.A_INTERVAL, tag=f'{tag}3A')
        e_at = time.time()
        # 按到 E 进 CD 为止(以前连按 0.26s 不确认, 被打断就没了); 后面的重击仍然锚在开始按 E 的时刻
        self.press_res_until_cd(sk, 0.80, f'{tag}E')
        self.spam_until(e_at + self.SHORE_E_TO_HEAVY, tag=f'{tag}2A')
        self.sk_heavy(self.SHORE_HEAVY_HOLD, tag=f'{tag}重击1')
        self.dodge(tag=f'{tag}闪避')
        self.macro_gap(self.SHORE_DODGE_TO_A)
        self.wait_forte_full(tag=f'{tag}等回路满')
        self.sk_heavy(self.SHORE_HEAVY2_HOLD, tag=f'{tag}重击2')
        self.macro_gap(self.SHORE_HEAVY_TO_QR)
        self.fire_echo(sk, tag=f'{tag}Q')
        return self.shore_hand_to_iuno(sk, iu, tag)

    def sk_heavy(self, hold, tag):
        """守岸人重击: 按住期间指示一直亮着没暗(= 没打出去: 被打断/按在别的动作里)就再按, 最多 HEAVY_RETRIES 次。
        指示暗了(打出去了)或者本来就没亮(普通重击)都不补。"""
        for i in range(1 + self.HEAVY_RETRIES):
            if self.hold_heavy(hold, self.SHORE_HEAVY_CAP, tag=tag if i == 0 else f'{tag}重按#{i}'):
                return True
        return False

    def sk_build_energy(self, sk, tag, rounds):
        """守岸人攒能量的一轮: 回路满了就打强化重击(能量多), 否则普攻一小段。"""
        try:
            forte = bool(sk.is_mouse_forte_full())
        except Exception:
            forte = False
        if forte:
            self.sk_heavy(self.SHORE_HEAVY2_HOLD, tag=f'{tag}攒能量重击#{rounds}')
        else:
            self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL, tag=f'{tag}攒能量A#{rounds}')

    def heron_hop(self, sk, iu, tag):
        """切尤诺点一下 Q(无常凶鹭)再切回守岸人。切不过去就跳过这一步(守岸人接着打), 切不回来整圈作废。"""
        if not self.quick_switch(iu, click_during=False):
            self.macro_mark(f'{tag}轻云(没切过去,跳过)')
            return
        self.fire_echo(iu, tag=f'{tag}轻云Q', window=self.IU_HERON_Q_WINDOW, presses=2)
        self.macro_wait(self.IU_HERON_STAY)
        self.need(self.switch_retry(sk, tag, click_during=False, time_out=self.IU_HERON_BACK_TIME_OUT),
                  'heron: back to shorekeeper')

    def shore_loop(self, sk, iu, tag):
        """守岸人·循环段(变奏入场): 3A -> E -> [轻云] -> 2A -> 重击 -> Q -> R -> 变奏尤诺。"""
        self.poking = False
        if not sk.has_intro:
            self.logger.warning('守岸人没拿到变奏入场(上一棒协奏没满)')
            self.macro_mark(f'{tag}无变奏')
        done = getattr(self, 'sk_intro_done_at', 0.0)
        anchor = done if 0 < time.time() - done < self.SK_L_ANCHOR_TTL else time.time()
        self.spam_until(anchor + self.SK_L_A_BEFORE_E, tag=f'{tag}3A')
        self.press_res_until_cd(sk, self.SK_L_E_CAP, f'{tag}E', poke=True)
        if self.IU_QINGYUN:
            self.heron_hop(sk, iu, tag)
        self.wait_forte_full(cap=self.SK_L_FORTE_CAP, tag=f'{tag}2A等回路')
        self.sk_heavy(self.SHORE_HEAVY2_HOLD, tag=f'{tag}重击')
        self.fire_echo(sk, tag=f'{tag}Q')
        if self.ensure_r(sk, tag, self.SHORE_LIB_WINDOW, self.SK_R_ENSURE_CAP,
                         lambda rounds: self.sk_build_energy(sk, tag, rounds)):
            # 大招全局时停 + 隐藏 HUD、第178F前不能切人; 演出里点击被丢掉, 回来之后也不点(马上要切人)
            self.wait_hud_back(tag=f'{tag}R演出')
        return self.shore_hand_to_iuno(sk, iu, tag)

    # ==================== 尤诺 ====================

    def iu_fire_r(self, iu, tag):
        """尤诺的 R 一定要放出来: 先读 R 亮不亮, 亮了直接按; 不亮就弓 A 攒一小段再读, 亮了才按。

        【R 暗的时候不干按】: 按 R + 等判定那两秒一下 A 都不点, 能量永远攒不上来(露丽守踩过)。
        每 IU_R_BLIND_EVERY 轮盲按一次, 防 R 的读数不准。只用普攻攒, 不按 E(弓 E 的充能留给大招后那两发)。
        """
        return self.ensure_r(iu, tag, self.IU_R_WINDOW, self.IU_R_BUILD_CAP,
                             lambda rounds: self.macro_spam(self.IU_R_BUILD_SLICE, self.A_INTERVAL,
                                                            tag=f'{tag}攒能量A#{rounds}'))

    def iu_jump_icon(self):
        """左下那格的"可跳"图标(内置模板 iuno_jump): 告终喧响给了月相就亮, 跳出去换了形态就换成别的图案。"""
        try:
            return bool(self.task.find_feature('iuno_jump', box='box_extra_action', threshold=0.6))
        except Exception:
            return False

    def iu_jump(self, tag):
        """连按跳到"可跳"图标灭(先见过亮才认灭)。一次都没见过图标就按满 IU_JUMP_BLIND 停手。"""
        start = time.time()
        n = gone = 0
        seen = False
        why = 'CAP'
        trace = []
        while time.time() - start < self.IU_JUMP_CAP:
            self.task.jump(after_sleep=0)
            n += 1
            self.macro_wait(self.IU_JUMP_INTERVAL)
            self.task.next_frame()
            if not self.hud_ok():
                continue
            lit = self.iu_jump_icon()
            if len(trace) < 30:
                trace.append(f'{time.time() - start:.2f}:{1 if lit else 0}')
            if lit:
                seen = True
                gone = 0
            elif seen:
                gone += 1
                if gone >= 2 and n >= self.IU_JUMP_MIN:
                    why = '图标灭'
                    break
            elif time.time() - start >= self.IU_JUMP_BLIND:
                why = '没见过图标'
                break
        self.macro_mark(f'{tag}({why},x{n},{time.time() - start:.2f}s)')
        if why != '图标灭':
            self.logger.info(f'{tag}: iuno_jump icon {why} — {" ".join(trace)}')
        return why

    def iu_heavy_icon(self):
        """左下那格的特殊重击图标(内置模板 iuno_heavy)。第二发弓 E 出手就换成它。"""
        try:
            return bool(self.task.find_feature('iuno_heavy', box='box_extra_action', threshold=0.6))
        except Exception:
            return False

    def iu_bow_e1(self, iu, tag):
        """大招之后第一发弓 E(越限弦引): 边 A 边连点 E, 到 E 框出现充能 CD 数字为止。返回出手时刻。

        弓形态 E 是两格充能, 第一发之后图标还亮着(剩一格)、只是多了回充的 CD 数字(~10s), 所以不能用
        "变暗"判, 只能看 CD 数字从无到有。读不到就按满上限当它出去了。
        """
        start = time.time()
        n = clicks = 0
        next_e = next_a = 0.0
        prev = None
        why = 'CAP'
        trace = []
        while time.time() - start < self.IU_E1_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                n += 1
                next_e = now + 0.08
            elif now >= next_a:
                self.click()
                clicks += 1
                next_a = now + self.A_INTERVAL
            self.task.next_frame()
            if self.hud_ok():
                cd = self.read_cd('resonance')
                if len(trace) < 20:
                    trace.append(f'{time.time() - start:.2f}:{cd:.1f}')
                if cd > 5.0 and (prev is None or prev < 1.0) and n >= 1:
                    why = 'CD'
                    break
                prev = cd
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({why},x{n},A x{clicks},{time.time() - start:.2f}s)')
        if why == 'CAP':
            self.logger.info(f'{tag}: bow E charge CD never showed up — {" ".join(trace)}')
        return time.time()

    def iu_bow_e2(self, iu, tag):
        """第二发弓 E: 只点 E 不点 A, 到特殊重击图标亮为止。返回出手时刻(读不到就是按满上限的时刻)。"""
        start = time.time()
        n = 0
        next_e = 0.0
        seen_dark = not self.iu_heavy_icon()
        lit = 0
        why = 'CAP'
        while time.time() - start < self.IU_E2_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                n += 1
                next_e = now + 0.08
            self.task.next_frame()
            if self.hud_ok():
                if self.iu_heavy_icon():
                    lit += 1
                    if lit >= 2 and n >= self.IU_E2_MIN_PRESSES and seen_dark:
                        why = '重击图标亮'
                        break
                else:
                    lit = 0
                    seen_dark = True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({why},x{n},{time.time() - start:.2f}s)')
        return time.time(), why

    def iu_heavy(self, iu, tag):
        """重击(至臻完满): 按住到特殊重击图标灭(见过亮才认灭), 最短 IU_HEAVY_MIN、最长 IU_HEAVY_CAP。"""
        self.task.mouse_down()
        self.holding_attack = True
        start = time.time()
        seen = False
        why = 'cap'
        try:
            self.macro_wait(self.IU_HEAVY_MIN)
            while time.time() - start < self.IU_HEAVY_CAP:
                self.task.next_frame()
                if self.hud_ok():
                    if self.iu_heavy_icon():
                        seen = True
                    elif seen:
                        why = '图标灭'
                        break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}({why},{time.time() - start:.2f}s)')
        return why, seen

    def iu_retry_e(self, iu, tag):
        """告终喧响没判到出手、"可跳"图标也没亮 = 月相没拿到(被打断/按在动作里被吞), 后面跳不出形态。
        再连按 E 到图标亮为止。解锁过期的话按出去的是 E1, E1 本身又会解锁告终喧响, 接着按就是它。"""
        start = time.time()
        n = lit = 0
        why = 'CAP'
        while time.time() - start < self.IU_E_RETRY_CAP:
            iu.send_resonance_key()
            n += 1
            self.macro_wait(0.08)
            self.task.next_frame()
            if self.hud_ok():
                lit = lit + 1 if self.iu_jump_icon() else 0
                if lit >= 2:
                    why = '月相'
                    break
        self.macro_mark(f'{tag}补E({why},x{n},{time.time() - start:.2f}s)')
        return why == '月相'

    def iu_e2_rescue(self, iu, tag):
        """第二发弓 E 按完特殊重击图标一直不亮: E 和 A 交替补到图标亮。没充能时按 E 是空操作; 有充能就是本该出去的那发。"""
        start = time.time()
        n = clicks = lit = 0
        next_e = 0.0
        why = 'CAP'
        while time.time() - start < self.IU_E2_RESCUE_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                n += 1
                next_e = now + 0.30
            else:
                self.click()
                clicks += 1
            self.task.next_frame()
            if self.hud_ok():
                lit = lit + 1 if self.iu_heavy_icon() else 0
                if lit >= 2:
                    why = '重击图标亮'
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}补E2({why},x{n},A x{clicks},{time.time() - start:.2f}s)')
        return time.time()

    def iu_ensure_con(self, iu, tag):
        """切景燃之前协奏要满: 边 A 边隔一会儿按一下 E(弓 E 还有充能就是一大笔协奏, 没有是空操作), 上限 IU_CON_CAP。"""
        start = time.time()
        hits = 0
        best = 0.0
        next_e = start + self.IU_CON_E_EVERY
        next_a = 0.0
        while time.time() - start < self.IU_CON_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                next_e = now + self.IU_CON_E_EVERY
            elif now >= next_a:
                self.click()
                next_a = now + self.A_INTERVAL
            self.task.next_frame()
            self.hud_ok()
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= self.SHORE_CON_ENOUGH else 0
            if hits >= self.SHORE_CON_FULL_CONFIRM:
                self.macro_mark(f'{tag}补协奏(满,{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}补协奏(CAP,con{best:.2f})')
        return False

    def seg_iuno(self, iu, jr, sk, opener, tag):
        """尤诺(变奏入场) -> ... -> 变奏景燃。返回切过去没有。

        E(告终喧响) 给月相, 马上用 Q/闪避断掉它的后摇; 月相里跳 = 换形态攻击, 起跳就用 R 打断;
        大招回来是弓形态: 2A-E-2A-E, 第二发 E 打满协奏, 接重击(至臻完满, 领域状态第1F就给)立刻切景燃。
        """
        self.poking = False
        if not iu.has_intro:
            self.logger.warning('尤诺没拿到变奏入场(上一棒协奏没满) — 告终喧响没解锁, 这一段会打歪')
            self.macro_mark(f'{tag}无变奏')
        e_ok = self.press_key_until_cd(iu.send_resonance_key, iu.resonance_available, self.IU_E_CAP, f'{tag}E',
                                       ready_grace=self.IU_E_BAR_SETTLE)
        if not e_ok and not self.lit_now(self.iu_jump_icon, 2):
            self.iu_retry_e(iu, tag)
        if opener or not self.IU_QINGYUN:
            self.fire_echo(iu, tag=f'{tag}Q', window=self.IU_Q_WINDOW, presses=2)
            self.macro_wait(self.IU_Q_TO_DODGE)
        self.dodge(tag=f'{tag}闪')
        self.macro_wait(self.IU_DODGE_TO_JUMP)
        if self.iu_jump(f'{tag}跳') == 'CAP':
            # 图标一直亮着 = 跳没出去(被打断/一直按在动作里), 再闪一下重跳一次
            self.dodge(tag=f'{tag}重闪')
            self.macro_wait(self.IU_DODGE_TO_JUMP)
            self.iu_jump(f'{tag}重跳')
        self.macro_wait(self.IU_JUMP_TO_R)
        if self.iu_fire_r(iu, tag):
            # 演出里点击被丢掉; HUD 回来之后的点击就是弓 A1
            self.poke_until_hud_back(tag=f'{tag}R演出', tail=0.0)
        back_at = time.time()
        self.spam_until(back_at + self.IU_A_BEFORE_E1, tag=f'{tag}2A')
        e1_at = self.iu_bow_e1(iu, f'{tag}E1')
        if opener and self.JR_CHAIN2_OPENER:
            if self.seg_jr_chain2(jr, iu, tag):
                self.macro_spam(self.IU_C2_BACK_A, self.A_INTERVAL, tag=f'{tag}2A')
            else:
                self.spam_until(e1_at + self.IU_E1_TO_E2, tag=f'{tag}2A')
        else:
            self.spam_until(e1_at + self.IU_E1_TO_E2, tag=f'{tag}2A')
        e2_at, why = self.iu_bow_e2(iu, f'{tag}E2')
        if why == 'CAP':
            e2_at = self.iu_e2_rescue(iu, tag)
        self.macro_wait(max(0.0, e2_at + self.IU_E2_TO_HEAVY - time.time()))
        why, seen = self.iu_heavy(iu, f'{tag}重击')
        if why == 'cap' and seen and self.lit_now(self.iu_heavy_icon, 2):
            # 松手后特殊重击图标还亮 = 没打出去, 重按一次
            self.iu_heavy(iu, f'{tag}重击重按')
        full = self.read_con(self.SHORE_CON_ENOUGH, settle=0.10) >= self.SHORE_CON_ENOUGH
        if not full:
            full = self.iu_ensure_con(iu, tag)
        self.warn_no_con(full, '月', '景')
        return self.switch_retry(jr, tag, assume_con=full, click_during=False)

    # ==================== 景燃 ====================

    def jr_stance(self):
        """回路条中间徽记的颜色 -> ('yang'|'yin'|None, 金占比, 青占比)。演出/重击里徽记常常发暗, 读成 None。"""
        try:
            x1, y1, x2, y2 = self.JR_STANCE_BOX
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, name='jr_stance', hcenter=True)
            crop = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            m = (s > 70) & (v > 110)
            gold = float((m & (h >= 10) & (h <= 35)).mean())
            teal = float((m & (h >= 78) & (h <= 105)).mean())
        except Exception:
            return None, 0.0, 0.0
        if gold >= self.JR_STANCE_MIN and gold > 2 * teal:
            return 'yang', gold, teal
        if teal >= self.JR_STANCE_MIN and teal > 2 * gold:
            return 'yin', gold, teal
        return None, gold, teal

    def jr_read(self):
        """一帧读三样: (重击提示亮没亮, 形态, E 框 CD)。HUD 不在(演出/处决)返回 None。"""
        self.task.next_frame()
        if not self.hud_ok():
            return None
        try:
            prompt = bool(self.is_mouse_forte_full())
        except Exception:
            prompt = False
        stance = self.jr_stance()[0]
        if stance is not None:
            self.jr_last_stance = stance
        cd = self.read_cd('resonance')
        if cd > self.JR_CD_MAX:
            cd = -1.0              # OCR 误读, 这一帧的 CD 不算
        elif cd < 0:
            # E 框没有数字时框架返回 0 - 距上次读屏的时间(负数), 意思就是"不在 CD"。
            # 实机 2026-09-28 第一版把它当误读丢了, 没有基线, 第一个 E 放出去一直判不到
            cd = 0.0
        return prompt, stance, cd

    @staticmethod
    def jr_fmt(r, t):
        if r is None:
            return f'{t:.2f}:H'
        p, s, cd = r
        return f'{t:.2f}:{"P" if p else "-"}{"Y" if s == "yang" else "N" if s == "yin" else "?"}{cd:.1f}'

    def jr_hold_start(self, tag):
        si = self.attack_down(self.JR_HOLD_SENDINPUT and self.game_foreground())
        self.macro_mark(f'{tag}按住({"SI" if si else "PM"})')
        return si

    def jr_regrip(self, tag):
        """长按没生效时松开重按一次。"""
        self.release_attack()
        self.macro_wait(0.05)
        self.jr_hold_start(f'{tag}重按')

    def jr_wait_heavy(self, tag, cap, blind, regrip_at=None, not_before=0.0):
        """等一发重击出手: 提示【连续亮 JR_PROMPT_CONFIRM 帧之后, 连续 JR_HEAVY_DARK 帧暗】。
        一直没亮过就按 blind 秒当它出手了。not_before 秒之内的读数不算。

        返回 'fired' / 'blind' / 'cap'。HUD 不在的帧不读(不算亮也不算暗)。
        regrip_at: 提示连续亮这么久还不暗 = 按住没生效, 松开重按一次(只用在大招刚完那一发)。
        """
        start = time.time()
        seen = False
        lit = 0
        dark = 0
        lit_since = None
        regripped = False
        why = 'cap'
        trace = []
        while time.time() - start < cap:
            r = self.jr_read()
            now = time.time()
            if len(trace) < 40:
                trace.append(self.jr_fmt(r, now - start))
            if r is not None and now - start >= not_before:
                if r[0]:
                    lit += 1
                    if lit >= self.JR_PROMPT_CONFIRM:
                        seen = True
                    dark = 0
                    if lit_since is None:
                        lit_since = now
                    if regrip_at is not None and not regripped and now - lit_since >= regrip_at:
                        regripped = True
                        self.jr_regrip(tag)
                else:
                    lit = 0
                    lit_since = None
                    if seen:
                        dark += 1
                        if dark >= self.JR_HEAVY_DARK:
                            why = 'fired'
                            break
                    elif now - start >= blind:
                        why = 'blind'
                        break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({why},{time.time() - start:.2f}s)')
        if why != 'fired':
            self.logger.info(f'{tag}: heavy prompt {why} — {" ".join(trace)}')
        return why

    def jr_spam_e(self, tag, cap):
        """连点 E(不投 activate)到 E 框 CD 往上跳到 ~15(= 刚放出去)为止。返回放出去没有。

        阴/阳两个 E 各自一个 CD: 第一个 E 之前 E 框读 0, 第二个 E 之前读的是第一个 E 剩下的 ~12,
        放出去都会跳到 15。所以判据是"读到 >= JR_E_CAST_MIN 并且比上一帧高出一截", 再要下一帧也 >= 12 确认。
        """
        key = self.get_resonance_key()
        start = time.time()
        n = 0
        next_e = 0.0
        prev = None
        cand = None
        ok = False
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if now >= next_e:
                self.key_na(key)
                n += 1
                next_e = now + self.JR_E_INTERVAL
            r = self.jr_read()
            if len(trace) < 40:
                trace.append(self.jr_fmt(r, time.time() - start))
            if r is not None and r[2] >= 0:
                cd = r[2]
                if cand is not None:
                    if cd >= 12.0:
                        ok = True
                        break
                    cand = None
                # 【要先读到一个基线再认"往上跳"】: 第二个 E 开始点的时候 E 框还是第一个 E 剩下的 ~13,
                # 没有基线的话第一帧就被当成刚放出去(干跑抓到的)。
                # 例外: 第一帧就 >= JR_E_FRESH —— 那只能是刚放出去的(第一个 E 剩下的时候已经过了 1.5s 以上)
                elif (prev is not None and cd >= self.JR_E_CAST_MIN and cd > prev + 1.5) or \
                        (prev is None and cd >= self.JR_E_FRESH):
                    cand = cd
                prev = cd
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"出手" if ok else "CAP"},x{n},{time.time() - start:.2f}s)')
        if not ok:
            self.logger.info(f'{tag}: E never read as cast — {" ".join(trace)}')
        return ok

    def jr_wait_flip(self, tag, cap, blind_at):
        """等这发重击打完、形态翻过去: 徽记颜色跟重击出手时相反(连续 JR_FLIP_CONFIRM 帧),
        或 E 框 CD 跳到另一个 E 的数(下一帧确认)。读不到就等到 blind_at(绝对时刻)当它翻了。

        【不写死翻成阴还是阳】: 形态的奇偶跟这一轮打了几发重击走(二链多一发、大招没放出来少几发),
        只认"跟出手时相反"。出手时的形态取进来之前最后读到的那个。返回 'stance' / 'cd' / 'blind'。
        """
        base = self.jr_last_stance
        want = None if base is None else ('yin' if base == 'yang' else 'yang')
        start = time.time()
        hits = 0
        prev = None
        prev_t = 0.0
        cand = None
        why = 'blind'
        trace = []
        while time.time() - start < cap:
            r = self.jr_read()
            now = time.time()
            if len(trace) < 40:
                trace.append(self.jr_fmt(r, now - start))
            if r is not None:
                _, stance, cd = r
                if want is None and stance is not None:
                    # 进来时不知道出手时的形态: 第一次读到的当基准(要是已经翻过了就只能靠 CD 跳变/兜底)
                    base, want = stance, ('yin' if stance == 'yang' else 'yang')
                hits = hits + 1 if (want is not None and stance == want) else 0
                if hits >= self.JR_FLIP_CONFIRM:
                    why = 'stance'
                    break
                if cd > 0.3:
                    if cand is not None:
                        # 下一帧跟候选值对得上(按时间衰减)才认, 单帧误读不算
                        if abs(cd - (cand[0] - (now - cand[1]))) < 0.6:
                            why = 'cd'
                            break
                        cand = None
                    elif prev is not None and abs(cd - (prev - (now - prev_t))) > self.JR_CD_JUMP:
                        cand = (cd, now)
                    prev, prev_t = cd, now
            if now >= blind_at:
                break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({why},{base}->{want},{time.time() - start:.2f}s)')
        if why == 'blind':
            self.logger.info(f'{tag}: stance flip {base}->{want} not seen — {" ".join(trace)}')
        return why

    def jr_f_break(self, tag):
        """处决窗口: 连按 F(不投 activate)。打出处决(HUD 连续消失)就等演出完 —— 左键照样按着。"""
        start = time.time()
        n = gone = 0
        next_f = 0.0
        anim = False
        while time.time() - start < self.JR_F_WINDOW:
            now = time.time()
            if now >= next_f:
                self.key_na('f')
                n += 1
                next_f = now + self.JR_F_INTERVAL
            self.task.next_frame()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    anim = True
                    break
            else:
                gone = 0
            self.macro_wait(0.02)
        self.task.can_break = False
        if anim:
            self.wait_hud_back(tag=f'{tag}处决演出', time_out=self.JR_EXEC_ANIM_CAP)
        self.macro_mark(f'{tag}F(x{n},{"处决" if anim else "没有"})')
        return anim

    def jr_dodge_on_prompt(self, tag, cap):
        """A4 给满炁提示亮 -> 等一下 -> 闪避断 A4(重击随即出手)。提示一直没亮就不闪(让 A4 自己演完)。"""
        start = time.time()
        lit = 0
        trace = []
        while time.time() - start < cap:
            r = self.jr_read()
            if len(trace) < 40:
                trace.append(self.jr_fmt(r, time.time() - start))
            if r is not None:
                lit = lit + 1 if r[0] else 0
                if lit >= self.JR_PROMPT_CONFIRM:
                    self.macro_wait(self.JR_PROMPT_TO_DODGE)
                    self.key_na(self.task.key_config.get('Dodge Key'))
                    self.macro_mark(f'{tag}闪避({time.time() - start:.2f}s)')
                    return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}不闪(提示没亮,{cap:.1f}s)')
        self.logger.info(f'{tag}: A4 prompt never lit — {" ".join(trace)}')
        return False

    def jr_con_before_switch(self, tag):
        """切守岸人之前读协奏; 不满就接着按住(按住就是在打)等满, 上限 JR_CON_CAP。"""
        start = time.time()
        hits = 0
        best = 0.0
        while time.time() - start < self.JR_CON_CAP:
            self.task.next_frame()
            self.hud_ok()
            try:
                con = float(self.task.get_current_con())
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= self.SHORE_CON_ENOUGH else 0
            if hits >= 2:
                self.macro_mark(f'{tag}协奏满({time.time() - start:.2f}s)')
                return True
            self.macro_wait(self.SHORE_CON_POLL)
        self.macro_mark(f'{tag}协奏不满(con{best:.2f})')
        return False

    def seg_jingran(self, sk, tag):
        """景燃(变奏入场) -> Q -> R -> 按住左键打完四发重击 -> 变奏守岸人。返回切过去没有。"""
        self.poking = False
        self.jr_last_stance = None
        if not self.has_intro:
            self.logger.warning('景燃没拿到变奏入场(上一棒协奏没满) — 少 100 炁, 第一发重击要靠普攻攒')
            self.macro_mark(f'{tag}无变奏')
        self.fire_echo(self, tag=f'{tag}Q', window=self.JR_Q_WINDOW, presses=2)
        # 先读 R 亮不亮: 刚到场那几帧技能栏还是尤诺的, 等到场 JR_R_READ_SETTLE 之后再读
        self.macro_wait(max(0.0, getattr(self, 'last_switch_in_time', 0.0) + self.JR_R_READ_SETTLE - time.time()))
        ready = self.lib_ready_reads(self, 2)
        fired = self.fire_r(self, f'{tag}R', self.JR_R_WINDOW) if ready else False
        try:
            if fired:
                if self.JR_HOLD_SENDINPUT and self.game_foreground():
                    # 系统级按下, 演出一完游戏就读到"按着"(视频里作者也是在演出里按下去的)
                    self.jr_hold_start(f'{tag}')
                    self.wait_hud_back(tag=f'{tag}R演出')
                else:
                    self.wait_hud_back(tag=f'{tag}R演出')
                    self.jr_hold_start(f'{tag}')
            else:
                self.macro_mark(f'{tag}R没亮(按住攒能量)' if not ready else f'{tag}R没放出去(按住接着试)')
                self.jr_hold_start(f'{tag}')
                if self.jr_build_r(tag):
                    fired = True
                    self.wait_hud_back(tag=f'{tag}R演出')
            hud_back_at = time.time()
            # ---- 重击1 -> E1 ----
            h1 = self.jr_wait_heavy(f'{tag}重击1', self.JR_H1_CAP, self.JR_H1_BLIND,
                                    regrip_at=self.JR_H1_REGRIP if fired else None)
            e1 = self.jr_spam_e(f'{tag}E1', self.JR_E_CAP)
            e1_at = time.time()
            # ---- (黄泉渡) -> 重击2 -> E2 ----
            h2 = self.jr_wait_heavy(f'{tag}重击2', self.JR_HEAVY_CAP,
                                    max(0.0, e1_at + self.JR_HEAVY_BLIND - time.time()) if e1 else 0.0,
                                    not_before=self.JR_HEAVY_NOT_BEFORE if e1 else 0.0)
            e2 = self.jr_spam_e(f'{tag}E2', self.JR_E_CAP)
            e2_at = time.time()
            # ---- (往生送) -> 重击3 -> 打完换形态 ----
            h3 = self.jr_wait_heavy(f'{tag}重击3', self.JR_HEAVY_CAP,
                                    max(0.0, e2_at + self.JR_HEAVY_BLIND - time.time()) if e2 else 0.0,
                                    not_before=self.JR_HEAVY_NOT_BEFORE if e2 else 0.0)
            h3_at = time.time()
            self.jr_wait_flip(f'{tag}重击3完', self.JR_FLIP_CAP, h3_at + self.JR_FLIP_BLIND)
            if self.JR_F_BREAK:
                self.jr_f_break(f'{tag}')
            # ---- A2 A3 A4 -> 提示亮就闪避断 A4 -> 重击4 -> 打完换形态 ----
            dodged = self.jr_dodge_on_prompt(f'{tag}A4', self.JR_A4_CAP)
            cap = self.JR_H4_CAP_DODGED if dodged else self.JR_H4_CAP_NODODGE
            flip = self.jr_wait_flip(f'{tag}重击4完', cap, time.time() + cap)
            if flip != 'blind':
                self.macro_wait(self.JR_H4_TO_SWITCH)
            # 四发里有没判到出手的: 被打断/读漏了, 游戏那边可能还欠着一发。趁荧惑没完接着按住等,
            # 只等真的出手(提示亮 -> 暗), 等不到就算了 —— 只花时间, 不多按任何键
            missed = sum(1 for w in (h1, h2, h3) if w != 'fired') + (1 if flip == 'blind' else 0)
            if fired and missed:
                self.jr_extra_heavies(tag, missed, hud_back_at)
            full = self.jr_con_before_switch(f'{tag}')
            self.warn_no_con(full, '景', '守')
            self.macro_mark(f'{tag}h3:{h3}')
        finally:
            self.release_attack()
        return self.switch_to_sk(sk, tag)

    def jr_extra_heavies(self, tag, missed, since):
        """补等没判到的重击: 每发都是"提示亮 -> 暗"才算, 然后等它打完换形态。荧惑(从大招演完起 JR_YINGHUO 秒)过了就不等。"""
        got = 0
        for k in range(missed):
            left = since + self.JR_YINGHUO - time.time()
            if left <= 0.5:
                break
            why = self.jr_wait_heavy(f'{tag}补重击#{k + 1}', min(self.JR_EXTRA_HEAVY_CAP, left), blind=99.0)
            if why != 'fired':
                break
            got += 1
            self.jr_wait_flip(f'{tag}补重击#{k + 1}完', self.JR_FLIP_CAP, time.time() + self.JR_FLIP_BLIND)
        self.macro_mark(f'{tag}补重击(漏{missed},补上{got})')

    def jr_build_r(self, tag):
        """景燃 R 能量不够: 左键已经按住(连续普攻、炁满出重击, 都在攒能量), R 图标连续亮两帧就按 R,
        读不亮也每 JR_R_BLIND_EVERY 秒盲按一下(没能量是空操作)。R 走 key_na —— 不投 activate, 不打断按住。
        HUD 连续消失 = 大招放出去了。返回放出去没有。
        """
        key = self.get_liberation_key()
        start = time.time()
        n = gone = lit = 0
        next_blind = start + self.JR_R_BLIND_EVERY
        while time.time() - start < self.JR_R_BUILD_CAP:
            self.task.next_frame()
            now = time.time()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM and n > 0:
                    self.record_liberation_use()
                    self.macro_mark(f'{tag}R_fired(攒能量,x{n},{now - start:.2f}s)')
                    self.logger.info(f'{tag}: 景燃按住攒了 {now - start:.1f}s 能量才放出 R')
                    return True
            else:
                gone = 0
                try:
                    lit = lit + 1 if self.liberation_available() else 0
                except Exception:
                    lit = 0
                if lit >= 2 or now >= next_blind:
                    self.key_na(key)
                    n += 1
                    next_blind = now + self.JR_R_BLIND_EVERY
                    lit = 0
            self.macro_wait(0.03)
        self.macro_mark(f'{tag}R_NOFIRE(攒了{self.JR_R_BUILD_CAP:.0f}s,x{n})')
        self.logger.warning(f'{tag}: 景燃按住攒了 {self.JR_R_BUILD_CAP:.0f}s 能量 R 还是没放出来')
        return False

    def seg_jr_chain2(self, jr, iu, tag):
        """二链(启动轴): 切景燃, 用进战斗送的 300 炁按一发重击, 再切回尤诺。返回切回来没有。"""
        if not self.quick_switch(jr, click_during=False):
            self.macro_mark(f'{tag}二链(没切过去)')
            return False
        self.jr_hold_start(f'{tag}二链')
        try:
            self.jr_wait_heavy(f'{tag}二链重击', self.JR_C2_HEAVY_CAP, self.JR_C2_HOLD_BLIND)
        finally:
            self.release_attack()
        self.need(self.quick_switch(iu, click_during=False, time_out=self.JR_C2_BACK_TIME_OUT),
                  'chain2: back to iuno')
        return True

    # ==================== 一圈 / 启动轴 / 循环轴 ====================

    def run_cycle(self, iu, sk, opener, tag):
        """守岸人(HUD 在场) -> 尤诺 -> 景燃 -> 守岸人变奏入场、HUD 回来。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if opener:
            self.need(self.shore_open(sk, iu, tag=f'{tag}守'), 'shorekeeper -> iuno')
        else:
            self.need(self.shore_loop(sk, iu, tag=f'{tag}守'), 'shorekeeper -> iuno')
        self.need(self.seg_iuno(iu, self, sk, opener, tag=f'{tag}月'), 'iuno -> jingran')
        self.need(self.seg_jingran(sk, tag=f'{tag}景'), 'jingran -> shorekeeper')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        iu = self.teammate('Iuno')
        sk = self.teammate('ShoreKeeper')
        if iu is None or sk is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        for char in (self, iu, sk):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return iu, sk, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 【中途被停(关自动战斗/切出窗口)也要把时间轴写下来】: 实机 2026-09-28 第一局尤诺那段就停了,
        # 时间轴只在一圈跑完才写, 结果什么证据都没留
        if not getattr(self, 'timeline_logged', True) and self.macro_marks:
            self.logger.info(f'jys timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.poking = False
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: 切守岸人 -> 一圈(守岸人启动段)。返回时守岸人刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        iu, sk, previous = got
        self.logger.info(f'jys opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            if self.cur is not sk:
                self.need(self.switch_retry(sk, 'op', click_during=False), 'opener: switch to shorekeeper')
            self.run_cycle(iu, sk, opener=True, tag='op')
            self.logger.info(
                f'jys opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'jys opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是守岸人(宏散过)就先把场子交给她。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        iu, sk, previous = got
        try:
            if self.cur is not sk:
                self.need(self.switch_to_sk(sk, tag='L接守'), 'loop: hand the field to shorekeeper')
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(iu, sk, opener=False, tag=f'L{n}')
            self.logger.info(
                f'jys loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'jys loop broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_tail(self, entry_char):
        """宏散掉后尤诺/景燃带着变奏上场: 从她/他这一段接着打到守岸人, 然后回到正常循环。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        iu, sk, previous = got
        try:
            if entry_char is iu:
                self.need(self.seg_iuno(iu, self, sk, False, tag='T月'), 'tail: iuno -> jingran')
            self.need(self.seg_jingran(sk, tag='T景'), 'tail: jingran -> shorekeeper')
            self.logger.info(f'jys tail timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        except MacroBroken as e:
            self.logger.warning(f'jys tail broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def drive(self, entry):
        """三个人的 do_perform 都走这里。entry = 框架这次调的是谁(= 场上是谁)。"""
        # 【HUD 不在就什么都不做】: 关卡结束/结算/过场时框架还会接着调进来(F 开始战斗那边撑着不脱战),
        # 实机 2026-09-28 19:17~19:21 结算画面上尾段每 30s 重跑一遍, 一直在按 E/Q/跳/R/左键
        if not self.wait_hud_up(cap=self.DRIVE_HUD_CAP, tag='drive'):
            if time.time() - self.drive_log_at > 10:
                self.drive_log_at = time.time()
                self.logger.info('jys: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        if (entry is not None and entry.name in ('Iuno', 'JingRan') and getattr(entry, 'has_intro', False)
                and time.time() - getattr(entry, 'last_switch_in_time', 0.0) < self.TAIL_INTRO_TTL):
            self.logger.info(f'jys: {entry.name} came in with an intro, running from there')
            return self.perform_tail(entry)
        self.logger.info(f'jys: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_jys:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时让守岸人先上。"""
        if self.is_in_jys and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
