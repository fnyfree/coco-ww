import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src.char.BaseChar import SwitchPriority
from src.char.Changli import Changli as NativeChangli
from src.combat.MacroKit import MacroKit

# ==================== 物理 F 键监视 ====================
# 跟 Brant__Changli__Lupa/Changli.py 里那份逐字相同(只多认一个"宏在跑"的标志), 说明见那边。
# 两个队伍文件都能启动这个线程, 但每个 task 只启一次(_f_watch_started) —— 所以宏在跑时
# 两个标志都要置上, 不管是哪个文件先启的线程都认得出"这是手动处决, 不是重开副本"。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
# 界面上那张设置卡片, 在 ok_tasks/FStartCombatTask.py。放 ok_tasks/ 是因为选项必须由
# 代码声明(json 里多出来的键会被 verify_config 丢掉), 而 config.py/src 每次更新都会被
# 盖回官方版 —— 上一版的开关和时间就是这么没的。ok_tasks/ 不在仓库里, 更新不碰。
F_TASK_NAME = 'FStartCombatTask'


# 监视线程: 椰果版用主程序 src/combat/AxisWatch.py 那份; 原版 OKWW 没有这个模块, 导入失败就用下面自带的同一份
# (逻辑和 AxisWatch.py 一致, 写到 task 上的标记也一样, 线程按标记只起一次)
try:
    from src.combat.AxisWatch import f_settings, start_f_watch
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


class AxisAbort(Exception):
    """连续切人失败, 整条轴放弃(再往下按只会越错越远)。"""


class Changli(MacroKit, NativeChangli):
    """长离。龙凤雪(今汐/长离/绯雪)里她兼任【整条轴的持宏者】。

    ================= 轴的出处 =================
    B 站「【鸣潮3.6】龙凤雪慢放招式配文字轴」(ImmortalRover), 本地 E:/轴视频/。
    整条视频是【一轮爆发】: 顶部倒计时 119.5 -> 88.0, 约 32 游戏秒, 以绯雪的归刃(R2)收尾。
    视频是变速慢放, 下面的秒数一律是倒计时插值出来的游戏秒(大招/龙喷/枯霜的全局时停里倒计时是停的,
    挨着这些演出的格子读数偏小, 标了 ~ 的只作参考)。
    编队: 1 今汐 / 2 长离 / 3 绯雪 (宏按类名找人, 不依赖槽位)。

    ================= 格子(游戏秒 = 这个人在场多久) =================
        01 长离 E                          开局
        02 今汐 入场A2 A3                   0.85
        03 绯雪 入场A2                      0.12   等长离换人CD
        04 长离 R, 演出里按住 -> 焚身以火     演出
        05 今汐 A4 E2                       0.15
        06 绯雪 A3 E A3                     1.60   心念 300 解锁重击
        07 今汐 A1 A2 R, 演出尾按住左键 -> E3+Q  0.68 + 演出 + 0.15
        08 绯雪 (按住带过来)重击 -> Q -> R -> 演完 E   2.06 + 演出
        09 长离 (切入自动)征                 0.08
        10 今汐 A3 -> 跳 -> A4 -> E4 龙喷     0.81 + 演出
        11 绯雪 变奏 A2 A3                   1.74
        12 长离 E                            0.14
        13 今汐 出场A2 -> A3(离场前才起手)    1.06(我们 0.82)
        14 绯雪 A4 A5                        1.16
        15 今汐 A4                           0.16
        16 长离 跳 -> 按住(空中重击)           0.98   等今汐换人CD
        17 今汐 E2(按到 E 位不再是神霓飞芒; 带变奏进来要等过 QTE)   0.21
        18 绯雪 [处决] A3(处决派生的预求身第3段)   0.65
        19 长离 出场A3 A4                    0.64
        20 今汐 A1 E3                        0.54
        21 绯雪 闪避 -> 居合                  0.65
        22 长离 征                           0.32
        23 今汐 A2                           0.74
        24 绯雪 居合 E                       0.31(作者是接 21 的连续居合; 我们到场直接闪避出刀 ~0.75, 见 G24_TRY_CONT)
        25 长离 E(打中、协奏满的那一帧就切, 离地之前)   0.14(我们 ~0.22)   满协奏离场
        26 今汐 变奏进场(QTE +10 协奏), QTE 里不点, 放人就走   1.10(我们 1.30)
        27 绯雪 变奏 A2 A3                   ~1.5
        28 今汐 变奏 A4 E4 龙喷               ~1.1 + 演出
        29 长离 征                           ~0.1
        30 绯雪 居合 -> 跳 -> E -> 切长离(视频是按住带过去, 我们不按: 淬寒满了按着会出枯霜)  1.10
        31 长离 到场按住 -> 重击                0.25(视频, 我们 ~0.5)
        32 今汐 出场A2 -> A3(离场前才起手)    0.82
        33 绯雪 A2                           0.46
        34 长离 R, 演出里按住 -> 焚身以火     演出
        35 绯雪 A3                           0.13
        36 今汐 A4 E2 -> 跳 -> 空中A1 A2 -> E3    1.15
           (等不到神霓飞芒就不按 E 不跳、地面直接切; E3 没出先下落落地再切 —— 绯雪必须地面进场)
        37 绯雪 A -> 闪避 -> 居合x3 -> 就地按住 -> 强化重击(枯霜)   2.62(含时停)
        38 长离 E                            0.31
        39 今汐 A3 R, 演完 Q A4(落地后)        0.37 + 演出 + 墙钟 0.85
           (A4 是凌霄第四段, 出手后 E 变惊龙破空, 这里不按, 留给循环1 的 L1-03)
        40 绯雪(地面) 到场直接长按 R(蓄力 0.6s 时补 Q) -> 归刃    演出
    技能栏 CD 读数(视频 OCR)对得上: 长离 R 20s / 今汐 R 24s / 今汐 E3 10s / 绯雪 R 25s / 今汐、绯雪 Q 20s。

    ================= 循环(作者正常速度完整版文字轴, 2026-11-13 用户给的截图) =================
    启动 40 格只打一次, 之后是三段各不相同的循环, 每段以绯雪归刃收尾: 循环1(37 格) -> 循环2(19 格) -> 循环3(20 格),
    文字轴到循环3 为止; 打完还没脱战就再从循环1 转(这一步文字轴没写, 是我们定的)。逐格见 loop1 / loop2 / loop3。
    文字轴的记法(凤=长离 龙=今汐 绯=绯雪):
      凤 a(征) = 心眼里地面普攻 / 凤 跳(冲) a(下落) = 心眼里起跳即心眼·冲、再接下落(启动轴 16 格那一段) /
      凤 a3 = 下落或出场之后普攻从第三段起, 打完第四段进心眼 / 凤 强Z = 焚身以火(离火 4 层) /
      龙 e(凌霄) = 神霓飞芒 E2 / 龙 e(龙喷) = 惊龙破空 / 凌霄里的 e = 逐天取月 /
      绯 闪居 = 闪避进架势打一刀居合 / 绯 Z(射箭) = 红绯重击 / 绯 强化Z = 枯霜 / 绯 R = 红绯时拔刀、蓝绯时归刃。
    文字轴里今汐凌霄那几格的写法和实际按键对不上(段号差一号、跳和闪避没写、龙喷前的第四段省掉),
    2026-11-13 用户拿红字逐格标了实际操作, 循环里今汐照红字打:
      L1-11 A2 跳 A3(不按 E) / 凌霄里的逐天取月是 跳 -> 空中一段 -> E(L2-04、L3-03 A1A2跳A3E);
      L1-24 E2 跳 A1A2 / L1-27 A3 R E / L1-30、L2-15 A4 E(龙喷) / L3-03 …E 闪 A4 E /
      L3-18 A2A3A4 E2 跳 A1A2 闪 A3 闪 A4 E。
    龙喷那格仍读 E 位(lp_jx_longpen): 挂着冷却就点到第四段出手再按, 段数点岔了也能补回来。
    红字另外几处: L1-13 绯雪空中居合后补 E、L1-29 长离跳下落不按 E、L1-37 绯雪 A2A3 后先 E 再处决、
    循环2 开头红绯先放一个 E 把协奏补满再变奏长离、L3-20 绯雪不普攻直接按住 R(R 还差一点没转好, 按到它转好)。
    循环里大招图标没亮就不按(fire_r 里 round_no > 1 那道门), 其余照打。
    循环里的协奏链(2026-11-19 矩阵实机 4 局查出来的): L1-15 长离必须带满协奏变奏今汐, 把长离协奏在这里清零;
    没满的话长离协奏一路攒到 L1-22 大招后满, L1-23 绯雪被迫变奏(QTE 吞闪避) -> 居合少一刀 -> L1-25 没枯霜(没时停)
    -> 凌霄撑不到 L1-30; 长离协奏在 L1-23 清零 -> L2-01 攒不满 -> L2-02 绯雪不变奏、心念不够拔不了刀 -> 循环2 全散。
    L1-27 今汐 R 能量差一截时(没有 3.4s 时停)把凌霄第四段提前到 L1-27, L1-30 直接龙喷。

    ================= 这条轴的几个关键规则 =================
    - 【普攻段数跨切人保留】: 视频里"今汐A4""绯雪A3"都是上一次在场那条连段接着打。
      出场技: 今汐/绯雪复用 A2, 长离复用 A3 —— 所以写着"出场A2/A3"的格子, 切人期间就在连点。
      第一个动作不是普攻的格子(E / R / 跳 / 闪避), 切人期间一下都不点, 否则会多出一段普攻。
      【接着上次连段往下打的格子(05/15/36 今汐A4)能不能接上, 看上一段的派生窗口还开不开】: 人下场后窗口照样走
      (大招演出的时停里不走)。今汐 A3 派生 43F、窗口再开 45F, 实机从 A3 起手算约 1.55s 之内回来点才是第四段,
      过了就从出场技(A2)重来。所以 13 / 32 把 A3 压到离场前最后一刻才起手(jx_a2_late_a3), 15 / 36 到场第一下就点。
    - 【25 -> 26 -> 27 -> 28 是一条变奏链, 源头是 25 长离那下 E 要打中】: 长离进场协奏 0.8~0.9, E-1 第10F命中 +14 才满;
      满了切今汐 = 变奏(QTE 地转空, +10 协奏, 到场就满) -> 切绯雪 = 变奏(预求QTE 落在地上, +10) -> 协奏满切今汐 = 变奏
      -> 第四段 + 惊龙破空 -> 今汐协奏只剩三成, 29 长离普通进场、在地上 -> 30 绯雪地面居合。
      长离 E 没打中就切: 今汐普通进场(人在长离 E 挑起来的空中)、协奏差 1~3% 不满 -> 绯雪也是普通进场, 接在空中一路空中A。
      变奏切人不受换人冷却限制(作者 26 今汐头像上还剩 0.6s 冷却就进场了)。
      长离也不能多留: E-1 第12F 离地, 在场 0.32s 才切的 6 局今汐全在半空接变奏, 之后人人悬空(29 长离的征变成冲、
      31 重击变下落、32 今汐下落攻击); 在场 0.22s 的局今汐变奏贴地、1.07s 已站在地上。命中(10F)到离地(12F)只有两三帧。
      凌霄第三、四段出在哪一格每局不一样(23 离场那下点击 / 26 的 QTE 尾巴 / 28), 所以 28 一律读 E 位:
      还挂着逐天取月冷却 = 第四段没出, 点到"冷却 -> 没冷却"再按 E4; 没冷却 = 已经出了, 直接 E4。
      第四段之后再变奏进场, E 还是惊龙破空(01:10 实机)。别用惊龙破空字形模板判(误认过, 见 jx_e_ready)。
    - 【凌霄普攻段数不重置】: 17 E2 进凌霄(10s) -> 20 A1 -> 23 A2 -> (23 尾 / 26 / 28) A3 A4 -> 28 惊龙破空;
      36 E2 -> 空中 A1 A2 -> 39 A3 ... A4 -> 惊龙破空。任何一格多点出一段, 第四段就提前, 凌霄提前结束, 后面的龙喷/神霓飞芒全错位。
      今汐没有输入缓存, 派生帧之前的点击直接丢, 所以"只出一段"= 连点时长短于派生帧(凌霄 A1 0.42 / A2 0.63 / A3 0.80)。
    - 【同奏】: 惊龙破空给今汐同奏(每 25s 只得一次), 有同奏时切走下一个人必定变奏、协奏不扣。10 的龙喷有同奏
      (11 绯雪必变奏); 28 的龙喷离 10 不到 25s 没有同奏, 29 长离变不变奏只看今汐协奏满没满 ——
      26 带满离场时今汐协奏在 26 清零, 28 龙喷后只有三成, 29 是普通进场; 26 没满, 28 龙喷 +20 就把它推满, 29 变奏在空中。
    - 【39 的游龙廻光留给循环1】: 36 的神霓飞芒 E2 进凌霄, 39 的 A4 是凌霄第四段, 出手 E 就是惊龙破空。39 不按,
      归刃之后循环1 第三步今汐进场直接龙喷(G39_E4 = False; 开着是以前"40 格原样循环"时的做法)。
    - 【绯雪的红 / 蓝】: 归刃之后回到红绯(常世身), 要重新 变奏(+200 心念) -> A3(+100) -> 重击射箭 -> R 拔刀 才是蓝绯;
      拔刀给 3 点武霜·居合, 三刀带武霜的居合攒满淬寒才有枯霜, 枯霜之后的居合只有发白圆牌(按时刻打)。
      每段循环里: 拔刀一次、带武霜居合三刀、枯霜一次、归刃一次。
    - 【换人CD = 离场那一刻起 1.0s】(正常速度视频头像上的数字逐帧读的, 每个人都是 1.0 从离场开始倒)。
      作者全程在狂按下一个人的切人键(画面上一直挂着「冷却状态下无法切换角色」), 游戏放行的第一帧就过去。
    - 【"切X"的格子很多只有 0.1~0.2s】, 那是在等下一个人的换人CD: 宏动作一做完就狂按切人,
      游戏放人的第一帧就走, 不写固定停留时间。
    - 【按住左键跨切人】(07->08 今汐->绯雪重击; 30->31 视频也是, 我们改成到长离再按): 按住期间的数字键、E、跳、闪避
      一律不激活窗口地投消息(tap_quiet / dodge_quiet)。task.send_key / click / mouse_down 都会先投
      WM_ACTIVATE, 窗口收到就把按住的左键松掉。
    - 今汐三种 E: E2(A4/出场之后, 不进CD) / E3(凌霄里, 进 10s CD, 按到进CD为止) / E4 龙喷(隐藏HUD, 按到HUD消失)。
    - 绯雪: 常世身时 A3 +100 心念, 常世E 给 A3 BUFF 之后的 A3 +200, 满 300 解锁重击, 重击第97F解锁 R(拔刀);
      拔刀后是预求身, 寒意 >=100 能闪避进居合架势; 枯霜(预求强化重击)给 1 层叠层C, 所以最后的归刃只蓄 1 层。
      回路条圆牌个数 = 寒意百位, 深蓝实心 = 还有武霜·居合。21/24/30 三刀各用一点武霜(攒满 3 层淬寒),
      37 三刀把剩下的寒意全用掉(作者慢放版字幕「打出三次居合」, 回路条 3 -> 0), 40 格没有居合。
      居合架势/居合有空中版, 但归刃蓄力只有地面版 —— 绯雪人在空中, R 按着要等落地才蓄力。
      所以 30/40 格前一个人必须在地面离场, 40 格也不能按地面 E(白玉切是地转空, 帧表 r949)。
    - 帧表里绯雪有「输入缓存」(提前点的普攻会排队), 今汐没有(早按的直接丢) —— 两个人的连点窗口不能一样处理。

    ================= 改这个文件之前先读的几条 =================
    - 引擎那一半(macro_* / quick_switch / fire_lib / press_key_until_cd …)是从
      Changli__Jinhsi__Verina/Changli.py 逐字抽出来的, 那边注释里提到的角色和日志是别的轴的标定记录。
    - 【自定义 __init__ 里跟内置版取值不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 轴在绯雪归刃演完、HUD 回来之后才返回 —— 【别把返回点挪进任何演出里】, 演出期间读不到目标,
      框架会判脱战、重进战斗。
    - 日志里每一段都有 `longfengxue part N timeline`, 实机报"哪一格慢/没出来"先读那一行。
    """

    # ==================== 全局节奏 ====================
    A_INTERVAL = 0.12            # 0.12 是实测的地板, 更密会让空中角色打出下落攻击
    KEY_INTERVAL = 0.10

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    SWITCH_SETTLE = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True
    # 切人途中密点(dash_click): 数字键按下 0.02s 后起每 0.03s 点一下, 直到到场。
    # 作者正常速度视频 36 格: 今汐到场 0.05s E 位就变神霓飞芒、0.17s 按 E2 —— 第四段是切人途中就点出去的。
    # v1.2.9 改成"到场才点"后, 36 到场那下打成出场技(复用 A2), 又走一遍 A2->A3->A4, 1.58s 才见神霓飞芒(20:32~20:36 三局)。
    # 跟切人键同一刻发的那一下会落在离场的人身上, 所以第一下要错开一点
    DASH_CLICK_FIRST = 0.02
    DASH_CLICK_INTERVAL = 0.03

    # ---- 大招 ----
    LIB_FIRE_WINDOW = 1.20
    LIB_KEY_INTERVAL = 0.03
    LIB_READ_EVERY = 2
    LIB_ANIM_TIME_OUT = 8.0
    LIB_ANIM_TAIL = 0.15
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防 fire_lib 假成功)

    # ---- 协奏 ----
    CON_WAIT_TIME_OUT = 2.0
    CON_POLL_INTERVAL = 0.05
    CON_FULL_LEVEL = 1.0
    CON_SAMPLE_MAX = 24

    # ---- 场上是谁 ----
    FIELD_SETTLE_TIME_OUT = 1.5

    # ---- press_e_until_cd 的重发间隔 ----
    E_RESEND_STEP = 0.06
    # 刚切进来的角色, 多久都没见过技能亮就当它在 CD 上(技能栏换成新角色要过几帧, 见 press_key_until_cd)
    SKILL_BAR_SETTLE = 0.40

    # ---- 长离重击(焚身以火): 按住到重击就绪图标熄灭 ----
    CL_HEAVY_CAP = 2.50          # HUD 在场之后最多按这么久
    CL_HEAVY_REPRESS = 0.90      # 按住这么久图标还亮 -> 松开重按一次(按下沿可能被收招丢了)
    CL_HEAVY_SEE_GRACE = 0.50    # 这么久一次都没读到亮 -> 判据读不到, 改盲按
    CL_HEAVY_BLIND_HOLD = 0.90

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 本条轴 ====================
    # 处决: 只在屏幕中央真有 F 提示时才按(F 也是拾取键)。不想让宏按就改 False, 手动按
    USE_AUTO_F_BREAK = True
    EXECUTE_ANIM = 1.60          # 处决时停里画面静止, 只能按秒等(穗露绯那边实机值)
    # 连续这么多次切人超时就放弃整条轴
    ABORT_AFTER_FAILS = 3
    KEY_TAP = 0.04               # 技能键连按间隔
    # 居合: 闪避后一直点, 架势顿帧派生 30F 之后第一刀出手(出手第 1 帧扣圆牌), 之后每刀隔约 0.70s。
    # 2026-11-11 18:53 实机 37 格录屏: 闪避后 0.54s、1.24s 各扣一个圆牌。原来按 0.20 + 0.65 算,
    # 第三刀(该在 1.94s)还没出就按住了。KENDO1_DELAY / KENDO_DERIVE 只有 37 格按时刻连点用
    DODGE_TO_KENDO = 0.05
    KENDO1_DELAY = 0.55
    KENDO_DERIVE = 0.70
    KENDO_SPAM = 0.10
    KENDO_SPAM_LAST_GAP = 0.05
    # 回路条上的圆牌: 中心(3840x2160 基准)和半径。个数 = 寒意的百位, 深蓝实心 = 这一格还有武霜·居合
    KENDO_MARK_XY = ((1686, 1992), (1904, 1992), (2118, 1992))
    KENDO_MARK_R = 24
    KENDO_LIT_RATIO = 0.40       # 深蓝实心占比: 亮 0.51~0.58 / 发白 0.00 / 刀身亮头扫过 <=0.28 (2K 录屏)
    KENDO_MARK_PRESENT = 0.20    # 宽判据, 亮头也会算进去, 只写日志
    KENDO_SEE = 0.25
    KENDO_UP_SETTLE = 0.20       # 冒出新圆牌时会闪暗 ~0.08s, 这么久之内不认变少
    KENDO_BLIND = 0.70           # 一个亮牌都没有(武霜用完/读不到)时按时刻连点这么久: 闪避后约 0.65s 出刀
    IAI_CAP = 0.95               # 闪避后约 0.65s 扣层, 留余量
    # 大招: 按 R 到演出开始的窗口。R 按早了(还没解锁)是空操作, 绯雪有输入缓存还会排队
    CL_R_WINDOW = 2.00
    JX_R_WINDOW = 1.50
    HY_R_WINDOW = 1.80
    JX_E4_CAP = 1.00             # E4 龙喷: 按到 HUD 消失的上限
    E4_ANIM_TIME_OUT = 5.0
    HY_R_ANIM_TIME_OUT = 6.0     # 拔刀 隐藏HUD 240F
    R2_ANIM_TIME_OUT = 8.0       # 归刃 隐藏HUD 336F
    # 焚身以火清空离火(第7F)之后到切人。视频里是立刻切
    CL_FENSHEN_TO_SWITCH = 0.0
    # 变奏交接: 等协奏真满的上限(满了立刻走)
    INTRO_CON_CAP = 1.20         # 差一两刀就满的时候多打一会儿; 没变奏的话下一个人会接在今汐的空中位置上空挥
    INTRO_CON_CAP_LOOP = 0.30    # 第二圈起今汐大招没转好, 协奏差得远, 不多等
    # 重击指示: 刚切进来技能栏/回路条要过几帧才是这个人的, 这么久一次都没读到亮就当读不到
    HEAVY_SEE_GRACE = 0.45

    # ---- 01~04 ----
    G01_CL_E = 0.10              # 长离 E 先按这么久再边按边切(E 地转空、可脱手)
    G02_JX_A2A3 = 0.90
    # 入场A2: 到场后再连点这么久(2 下)。这一下没出, 06 进场那刀就是 A2 不是 A3, 心念凑不满 300
    # (2026-11-11 实机 8 局里 7 局 `06绯雪A3心念(CAP`)
    G03_HY_DWELL = 0.12
    # ---- 05 今汐 A4 E2 ----
    G05_A4_TO_E = 0.08           # 今汐没有输入缓存: E 按早了(A4 还没被接受)出来的是 E1
    G05_E_ONLY = 0.10            # 按完才切绯雪, 不边切边按 E(E 会漏到绯雪身上, 变成先 E 后 A3)
    G05_E4_SEE = 0.25            # 第二圈起: E 按完这么久里 HUD 没了 = 放成了龙喷(10 格龙喷 HUD 在按 E 后 0.18s 内就没)
    # ---- 06 绯雪 A3 E A3 ----
    # 06 进场那刀(A3)给 100 心念, 回路条第一段变蓝才按 E; 作者视频里到场 2 帧就亮、0.13s 按 E。
    # 03 的 A2 没出时这里要多点一刀(A2 再 A3), 所以上限放到 0.90; 到上限照样按 E
    G06_SEG1_CAP = 0.90
    G06_CLICK = 0.08
    # 心念第一段: 3840x2160 下的框, 亮 = 蓝色像素占比过线(没亮是灰点 0.00, 亮了 0.8~0.98, 2K/1080 一致)
    XN_SEG1_BOX = (1640, 1980, 1772, 1994)
    XN_SEG1_RATIO = 0.30
    G06_E_CAP = 1.00
    # 常世E 出去之后点到心念满(重击指示亮)的上限。E-2 第78F(1.30s)前普攻打不断 E
    G06_A3_CAP = 1.60
    G06_FORTE_SETTLE = 0.30
    # ---- 07 今汐 A1 A2 R -> 演出尾按住 -> E3 + Q ----
    G07_JX_A1A2 = 0.75
    # 大招出手后多久按住左键。隐藏HUD 207F(3.45s), 第204F前不响应输入 —— 按早了没副作用,
    # 按晚了演完那一帧没按着, 绯雪进场接不上重击
    G07_HOLD_AT = 2.50
    G07_E3Q_WINDOW = 0.12
    # ---- 08 绯雪 重击 Q R E ----
    G08_HEAVY_CAP = 1.00
    G08_HEAVY_BLIND = 0.50       # 重击指示读不到时盲按这么久
    G08_Q_AT = 1.55              # 从到场算。视频 CD 读数: 到场后 ~1.6s Q 已经在转
    G08_R_AT = 1.70              # 重击 箭矢3 第97F(1.62s)解锁 R; 早按的排队
    G08_E_ONLY = 0.06
    # ---- 09 长离 征(切入自动) ----
    G09_CL_DWELL = 0.08
    # ---- 10 今汐 A3 跳 A4 E4 ----
    G10_A3_TO_JUMP = 0.60        # A3-2 特效 第39F(0.65s)
    G10_JUMP_TO_A4 = 0.06
    G10_A4_TO_E4 = 0.10
    # ---- 11~15 ----
    G11_HY_INTRO_A2A3 = 1.70     # 预求QTE 60F 不能切人 + A2 派生 32F + A3 起手
    G12_CL_E = 0.08
    # 13 / 32 今汐 出场A2 -> A3: A3 要压到离场前最后一刻才起手。
    # 下一次上场(15 / 36)的"A4"接的是这下 A3 的派生窗口, 窗口在今汐下场后照样走(演出时停里不走):
    # 35 局实机里 A3 起手到下次到场 1.54s(13->15)成 32 局、败 3 局(败的那局 17 的 E 是 3s 冷却的普通 E, 整条凌霄没了);
    # 1.62s(32->36)一局没成, 每次都从出场技重打 1.58s。帧表 A3 派生 43F + 派生持续 45F = 1.47s, 加顿帧正好卡在这。
    # 原来 13 按 0.12 间隔点到 0.85, A3 0.72 起手后白站 0.13s; 32 按 0.06 密点, A3 0.65 就起手、站到 0.88。
    # 现在: 到场点出 A2 -> 停手 -> 0.76 起点两下(出场A2 派生 38F=0.63, 留 0.13 给到场那下被吞的情况) -> 马上切。
    # A3 起手到下次到场: 13->15 约 1.43, 32->36 约 1.45
    G13_A2_SPAM = 0.20
    G13_A3_FROM = 0.76           # 从今汐到场算
    G13_A3_SPAM = 0.06           # 0.76 / 0.81 两下, 第二下是保险
    G13_A3_CLICK = 0.05
    G14_HY_A4A5 = 1.28          # 预求A4 派生 58F + A5-1 起手(A5-2 脱手后自行生成)
    G15_JX_A4 = 0.28             # 0.20 实机有时第四段没出手(E 图标没变神霓飞芒)
    G15_CLICK = 0.06
    # ---- 16 长离 跳 + 按住 ----
    G16_JUMP_TO_HOLD = 0.10
    G16_HOLD = 0.50              # 空中重击-下落 第34F 起手, 按住要盖过它
    # ---- 17~25 ----
    # 17 今汐 E2: 一直按 E, 直到 E 位不再是神霓飞芒(E2 真放出去了)才切。
    # 19:43 第二圈实机: 16 长离协奏满, 今汐带变奏进场(QTE 第60F前不响应输入), 原来 0.08s 的 E 全被吞,
    # 没进凌霄 -> 20 逐天取月按不出(`20今汐E3_CD?`) -> 26~28 变奏链断。上限要盖过 QTE 那 1.0s
    G17_JX_E2 = 0.08             # E 位一开始就不是神霓飞芒(15 格第四段没出)时, 照老办法连按这么久
    G17_E2_CAP = 1.50
    G18_HY_A3 = 0.40             # 没处决时: 点 A3 之后停这么久
    G18_F_WAIT = 0.60            # 到场没看到处决提示时最多等这么久(实机提示在到场 0.2~0.5s 才出), 等的时候不点
    # 处决后接 A3: 谐度破坏-2 派生帧 89F, 之后短按普攻 = 预求身第3段。处决时停里按的会丢, 连点盖住派生那一刻
    # (2026-11-11 18:06 录屏: F 之后 1.25s 演出结束, 绯雪站了 0.5s 什么都没打就切走了)
    G18_A3_AT = 1.20             # 从按 F 算, 开始连点
    # 20:36 实机: 18 格占 1.02 游戏秒, 作者 0.43。处决演出 F 后 ~1.25s 结束, A3 一起手(可脱手)就能切
    G18_A3_SPAM = 0.40
    G18_A3_CLICK = 0.08
    G18_A3_TAIL = 0.05           # 连点完再停这么久才切(A3 可脱手, 切走不断)
    G19_CL_A3A4 = 0.72           # 出场A3 派生 36F 接 A4(A4 切人留场到 205F)
    # 凌霄A1 第16F才命中, 0.20 时 E3 把 A1 掐在命中前(段数照记, 伤害协奏没拿到)。作者到场 0.35s 按 E3
    G20_A1_TO_E3 = 0.30
    G20_E3_CAP = 0.50
    # 判到 E3 进 CD 后再留这么久: 逐天取月第一段第20F出手、不可脱手, 协奏 +8 就在这一下。
    # 作者 26 是变奏进场(+10), 我们长离 25 协奏不满、26 普通进场, 少了这 10 点; 01:37 实机 26 协奏停在 0.97,
    # 没在 26->27 用掉, 28 龙喷 +20 一满, 切长离就带变奏 -> 长离 QTE 整段在空中 -> 30 绯雪接在空中居合。
    # 补上这 +8, 26 才能满、照作者那样在 26->27 把协奏交出去
    G20_E3_HIT = 0.26
    G21_HY_KENDO = 0.40          # 闪避后连点到这么久, 只出一刀(下一刀要 0.65s 之后)
    # 21 看到出刀后, 从最后一下点击算至少留这么久再切长离。24 那刀"到场就出"的连续居合成不成, 35 局实机只跟这个有关:
    # 最后一下点击到切人键 0.03s 的 5 局成 1 局, 0.06s 的 19 局成 7 局, 0.09~0.10s 的 12 局成 10 局。
    # 接不上就得闪避重进架势补刀(+0.9s), 长离 25 那格也跟着变样(见 G25_CD_COVERS 上面)
    # 2026-11-19 矩阵实机 4 局(v1.3.1): 停 0.14 之后 24 连续居合 4/4 都没接上, 白试 0.40s 再补刀,
    # 24 整格 1.27s(作者 0.31), 28 的凌霄跟着超时(`28距17E2` 10.33~10.40, 4/4 `28今汐E4_NOHUDGONE`)。
    # 24 改成到场直接闪避补刀(G24_TRY_CONT), 21 也就不用多站了
    G21_STAY_AFTER_CLICK = 0.0
    G22_CL_ZHENG = 0.28
    G23_JX_A2 = 0.62             # 0.70 时 24 格离 21 那刀 1.3s, 连续居合接不上
    # 24 先试连续居合(不闪避)。关掉 = 到场直接闪避进架势出刀: 慢作者 ~0.4s, 但不会白试 0.4s 再补(省 ~0.5s 给 28 的凌霄)
    G24_TRY_CONT = False
    G24_CONT_CAP = 0.36          # 作者到场 0.23s 扣层
    G24_SEE = 0.12
    G24_HY_E = 0.08
    G25_CL_E = 0.08
    # 【25 长离的 E 要打中了才切今汐, 26~30 整条变奏链靠这一下】。长离进场协奏 0.8~0.9, E-1 第10F(0.17s)命中 +14 才满。
    # 35 局实机: 长离到场 0.14~0.17s 才切走的 15 局, 14 局今汐是变奏进场(到场 0.02s 内协奏读 1.00, QTE +10),
    # 后面 27 绯雪变奏(落在地上)、28 今汐变奏龙喷、29 长离普通进场、30 绯雪地面居合全对;
    # 0.06~0.12s 就切走的 18 局只有 3 局是变奏, 其余今汐普通进场、协奏 0.91~0.99 差一点 -> 绯雪接在今汐的空中位置上一路空中A。
    # 以前那 14 局能成是今汐自己的换人冷却(离场起 1.0s)没走完, 普通切人被拦着, 等长离协奏一满、变奏切人不受冷却限制
    # 才放行; 24 格走闪避补刀(+0.9s)的局冷却早走完了, 切人键一按就过去, 正好赶在 E 命中之前。
    # 作者视频这格全程顶着「冷却状态下无法切换角色」在按, 今汐头像上还剩 0.6 冷却就进场了, 同一个机制。
    # (另有 2 局长离被冷却拦了 0.5s 协奏还是没满, 那种只能走普通进场。)
    # 【也不能晚】: 长离 E-1 第12F 就离地往上飞。录屏逐帧数长离在场: 作者 ~0.14s、v1.2.16 成功局 0.22s, 今汐的变奏贴着地、
    # 1.07s 时已经站在地上; v1.2.17 等到 0.22s 再读协奏才切, 在场 0.32s(6 局全是), 今汐在半空接变奏、之后一直悬着,
    # 27 绯雪 / 28 今汐龙喷 / 29 长离 / 30~32 全在空中(长离的征变成空中的冲, 31 重击变下落)。
    # 所以分两种: 今汐冷却还盖得住命中那一帧 -> 照作者一到场就狂按, 游戏在协奏满的那一帧自己放行;
    # 冷却已经走完(24 走了闪避补刀) -> 盯长离自己的协奏环, 读满那一帧立刻按。
    G25_JX_CD = 1.0              # 换人冷却: 离场起 1.0s
    G25_CD_COVERS = 0.30         # 长离到场时今汐冷却至少还剩这么多才算盖得住(命中在到场后 ~0.2s; 快路径实测剩 0.6, 慢路径 -0.2)
    G25_HIT_CAP = 0.30           # 冷却已过的局: 从长离到场算, 盯到这时协奏还没满也切(走普通进场那套)
    # ---- 26~29 变奏链 ----
    # 【凌霄普攻段数不重置】: 17 E2 进凌霄 -> 20 A1 -> 23 A2(离场那下点击常常已经起了 A3) -> 26/28 A3 A4 -> 28 惊龙破空。
    # 凌霄 A4 一出凌霄就结束、换成游龙廻光(5s 内 E = 惊龙破空)。
    # 26 变奏进场的局: QTE 的 +10 协奏到场 0.2~0.3s 才到账; QTE 放开输入(第60F)之后别再点 —— v1.2.17 在 0.9~1.35s
    # 点出了一段空中凌霄 A3, 人挂在半空等到 1.56s 才走。成功局(033908)到场的点击全被 QTE 吞掉、第78F 放人就走。
    # 【26 必须带满协奏离场, 整条 26~29 变奏链靠它】: 满 -> 27 绯雪变奏(QTE 落地) -> 28 今汐变奏 ->
    # 惊龙破空后协奏只有三成 -> 29 长离普通进场、在地上。
    # 只打 A3 不够: 三局实机(013721/014415/021353) 26 都没满。021353 录屏环逐度量: 进场缺 ~11°, A3 打中补 8°,
    # 剩 3°(~0.8%) -> 27 普通进场, 绯雪接在今汐空中的位置(25 长离 E 地转空, 今汐进场就在空中)一路空中A。
    # 但不是每局都差: 11-12 03:07 实机(v1.2.15)今汐进场 0.972、A3 打中到场 0.2s 就满(作者也是到场 0.1s 内满),
    # 那局为了等 A4 连点满 1.62s 上限, A4 还没出(E 位一直是逐天取月冷却 7.2->5.6)。
    # 【A4 能留在 28 就留在 28】: 第四段普攻给 5s 神霓飞芒窗口。作者 28 的 A4 到 36 到场 3.9 游戏秒, 36 到场 E 位
    # 就是神霓飞芒; A4 挪到 26 就过期, 36 要从出场技重打到第四段(+1.5s)。
    # 所以: A3 -> 不点等协奏 -> 满了直接带变奏切; 等不满(021353 那种差 ~0.8%)才接着点出 A4, 靠 A4-1 每 9F 一跳补满。
    G26_A3_SPAM = 0.45           # 到场连点这么久只出 A3: 切人后 ~0.3s 的点击进不去, A3 0.35~0.45s 才出, 派生 0.83 前点不出 A4
    G26_CLICK = 0.06
    G26_CON_WAIT = 0.30          # A3 之后不点、等协奏真满这么久(满的局到场 0.2s 就满了)
    # 没满: 接着点(A4)边点边等满。A3 最晚 0.45 起 + 派生 0.83 -> A4 最晚 ~1.3s 出; 02:45 实机 A4-1 补满用了 0.31s。
    # 26 离场最晚 ~1.9s, 28 按 E 离 A4 ~4s, 还在游龙廻光 5s 里
    G26_A4_CON_CAP = 1.10
    # 27 绯雪变奏 A2 A3: 连点到 1.55, 之后边点边等协奏满就走(上限 G27_CON_CAP)。
    # 原来固定点满 1.68 再等: 变奏进场的 6 局协奏 1.68~2.11s 才判到满, 作者这格 1.78s
    G27_HY_INTRO_A2A3 = 1.55
    G27_CON_CAP = 0.65
    G28_INTRO_IDLE = 0.90        # 同 26: 变奏进场先空等 QTE
    # 28: E 位还挂着逐天取月冷却 = 第四段还没出 -> 一直点到"冷却 -> 没冷却"(jx_a4_until_jinglong)再按 E4。
    # 26 没点进凌霄 A3 的局这里要先 A3(派生 48F=0.80)再 A4, 所以上限给到 1.30;
    # 第四段早就出了(E 位没冷却)就不点, 直接 E4。读不到 E 位才按老办法盲点 G28_A4_SPAM
    G28_A4_CAP = 1.30
    G28_A4_SPAM = 0.20
    G28_CLICK = 0.06
    G29_CL_ZHENG = 0.15
    G29_INTRO_IDLE = 0.80        # 长离带变奏进来时 QTE 第46F前不响应输入, 先不按
    G29_PLUNGE_HOLD = 0.45       # 长按出空中重击-下落(第1F 起算空中计数, 第34F出手), 下落持续到落地
    G29_PLUNGE_TO_SW = 0.55      # 松开到切人: 下落 + 落地派生 17F; 下落不可脱手, 切早了长离还在半空
    # ---- 30~31 绯雪 居合 按住 跳 E -> 长离重击 ----
    G30_HY_KENDO = 0.30
    # 29 长离带变奏进来时(27 协奏没满的连锁)长离 QTE / QTE-空中A4 都在空中, 绯雪接在空中:
    # 先长按普攻(空中下落攻击)落地, 再闪避进地面架势
    G30_LAND_FIRST = True
    G30_PLUNGE_HOLD = 0.40
    G30_PLUNGE_TO_DODGE = 0.45   # 下落-落地后第38F前可跳转架势
    # 30 格不在绯雪身上按住左键: 这一刀刚攒满 3 层淬寒, 按着的时间一长(落地后)就是强化重击。
    # 19:23 实机: 按住 42.15 -> 跳 42.50 -> E -> 42.78 切人被拒 -> 43.15 枯霜在 30 格放了, 31 长离重击和 37 的枯霜都没了。
    # 原来按着切过去是给 31 长离重击用的, 改成切到长离再按
    # 出刀 -> 跳。判到出刀时居合-1(15F, 可脱手)已经过半, 居合-2 自行生成。
    # 30+31 实机 2.00 游戏秒、作者 1.32, 0.35 -> 0.22 先收一点; 跳被吞的话地面 E 是地转空, 别再往小压
    G30_KENDO_TO_JUMP = 0.22
    G30_JUMP_TO_E = 0.10
    G30_E_WINDOW = 0.08
    # 长离到场才按住, 要盖过重击长按门槛(原来按着带过来只等 0.25)。估的, 没实测。
    # 0.50 -> 0.40: 28 的 A4 到 36 到场要压在 5s 神霓飞芒窗口里, 03:07 实机 5.16 游戏秒超了(作者 3.9), 31 比作者多 0.27
    G31_CL_HOLD = 0.40
    # ---- 32~36 ----
    # 32 今汐 出场A2 -> A3: 跟 13 一样把 A3 压到离场前才起手(见 G13_A2_SPAM 上面), 36 到场那下才接得上第四段。
    # 作者这格 0.83s, 也是 A3 刚起手就走(A3-1 第15F才命中, 他没等)
    G33_HY_A2 = 0.42
    G35_HY_A3 = 0.10
    # 36 今汐: E 要等第四段普攻真打出去、E 位换成神霓飞芒才按, 早按就是普通 E(进 3s CD), 后面逐天取月按不出。
    # 18:53 / 19:23 / 19:24 实机: 到场点 A4 后 0.1~0.28s 就按 E, 全是普通 E(`36今汐E3_没见过亮`)。
    # 现在切人途中就点, 到场后一直点到 E 位认成神霓飞芒(jx_click_until_shenni, 上限 G36_SHENNI_CAP)
    G36_CLICK = 0.06
    G36_E2 = 0.08
    G36_E2_TO_JUMP = 0.35        # 从按 E2 算。E2 第26F地转空
    G36_JUMP_TO_A = 0.08
    # 空中凌霄A1 派生 25F(0.42s)。原来 0.45s 按 0.13 间隔只点到 0.39, A2 没出, 39 的"A4"实际是第三段,
    # 凌霄留到第二圈 05 才打出第四段
    G36_AIR_A = 0.55
    G36_AIR_CLICK = 0.06
    G36_E3_CAP = 0.35
    # ---- 今汐 E 位图标(16x16 灰度字形模板, 从 Hsin__Jinhsi__ShoreKeeper/Hsin.py 逐字搬来) ----
    # 流光夕影=普通E / 神霓飞芒 / 逐天取月(含转 CD) / 惊龙破空。那边实机: 神霓飞芒 0.85~1.00, 2K 和 1080 都量过
    JX_ICON_MIN = 0.85
    JX_ICON_MARGIN = 0.05
    JX_JINGLONG_MIN = 0.92
    JX_JINGLONG_MARGIN = 0.12
    JX_ICON_SETTLE = 0.15         # 刚切进来这么久不认(技能栏可能还是上一个人的)
    # jx_shenni_state 用(只比神霓飞芒和流光夕影/逐天取月/冷却数字, 不看惊龙破空), 20:12 录屏量的
    JX_SHENNI_MIN = 0.80
    JX_SHENNI_MARGIN = 0.05
    JX_OTHER_MIN = 0.82
    # 36 格点普攻等 E 位变神霓飞芒的上限。到场时连段常停在 A3 不是 A4(19:42 实机两圈都是: 到场 0.9s 才起 A4,
    # 1.2s 时图标还没变就按了 E -> 普通 E -> 起跳后没有逐天取月带她落地 -> 绯雪空中进场, 37 三刀全是空中居合)。
    # A3 派生 0.72s + A4-1 命中 0.43s 再留余量。到了上限就不按 E、不起跳, 今汐站在地上直接切绯雪
    G36_SHENNI_CAP = 2.20
    # 神霓飞芒出了但逐天取月没出(今汐还在空中): 长按普攻 = 凌霄重击-下落, 落地派生 33F 后再切
    G36_PLUNGE_HOLD = 0.45
    G36_PLUNGE_TO_SW = 0.60
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
    # ---- 37 绯雪 三居合 -> 枯霜 ----
    G37_A_TO_DODGE = 0.25
    G37_KENDO_COUNT = 3
    G37_OUT_TO_HOLD = 0.10       # 最后一刀出刀之后多久按住
    # 第三刀出去就地按住 = 强化重击(长按门槛 ~0.4s, 预求重击-长按 24F)。原来按住后跳+闪(照慢放版字幕),
    # 19:24 实机跳闪之后枯霜晚了 1.1s 才出; 用户: 第三刀出去直接接强化重击
    G37_HEAVY_HOLD = 0.60
    G37_SWITCH_TIME_OUT = 4.5    # 强化重击 150F 全局时停、无敌期间不能切人
    # ---- 38~40 ----
    G38_CL_E = 0.08
    G39_JX_A3 = 0.50
    # 今汐大招-前置是空中动作, HUD 回来时人还在往下落: 这时点普攻出的是空中凌霄A4(在空中挂 1.9s),
    # 马上切绯雪她就接在空中, 归刃蓄力只有地面版, 要落地才放。
    # 作者正常速度视频: HUD 回来 +0.1 Q, +0.3 地面 A4, +0.85 切绯雪(18:06 实机是 +0.30 就切了; 18:53 实机今汐已落地)
    # 18:53 实机 +0.30 时今汐已落地, 凌霄A4-1 第1帧出手、可脱手, A4 起手就能切。
    # 作者慢放版 HUD 回来 +0.30 游戏秒绯雪就进场了; 20:36 实机 39 格比作者多 0.6
    # 今汐 R 隐藏 HUD 207F, 实机从判到放出到 HUD 回来 3.43s。11-12 01:11 那局 37 枯霜之后, 0.52s 时 HUD
    # 闪回来一下就判成演完, Q 和 A4 全按在演出里被吞、切绯雪又卡 2.5s。见过消失之后不到这么久不认回来
    JX_R_ANIM_MIN = 3.00
    G39_A4_AT = 0.30             # 以下从 HUD 回来算
    G39_JX_A4 = 0.12
    G39_SWITCH_AT = 0.45
    # 39 的 A4 是凌霄第四段(36 E2 进的凌霄), 出手就给游龙廻光, E 变惊龙破空。两个教学视频这里都【不按】:
    # 作者 39 结束 E 位已是惊龙破空(正常速度视频 56.90 金色龙头)就切绯雪, 第二轮 05 今汐进场直接龙喷。
    # 我们是一圈 40 格循环(用户要求): 留到第二圈, 05 的 E2 放成龙喷(不进凌霄)、龙喷给的同奏让 06 绯雪变奏进场,
    # QTE 吞掉 06 的 E(03:07 实机 `05今汐E放成龙喷` -> `06绯雪E_CD?`), 第二圈从头乱。用户要求 R2 之前把它按掉。
    # 关掉 = 回到作者打法。
    # 2026-11-13: 用户给了作者的完整文字轴, 循环不是把这 40 格重打, 而是另外三段(循环1/2/3), 循环1 第三步就是
    # "今汐 E(龙喷)" —— 用的正是 39 这下第四段留下的游龙廻光。所以这里回到作者打法不按, 留给 L1-03。
    G39_E4 = False
    # A4 之后等 E 位变惊龙破空的上限。认法: 先见冷却数字、再连两帧没冷却(03:07 录屏: A4 出手那下 E 位闪一下 9.9,
    # 0.1s 后变龙头)。字形模板和"没冷却"单看都不行: 39 HUD 回来时逐天取月冷却刚好走完(0.1), 模板也读 jinglong
    G39_E4_SEE = 0.45
    # 39 按了惊龙破空 -> 同奏/满协奏 -> 绯雪变奏进场。QTE 派生 60F, R 只按下一次(按住蓄力), 落在 QTE 里被吞就没有归刃,
    # 所以按了惊龙破空就先空等这么久再按 R(作者正常速度视频 40 也是到场 1.3s 才按 R)
    G40_INTRO_IDLE = 1.05
    # 40 绯雪: 37 格三刀把寒意用光了, 这里没有居合, 也不按 E —— 地面 E(白玉切)是地转空(帧表 r949),
    # 18:53 实机按了 E 人就上天, R 按着等落地才蓄力。到场直接长按 R(慢放版里"顿一下等长离"用户说是没用的操作)
    # 归刃: R 只按下一次一直按住到演出开始(HUD 消失), 中途不重发; 叠层用完游戏自己放。
    # 正常速度视频: 到场 1.3s 后按下, 蓄力(黑边、全局时停)墙钟 3.2s, 蓄力 0.6s 时补 Q
    G40_R2_HOLD = 4.00           # 按住上限
    G40_R2_MIN = 1.00            # 按下后这么久之内不认"HUD 消失"
    G40_Q_AT = 0.60

    # ==================== 循环 1 / 2 / 3 ====================
    # 2026-11-13 照作者完整文字轴新写, 全部没实机。能复用启动轴常量的格子直接用 Gxx_, 这里只放循环独有的。
    # 文字轴只有循环1 前 17 格在正常速度视频里有画面, 其余时长是按帧表和启动轴同类动作估的。
    LP_CON_CAP = 0.50            # 写着"变奏X"的切人: 等协奏读满的上限, 到了没满也切
    # L1-15 长离 征 -> 变奏今汐: 这一下长离必须带满协奏走(在这里把协奏清零)。
    # 2026-11-19 矩阵实机 4 局都只有 0.87~0.94(不点干等 0.50s) -> 没变奏、长离协奏留着一路攒,
    # L1-22 大招后满了, L1-23 切绯雪被迫变奏(QTE 吞掉闪避, 居合没出) -> L1-25 淬寒不够没枯霜(没时停)
    # -> 凌霄撑不到 L1-30 龙喷; 长离协奏在 L1-23 清零, L2-01 冲+E 只到 0.62~0.69 -> L2-02 绯雪没变奏拔不了刀。
    # 唯一 L1-23 没变奏的那局(115111) L1-30 龙喷、L2-01 协奏都对。所以这里边点边等满
    LP_CL_CON_CAP = 1.20
    # 绯雪带变奏进来(QTE 前 ~1.0s 不响应输入)又要"闪避进架势"的格子: 先空等这么久再闪避, 不然闪避被吞、居合不出
    LP_HY_QTE_IDLE = 0.90
    # L2-01 长离 冲 + E 之后协奏还没满: 再边点边等这么久(都没满就普通切, L2-02 走补心念)
    LP_CL_E_CON_POKE = 0.60
    LP_F_WAIT = 0.35             # 处决提示最多等这么久(提示出不出看当局谐度), 等的时候不点
    LP_F_TO_HOLD = 0.20          # 长离按 F 之后多久按住左键预输入焚身以火
    # ---- 长离 ----
    # 变奏进场: QTE 第46F(0.77s)前不响应输入, 从到场连点到这时, 放开输入后第一下就是心眼·冲
    LP_CL_INTRO_SPAM = 0.95
    LP_CL_INTRO_CLICK = 0.08
    LP_CL_CHONG_TO_E = 0.25      # 冲出手到按 E
    LP_CL_E_CON_CAP = 0.30       # E 之后逐帧盯长离协奏, 读满就带变奏切(同 25 格 G25_HIT_CAP)
    # ---- 今汐 ----
    LP_JX_ONE = 0.32             # 点一段凌霄普攻后留这么久(凌霄A1 第16F 命中)
    LP_JX_A2 = 0.35              # 常态出场A2 连点这么久
    LP_JX_LX_A123 = 1.25         # 凌霄普攻连打三段: A1 派生 0.42 + A2 派生 0.63 + A3 起手
    LP_JX_A1234_CAP = 3.00       # 常态从第一段点到第四段(E 位变神霓飞芒)的上限: 0.42 + 0.63 + 0.72 + A4 出手 0.43
    LP_JX_DODGE = 0.20           # 闪避取消后摇之后多久再点
    LP_E2_SEE = 0.45             # 到场这么久 E 位都没认成神霓飞芒 = 这次没有 E2, 不按
    LP_E2_SEE_INTRO = 0.60
    LP_E4_FIRST_CAP = 0.50       # E 位没冷却时直接按龙喷的上限(龙喷按 E 后 0.18s 内 HUD 就没)
    LP_E4_RETRY_CAP = 0.60
    LP_JX_LX_A4 = 0.30           # E 位没冷却(这段凌霄没用过逐天取月)时读不出第四段出没出手, 先连点这么久再按龙喷(估的)
    LP_JX_E2_SEEN_TO_JUMP = 0.25 # 认到 E2 放出去(图标变掉)之后多久跳; 启动轴 36 是从按下算 0.35
    # ---- 绯雪 ----
    LP_HY_E = 0.10
    LP_RED_E_CON_CAP = 1.20      # 循环2 开头红绯 E(升空再劈下)之后等协奏读满的上限(估的)
    LP_HY_E2_LAND = 0.45         # 蓝绯第二段 E 下砸到落地(估的)
    LP_R2_CD_SLACK = 2.00        # L3-20 归刃: 绯雪 R 可能还差一点没转好, 按住上限多给这么久
    LP_HY_A123 = 1.10            # 蓝绯 A1 派生 20F + A2 派生 32F + A3 起手(A3-2 脱手后自行生成)
    LP_HY_A23 = 0.80             # 出场A2 派生 32F + A3 起手
    LP_HY_A12 = 0.60
    LP_RED_A3_CAP = 2.20         # 红绯变奏 QTE 派生 60F + A3 到心念满(重击指示亮)的上限
    LP_RED_DODGE_TO_HOLD = 0.05
    LP_IAI_GAP = 0.12            # 一刀居合出刀到下一次闪避(居合-1 15F 可脱手)
    LP_IAI_TO_R = 0.15           # 最后一刀居合出刀到按住 R
    LP_KUSHUANG_ANIM = 2.50      # 枯霜松手后全局时停 150F, 这段里的点击被吞, 之后才接得上普攻
    # 居合后按住不松 -> 跳 -> E -> 落地自己出枯霜: 11-11 19:23 实机(当时是误触发)按住后 1.0s 枯霜出手
    LP_HOLD_E_TO_KUSHUANG = 0.75
    LP_HY_JUMP_TO_DODGE = 0.12
    LP_HY_AIR_DODGE = 0.30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.holding_attack = False
        self.poking = False
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.switch_fails = 0
        self.round_no = 1

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        start_f_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_longfengxue(self) -> bool:
        """本队 = 今汐 + 长离 + 绯雪。【按类名判】, 今汐/长离都有两套立绘两个 char_name。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Jinhsi', 'Hiyuki'}:
                team_members += 1
        return team_members == 2

    def opener_blocked_by(self):
        """轴不跑的原因; 返回 None 表示该跑。

        【这里不查任何"某个技能可用"】: 场下角色的可用性读的是上次在场时 OCR 到的 CD 残值,
        拿它当门会开场愣一下。防重入靠 opener_armed + OPENER_MIN_GAP。
        """
        if not self.is_in_longfengxue:
            return 'team is not jinhsi/changli/hiyuki'
        if not self.opener_armed:
            return 'already armed-down (ran once this combat)'
        # 玩家按过 F = 真的重开了一局, 直接放行(战斗判定抖动不会有人按 F)
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

    # ==================== 宏原语 ====================
    # 通用的在 src/combat/MacroKit.py (MacroKit); 这里只有这条轴自己的写法, 同名的盖过 MacroKit 的。

    def quick_switch(self, target, click=False, click_during=None, settle=None, time_out=None,
                     read_con=False, assume_con=False, click_interval=None,
                     click_first=False, dash_click=False):
        """按数字键直切给指定角色。

        不走 switch_next_char(): 那个要先跑 _choose_switch_target 选人, 顺序是框架
        说了算; 而且它每次发数字键都跟一次左键, 想不要那下 A 就没法不要。

        Args:
            click: 到场之后补一下普攻(复刻框架 BaseCombatTask.py:644 的行为)。
                这条轴上一般不用 —— 持续普攻本身就在点, 再补就是多推一段连段。
            click_during: 等换人生效的这段时间里继续点。默认跟随持续普攻开关。
                这就是用户说的"狂点普攻切人": 换人不是瞬间的, 这几下落在上一个
                角色身上, 正好把他最后那一段打出来。
            read_con: 按键之前读一次协奏, 决定要不要按"带变奏"记账。
                【不便宜】(能量环连通域分析), 只有真的要变奏的那两三处才传 True。
            assume_con: 不读屏, 直接按满协奏记。用在【刚放完大招】之后 ——
                那时协奏必满, 而演出期间能量环读数不可信。

        Returns: 是否真的切过去了。
        """
        if target is None:
            return False
        if target is self.cur:
            # 目标已经在场。【这里必须早退】: 不早退的话到场判据立刻成立,
            # 整步 50 毫秒走完, 后面那些"给他的"按键其实发在了别人身上
            self.macro_mark(f'>{target.name[:3]}(already)')
            return True
        entry = time.time()
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        settle = self.SWITCH_SETTLE if settle is None else settle
        click_during = self.poking if click_during is None else click_during
        click_interval = self.A_INTERVAL if click_interval is None else click_interval
        if dash_click:
            click_during = True
            click_interval = self.DASH_CLICK_INTERVAL
        source = self.cur if self.cur is not None else self
        # 【第一个数字键要抢在读协奏之前发】: 读协奏是能量环的连通域分析, 少则
        # 二三十毫秒、睡完之后的第一次读屏能到 0.86 秒 —— 夹在"上一个动作"和
        # "数字键"之间就是白白的延迟。切人本身要 0.06~0.11 才落地, 来得及读完
        slot = target.index + 1
        # 【记下第一个数字键的时刻】: 有几格是靠它当锚点的。
        # 用户逐帧量的都是"从切人到 A 出手", 而窗口默认从【检测到到场】算起 ——
        # 中间隔着切人生效 + 读屏确认, 两者差好几十毫秒
        self.switch_press_at = time.time()
        # 【先点几下再发第一个数字键】(click_first = 点几下)。用在"边打A边不停切人"
        # 的格子上: 游戏在角色动作期间会拒绝切人 —— 这正是那种写法不会掐掉 A 的原因。
        # 但如果这一刻场上角色【刚到场、还没起手】, 切人键就没有东西挡着, 会当场生效,
        # 她一下都不打就走。
        # 【一下不够】(用户 2026-08-16: "这里千咲又没A出来了"): 那一下要是没被接受
        # 就白给了。所以改成可以指定点几下 —— 每多一下只把切人键推迟一个
        # click_interval(0.06), 但多一次撞上"能出招的那一帧"的机会
        for i in range(int(click_first)):
            self.click()
            if i + 1 < int(click_first):
                self.macro_wait(click_interval)
        self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
        if assume_con:
            con_full = True
        elif read_con:
            con_full = bool(source.is_con_full())
        else:
            con_full = False
        con_cost = time.time() - entry

        start = time.time()
        last = start   # 第一个数字键已经在上面发过了
        next_click = self.switch_press_at + self.DASH_CLICK_FIRST if dash_click else 0.0
        trace = [] if self.TRACE_SWITCH else None
        team_size = len([c for c in self.task.chars if c is not None])
        untrusted_hits = 0
        clicks = 0
        while time.time() - start < time_out:
            now = time.time()
            if now - last >= self.SWITCH_KEY_INTERVAL:
                self.task.send_key(slot, down_time=self.SWITCH_KEY_DOWN_TIME)
                last = now
            # 点击的节奏跟切人键解耦: 切人键 0.03 秒一发, 点击照旧 A_INTERVAL,
            # 否则这段时间会以 0.03 的密度灌点击, 远低于安全下限
            if click_during and now >= next_click:
                self.click()
                clicks += 1
                next_click = now + click_interval
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            # 【count 不达标的 index 打折扣, 但不能一票否决】: in_team() 在只找到
            # 一个槽位文本时照样返回 True 和一个 index(那是渲染顺序的产物);
            # 但一票否决会造成假失败 —— 有槽位被特效盖住时 count 就是不达标,
            # 切人明明成功了也判不出来, 卡满超时, 整条轴从这里崩
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
                    self.macro_mark(f'{target.name[:3]}A')
                self.macro_gap(settle)
                return True
        # 【失败时更要打 trace】:
        #   一直是旧角色的 index  -> 游戏真的在拒绝切人(上一个动作还没演完)
        #   带 ? 的都是目标 index -> 切过去了, 是 count 判据把它挡住了
        #   全是 x               -> HUD 整个不在(演出/时停里)
        self.logger.warning(f'switch to {target.name} slot {slot} timed out '
                            f'after {time_out:.1f}s (stuck in an animation?)'
                            + (f' trace: {" ".join(trace)}' if trace else ''))
        self.macro_mark(f'>{target.name[:3]}FAIL')
        return False

    def wait_hud_back(self, tag='anim', time_out=None, tail=0.0, min_dur=0.0):
        """等演出结束(队伍 HUD 回来), 【期间一下都不点】。

        跟 poke_until_hud_back 的唯一差别就是不点普攻 —— 【角色在空中时必须用这个】:
        那时候的点击会变成下落攻击, 一下就把她砸回地面, 后面整段空中戏就没了。

        【"HUD 回来"要先确认它消失过】: 进来的这一刻画面可能还在渐变, 读一帧很可能
        读回 True, 那就是"演出刚开头就判成结束"。
        min_dur: 消失过之后, 不到这么久 HUD 读回来也不认(演出开头 HUD 会闪回来一下)。
        """
        time_out = self.LIB_ANIM_TIME_OUT if time_out is None else time_out
        start = time.time()
        gone_seen = False
        back = False
        while time.time() - start < time_out:
            self.task.next_frame()
            el = time.time() - start
            if not self.task.in_team()[0]:
                gone_seen = True
            elif (gone_seen and el >= min_dur) or (not gone_seen and el >= self.HUD_NO_ANIM_GRACE):
                back = True
                break
            self.macro_wait(0.01)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}({"ok" if back else "timeout"},'
                        f'{"anim" if gone_seen else "NOANIM"},{time.time() - start:.2f}s)')
        self.macro_wait(tail)
        return back

    def hold_until_forte_cleared(self, tag):
        """(调用时左键已经按着)按到重击就绪图标熄灭为止。

        - 【先要读到过"满"才认"清空"】: HUD 刚回来那几帧图标还在渐入, 读不到 != 清空了。
          HUD 回来 CL_HEAVY_SEE_GRACE 秒还一次都没读到满, 就当判据读不到, 改盲按
          CL_HEAVY_BLIND_HOLD 秒 —— 不能拿"没读到"当"已经放出去"。
        - 【按了 CL_HEAVY_REPRESS 秒还满着 -> 松开重按】: 大招收招里那个按下沿可能被
          游戏丢了, 按住的状态不一定会被补读; 给它一个新的按下沿。
        - HUD 又不在(被别的演出盖住)的那几帧不判。
        """
        start = time.time()
        press_at = start
        seen_full = False
        cleared = False
        blind = False
        represses = 0
        trace = []
        while time.time() - start < self.CL_HEAVY_CAP:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                self.macro_wait(0.02)
                continue
            try:
                full = bool(self.is_mouse_forte_full())
            except Exception:
                full = False
            now = time.time()
            if len(trace) < 30:
                trace.append(f'{now - start:.2f}:{1 if full else 0}')
            if full:
                seen_full = True
                if now - press_at >= self.CL_HEAVY_REPRESS:
                    self.task.mouse_up()
                    self.macro_wait(0.05)
                    self.task.mouse_down()
                    press_at = time.time()
                    represses += 1
            elif seen_full:
                cleared = True
                break
            elif now - start >= self.CL_HEAVY_SEE_GRACE:
                blind = True
                break
            self.macro_wait(0.02)
        if blind:
            left = self.CL_HEAVY_BLIND_HOLD - (time.time() - press_at)
            if left > 0:
                self.macro_wait(left)
        state = 'cleared' if cleared else ('blind' if blind else 'CAP')
        self.macro_mark(f'{tag}焚身以火({state},repress{represses},{time.time() - start:.2f}s)')
        if not cleared:
            self.logger.info(f'{tag}: heavy-ready icon never read as cleared ({state}) '
                             f'— trace: {" ".join(trace)}')
        return cleared

    def wait_hud_up(self, cap=None, tag='HUD'):
        """等队伍 HUD 【现在】在场。按 R 之前用 —— HUD 本来就不在的时候 fire_lib 会当场判
        "演出开始了", 一发都没按出去就记成放了。跟 wait_hud_back(先消失再回来)是两回事。"""
        cap = self.HUD_UP_CAP if cap is None else cap
        start = time.time()
        while time.time() - start < cap:
            self.task.next_frame()
            if self.task.in_team()[0]:
                if time.time() - start > 0.05:
                    self.macro_mark(f'{tag}在场({time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}一直不在({cap:.1f}s)')
        return False

    def fire_r(self, char, tag, window, skip_if_dark=True):
        """先等 HUD 在场再 fire_lib。fire_lib 里的 self.sleep 会把 skip_combat_check 放回 False,
        这里重新置上(宏里后面还有好几格高频切人)。"""
        self.wait_hud_up(tag=f'{tag}前HUD')
        # 第二圈起大招可能还没转好: 图标没亮就不按, 省下整个按键窗口(第一圈照旧直接按)
        if skip_if_dark and getattr(self, 'round_no', 1) > 1:
            self.task.next_frame()
            try:
                ready = bool(char.liberation_available())
            except Exception:
                ready = True
            if not ready:
                self.macro_mark(f'{tag}_没转好跳过')
                return False
        fired = self.fire_lib(char, tag=tag, window=window)
        self.task.skip_combat_check = True
        return fired

    def press_until_hud_gone(self, char, cap, tag, poke=True):
        """一直按 E 到队伍 HUD 连续消失(惊龙破空的全局时停开始)。返回按出来没有。

        发 E 和点普攻各走各的节奏, 不挤在同一帧(同一帧两个键游戏只认后按的那个)。
        【只有 HUD 在场时发的 E 才算按过】: 进来那一刻如果别的演出还没过去, 那几发不算数。
        """
        start = time.time()
        ne = live = gone = clicks = 0
        next_click = 0.0
        while time.time() - start < cap:
            self.task.next_frame()
            hud = bool(self.task.in_team()[0])
            if not hud:
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
                if poke and now >= next_click:
                    self.click()
                    clicks += 1
                    next_click = now + self.A_INTERVAL
                self.macro_wait(min(0.02, step_end - now))
        self.macro_mark(f'{tag}_NOHUDGONE(E x{ne},A x{clicks},{cap:.1f}s)')
        try:
            ready = bool(char.resonance_available())
        except Exception:
            ready = 'err'
        self.logger.info(f'{tag}: HUD never went away after {ne} E presses (E available={ready}) — '
                         f'available=False 说明回路没攒够、E 位置还是普通E 的 CD')
        return False

    def changli_lib_then_heavy(self, tag):
        """长离 R -> 预输入重击(一路按过演出) -> 读到重击就绪图标熄灭才松手。返回清掉没有。

        大招第157F离火+4, 所以演完手上就是满离火, 紧接着的重击就是焚身以火。
        【R 一出手就按住】: 大招第191F前不响应输入(演出 168F 就结束了), HUD 回来之后按
        不等于能出招; 按住的状态会在能出招的第一帧被读到。
        """
        fired = self.fire_r(self, f'{tag}R', self.CL_R_WINDOW)
        if not fired:
            self.logger.info('changli R did not start an animation, carrying on')
            if getattr(self, 'round_no', 1) > 1:
                return False
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}预输入按住')
        try:
            if fired:
                self.wait_hud_back(tag=f'{tag}Ranim')
            return self.hold_until_forte_cleared(tag)
        finally:
            self.release_attack()
            self.macro_mark(f'{tag}松开')

    # ==================== 本条轴额外用到的原语 ====================

    def note_switch(self, ok, what):
        if ok:
            self.switch_fails = 0
            return ok
        self.switch_fails += 1
        if self.switch_fails >= self.ABORT_AFTER_FAILS:
            raise AxisAbort(f'{self.switch_fails} switches in a row failed (last: {what})')
        return ok

    def sw(self, target, **kwargs):
        """quick_switch + 失败计数。超时那一刻 HUD 不在(有演出盖着), 等演出完再切一次。
        20:38 实机第二圈: 05 今汐的 E 放成了龙喷, HUD 藏了 2.9s, 切绯雪 2.5s 超时, 06~08 全按在今汐身上。"""
        ok = self.quick_switch(target, **kwargs)
        if not ok:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                if not self.wait_hud_back(tag=f'>{target.name[:3]}等HUD', time_out=self.E4_ANIM_TIME_OUT):
                    # 切人超时 + 再等 5s HUD 还不在: 不是演出, 是结算/脱战界面。
                    # 11-19 矩阵实机 Boss 184.6s 打完, 轴又在结算界面里按了 50 秒键(R、普攻、切人)才凑够 3 次失败
                    raise AxisAbort(f'HUD gone for {self.SWITCH_TIME_OUT + self.E4_ANIM_TIME_OUT:.1f}s '
                                    f'while switching to {target.name} (result screen / out of combat?)')
                ok = self.quick_switch(target, **kwargs)
        return self.note_switch(ok, target.name)

    def hud_gone_within(self, sec, tag):
        """sec 秒内 HUD 连续 LIB_GONE_CONFIRM 帧不在 = 刚按的键放出了演出。"""
        start = time.time()
        gone = 0
        while time.time() - start < sec:
            self.task.next_frame()
            gone = 0 if self.task.in_team()[0] else gone + 1
            if gone >= self.LIB_GONE_CONFIRM:
                self.macro_mark(f'{tag}({time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.01)
        return False

    def sw_key(self, target, source, key_send, tag, click=False, read_con=False):
        """switch_out_while_key + 失败计数。"""
        return self.note_switch(self.switch_out_while_key(target, source, key_send, click=click,
                                                          read_con=read_con, tag=tag), target.name)

    def sw_intro(self, source, target, tag, poke=False):
        """等 source 协奏真满(上限 INTRO_CON_CAP) -> 带变奏记账切给 target。"""
        cap = self.INTRO_CON_CAP if getattr(self, 'round_no', 1) == 1 else self.INTRO_CON_CAP_LOOP
        return self.note_switch(self.pass_intro(source, target, cap, tag=tag, poke=poke),
                                target.name)

    def tap_quiet(self, key):
        """点一下键但不激活窗口(按住左键期间用)。activate 参数只有 PostMessage 后端认,
        别的后端退回普通发键。"""
        interaction = self.task.executor.interaction
        k = self.task.validate_key(str(key))
        try:
            interaction.send_key_down(k, activate=False)
        except TypeError:
            self.task.send_key(k)
            return
        time.sleep(0.01)
        interaction.send_key_up(k)

    def dodge_quiet(self):
        """按住左键期间闪避: 右键直接投消息。interaction.click 里的 update_mouse_pos 会激活窗口,
        那样按住的左键就松了。wParam 带上 MK_LBUTTON, 告诉游戏左键还按着。"""
        interaction = self.task.executor.interaction
        post = getattr(interaction, 'post', None)
        if post is None:
            self.task.click(key='right')
            return
        import win32api
        import win32con
        x, y = getattr(interaction, 'bg_mouse_pos', (0, 0))
        pos = win32api.MAKELONG(int(x), int(y))
        post(win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON | win32con.MK_LBUTTON, pos)
        time.sleep(0.01)
        post(win32con.WM_RBUTTONUP, win32con.MK_LBUTTON, pos)

    def dodge(self, tag):
        self.task.click(key='right')
        self.macro_mark(tag)

    def jump(self, tag, quiet=False):
        if quiet:
            self.tap_quiet(self.task.key_config.get('Jump Key'))
        else:
            self.task.jump(after_sleep=0)
        self.macro_mark(tag)

    def tap(self, tag):
        self.click()
        self.macro_mark(tag)

    def hold_down(self, tag):
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(tag)

    def wait_until_since(self, t0, sec):
        """等到 t0 之后 sec 秒(已经过了就不等)。"""
        self.macro_wait(max(0.0, t0 + sec - time.time()))

    def spam_e(self, char, window, tag):
        self.spam_key(char.send_resonance_key, window, interval=self.KEY_TAP, tag=tag, poke=False)
        char.record_resonance_use()

    def tap_keys_quiet(self, keys, window, tag):
        """按住左键期间轮流点几个键, 铺满 window。"""
        start = time.time()
        n = 0
        while True:
            self.tap_quiet(keys[n % len(keys)])
            n += 1
            if time.time() - start >= window:
                break
            self.macro_wait(self.KEY_TAP)
        self.macro_mark(f'{tag}(x{n})')

    def switch_holding(self, target, tag, time_out=None):
        """按住左键不松切人。数字键不激活窗口地发; 期间不点、不发任何别的键。"""
        time_out = self.SWITCH_TIME_OUT if time_out is None else time_out
        source = self.cur if self.cur is not None else self
        start = time.time()
        team_size = len([c for c in self.task.chars if c is not None])
        n = untrusted = 0
        while time.time() - start < time_out:
            self.tap_quiet(target.index + 1)
            n += 1
            self.task.next_frame()
            in_team, index, count = self.task.in_team()
            hit = in_team and index == target.index
            trusted = in_team and count >= team_size
            untrusted = untrusted + 1 if (hit and not trusted) else 0
            if hit and (trusted or untrusted >= self.UNTRUSTED_CONFIRM):
                self.handoff(source, target, False)
                self.macro_mark(f'{tag}({time.time() - start:.2f},x{n})')
                return self.note_switch(True, target.name)
        self.logger.warning(f'switch_holding to {target.name} timed out after {time_out:.1f}s ({n} keys)')
        self.macro_mark(f'{tag}FAIL(x{n})')
        return self.note_switch(False, target.name)

    def click_until_forte(self, char, cap, settle, tag):
        """连点到重击指示亮(连续 2 帧)。返回亮了没有。"""
        start = time.time()
        n = streak = 0
        next_click = 0.0
        lit = False
        while time.time() - start < cap:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            if time.time() - start >= settle and self.task.in_team()[0]:
                try:
                    full = bool(char.is_mouse_forte_full())
                except Exception:
                    full = False
                streak = streak + 1 if full else 0
                if streak >= 2:
                    lit = True
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"亮" if lit else "CAP"},A x{n},{time.time() - start:.2f}s)')
        if not lit:
            self.logger.info(f'{tag}: heavy-ready indicator never lit in {cap:.2f}s ({n} clicks)')
        return lit

    def xn_seg1_lit(self):
        """绯雪回路条第一段(心念 >=100)亮没亮。HUD 在不在调用方管。"""
        try:
            x1, y1, x2, y2 = self.XN_SEG1_BOX
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, hcenter=True)
            crop = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.size == 0:
                return False
            crop = crop.astype(np.int16)
            b, r = crop[..., 0], crop[..., 2]
            return float(((b > 170) & (b - r > 50)).mean()) >= self.XN_SEG1_RATIO
        except Exception:
            return False

    def click_until_xn_seg1(self, cap, tag):
        """连点到心念第一段亮(连续 2 帧)。到上限也返回, 后面的 E 照按。"""
        start = time.time()
        n = streak = 0
        next_click = 0.0
        lit = False
        while time.time() - start < cap:
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.G06_CLICK
            self.task.next_frame()
            if self.task.in_team()[0]:
                streak = streak + 1 if self.xn_seg1_lit() else 0
                if streak >= 2:
                    lit = True
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"心念1段" if lit else "CAP"},A x{n},{time.time() - start:.2f}s)')
        if not lit:
            self.logger.info(f'{tag}: xinnian seg1 never lit in {cap:.2f}s ({n} clicks)')
        return lit

    def kendo_state(self):
        """回路条上三个圆牌: 返回 (深蓝实心几个, 看起来还在几个)。

        圆牌个数 = 寒意的百位(每 100 一个, 上限 3); 深蓝实心 = 这一格还有【武霜·居合】, 发白 = 只有寒意。
        带武霜的居合出手时深蓝的少一个, 所以 21/24/30 三刀只看深蓝的。
        深蓝只认饱和的(B>150、B-R>110、B-G>70): 2K 录屏亮牌 0.51~0.58、发白 0.00、刀身寒意亮头扫过 <=0.28。
        旧判据(B>150、B-R>80 占比 >=0.50)会把亮头读成亮牌 —— 2026-11-11 18:06 实机 24 格被它骗了,
        补的那刀没出就判出刀、紧接着按 E 把居合打断了。
        "还在"用宽判据, 亮头也会算进去, 只写日志不做判断。
        """
        lit = present = 0
        r = self.KENDO_MARK_R
        for x, y in self.KENDO_MARK_XY:
            box = self.task.box_of_screen_scaled(3840, 2160, x - r, y - r, x + r, y + r, hcenter=True)
            crop = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.size == 0:
                continue
            c = crop.astype(np.int16)
            b, g, red = c[..., 0], c[..., 1], c[..., 2]
            if float(((b > 150) & (b - red > 110) & (b - g > 70)).mean()) >= self.KENDO_LIT_RATIO:
                lit += 1
            if float(((b > 150) & (b - red > 80)).mean()) >= self.KENDO_MARK_PRESENT:
                present += 1
        return lit, present

    def kendo_marks(self):
        return self.kendo_state()[0]

    def iai_until_drop(self, cap, tag, see=None, blind=None):
        """连点到深蓝圆牌少一个 = 这一刀用掉了武霜·居合、真出了。返回 True = 看到出刀。

        - 闪避进架势后约 0.65s 才出刀(居合架势-顿帧 派生 30F + 输入延迟); 在那之前切走/按 E, 这一刀就没了。
        - 到场头 see 秒回路条还在切换动画, 只记最大值不判少。
        - 深蓝的变多(普攻命中涨寒意、冒出新圆牌)之后 KENDO_UP_SETTLE 秒内不认变少: 新圆牌出现时会闪暗 ~0.08s。
        - 一个深蓝的都没看到(武霜已经用完只剩发白的, 或者读不到): 不判, 连点到 blind 秒按时刻返回。
        """
        see = self.KENDO_SEE if see is None else see
        blind = self.KENDO_BLIND if blind is None else blind
        start = time.time()
        up_at = start
        n = streak = top = 0
        next_click = 0.0
        state = 'CAP'
        while True:
            now = time.time()
            el = now - start
            if el >= cap:
                break
            if not top and el >= max(see, blind):
                state = '按时'
                break
            if now >= next_click:
                self.click()
                n += 1
                self.iai_last_click = now
                next_click = now + self.KENDO_SPAM
            self.task.next_frame()
            if self.task.in_team()[0]:
                lit = self.kendo_state()[0]
                t = time.time()
                if t - start < see or lit > top:
                    if lit > top and t - start >= see:
                        up_at = t
                    top = max(top, lit)
                    streak = 0
                elif lit < top and t - up_at >= self.KENDO_UP_SETTLE:
                    streak += 1
                    if streak >= 2:
                        state = '出刀'
                        break
                else:
                    streak = 0
            self.macro_wait(0.02)
        self.iai_top = top
        self.macro_mark(f'{tag}({state},亮{top},A x{n},{time.time() - start:.2f}s)')
        if state == 'CAP':
            self.logger.info(f'{tag}: lit iai mark never dropped in {cap:.2f}s (lit {top}, {n} clicks)')
        return state == '出刀'

    def hold_heavy_until_clear(self, char, cap, blind, tag):
        """(调用时左键已经按着)按到重击指示熄灭 = 重击出手, 然后松开。

        先读到过亮才认熄灭; HEAVY_SEE_GRACE 秒一次都没读到亮 = 读不到(或者一进场就出手了), 盲按到 blind 秒。
        """
        start = time.time()
        seen = cleared = False
        while time.time() - start < cap:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                self.macro_wait(0.02)
                continue
            try:
                full = bool(char.is_mouse_forte_full())
            except Exception:
                full = False
            if full:
                seen = True
            elif seen:
                cleared = True
                break
            elif time.time() - start >= self.HEAVY_SEE_GRACE:
                break
            self.macro_wait(0.02)
        if not seen:
            self.wait_until_since(start, blind)
        self.release_attack()
        state = 'cleared' if cleared else ('blind' if not seen else 'CAP')
        self.macro_mark(f'{tag}({state},{time.time() - start:.2f}s)')
        return cleared

    def hy_execute(self, tag, wait=0.0, click_while_waiting=False, anim=None):
        """绯雪处决: 只认屏幕中央的 F 提示(can_break 是粘滞标记、f_break_full 是顿感条满, 都不能当判据)。

        anim: 给了就从按 F 起只等这么久就返回(后面接处决派生的普攻), 不给就等提示消失 + EXECUTE_ANIM。
        """
        if not self.USE_AUTO_F_BREAK:
            return False
        box = self.task.box_of_screen(0.2, 0.2, 0.75, 0.8, hcenter=True, vcenter=True)

        def prompt():
            return bool(self.task.find_one('f_break', box=box, target_height=720))

        seen = False
        t0 = time.time()
        while True:
            self.task.next_frame()
            if prompt() and not self.task.is_pick_f():
                seen = True
                break
            if time.time() - t0 >= wait:
                break
            if click_while_waiting:
                self.click()
            # 18 格提示一出就要按 F(20:36 实机 18 格比作者多 0.6s), 不点的时候轮询压到 0.02
            self.macro_wait(0.05 if click_while_waiting else 0.02)
        if not seen:
            self.macro_mark(f'{tag}_无提示({time.time() - t0:.2f}s)')
            return False
        start = time.time()
        self.task.send_key('f')
        self.task.can_break = False
        if anim is not None:
            self.wait_until_since(start, anim)
        else:
            while time.time() - start < 0.8:
                self.task.next_frame()
                if not prompt():
                    break
                self.macro_wait(0.02)
            self.macro_wait(self.EXECUTE_ANIM)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}_F({time.time() - start:.2f}s)')
        return True

    def cl_r_fenshen(self, target, tag, **sw_kwargs):
        """长离 R -> 演出里一路按住 -> 焚身以火清空离火 -> 切 target(不点)。sw_kwargs 透传给切人(循环里要读协奏的格子用)。"""
        if self.changli_lib_then_heavy(tag=tag) is not False or getattr(self, 'round_no', 1) == 1:
            self.macro_wait(self.CL_FENSHEN_TO_SWITCH)
        self.sw(target, click_during=False, **sw_kwargs)

    # ==================== 轴 ====================

    def part1(self, jx, hy):
        """01~11: 开局 -> 长离R -> 绯雪攒心念 -> 今汐R -> 绯雪重击拔刀 -> 龙喷 -> 变奏绯雪。"""
        # 01 长离 E
        self.spam_e(self, self.G01_CL_E, tag='01长离E')
        self.sw(jx, click_during=False)
        # 02 今汐 入场A2 A3
        self.macro_spam(self.G02_JX_A2A3, tag='02今汐A2A3')
        # 切人途中密点: 绯雪进场那一下要是出场技(复用 A2), 06 进场才接得上 A3
        self.sw(hy, dash_click=True, click=True)
        # 03 绯雪 入场A2 -> 狂按切长离(被长离的换人CD挡着)
        self.macro_spam(self.G03_HY_DWELL, self.G06_CLICK, tag='03绯雪入场A2')
        self.sw(self, click_during=False)
        # 04 长离 R 焚身以火
        self.cl_r_fenshen(jx, tag='04长离')
        # 05 今汐 A4 E2
        self.tap('05今汐A4')
        self.macro_wait(self.G05_A4_TO_E)
        self.spam_e(jx, self.G05_E_ONLY, tag='05今汐E2')
        if getattr(self, 'round_no', 1) > 1 and self.hud_gone_within(self.G05_E4_SEE, tag='05今汐E放成龙喷'):
            # 第二圈起今汐还在凌霄里(上一圈 36 的神霓飞芒管 10s, 39 又打了凌霄 A4), 这下 E 是惊龙破空。
            # 20:38 实机: 龙喷藏 HUD 2.9s, 切绯雪超时, 06~08 全按在今汐身上。等演完带变奏切(惊龙破空给同奏),
            # 绯雪变奏给 200 心念, 06 照常 A3/E/A3 补满
            jx.record_resonance_use()
            self.wait_hud_back(tag='05龙喷anim', time_out=self.E4_ANIM_TIME_OUT)
            self.note_switch(self.quick_switch(hy, read_con=True), hy.name)
        else:
            # 06 每局都是到场 0.7s 心念第一段才亮(先打出一刀 A2, A2 派生 0.60s 才接 A3); 作者到场第一帧就在涨。
            # 切人途中密点, 让进场那一下直接接上 03 的出场A2
            self.sw(hy, dash_click=True, click=True)
        # 06 绯雪 进场A3(连点到心念第一段亮) -> E -> A3
        self.click_until_xn_seg1(self.G06_SEG1_CAP, tag='06绯雪A3')
        self.press_res_until_cd(hy, self.G06_E_CAP, tag='06绯雪E')
        hy.record_resonance_use()
        self.click_until_forte(hy, self.G06_A3_CAP, self.G06_FORTE_SETTLE, tag='06绯雪A3心念')
        self.sw(jx, click_during=False)
        # 07 今汐 A1 A2 R -> 演出尾按住左键 -> E3 + Q -> 按着切绯雪
        self.macro_spam(self.G07_JX_A1A2, tag='07今汐A1A2')
        fired = self.fire_r(jx, '07今汐R', self.JX_R_WINDOW)
        if fired:
            self.macro_wait(self.G07_HOLD_AT)
        self.hold_down('07按住')
        if fired:
            self.wait_hud_back(tag='07今汐Ranim')
        self.tap_keys_quiet([jx.get_resonance_key(), jx.get_echo_key()], self.G07_E3Q_WINDOW, tag='07今汐E3Q')
        jx.record_resonance_use()
        jx.record_echo_use()
        self.switch_holding(hy, tag='>07按住切绯雪')
        # 08 绯雪 重击 -> Q -> R -> 演完 E -> 切长离
        arrive = time.time()
        self.hold_heavy_until_clear(hy, self.G08_HEAVY_CAP, self.G08_HEAVY_BLIND, tag='08绯雪重击')
        self.wait_until_since(arrive, self.G08_Q_AT)
        self.send_echo_now(hy, tag='08绯雪Q', window=self.KEY_TAP)
        self.wait_until_since(arrive, self.G08_R_AT)
        if self.fire_r(hy, '08绯雪R', self.HY_R_WINDOW, skip_if_dark=False):
            self.wait_hud_back(tag='08绯雪Ranim', time_out=self.HY_R_ANIM_TIME_OUT)
        self.spam_e(hy, self.G08_E_ONLY, tag='08绯雪E')
        self.sw_key(self, hy, hy.send_resonance_key, tag='>08切长离+E')
        # 09 长离 征(切入自动)
        self.macro_wait(self.G09_CL_DWELL)
        self.sw(jx, click_during=False)
        # 10 今汐 A3 -> 跳 -> A4 -> E4 龙喷 -> 变奏绯雪
        self.tap('10今汐A3')
        self.macro_wait(self.G10_A3_TO_JUMP)
        self.jump('10跳')
        self.macro_wait(self.G10_JUMP_TO_A4)
        self.tap('10今汐A4')
        self.macro_wait(self.G10_A4_TO_E4)
        if self.press_until_hud_gone(jx, self.JX_E4_CAP, tag='10今汐E4', poke=False):
            jx.record_resonance_use()
            self.wait_hud_back(tag='10龙喷anim', time_out=self.E4_ANIM_TIME_OUT)
            # 惊龙破空给同奏, 切人必带变奏, 不用等协奏读满
            self.note_switch(self.quick_switch(hy, read_con=True), hy.name)
        else:
            self.sw_intro(jx, hy, tag='10今汐协奏')
        # 11 绯雪 变奏 A2 A3
        self.macro_spam(self.G11_HY_INTRO_A2A3, tag='11绯雪变奏A2A3')
        self.sw(self, click_during=False)

    def part2(self, jx, hy):
        """12~29: 短格穿插 -> 满协奏变奏链 -> 第二发龙喷。"""
        # 12 长离 E
        self.spam_e(self, self.G12_CL_E, tag='12长离E')
        self.sw(jx, click_during=True)
        # 13 今汐 出场A2 -> A3(压到离场前才起手, 15 的 A4 接它的派生窗口) -> 马上切
        self.jx_a2_late_a3(jx, '13今汐')
        self.sw(hy, click_during=True)
        # 14 绯雪 A4 A5
        self.macro_spam(self.G14_HY_A4A5, tag='14绯雪A4A5')
        self.sw(jx, click_during=True)
        # 15 今汐 A4: 切人途中就开始点, 到场再连点一小段, 保证第四段出手
        self.macro_spam(self.G15_JX_A4, self.G15_CLICK, tag='15今汐A4')
        self.sw(self, click_during=False)
        # 16 长离 跳 -> 按住(空中重击) -> 狂点切今汐(被今汐换人CD挡着)
        self.jump('16长离跳')
        self.macro_wait(self.G16_JUMP_TO_HOLD)
        self.hold_down('16按住')
        self.macro_wait(self.G16_HOLD)
        self.release_attack()
        self.sw(jx, click_during=True)
        # 17 今汐 E2: 按到 E 位不再是神霓飞芒(带变奏进场时 QTE 前 1.0s 的 E 会被吞)
        self.jx_e2_until_used(jx, self.G17_E2_CAP, tag='17今汐E2')
        self.g17_e2_at = time.time()   # 凌霄 10s 从这里起算, 28 的凌霄A4 必须在这之内
        self.sw(hy, click_during=False)
        # 18 绯雪 [处决] A3: A3 是处决之后那一下(谐度破坏后短按普攻 = 预求身第3段, 命中涨寒意,
        # 21 格到场才有 3 个圆牌)。等提示时不点 —— 先点的那下会被 F 吃掉
        if self.hy_execute('18绯雪处决', wait=self.G18_F_WAIT, anim=self.G18_A3_AT):
            self.macro_spam(self.G18_A3_SPAM, self.G18_A3_CLICK, tag='18绯雪处决后A3')
            self.macro_wait(self.G18_A3_TAIL)
        else:
            self.tap('18绯雪A3')
            self.macro_wait(self.G18_HY_A3)
        self.sw(self, click_during=True)
        # 19 长离 出场A3 A4
        self.macro_spam(self.G19_CL_A3A4, tag='19长离A3A4')
        self.sw(jx, click_during=False)
        # 20 今汐 A1 E3
        self.tap('20今汐A1')
        self.macro_wait(self.G20_A1_TO_E3)
        if self.press_res_until_cd(jx, self.G20_E3_CAP, tag='20今汐E3', ready_grace=0.15):
            self.macro_wait(self.G20_E3_HIT)
        jx.record_resonance_use()
        self.sw(hy, click_during=False)
        # 21 绯雪 闪避 -> 居合
        self.dodge('21绯雪闪避')
        self.macro_wait(self.DODGE_TO_KENDO)
        if self.iai_until_drop(self.IAI_CAP, tag='21绯雪居合'):
            # 出刀后多站一小会儿再切, 24 那刀连续居合才接得上(见 G21_STAY_AFTER_CLICK)
            self.wait_until_since(getattr(self, 'iai_last_click', 0.0), self.G21_STAY_AFTER_CLICK)
        self.sw(self, click_during=False)
        # 22 长离 征
        self.tap('22长离征')
        self.macro_wait(self.G22_CL_ZHENG)
        self.sw(jx, click_during=False)
        # 23 今汐 A2
        self.tap('23今汐A2')
        self.macro_wait(self.G23_JX_A2)
        self.sw(hy, click_during=True)
        self.g23_jx_left = getattr(hy, 'last_switch_in_time', 0.0) or time.time()   # 今汐换人冷却从这里起算(25 用)
        # 24 绯雪 居合 E
        # 这一刀是接 21 那刀的连续居合: 作者到场 0.13~0.2s 就扣(不用闪避)。接不上就闪避重进架势补这一刀 ——
        # 少这一刀, 30 格那刀就没有武霜了(21/24/30 三刀各用一点武霜, 攒满 3 层淬寒)
        if not (self.G24_TRY_CONT and self.iai_until_drop(self.G24_CONT_CAP, tag='24绯雪连续居合', see=self.G24_SEE)):
            self.dodge('24绯雪闪避')
            self.macro_wait(self.DODGE_TO_KENDO)
            self.iai_until_drop(self.IAI_CAP, tag='24绯雪补居合')
        self.spam_e(hy, self.G24_HY_E, tag='24绯雪E')
        self.sw_key(self, hy, hy.send_resonance_key, tag='>24切长离+E')
        # 25 长离 E -> E 打中(协奏满)那一帧就变奏今汐: 早了今汐是普通进场、变奏链断; 晚了长离已经离地,
        # 今汐在半空接变奏, 后面全队悬在空中(见 G25_CD_COVERS 上面)
        t25 = getattr(self, 'last_switch_in_time', 0.0) or time.time()
        self.spam_e(self, self.G25_CL_E, tag='25长离E')
        cd_left = getattr(self, 'g23_jx_left', 0.0) + self.G25_JX_CD - t25
        if cd_left > self.G25_CD_COVERS:
            self.macro_mark(f'25今汐冷却{cd_left:.2f}_狂按')
            self.sw_key(jx, self, self.send_resonance_key, tag='>25变奏今汐+E')
        else:
            self.macro_mark(f'25今汐冷却{cd_left:.2f}_盯协奏')
            cl_full = self.cl_wait_e_hit(t25, self.G25_HIT_CAP, tag='25长离协奏')
            self.sw(jx, click_during=False, assume_con=cl_full)
        self.macro_mark(f'25长离在场({time.time() - t25:.2f}s)')
        # 26 今汐: 到场连点一小段 —— 变奏进场时全被 QTE 吞掉(无害), 普通进场时是地面凌霄 A3 —— 然后不点等协奏:
        # 满了就按切绯雪(变奏进场要到 QTE 第78F 才放人, 这期间不点, 见 G26_A3_SPAM 上面); 不满才接着点 A4 补
        self.macro_spam(self.G26_A3_SPAM, self.G26_CLICK, tag='26今汐凌霄A3')
        if not self.wait_con_at_least(jx, self.CON_FULL_LEVEL, tag='26今汐协奏', cap=self.G26_CON_WAIT, poke=False):
            self.wait_con_at_least(jx, self.CON_FULL_LEVEL, tag='26今汐点A4补协奏', cap=self.G26_A4_CON_CAP, poke=True)
        ok = self.quick_switch(hy, read_con=True)
        self.g26_a4_at = time.time()
        if getattr(self, 'g17_e2_at', None):
            # 扣掉演出时停的近似游戏秒; 超过 10 就是凌霄已经没了, A3/A4 打成普攻
            self.macro_mark(f'26距17E2({self.time_elapsed_accounting_for_freeze(self.g17_e2_at):.2f}s)')
        self.note_switch(ok, hy.name)
        # 27 绯雪 变奏 A2 A3 -> 协奏一满就变奏今汐
        self.macro_spam(self.G27_HY_INTRO_A2A3, tag='27绯雪变奏A2A3')
        self.note_switch(self.pass_intro(hy, jx, self.G27_CON_CAP, tag='27绯雪协奏', poke=True), jx.name)
        # 28 今汐 (凌霄A3) A4 -> E4 龙喷 -> 切长离。第四段在 23 离场 / 26 / 这里哪一格出的每局不一样, 一律读 E 位:
        # 没冷却 = 第四段已经出了, 直接 E4; 还挂着逐天取月冷却 = 没出, 点到"冷却 -> 没冷却"再 E4
        if getattr(jx, 'has_intro', False):
            self.macro_wait(self.G28_INTRO_IDLE)
        settle = getattr(jx, 'last_switch_in_time', 0.0) + self.JX_ICON_SETTLE - time.time()
        if settle > 0:
            self.macro_wait(settle)   # 刚切进来技能栏可能还是绯雪的
        ready = None
        for _ in range(3):
            self.task.next_frame()
            ready = self.jx_e_ready(jx)
            if ready is not None:
                break
            self.macro_wait(0.03)
        e_txt = '亮' if ready else ('冷却' if ready is False else '?')
        self.macro_mark(f'28距26离场({self.time_elapsed_accounting_for_freeze(self.g26_a4_at):.2f}s,E{e_txt})')
        if ready is False:
            self.jx_a4_until_jinglong(jx, self.G28_A4_CAP, self.G28_A4_CAP, tag='28今汐凌霄A4')
        elif ready is None:
            self.macro_spam(self.G28_A4_SPAM, self.G28_CLICK, tag='28今汐凌霄A4')
        if getattr(self, 'g17_e2_at', None):
            self.macro_mark(f'28距17E2({self.time_elapsed_accounting_for_freeze(self.g17_e2_at):.2f}s)')
        if self.press_until_hud_gone(jx, self.JX_E4_CAP, tag='28今汐E4', poke=False):
            jx.record_resonance_use()
            self.wait_hud_back(tag='28龙喷anim', time_out=self.E4_ANIM_TIME_OUT)
        self.sw(self, click_during=False, read_con=True)
        # 29 长离 征
        # 正常是普通切入(作者视频里长离落在地上); 27 协奏没满时 28 今汐没变奏、离场满协奏, 长离就带着变奏进来
        self.g29_intro = bool(getattr(self, 'has_intro', False))
        if self.g29_intro:
            # 长离 QTE / QTE-空中A4 全程在空中(帧表位置状态=空中), 这时切绯雪, 绯雪就接在空中 ——
            # 01:37 实机 30 格绯雪先空中下落、再半空闪避居合。改成长离自己先落地: QTE 不响应输入那段不点,
            # 然后长按 = 空中重击-下落(落地派生 17F), 落地再切, 30 格就不用绯雪下落了
            self.macro_wait(self.G29_INTRO_IDLE)
            self.hold_down('29长离下落按住')
            self.macro_wait(self.G29_PLUNGE_HOLD)
            self.release_attack()
            self.macro_wait(self.G29_PLUNGE_TO_SW)
            self.macro_mark('29长离落地')
            self.g29_intro = False
        else:
            self.macro_spam(self.G29_CL_ZHENG, tag='29长离征')
        self.sw(hy, click_during=False)

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

    def jx_shenni_state(self, jx):
        """今汐 E 位: 'shenni' / 'other'(流光夕影、逐天取月或冷却数字) / '?' / None(HUD 不在或今汐不在场)。

        不看惊龙破空模板: 这条轴里 E 位外圈的金光、冷却数字都会让它读到 0.92~0.98, 把神霓飞芒的领先幅度吃掉
        (20:12 实机 36 格到场 1.5s 就是神霓飞芒, jx_e_icon 一直给 ?/jinglong, 撞了 2.2s 上限)。
        阈值是 20:12 录屏 2K/1080 量的: 神霓飞芒 0.84~0.91(金光闪的那一两帧掉到 0.74);
        别的图标时 shenni <= 0.73, 别的模板 0.83~0.99。
        """
        try:
            in_team, index, _count = self.task.in_team()
            if not in_team or index != jx.index:
                return None
            if getattr(self, '_jx_glyph_vecs', None) is None:
                self.jx_e_icon(jx)
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
            for lab, t in self._jx_glyph_vecs:
                score[lab] = max(score.get(lab, -1.0), float(v @ t))
            sh = score.get('shenni', -1.0)
            other = max(score.get(k, -1.0) for k in ('liuguang', 'zhutian', 'cd'))
            if sh >= self.JX_SHENNI_MIN and sh - other >= self.JX_SHENNI_MARGIN:
                return 'shenni'
            if other >= self.JX_OTHER_MIN and other - sh >= self.JX_SHENNI_MARGIN:
                return 'other'
            return '?'
        except Exception as e:
            self.logger.info(f'jx_shenni_state probe failed ({e})')
            return '?'

    def jx_e_ready(self, jx):
        """今汐 E 位有没有冷却数字: True = 没有(能按), False = 在冷却, None = HUD 不在 / 今汐不在场。

        读框架的冷却数字(has_cd), 不用惊龙破空字形模板: 11-12 02:44 实机模板把逐天取月转冷却(6.1/2.9)
        的图标认成惊龙破空, 26 没打 A4 就停了连点、28 也没补 A4, 按 E 只按在冷却上(`28今汐E4_NOHUDGONE`)。
        26~28 之间 E 只有两种样子: 逐天取月(20 格按的, 10s 冷却, 26/28 时还剩 6s/2~3s) 或 惊龙破空(没冷却)。
        """
        try:
            in_team, index, _count = self.task.in_team()
            if not in_team or index != jx.index:
                return None
            return not self.task.has_cd('resonance')
        except Exception as e:
            self.logger.info(f'jx_e_ready probe failed ({e})')
            return None

    def jx_a4_until_jinglong(self, jx, spam, cap, tag):
        """点凌霄 A4(spam 秒), 同时盯 E 位: 先见冷却数字、再连两帧没冷却 = A4 出手、E 变惊龙破空。

        39 格用: HUD 回来时逐天取月冷却可能刚好走完, 只看"没冷却"会把逐天取月当成惊龙破空(按出去就是 E3);
        03:07 录屏 A4 出手那下 E 位闪一下 9.9、0.1s 后变龙头, 这道"先冷却后没冷却"才是 A4 出手的信号。
        逐天取月还在冷却的局, 冷却数字一直在, 变龙头那下照样是"冷却 -> 没冷却"。
        cap(从开始点算)内没认到返回 False, 调用方不按 E。
        """
        start = time.time()
        n = hits = 0
        seen_cd = False
        next_tap = start
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if now - start < spam and now >= next_tap:
                self.click()
                n += 1
                next_tap = now + self.G28_CLICK
            self.task.next_frame()
            ready = self.jx_e_ready(jx)
            if len(trace) < 30:
                trace.append(f'{time.time() - start:.2f}:{"-" if ready is None else int(ready)}')
            if ready is False:
                seen_cd = True
                hits = 0
            elif ready and seen_cd:
                hits += 1
                if hits >= 2:
                    self.macro_mark(f'{tag}(惊龙,A x{n},{time.time() - start:.2f}s)')
                    return True
            else:
                hits = 0
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}(没认到惊龙{"" if seen_cd else ",没见冷却"},A x{n})')
        self.logger.info(f'{tag}: E 位没见"冷却->没冷却" — {" ".join(trace)}')
        return False

    def cl_wait_e_hit(self, t0, cap, tag):
        """长离 E 之后逐帧盯自己的协奏环, 读满那一帧就返回 True(调用方立刻发切人键); 到 t0+cap 还没满返回 False。
        每圈都取新帧、圈里不睡: 晚一两帧长离就离地了。"""
        n = 0
        con = 0.0
        while time.time() - t0 < cap:
            self.task.next_frame()
            try:
                con = float(self.get_current_con())
            except Exception:
                con = 0.0
            n += 1
            if con >= self.CON_FULL_LEVEL:
                self.macro_mark(f'{tag}(满,{time.time() - t0:.2f}s,x{n})')
                return True
        self.macro_mark(f'{tag}({con:.2f},CAP,x{n})')
        return False

    def jx_a2_late_a3(self, jx, tag):
        """今汐 出场A2 -> 停手 -> 离场前才点出 A3; 调用方接着马上切人(见 G13_A2_SPAM 上面)。
        今汐没有输入缓存, 派生帧之后的第一下点击当场起手, 所以 A3 什么时候起由这里的时刻说了算。"""
        arrive = getattr(jx, 'last_switch_in_time', 0.0) or time.time()
        self.macro_spam(self.G13_A2_SPAM, tag=f'{tag}A2')
        self.wait_until_since(arrive, self.G13_A3_FROM)
        self.macro_spam(self.G13_A3_SPAM, self.G13_A3_CLICK, tag=f'{tag}A3')

    def jx_click_until_shenni(self, jx, cap, tag, look_first=False):
        """点普攻到今汐 E 位认成神霓飞芒(连两帧)才返回: 第四段普攻真打出去了, 这时按 E 才是神霓飞芒。

        18:53 / 19:23 / 19:24 实机: 到场点 A4 后 0.28s 就按 E, 图标还是流光夕影, 按出普通 E(进 3s CD), 逐天取月按不出。
        上限到了返回 False, 调用方不按 E、不起跳。认图用 jx_shenni_state(不看惊龙破空模板)。
        look_first: 到场可能已经是神霓飞芒(前面第四段给的 5s 窗口还在), 先认一眼再点; 认到神霓飞芒那一帧不点,
        不然多点出的出场技挡在 E2 前面。
        """
        start = time.time()
        read_from = max(start, getattr(jx, 'last_switch_in_time', 0.0) + self.JX_ICON_SETTLE)
        hits = taps = 0
        next_tap = read_from + 0.05 if look_first else 0.0
        lab = None
        trace = []
        while time.time() - start < cap:
            if time.time() >= next_tap and lab != 'shenni':
                self.click()
                taps += 1
                next_tap = time.time() + self.G36_CLICK
            self.task.next_frame()
            if time.time() < read_from:
                continue
            lab = self.jx_shenni_state(jx)
            if len(trace) < 25:
                trace.append(f'{time.time() - start:.2f}:{lab or "-"}')
            hits = hits + 1 if lab == 'shenni' else 0
            if hits >= 2:
                self.macro_mark(f'{tag}(神霓飞芒,A x{taps},{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}(CAP,A x{taps})')
        self.logger.info(f'{tag}: E 位没认出神霓飞芒 — {" ".join(trace)}')
        return False

    def jx_e2_until_used(self, jx, cap, tag):
        """一直按 E, 直到今汐 E 位不再是神霓飞芒(连两帧), 也就是 E2 真放出去了。

        头 G17_JX_E2 秒照老办法连按(正常进场第一下就出 E2); 之后只在读到神霓飞芒时才接着按 ——
        E2 之后逐天取月只锁 ~0.2s, 多按会把 20 格要用的逐天取月放掉。
        收尾标记: 等到E2 = 读到过神霓飞芒、按到它变了; 到场已变 = 第一下就出了(或者本来就不是神霓飞芒)。
        """
        start = time.time()
        read_from = max(start, getattr(jx, 'last_switch_in_time', 0.0) + self.JX_ICON_SETTLE)
        presses = other = 0
        seen_shenni = False
        last_shenni = False
        next_press = 0.0
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if (now - start < self.G17_JX_E2 or last_shenni) and now >= next_press:
                jx.send_resonance_key()
                presses += 1
                next_press = now + self.KEY_TAP
            self.task.next_frame()
            if time.time() < read_from:
                continue
            lab = self.jx_shenni_state(jx)
            if len(trace) < 25:
                trace.append(f'{time.time() - start:.2f}:{lab or "-"}')
            if lab == 'shenni':
                seen_shenni = last_shenni = True
                other = 0
            elif lab == 'other':
                last_shenni = False
                other += 1
                if other >= 2:
                    self.macro_mark(f'{tag}({"等到E2" if seen_shenni else "到场已变"},x{presses},'
                                    f'{time.time() - start:.2f}s)')
                    jx.record_resonance_use()
                    return True
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}(CAP,x{presses})')
        self.logger.info(f'{tag}: E 位一直是神霓飞芒/认不出 — {" ".join(trace)}')
        jx.record_resonance_use()
        return False

    def part3(self, jx, hy):
        """30~40: 绯雪居合带重击 -> 长离第二发R -> 三居合枯霜 -> 今汐第二发R -> 归刃。"""
        # 30 绯雪 [下落落地] 闪避 -> 居合 -> 按住 -> 跳 -> E -> 按着切长离
        if self.G30_LAND_FIRST and getattr(self, 'g29_intro', False):
            # 长离 QTE / QTE-空中A4 都在空中, 绯雪接在空中位置上: 空中闪避后连点出的是空中普攻/空中居合
            # (18:06 实机这一刀直接没出)。长按普攻 = 空中下落攻击, 落地再闪避
            self.hold_down('30下落按住')
            self.macro_wait(self.G30_PLUNGE_HOLD)
            self.release_attack()
            self.macro_wait(self.G30_PLUNGE_TO_DODGE)
        self.dodge('30绯雪闪避')
        self.macro_wait(self.DODGE_TO_KENDO)
        self.iai_until_drop(self.IAI_CAP, tag='30绯雪居合')
        # 不按住(见 G30_KENDO_TO_JUMP 上面的注释)
        self.macro_wait(self.G30_KENDO_TO_JUMP)
        self.jump('30跳')
        self.macro_wait(self.G30_JUMP_TO_E)
        self.tap_keys_quiet([hy.get_resonance_key()], self.G30_E_WINDOW, tag='30绯雪E')
        hy.record_resonance_use()
        self.sw(self, click_during=False)
        # 31 长离 到场按住 -> 重击
        self.hold_down('31长离按住')
        self.macro_wait(self.G31_CL_HOLD)
        self.release_attack()
        self.macro_mark('31长离重击松开')
        self.sw(jx, dash_click=True)
        # 32 今汐 出场A2 -> A3(压到离场前才起手, 36 的第四段接它的派生窗口) -> 马上切
        self.jx_a2_late_a3(jx, '32今汐')
        self.sw(hy, click_during=True)
        # 33 绯雪 A2
        self.macro_spam(self.G33_HY_A2, tag='33绯雪A2')
        self.sw(self, click_during=False)
        # 34 长离 R 焚身以火
        self.cl_r_fenshen(hy, tag='34长离')
        # 35 绯雪 A3
        self.tap('35绯雪A3')
        self.macro_wait(self.G35_HY_A3)
        # 作者 36 到场 0.05s E 位就是神霓飞芒: 到场那下点击接上了 32 那下 A3 的派生窗口, 直接出第四段。
        # 我们以前到场时窗口刚过(A3 起手到这里 1.62s), 每局都从出场技重打到第四段(~1.6s); 现在 32 把 A3 压晚了,
        # 这里到场第一下就要点上, 所以切人途中也点、到场不先认图
        self.sw(jx, click_during=True)
        # 36 今汐 A4: 一直点到 E 位变成神霓飞芒 -> E2 -> 跳 -> 空中A1 A2 -> E3(逐天取月是地面招, 带她落地)
        # 绯雪必须地面进场: 37 的三刀居合和枯霜在空中就是空中版
        self.g36_lingxiao = False
        if self.jx_click_until_shenni(jx, self.G36_SHENNI_CAP, tag='36今汐A4', look_first=False):
            self.g36_lingxiao = True   # E2 进凌霄: 39 的 A4 是凌霄第四段, 出手给游龙廻光
            e2_at = time.time()
            self.spam_e(jx, self.G36_E2, tag='36今汐E2')
            self.wait_until_since(e2_at, self.G36_E2_TO_JUMP)
            self.jump('36跳')
            self.macro_wait(self.G36_JUMP_TO_A)
            self.macro_spam(self.G36_AIR_A, self.G36_AIR_CLICK, tag='36今汐空中A1A2')
            if self.press_res_until_cd(jx, self.G36_E3_CAP, tag='36今汐E3', ready_grace=0.15):
                jx.record_resonance_use()
            else:
                # 逐天取月没出, 今汐还在空中: 先下落落地再切
                self.hold_down('36今汐下落按住')
                self.macro_wait(self.G36_PLUNGE_HOLD)
                self.release_attack()
                self.macro_wait(self.G36_PLUNGE_TO_SW)
                self.macro_mark('36今汐落地')
        else:
            # 没等到神霓飞芒: 不按 E 也不跳(普通 E 起跳后没有逐天取月带她落地), 站在地上切绯雪
            self.macro_mark('36今汐没神霓_不跳直接切')
        self.sw(hy, click_during=False)
        # 37 绯雪 A -> 闪避 -> 居合x3(三个寒意圆牌全用掉) -> 第三刀出刀时按住 -> 跳 -> 闪 -> 枯霜
        # 圆牌这时都是发白的(没有武霜), 深蓝判据读不到, 只能按时刻: 闪避后 0.55 / 1.25 / 1.95s 出刀
        self.tap('37绯雪A')
        self.macro_wait(self.G37_A_TO_DODGE)
        dodge_at = time.time()
        self.dodge('37绯雪闪避')
        self.macro_wait(self.DODGE_TO_KENDO)
        last_out = dodge_at + self.KENDO1_DELAY + (self.G37_KENDO_COUNT - 1) * self.KENDO_DERIVE
        spam_until = last_out - self.KENDO_SPAM_LAST_GAP
        n = 0
        while True:
            self.click()
            n += 1
            nxt = time.time() + self.KENDO_SPAM
            if nxt > spam_until:
                break
            self.macro_wait(max(0.0, nxt - time.time()))
        self.macro_mark(f'37居合x{self.G37_KENDO_COUNT}(A x{n})')
        self.wait_until_since(last_out, self.G37_OUT_TO_HOLD)
        self.hold_down('37按住')
        self.macro_wait(self.G37_HEAVY_HOLD)
        self.release_attack()
        self.macro_mark('37枯霜松开')
        self.sw(self, click_during=False, time_out=self.G37_SWITCH_TIME_OUT)
        # 38 长离 E
        self.spam_e(self, self.G38_CL_E, tag='38长离E')
        self.sw(jx, click_during=True)
        # 39 今汐 A3 R -> 演完 Q A4
        self.macro_spam(self.G39_JX_A3, tag='39今汐A3')
        if self.fire_r(jx, '39今汐R', self.JX_R_WINDOW):
            self.wait_hud_back(tag='39今汐Ranim', min_dur=self.JX_R_ANIM_MIN)
        back = time.time()
        self.send_echo_now(jx, tag='39今汐Q', window=self.KEY_TAP)
        # 等今汐落地再点 A4、再切: 绯雪要在地面进场(归刃蓄力只有地面版)
        self.wait_until_since(back, self.G39_A4_AT)
        e4 = False
        if self.G39_E4 and getattr(self, 'g36_lingxiao', False):
            # A4 -> 认到惊龙破空 -> 按掉 -> 等演完再切(见 G39_E4 上面)。认不到就不按, 照作者直接切
            if self.jx_a4_until_jinglong(jx, self.G39_JX_A4, self.G39_E4_SEE, tag='39今汐A4'):
                if self.press_until_hud_gone(jx, self.JX_E4_CAP, tag='39今汐E4', poke=False):
                    e4 = True
                    jx.record_resonance_use()
                    self.wait_hud_back(tag='39龙喷anim', time_out=self.E4_ANIM_TIME_OUT)
        else:
            self.macro_spam(self.G39_JX_A4, tag='39今汐A4')
        if not e4:
            self.wait_until_since(back, self.G39_SWITCH_AT)
        self.sw(hy, click_during=False, read_con=True)
        # 40 绯雪 到场直接长按 R(蓄力中补 Q) -> 归刃。不闪避、不居合、不按 E(地面 E 会把人挑上天)
        # 39 按了惊龙破空就当变奏进场(同奏不看协奏, 读数可能说没满), 先等过 QTE
        if e4 or getattr(hy, 'has_intro', False):
            self.macro_wait(self.G40_INTRO_IDLE)
            self.macro_mark('40绯雪等QTE')
        self.hy_guiren(hy, '40')

    # ==================== 循环 1~3 用的小段 ====================

    def hy_guiren(self, hy, tag, hold=None):
        """绯雪归刃(二段大招): R 只按下一次、一路按住到演出开始(HUD 消失), 蓄力中补 Q, 演完(HUD 回来)才返回。
        人要在地面(蓄力只有地面版); 刚变奏进来的话调用方先等过 QTE(R 落在 QTE 里被吞就没有归刃)。"""
        key = hy.get_liberation_key()
        start = time.time()
        self.task.send_key_down(key)
        self.macro_mark(f'{tag}绯雪R按下')
        try:
            self.macro_wait(self.G40_Q_AT)
            self.tap_quiet(hy.get_echo_key())
            hy.record_echo_use()
            self.macro_mark(f'{tag}绯雪Q')
            gone = 0
            cap = self.G40_R2_HOLD if hold is None else hold
            while time.time() - start < cap:
                self.task.next_frame()
                if time.time() - start >= self.G40_R2_MIN:
                    gone = 0 if self.task.in_team()[0] else gone + 1
                    if gone >= self.LIB_GONE_CONFIRM:
                        break
                self.macro_wait(0.02)
        finally:
            self.task.send_key_up(key)
        self.macro_mark(f'{tag}绯雪R松开({time.time() - start:.2f}s)')
        hy.record_liberation_use()
        self.wait_hud_back(tag=f'{tag}归刃anim', time_out=self.R2_ANIM_TIME_OUT)
        self.add_freeze_duration(start, time.time() - start)

    def lp_qte_idle(self, char, sec):
        """char 是带变奏进来的, 就等到它到场后 sec 秒(QTE 不响应输入那段)。返回是不是变奏进场。"""
        if getattr(char, 'has_intro', False):
            self.wait_until_since(getattr(char, 'last_switch_in_time', 0.0) or time.time(), sec)
            return True
        return False

    def lp_intro(self, source, target, tag, cap=None, poke=False):
        """循环里写着"变奏X"的切人: 等 source 协奏读满(上限 cap) -> 切给 target, 满了按变奏记账。
        没满也照切(按普通进场记), 后面的格子都得两种进场都扛得住。"""
        cap = self.LP_CON_CAP if cap is None else cap
        return self.note_switch(self.pass_intro(source, target, cap, tag=tag, poke=poke), target.name)

    def lp_execute(self, tag, wait=None, anim=None):
        """处决: 屏幕中央有 F 提示才按(hy_execute 不分角色)。循环里提示出不出看当局的谐度, 没有就跳过。"""
        return self.hy_execute(tag, wait=self.LP_F_WAIT if wait is None else wait, anim=anim)

    # ---- 长离 ----

    def lp_cl_e(self, tag):
        self.spam_e(self, self.G12_CL_E, tag=tag)

    def lp_cl_zheng(self, tag):
        """心眼里地面点一下 = 心眼·征(+1 离火)。"""
        self.tap(tag)
        self.macro_wait(self.G22_CL_ZHENG)

    def lp_cl_jump_plunge(self, tag):
        """长离 跳(心眼里起跳就是心眼·冲, +1 离火) -> 按住 = 下落攻击。和启动轴 16 格同一段。
        下落之后再上场的普攻从第三段起(所以后面写的是"出场A3 A4")。"""
        self.jump(f'{tag}跳')
        self.macro_wait(self.G16_JUMP_TO_HOLD)
        self.hold_down(f'{tag}按住')
        self.macro_wait(self.G16_HOLD)
        self.release_attack()

    def lp_cl_intro_chong(self, tag):
        """长离变奏进场(QTE 在空中、给心眼) -> 点普攻 = 心眼·冲(+1 离火)。
        从到场一直点到 LP_CL_INTRO_SPAM: QTE 第46F前的点击被吞, 放开输入后第一下就是冲。
        协奏读数没说满也照这么点(同奏给的变奏读不出来); 真是普通进场的话这几下只是地面普攻。"""
        arrive = getattr(self, 'last_switch_in_time', 0.0) or time.time()
        left = arrive + self.LP_CL_INTRO_SPAM - time.time()
        if left > 0:
            self.macro_spam(left, self.LP_CL_INTRO_CLICK, tag=tag)

    def lp_cl_heavy(self, tag):
        """长离 强化重击(焚身以火): 按住到重击就绪图标熄灭。离火没满的话图标读不到亮, 盲按一小段就松。"""
        self.hold_down(f'{tag}按住')
        try:
            return self.hold_until_forte_cleared(tag)
        finally:
            self.release_attack()
            self.macro_mark(f'{tag}松开')

    # ---- 今汐 ----

    def lp_jx_tap(self, tag, stay=None):
        """今汐点一段普攻(凌霄里就是一段凌霄普攻), 留到它出手再走。今汐没有输入缓存, 一格只点一下 = 只出一段。"""
        self.tap(tag)
        self.macro_wait(self.LP_JX_ONE if stay is None else stay)

    def lp_jx_e3(self, jx, tag):
        """凌霄里的 E(逐天取月): 按到进冷却, 再留到第一段出手(协奏 +8 在这一下)。"""
        if self.press_res_until_cd(jx, self.G20_E3_CAP, tag=tag, ready_grace=0.15):
            self.macro_wait(self.G20_E3_HIT)
        jx.record_resonance_use()

    def lp_jx_e2(self, jx, tag):
        """今汐 神霓飞芒(E2)进凌霄: 只在 E 位认成神霓飞芒的时候按, 按到图标变掉。返回进没进凌霄。

        跟启动轴 17 格的 jx_e2_until_used 差在"不盲按": 循环里前面那下第四段 / 变奏没成的话 E 位是普通 E,
        盲按会把它放出去(3 秒冷却), 后面的逐天取月跟着错位。变奏进场时 QTE 里图标就是神霓飞芒, 一路按到 QTE 放开输入。
        """
        start = time.time()
        read_from = max(start, getattr(jx, 'last_switch_in_time', 0.0) + self.JX_ICON_SETTLE)
        see_cap = self.LP_E2_SEE_INTRO if getattr(jx, 'has_intro', False) else self.LP_E2_SEE
        presses = other = 0
        seen = False
        next_press = 0.0
        trace = []
        while time.time() - start < self.G17_E2_CAP:
            self.task.next_frame()
            now = time.time()
            if now < read_from:
                continue
            lab = self.jx_shenni_state(jx)
            if len(trace) < 25:
                trace.append(f'{now - start:.2f}:{lab or "-"}')
            if lab == 'shenni':
                seen = True
                other = 0
                if now >= next_press:
                    jx.send_resonance_key()
                    presses += 1
                    next_press = now + self.KEY_TAP
            elif lab == 'other':
                other += 1
                if seen and other >= 2:
                    self.macro_mark(f'{tag}(进凌霄,x{presses},{time.time() - start:.2f}s)')
                    jx.record_resonance_use()
                    return True
            if not seen and now - read_from >= see_cap:
                break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}({"按了没见图标变" if seen else "没神霓_不按"},x{presses})')
        self.logger.info(f'{tag}: E2 没确认放出去 — {" ".join(trace)}')
        if presses:
            jx.record_resonance_use()
        return False

    def lp_jx_a4_e2(self, jx, tag, cap=None):
        """今汐 常态普攻点到第四段(E 位认成神霓飞芒) -> E2 进凌霄。认不到就不按 E。"""
        if self.jx_click_until_shenni(jx, self.G36_SHENNI_CAP if cap is None else cap, tag=f'{tag}A4'):
            self.spam_e(jx, self.G36_E2, tag=f'{tag}E2')
            return True
        self.macro_mark(f'{tag}没神霓_不按E')
        return False

    def lp_jx_plunge(self, tag):
        """今汐人在空中又没有逐天取月带她落地: 按住下落、落地再走(同启动轴 36 格的兜底)。"""
        self.hold_down(f'{tag}下落按住')
        self.macro_wait(self.G36_PLUNGE_HOLD)
        self.release_attack()
        self.macro_wait(self.G36_PLUNGE_TO_SW)
        self.macro_mark(f'{tag}落地')

    def lp_jx_jump_a_e3(self, jx, tag):
        """凌霄里 跳 -> 空中点一段 -> E3 逐天取月(带她落地)。E3 没按出来就下落落地。返回 E3 出没出。"""
        self.jump(f'{tag}跳')
        self.macro_wait(self.G36_JUMP_TO_A)
        self.lp_jx_tap(f'{tag}空中凌霄A', stay=self.G20_A1_TO_E3)
        ok = self.press_res_until_cd(jx, self.G20_E3_CAP, tag=f'{tag}E3', ready_grace=0.15)
        if ok:
            self.macro_wait(self.G20_E3_HIT)
            jx.record_resonance_use()
        else:
            self.lp_jx_plunge(tag)
        return ok

    def lp_jx_jump_aa(self, tag, since, delay):
        """E2 进凌霄之后 跳 -> 空中凌霄 A1 A2(同启动轴 36 格那一段, 不带 E3)。since 起 delay 秒才跳(E2 第26F 地转空)。"""
        self.wait_until_since(since, delay)
        self.jump(f'{tag}跳')
        self.macro_wait(self.G36_JUMP_TO_A)
        self.macro_spam(self.G36_AIR_A, self.G36_AIR_CLICK, tag=f'{tag}空中A1A2')

    def lp_jx_longpen(self, jx, tag, a4=False):
        """今汐 惊龙破空(龙喷): 凌霄第四段没出就先点出来, 然后按到 HUD 消失、等演完。返回放出来没有。
        a4=True: 这一格本身写着"A4 E", 第四段还没打 —— E 位没冷却时读不出它出没出手, 先连点 LP_JX_LX_A4 再按。

        和启动轴 28 格同一套读法: E 位挂着冷却(逐天取月刚用过) = 第四段还没出, 点到"冷却 -> 没冷却"再按;
        没冷却就直接按。直接按没放出来(E 位其实是逐天取月, 这一下把它放了、进冷却) -> 再点一轮等"冷却 -> 没冷却", 认到才补按。
        文字轴的凌霄段数和实际出手数对不上的地方靠这里兜: 少点了它补, 点够了它不多点。
        """
        self.lp_qte_idle(jx, self.G28_INTRO_IDLE)
        self.wait_until_since(getattr(jx, 'last_switch_in_time', 0.0), self.JX_ICON_SETTLE)
        ready = None
        for _ in range(3):
            self.task.next_frame()
            ready = self.jx_e_ready(jx)
            if ready is not None:
                break
            self.macro_wait(0.03)
        self.macro_mark(f'{tag}E位{"亮" if ready else ("冷却" if ready is False else "?")}')
        if ready is False:
            # 冷却一直没变掉 = 第四段点不出来(不在凌霄里 / 段数差太多): E 在冷却上按了也是空的, 这发不放
            if not self.jx_a4_until_jinglong(jx, self.G28_A4_CAP, self.G28_A4_CAP, tag=f'{tag}凌霄A4'):
                return False
            fired = self.press_until_hud_gone(jx, self.JX_E4_CAP, tag=f'{tag}E4', poke=False)
        else:
            if a4:
                self.macro_spam(self.LP_JX_LX_A4, self.G28_CLICK, tag=f'{tag}凌霄A4')
            elif ready is None:
                self.macro_spam(self.G28_A4_SPAM, self.G28_CLICK, tag=f'{tag}凌霄A4')
            fired = self.press_until_hud_gone(jx, self.LP_E4_FIRST_CAP, tag=f'{tag}E4', poke=False)
            if not fired and self.jx_a4_until_jinglong(jx, self.G28_A4_CAP, self.G28_A4_CAP, tag=f'{tag}补凌霄A4'):
                fired = self.press_until_hud_gone(jx, self.LP_E4_RETRY_CAP, tag=f'{tag}补E4', poke=False)
        if fired:
            jx.record_resonance_use()
            self.wait_hud_back(tag=f'{tag}龙喷anim', time_out=self.E4_ANIM_TIME_OUT)
        return fired

    def lp_jx_r(self, jx, tag):
        """今汐 R(没转好就不按) -> 演完补 Q。返回放了没有。"""
        if self.fire_r(jx, f'{tag}R', self.JX_R_WINDOW):
            self.wait_hud_back(tag=f'{tag}Ranim', min_dur=self.JX_R_ANIM_MIN)
            self.send_echo_now(jx, tag=f'{tag}Q', window=self.KEY_TAP)
            return True
        return False

    # ---- 绯雪 ----

    def lp_hy_iai(self, tag, dodge=True):
        """绯雪 [闪避进架势 ->] 居合一刀。有武霜看深蓝圆牌少一个, 没武霜(只剩发白的)按时刻。
        闪避只按一下: 连按两下第二下会退出架势, 后面点的就成普攻了。
        刚带变奏进来(切人时读到前一个人协奏满)就先等过 QTE 再闪避, 否则闪避被 QTE 吞掉、架势没进去。"""
        if dodge:
            hy = self.cur
            arrive = getattr(hy, 'last_switch_in_time', 0.0) or 0.0
            if getattr(hy, 'has_intro', False) and time.time() - arrive < self.LP_HY_QTE_IDLE:
                self.macro_mark(f'{tag}变奏进场_等QTE')
                self.wait_until_since(arrive, self.LP_HY_QTE_IDLE)
            self.dodge(f'{tag}闪避')
            self.macro_wait(self.DODGE_TO_KENDO)
        return self.iai_until_drop(self.IAI_CAP, tag=f'{tag}居合')

    def lp_hy_iai_x2(self, tag):
        self.lp_hy_iai(f'{tag}1')
        self.macro_wait(self.LP_IAI_GAP)
        self.lp_hy_iai(f'{tag}2')

    def lp_hy_iai_cont(self, tag):
        """上场接上一刀的连续居合(不闪避), 接不上就闪避重进架势补。和启动轴 24 格同一段, 只用在还有武霜的时候。
        G24_TRY_CONT 关着(11-19 矩阵实机 L1-25 4/4、L2-19 1/1 都没接上)就直接闪避出刀。"""
        if not (self.G24_TRY_CONT and self.iai_until_drop(self.G24_CONT_CAP, tag=f'{tag}连续居合', see=self.G24_SEE)):
            self.lp_hy_iai(f'{tag}补', dodge=True)

    def lp_hy_kushuang(self, tag):
        """绯雪 强化重击(枯霜, 要 3 层淬寒): 居合出刀后就地按住过长按门槛再松。全局时停 150F, 期间不能切人。"""
        self.macro_wait(self.G37_OUT_TO_HOLD)
        self.hold_down(f'{tag}按住')
        self.macro_wait(self.G37_HEAVY_HOLD)
        self.release_attack()
        self.macro_mark(f'{tag}枯霜松开')

    def lp_hy_red_burst(self, hy, tag):
        """红绯(归刃后的常世身) 变奏进场 -> A3 凑满心念 -> 闪避 -> 重击射三箭 -> R 拔刀进蓝绯, 演完返回拔没拔。

        变奏 QTE 给 200 心念, 接着的普攻直接是第三段(+100)就满 300; 连点到重击指示亮为止(QTE 里的点击被吞, 无害)。
        没带变奏进来(少 200): 照启动轴 06 格 E -> 带强化的 A3 补满。
        重击出手后 R 才亮, 所以 R 不看图标亮不亮直接连按(和 08 格一样)。
        """
        if not self.click_until_forte(hy, self.LP_RED_A3_CAP, self.G06_FORTE_SETTLE, tag=f'{tag}A3心念'):
            self.press_res_until_cd(hy, self.G06_E_CAP, tag=f'{tag}补E')
            hy.record_resonance_use()
            self.click_until_forte(hy, self.G06_A3_CAP, self.G06_FORTE_SETTLE, tag=f'{tag}补A3心念')
        self.dodge(f'{tag}闪避')
        self.macro_wait(self.LP_RED_DODGE_TO_HOLD)
        hold_at = time.time()
        self.hold_down(f'{tag}按住')
        self.hold_heavy_until_clear(hy, self.G08_HEAVY_CAP, self.G08_HEAVY_BLIND, tag=f'{tag}重击')
        self.wait_until_since(hold_at, self.G08_R_AT)
        if self.fire_r(hy, f'{tag}R', self.HY_R_WINDOW, skip_if_dark=False):
            self.wait_hud_back(tag=f'{tag}Ranim', time_out=self.HY_R_ANIM_TIME_OUT)
            return True
        return False

    # ==================== 循环 1 / 2 / 3 ====================
    # 三段各不相同, 都以绯雪归刃收尾。格子号 L1-01 … 对应文字轴一行里的一个"谁 + 动作"。
    # 文字轴只有 循环1 的前 17 格有正常速度视频可对, 其余时长都是照启动轴里同样的动作抄的, 没实机。

    def loop1(self, jx, hy):
        """循环1(37 格): 龙喷 -> 红绯拔刀 -> 凌霄 -> 变奏龙喷 -> 长离四离火 -> 长离R -> 枯霜 -> 今汐R -> 龙喷 -> 归刃。"""
        # L1-01 绯雪(归刃后回到红绯) E + Q
        self.spam_e(hy, self.LP_HY_E, tag='L1-01绯雪E')
        self.send_echo_now(hy, tag='L1-01绯雪Q', window=self.KEY_TAP)
        self.sw(self)
        # L1-02 长离 跳(38 格 E 给的心眼 -> 冲) -> 下落
        self.lp_cl_jump_plunge('L1-02长离')
        self.sw(jx)
        # L1-03 今汐 龙喷(39 格凌霄第四段留下的游龙廻光) -> 变奏绯雪
        self.lp_jx_longpen(jx, 'L1-03今汐')
        self.sw(hy, read_con=True)
        # L1-04 绯雪(红) 变奏 A3 -> 闪避 -> 重击 -> R 拔刀 -> 蓝绯 A1 A2 A3 -> 协奏满变奏今汐
        self.lp_hy_red_burst(hy, 'L1-04绯雪')
        self.macro_spam(self.LP_HY_A123, tag='L1-04绯雪A123')
        self.lp_intro(hy, jx, tag='L1-04绯雪协奏', poke=True)
        # L1-05 今汐 变奏 -> E2 进凌霄
        self.lp_jx_e2(jx, 'L1-05今汐E2')
        self.sw(self)
        # L1-06 长离 E
        self.lp_cl_e('L1-06长离E')
        self.sw(hy, click_during=True)
        # L1-07 绯雪 A4 A5
        self.macro_spam(self.G14_HY_A4A5, tag='L1-07绯雪A4A5')
        self.sw(jx)
        # L1-08 今汐 凌霄 A1
        self.lp_jx_tap('L1-08今汐凌霄A1')
        self.sw(self)
        # L1-09 长离 跳(冲) -> 下落
        self.lp_cl_jump_plunge('L1-09长离')
        self.sw(hy)
        # L1-10 绯雪 E(升空)
        self.spam_e(hy, self.LP_HY_E, tag='L1-10绯雪E')
        self.sw(jx)
        # L1-11 今汐 凌霄 A2 -> 跳 -> 空中 A3(这里不按逐天取月, 用户 11-13 确认)
        self.lp_jx_tap('L1-11今汐凌霄A2')
        self.jump('L1-11今汐跳')
        self.macro_wait(self.G36_JUMP_TO_A)
        self.lp_jx_tap('L1-11今汐空中凌霄A3')
        self.sw(self, click_during=True)
        # L1-12 长离 出场A3 A4(进心眼)
        self.macro_spam(self.G19_CL_A3A4, tag='L1-12长离A3A4')
        self.sw(hy)
        # L1-13 绯雪(L1-10 的 E 升空后人在空中) 闪避 -> 居合 -> E(落地)
        self.lp_hy_iai('L1-13绯雪')
        self.spam_e(hy, self.G24_HY_E, tag='L1-13绯雪E')
        self.sw(jx)
        # L1-14 今汐 凌霄 A4(出手后 E 变惊龙破空, L1-16 才按)
        self.lp_jx_tap('L1-14今汐凌霄A4')
        self.sw(self)
        # L1-15 长离 征 -> 协奏满变奏今汐(没满就边点边等, 见 LP_CL_CON_CAP)
        self.lp_cl_zheng('L1-15长离征')
        self.lp_intro(self, jx, tag='L1-15长离协奏', cap=self.LP_CL_CON_CAP, poke=True)
        # L1-16 今汐 变奏 -> 龙喷 -> (同奏)变奏长离
        self.lp_jx_longpen(jx, 'L1-16今汐')
        self.sw(self, read_con=True)
        # L1-17 长离 变奏(空中、给心眼) -> 冲 = 第 4 层离火
        self.lp_cl_intro_chong('L1-17长离冲')
        # L1-18 长离 [处决] -> 预输入强化重击(焚身以火)
        self.lp_execute('L1-18长离处决', anim=self.LP_F_TO_HOLD)
        self.lp_cl_heavy('L1-18长离')
        self.sw(jx, click_during=True)
        # L1-19 今汐 出场A2 -> A3
        self.jx_a2_late_a3(jx, 'L1-19今汐')
        self.sw(hy, click_during=True)
        # L1-20 绯雪 A2 A3
        self.macro_spam(self.LP_HY_A23, tag='L1-20绯雪A2A3')
        self.sw(jx, click_during=True)
        # L1-21 今汐 A4(第四段, 给 5s 神霓飞芒)
        self.macro_spam(self.G15_JX_A4, self.G15_CLICK, tag='L1-21今汐A4')
        self.sw(self)
        # L1-22 长离 R -> 焚身以火。读协奏: 大招后长离协奏满了的话绯雪是变奏进场, L1-23 要先等过 QTE 再闪避
        self.cl_r_fenshen(hy, tag='L1-22长离', read_con=True)
        # L1-23 绯雪 闪避 -> 居合
        self.lp_hy_iai('L1-23绯雪')
        self.sw(jx)
        # L1-24 今汐 E2 进凌霄 -> 跳 -> 空中 A1 A2
        if self.lp_jx_e2(jx, 'L1-24今汐E2'):
            self.lp_jx_jump_aa('L1-24今汐', time.time(), self.LP_JX_E2_SEEN_TO_JUMP)
        self.sw(hy)
        # L1-25 绯雪 居合(L1-13 / 23 / 25 三刀攒满淬寒) -> 枯霜 -> A1 A2 A3
        self.lp_hy_iai_cont('L1-25绯雪')
        self.lp_hy_kushuang('L1-25绯雪')
        self.macro_spam(self.LP_KUSHUANG_ANIM + self.LP_HY_A123, tag='L1-25绯雪A123')
        self.sw(self)
        # L1-26 长离 E
        self.lp_cl_e('L1-26长离E')
        self.sw(jx)
        # L1-27 今汐 凌霄 A3 -> R -> E3 逐天取月 -> 协奏满变奏绯雪
        self.lp_jx_tap('L1-27今汐凌霄A3')
        r_ok = self.lp_jx_r(jx, 'L1-27今汐')
        self.lp_jx_e3(jx, 'L1-27今汐E3')
        # R 没转好(11-19 矩阵实机 4/4: 冷却好了、能量差一截, 图标外圈只到 ~80%)就没有大招那 3.4s 时停,
        # L1-24 进的凌霄撑不到 L1-30(114830 局 L1-30 到场凌霄已经没了, E 是普通 E)。
        # 这时把凌霄第四段挪到这里打: 出手 E 就变惊龙破空(不带逐天取月的冷却), 游龙廻光 5s 够撑到 L1-30
        self.l127_a4 = False
        if not r_ok:
            self.l127_a4 = self.jx_a4_until_jinglong(jx, self.G28_A4_CAP, self.G28_A4_CAP,
                                                     tag='L1-27今汐R没放_凌霄A4提前')
        self.lp_intro(jx, hy, tag='L1-27今汐协奏')
        # L1-28 绯雪 变奏 A2 A3
        self.macro_spam(self.G11_HY_INTRO_A2A3, tag='L1-28绯雪变奏A2A3')
        self.sw(self)
        # L1-29 长离 跳(冲) -> 下落(不按 E)
        self.lp_cl_jump_plunge('L1-29长离')
        self.sw(jx)
        # L1-30 今汐 凌霄 A4 -> 龙喷(L1-27 已经把第四段打了就直接按)
        self.lp_jx_longpen(jx, 'L1-30今汐', a4=not getattr(self, 'l127_a4', False))
        self.sw(hy, read_con=True)
        # L1-31 绯雪 A4 A5
        self.macro_spam(self.G14_HY_A4A5, tag='L1-31绯雪A4A5')
        self.sw(self, click_during=True)
        # L1-32 长离 出场A3 A4(进心眼; 文字轴把 A4 写在 L1-36, 这里一次打完, 36 只点征)
        self.macro_spam(self.G19_CL_A3A4, tag='L1-32长离A3A4')
        self.sw(jx, click_during=True)
        # L1-33 今汐 出场A2 -> A3
        self.jx_a2_late_a3(jx, 'L1-33今汐')
        self.sw(hy)
        # L1-34 绯雪 闪避居合 x2(武霜在 L1-25 用完了, 按时刻)
        self.lp_hy_iai_x2('L1-34绯雪')
        self.sw(jx, click_during=True)
        # L1-35 今汐 A4 -> E2 进凌霄
        self.lp_jx_a4_e2(jx, 'L1-35今汐')
        self.sw(self)
        # L1-36 长离 征
        self.lp_cl_zheng('L1-36长离征')
        self.sw(hy, click_during=True)
        # L1-37 绯雪 A2 A3 -> E(升空) -> 处决 -> A3 -> 闪避居合 -> 归刃
        # 没有处决提示: 人还在空中, 再按一下 E 砸下来落地(归刃蓄力只有地面版)
        self.macro_spam(self.LP_HY_A23, tag='L1-37绯雪A2A3')
        self.spam_e(hy, self.LP_HY_E, tag='L1-37绯雪E')
        if self.lp_execute('L1-37绯雪处决', anim=self.G18_A3_AT):
            self.macro_spam(self.G18_A3_SPAM, self.G18_A3_CLICK, tag='L1-37绯雪处决后A3')
        else:
            self.spam_e(hy, self.LP_HY_E, tag='L1-37绯雪E下砸')
            self.macro_wait(self.LP_HY_E2_LAND)
        self.lp_hy_iai('L1-37绯雪')
        self.macro_wait(self.LP_IAI_TO_R)
        self.hy_guiren(hy, 'L1-37')

    def loop2(self, jx, hy):
        """循环2(19 格): 变奏长离 -> 红绯拔刀 -> 长离双焚身 -> 龙喷接凌霄 -> 龙喷 + 今汐R -> 双居合 -> 枯霜 -> 归刃。"""
        # L2-01 红绯先放一个 E 把协奏补满(不然这次变奏不出来、长离下一次延奏协奏也不满)
        #       -> 变奏长离 -> 冲 -> E 打中、协奏满 -> 变奏绯雪
        self.spam_e(hy, self.LP_HY_E, tag='L2-01绯雪E')
        self.lp_intro(hy, self, tag='L2-01绯雪协奏', cap=self.LP_RED_E_CON_CAP)
        self.lp_cl_intro_chong('L2-01长离冲')
        self.macro_wait(self.LP_CL_CHONG_TO_E)
        e_at = time.time()
        self.lp_cl_e('L2-01长离E')
        full = self.cl_wait_e_hit(e_at, self.LP_CL_E_CON_CAP, tag='L2-01长离协奏')
        if not full:
            # 没满绯雪就是普通进场, 红绯 E 刚在上面用掉(冷却 ~20s), L2-02 心念凑不满 300、拔不了刀,
            # 循环2 后面全散(11-19 矩阵实机 3/4 局 `L2-02绯雪R_NOFIRE`)。多点几下补
            full = self.wait_con_at_least(self, self.CON_FULL_LEVEL, tag='L2-01长离补协奏',
                                          cap=self.LP_CL_E_CON_POKE, poke=True)
        self.sw(hy, assume_con=full)
        # L2-02 绯雪(红) 变奏 A3 -> 闪避 -> 重击 -> R 拔刀 -> E
        self.lp_hy_red_burst(hy, 'L2-02绯雪')
        self.spam_e(hy, self.G08_E_ONLY, tag='L2-02绯雪E')
        self.sw_key(self, hy, hy.send_resonance_key, tag='>L2-02切长离+E')
        # L2-03 长离 征(L2-01 的 E 给的心眼) = 第 4 层离火
        self.lp_cl_zheng('L2-03长离征')
        self.sw(jx)
        # L2-04 今汐 凌霄 A1 A2(L1-35 进的凌霄) -> 跳 -> 空中 A3 -> E3
        self.macro_spam(self.G07_JX_A1A2, tag='L2-04今汐凌霄A1A2')
        self.lp_jx_jump_a_e3(jx, 'L2-04今汐')
        self.sw(self)
        # L2-05 长离 焚身以火 -> R -> 焚身以火
        self.lp_cl_heavy('L2-05长离')
        self.cl_r_fenshen(jx, tag='L2-05长离')
        # L2-06 今汐 凌霄 A4 -> 龙喷 -> 常态 A1~A4 -> E2 进凌霄 -> (同奏)变奏绯雪
        self.lp_jx_longpen(jx, 'L2-06今汐', a4=True)
        self.lp_jx_a4_e2(jx, 'L2-06今汐', cap=self.LP_JX_A1234_CAP)
        self.sw(hy, read_con=True)
        # L2-07 绯雪 变奏 A2 A3
        self.macro_spam(self.G11_HY_INTRO_A2A3, tag='L2-07绯雪变奏A2A3')
        self.sw(self)
        # L2-08 长离 E
        self.lp_cl_e('L2-08长离E')
        self.sw(jx)
        # L2-09 今汐 凌霄 A1 A2
        self.macro_spam(self.G07_JX_A1A2, tag='L2-09今汐凌霄A1A2')
        self.sw(hy)
        # L2-10 绯雪 [处决] A3
        if self.lp_execute('L2-10绯雪处决', anim=self.G18_A3_AT):
            self.macro_spam(self.G18_A3_SPAM, self.G18_A3_CLICK, tag='L2-10绯雪处决后A3')
            self.macro_wait(self.G18_A3_TAIL)
        else:
            self.tap('L2-10绯雪A3')
            self.macro_wait(self.G18_HY_A3)
        self.sw(self)
        # L2-11 长离 跳(冲) -> 下落
        self.lp_cl_jump_plunge('L2-11长离')
        self.sw(jx)
        # L2-12 今汐 凌霄 A3
        self.lp_jx_tap('L2-12今汐凌霄A3')
        self.sw(hy, click_during=True)
        # L2-13 绯雪 A4 A5
        self.macro_spam(self.G14_HY_A4A5, tag='L2-13绯雪A4A5')
        self.sw(self, click_during=True)
        # L2-14 长离 出场A3 A4(进心眼)
        self.macro_spam(self.G19_CL_A3A4, tag='L2-14长离A3A4')
        self.sw(jx)
        # L2-15 今汐 凌霄 A4 -> 龙喷 -> R -> 协奏满变奏绯雪
        self.lp_jx_longpen(jx, 'L2-15今汐', a4=True)
        self.lp_jx_r(jx, 'L2-15今汐')
        self.lp_intro(jx, hy, tag='L2-15今汐协奏')
        # L2-16 绯雪 变奏 A2 A3 -> 闪避居合 x2 -> 协奏满变奏今汐
        self.macro_spam(self.G11_HY_INTRO_A2A3, tag='L2-16绯雪变奏A2A3')
        self.lp_hy_iai_x2('L2-16绯雪')
        self.lp_intro(hy, jx, tag='L2-16绯雪协奏')
        # L2-17 今汐 变奏 -> E2 进凌霄
        self.lp_jx_e2(jx, 'L2-17今汐E2')
        self.sw(self)
        # L2-18 长离 征
        self.lp_cl_zheng('L2-18长离征')
        self.sw(hy)
        # L2-19 绯雪 居合(第三层淬寒) -> 按住不松 跳 E -> 落地出枯霜 -> A1 A2 -> 闪避居合 -> 归刃
        self.lp_hy_iai_cont('L2-19绯雪')
        self.macro_wait(self.G37_OUT_TO_HOLD)
        self.hold_down('L2-19按住')
        self.macro_wait(self.G30_KENDO_TO_JUMP)
        self.jump('L2-19跳', quiet=True)
        self.macro_wait(self.G30_JUMP_TO_E)
        self.tap_keys_quiet([hy.get_resonance_key()], self.G30_E_WINDOW, tag='L2-19绯雪E')
        hy.record_resonance_use()
        self.macro_wait(self.LP_HOLD_E_TO_KUSHUANG)
        self.release_attack()
        self.macro_mark('L2-19枯霜松开')
        self.macro_spam(self.LP_KUSHUANG_ANIM + self.LP_HY_A12, tag='L2-19绯雪A12')
        self.lp_hy_iai('L2-19绯雪收尾')
        self.macro_wait(self.LP_IAI_TO_R)
        self.hy_guiren(hy, 'L2-19')

    def loop3(self, jx, hy):
        """循环3(20 格): 龙喷 -> 长离R -> 红绯拔刀 -> 今汐处决 -> 凌霄 -> 龙喷 -> 双居合 -> 今汐R -> 枯霜 -> 长离R -> 归刃。"""
        # L3-01 绯雪(归刃后回到红绯) E + Q
        self.spam_e(hy, self.LP_HY_E, tag='L3-01绯雪E')
        self.send_echo_now(hy, tag='L3-01绯雪Q', window=self.KEY_TAP)
        self.sw(self)
        # L3-02 长离 E
        self.lp_cl_e('L3-02长离E')
        self.sw(jx)
        # L3-03 今汐 凌霄 A1 A2(L2-17 进的凌霄) -> 跳 -> 空中 A3 -> E3 -> 闪避 -> A4 -> 龙喷
        self.macro_spam(self.G07_JX_A1A2, tag='L3-03今汐凌霄A1A2')
        self.lp_jx_jump_a_e3(jx, 'L3-03今汐')
        self.dodge('L3-03今汐闪避')
        self.macro_wait(self.LP_JX_DODGE)
        self.lp_jx_longpen(jx, 'L3-03今汐', a4=True)
        self.sw(self, read_con=True)
        # L3-04 长离 R -> 焚身以火 -> 协奏满变奏绯雪
        # 手上只有两层离火也要开: 这里不开, L3-19 那发大招转不好
        self.cl_r_fenshen(hy, tag='L3-04长离', read_con=True)
        # L3-05 绯雪(红) 变奏 A3 -> 闪避 -> 重击 -> R 拔刀 -> E
        self.lp_hy_red_burst(hy, 'L3-05绯雪')
        self.spam_e(hy, self.G08_E_ONLY, tag='L3-05绯雪E')
        self.sw_key(self, hy, hy.send_resonance_key, tag='>L3-05切长离+E')
        # L3-06 长离 征(L3-02 的 E 给的心眼)
        self.lp_cl_zheng('L3-06长离征')
        self.sw(jx, click_during=True)
        # L3-07 今汐 出场A2 -> [处决]
        self.macro_spam(self.LP_JX_A2, tag='L3-07今汐A2')
        self.lp_execute('L3-07今汐处决')
        self.sw(hy, click_during=True)
        # L3-08 绯雪 A2 A3 -> E -> 协奏满变奏今汐
        self.macro_spam(self.LP_HY_A23, tag='L3-08绯雪A2A3')
        self.spam_e(hy, self.LP_HY_E, tag='L3-08绯雪E')
        self.lp_intro(hy, jx, tag='L3-08绯雪协奏')
        # L3-09 今汐 变奏 -> E2 进凌霄 -> 闪避 -> 凌霄普攻三段
        self.lp_jx_e2(jx, 'L3-09今汐E2')
        self.dodge('L3-09今汐闪避')
        self.macro_wait(self.LP_JX_DODGE)
        self.macro_spam(self.LP_JX_LX_A123, tag='L3-09今汐凌霄AAA')
        self.sw(self)
        # L3-10 长离 E
        self.lp_cl_e('L3-10长离E')
        self.sw(hy)
        # L3-11 绯雪 (空中)闪避 -> 居合 -> E(落地)
        self.lp_hy_iai('L3-11绯雪')
        self.spam_e(hy, self.G24_HY_E, tag='L3-11绯雪E')
        self.sw(jx)
        # L3-12 今汐 凌霄 A4 -> 龙喷 -> (同奏)变奏绯雪
        self.lp_jx_longpen(jx, 'L3-12今汐', a4=True)
        self.sw(hy, read_con=True)
        # L3-13 绯雪 变奏 A2 A3 -> 跳 + 闪避(空中闪避不进架势) -> A1 A2 A3 -> 闪避居合 x2
        self.macro_spam(self.G11_HY_INTRO_A2A3, tag='L3-13绯雪变奏A2A3')
        self.jump('L3-13跳')
        self.macro_wait(self.LP_HY_JUMP_TO_DODGE)
        self.dodge('L3-13空中闪避')
        self.macro_wait(self.LP_HY_AIR_DODGE)
        self.macro_spam(self.LP_HY_A123, tag='L3-13绯雪A123')
        self.lp_hy_iai_x2('L3-13绯雪')
        self.sw(self)
        # L3-14 长离 征(L3-10 的 E 给的心眼)
        self.lp_cl_zheng('L3-14长离征')
        self.sw(jx)
        # L3-15 今汐 R -> 协奏满变奏长离
        self.lp_jx_r(jx, 'L3-15今汐')
        self.lp_intro(jx, self, tag='L3-15今汐协奏')
        # L3-16 长离 变奏 -> 冲 -> E
        self.lp_cl_intro_chong('L3-16长离冲')
        self.macro_wait(self.LP_CL_CHONG_TO_E)
        self.lp_cl_e('L3-16长离E')
        self.sw(hy)
        # L3-17 绯雪 枯霜(L3-11 / 13 三刀攒的淬寒)
        self.lp_hy_kushuang('L3-17绯雪')
        self.sw(jx, click_during=True, time_out=self.G37_SWITCH_TIME_OUT)
        # L3-18 今汐 出场A2 A3 A4 -> E2 进凌霄 -> 跳 -> 空中 A1 A2 -> 闪避 -> A3 -> 闪避 -> A4 -> 龙喷
        if self.lp_jx_a4_e2(jx, 'L3-18今汐', cap=self.LP_JX_A1234_CAP):
            self.lp_jx_jump_aa('L3-18今汐', time.time() - self.G36_E2, self.G36_E2_TO_JUMP)
            self.dodge('L3-18今汐闪避1')
            self.macro_wait(self.LP_JX_DODGE)
            self.lp_jx_tap('L3-18今汐凌霄A3')
            self.dodge('L3-18今汐闪避2')
            self.macro_wait(self.LP_JX_DODGE)
            self.lp_jx_longpen(jx, 'L3-18今汐', a4=True)
        self.sw(self, read_con=True)
        # L3-19 长离 征(L3-16 的 E 给的心眼) -> R -> 焚身以火
        self.lp_cl_zheng('L3-19长离征')
        self.cl_r_fenshen(hy, tag='L3-19长离', read_con=True)
        # L3-20 绯雪 归刃(带变奏进来先等过 QTE)。不普攻, 直接按住 R: 这时 R 可能还差一点没转好, 按到它转好自己放出来
        self.lp_qte_idle(hy, self.G40_INTRO_IDLE)
        self.hy_guiren(hy, 'L3-20', hold=self.G40_R2_HOLD + self.LP_R2_CD_SLACK)

    def perform_axis(self):
        """整条轴: 启动 40 格(part1~3) -> 循环1 -> 循环2 -> 循环3 -> 再从循环1 转。
        每段都以绯雪归刃收尾, 归刃演完才查还在不在打; 脱战或连续切人失败才返回。"""
        jx = self.teammate('Jinhsi')
        hy = self.teammate('Hiyuki')
        if jx is None or hy is None:
            return False
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = self
        self.poking = False
        self.switch_fails = 0
        # 处决由轴上那一格自己按, 框架切人时那一发一律关掉(内置非治疗角色默认会按)
        for char in (self, jx, hy):
            char.check_f_on_switch = False
        self.macro_start()
        self.logger.info('longfengxue axis start')
        rounds = 0

        def still_fighting():
            # 一段打完(归刃演完、HUD 在场)才看还在不在打; 段中间不查, 高频切人时这个读数会抖
            try:
                return bool(self.task.in_combat())
            except Exception:
                return True

        def run(name, seg):
            marks = len(self.macro_marks)
            try:
                seg(jx, hy)
            finally:
                self.logger.info(f'longfengxue round {self.round_no} {name} timeline: '
                                 f'{self.macro_timeline(marks)}')

        try:
            # 启动轴: round_no == 1。循环里 round_no >= 2(大招没转好就不按那几道门认这个)
            self.round_no = 1
            for i, part in enumerate((self.part1, self.part2, self.part3), start=1):
                run(f'part {i}', part)
            rounds = 1
            fighting = still_fighting()
            while fighting:
                for name, seg in (('loop1', self.loop1), ('loop2', self.loop2), ('loop3', self.loop3)):
                    self.round_no = rounds + 1
                    run(name, seg)
                    rounds += 1
                    fighting = still_fighting()
                    if not fighting:
                        break
            self.logger.info(f'longfengxue: out of combat after segment {rounds}, axis stops')
        except AxisAbort as e:
            self.logger.warning(f'longfengxue axis aborted in round {rounds + 1}: {e}')
        finally:
            self.release_attack()
            self.poking = False
            self.task.skip_combat_check = previous
            self.mark_busy(False)
        self.logger.info(
            f'longfengxue axis end ({rounds} rounds) '
            f'{self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s (wall {time.time() - self.macro_t0:.2f}s)')
        return True

    # ==================== 框架接口 ====================

    def do_perform(self):
        """本队里长离持宏: 开局(或按 F 重开)起循环跑整条轴, 轴停了之后走内置逻辑。"""
        if not self.is_in_longfengxue:
            return super().do_perform()
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            if self.perform_axis():
                return
            self.logger.warning('longfengxue axis could not start, falling back to the native rotation')
            return super().do_perform()
        self.logger.info(f'longfengxue: axis not pending ({blocked}), native rotation')
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """轴待跑时必须是长离先上(第一个动作是她的 E)。"""
        if self.is_in_longfengxue and self.opener_pending():
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
