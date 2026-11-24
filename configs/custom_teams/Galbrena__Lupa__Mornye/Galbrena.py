import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src import text_white_color
from src.char.BaseChar import SwitchPriority, forte_white_color
from src.Labels import Labels
from src.char.Galbrena import Galbrena as NativeGalbrena
from src.combat.MacroKit import MacroKit, MacroBroken, send_input_left

# ==================== 物理 F 键监视 ====================
# 跟 Hsin__Rover__Suisui/Hsin.py 里那份逐字相同, 说明见 Chisa__Suisui__YangYangSp/YangYangSp.py。
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


class Galbrena(MacroKit, NativeGalbrena):
    """嘉贝莉娜。嘉露莫(嘉贝莉娜/露帕/莫宁)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    长风ei 的「嘉露宁 25s 循环轴」教学视频(E:/轴视频/【嘉露宁】25s循环轴.mp4, 1080p, 1.0x + 讲解定格),
    步骤条 + 游戏倒计时 + 技能栏 CD 数字逐帧扒的, 157~188s 的第二圈不定格。作者编队 1 嘉 / 2 露 / 3 莫; 代码【按角色切人】, 编队随意。
    配置按用户的: 莫宁 1 链, 嘉贝 / 露帕 0 链; 共鸣效率够(嘉 120 / 露 125 / 莫 240+); 声骸 蚀脊龙 / 荣耀狮像 / 炉芯机骸。

    ================= 轴的形状 =================
    启动轴: 嘉 按住重击到第三段 -> 切莫宁
            莫 地面普攻到静质量能满 -> 按住出位势转换(升空) -> R
    循环轴: 莫(嘉变奏进来, 入场自带升空) -> R
    之后两条一样:
      莫 R 演完 -> E -> 按住普攻(观测 A 循环, 相对动能满自动出重击·反演) -> 协奏满松手 -> Q -> 变奏露帕
      露 (变奏) R -> 演完普攻 = 破敌(升空) -> 空中 Q -> 空中 A 到焰袭 -> 落地 A = 锐爪 -> 强化E 图标亮 -> 切嘉
      嘉 按住重击到第三段(子弹脱手就切) -> 切露
      露 强化E(狼舞的决意·极) -> 协奏满 -> 变奏嘉
      嘉 (变奏) Q -> A -> 有处决提示就处决 -> 强化E(恶翼扬升) -> R -> 恶魔连段: 点普攻 ~3.5s 闪避 / ~3.5s 闪避 / 点 ~0.9s
          -> 按住 3 段重击 -> 协奏满 -> 变奏莫
    宏在【莫宁变奏入场、HUD 在场】时返回, 下一次框架调的就是她(循环轴从她的 R 开始)。

    ================= 为什么这么打(nanoka 技能文本 + 帧表) =================
    - 嘉: 罪火只靠阈限态常态攻击命中攒, 满 100 E 才是恶翼扬升。两次 3 段重击(各 ~43) + 变奏 10 = 96, Q 后那下 A 补满。
      余火只在阈限态监听全队不同名声骸 —— 莫 Q / 露 Q / 嘉 Q 都在 E3 之前。R 第 1F 给 14 秒强化状态, 爆发段就是这 14 秒。
      恶魔位格里处决会重置普攻段数, 所以处决只放在变奏入场、E3 之前。
    - 露: R 后一段时间内短按普攻或 E 都是破敌(清空狼焰、进 12s 赛点沸腾、升空); 空中第 3 段在狼焰 >= 50 时是焰袭(+1 狼魂),
      焰袭后短按普攻是锐爪(+1 狼魂)。2 狼魂 + 沸腾 = 狼舞的决意·极, 放完 8 秒内切走, 队友放 R 时露帕协助一次(就是嘉的 R)。
      循环里莫 -> 露那次变奏是强化变奏(追猎 3 层, 141F 不响应输入 + 全局时停), 启动轴那次是普通变奏(60F)。
    - 莫: 地面普攻 A1/A2/A3 第 1F 给 20/43/37 静质量能, 满 100 重击 = 位势转换(升空进观测模式、展开谐振场);
      观测模式里 R 把谐振场换成强谐振场; 长按普攻循环观测 A1~A3, 相对动能满了自动出重击·反演。变奏入场第 69F 进观测模式。

    ================= 判据 =================
    - 莫: 地面静质量能 = 回路条橙色格(mor_mass_fill), 空中相对动能 = 同一条带子的白格(mor_forte_fill),
      在不在观测模式 = E 图标字形(mor_e_air); 三个都逐字抄自爱琳莫 v2.0.0 定稿, 2K/1080p 验过。
      出没出重击·反演 / 协奏满没满的按住写法也是爱琳莫的(v6_hold_until_cannon / v6_hold_until_con)。
    - 露: 两个狼魂 = E 图标换成 lupa_wolf_icon2(框架内置 Lupa 同一个判据)。破敌 = 狼焰条(is_forte_full)从满到清空
      (E 图标变灰但没有冷却数字, 框架当成"可用", 不能用); 条读不到退回 R 出手 + LU_POD_UNTIL 计时。
    - 嘉: 强化E 就绪 = is_forte_full() 或 is_e_forte_full()(嘉仇守 09-04 实机定的), 亮了当帧按;
      按出去没有看 E 图标外圈(is_e_forte_full)灭没灭, 没灭再按。变奏入场 44F 里按的 A/F/E 全丢, 等过了再出手(Q 不受影响)。
      处决看屏幕中间的 F 提示; boss 头像外圈(f_break_full)满着就等提示从入场镜头里转回来(见 GA_BREAK_UNTIL)。
    - 被怪打断: R / E / Q 到上限没出去、图标还亮着就等 HUD 回来补一轮; 莫宁按住出炮被打断就重新按住一次;
      露帕短按没出锐爪(两个狼魂图标没亮)就长按重击补狼魂(露1 收尾、露2 到场各一次);
      露帕变奏进来 R 按满窗口还没放、R 图标暗着(能量不够)就点普攻攒到图标亮再放。
    - 协奏判满走 con_now(): 框架 0.99 哨兵里面积已经满的也算满(心卜穗 10-09)。

    ================= 改这个文件之前先读 =================
    - 这个文件是拼出来的: 引擎(macro_* / quick_switch / fire_r / wait_hud_back ...)逐字抄自 Hsin__Rover__Suisui/Hsin.py(心雷穗 v1.1.0),
      莫宁的读条和按住逐字抄自爱琳莫 v2.0.0 定稿(team_backup), opener_blocked_by 是弗坎莫 10-13 修过的那版。
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
    # 协奏: 0.99 是环没闭合的哨兵值, 要真满; 连着几帧才算
    CON_ENOUGH = 1.0
    CON_FULL_CONFIRM = 3
    # 【面积已经满了但环没认成闭合】: 框架只在协奏环轮廓闭合时报 1.0, 面积 >= 满环面积但轮廓没闭合(特效/背景压在环上)
    # 报 0.99 哨兵。心卜穗 10-09 日志: 面积比 1.00~1.11 的录屏里环是满的, 1.28~2.01 是同色特效混进来、协奏其实没满。
    # -> 哨兵 + 面积比在 CON_SOFT_RATIO 之间连 CON_SOFT_FRAMES 帧就当满
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
    HUD_LOST_ABORT = 9.00        # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃(最长的演出是莫宁 R 4.8s)
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    # 被怪打断(击飞/硬直)时按键全被丢: R / E / Q 到上限还没出去、图标【还亮着】就等 HUD 回来再按一轮。
    # 图标不亮(没能量/在冷却)不补。这几个键在冷却/没能量时按了什么都不发生, 补按不会多放一次
    R_RETRIES = 2
    R_RETRY_WINDOW = 1.50
    KEY_RETRY_CAP = 1.00
    RETRY_HUD_CAP = 2.00         # 补按前等 HUD 回来的上限(被打飞落地)
    LIT_FRAMES = 2               # 图标连这么多帧亮才补

    # ==================== 嘉贝莉娜 ====================
    # ---- 常态重击链(嘉仇守 09-04 实机定标): 按住时按派生帧推进 ----
    #   重击1 0F -> 重击2 29F -> 重击3 45F, 重击3 子弹第 30F(绝对 75F = 1.25s)脱手, 松手/切走也自己打完;
    #   第二轮重击1 从 115F = 1.92s 起手。所以按住只能在 1.25 ~ 1.92 之间取
    # 启动轴开场 / 循环里露帕中间那下: 第三段子弹脱手就松手切人(视频嘉在场 1.3s)。刚切进来按下沿可能被入场吃掉几帧, 留 0.15 余量
    GA_OPEN_HOLD = 1.40
    GA_MID_HOLD = 1.40
    # 刚到场就按住的这两下走 SendInput(游戏在前台时): PostMessage 的按下沿落在切人动作里会被丢掉, 整段重击不出(心雷穗同一个问题)
    GA_HOLD_SENDINPUT = True
    # 爆发收尾那下按满(嘉仇守实机 1.70), 松手后等协奏满变奏莫
    GA_END_HOLD = 1.70
    # ---- 变奏入场: Q -> A -> 处决 -> 强化E -> R ----
    # Q 从到场起一直按到进 CD(10-16 录屏: 到场 0.1s 按的 Q 就进了冷却, 入场动作不挡声骸)
    GA_Q_CAP = 1.20
    # QTE 第 44F(0.73s)前按的 A / F / E 被丢掉、不排队: 从切人确认起等够这么久再出手。
    # 10-16 实机: 到场 0.53s 按的那一下强化E 被吞, E 图标一直亮到 R 演出, 这一圈没有恶魔位格
    GA_INTRO_NO_INPUT = 0.80
    GA_FILL_TAPS = 1             # Q 后补的那下普攻(罪火差的 4 点)
    # 处决: 只在怪身上的 F 提示(屏幕中间 f_break, 不是拾取 F)在的时候按 F, 没提示按 F 是空按。
    # boss 头像外圈(f_break_full, 共振度满)跟镜头无关, 2K/1080 都读 0.98~0.99, 处决打完才清空(教学视频 182.4 -> 182.7)。
    # 10-16 实机 4 次变奏入场: 外圈从 85s 起一直满着(全程没处决过), 中间的提示在入场镜头甩开时读不到(0.55~0.70),
    # 到场 +1.05~1.45s 才回来; 旧写法到场 0.8s 后只读 3 帧, 4 次全漏。
    # -> 外圈满着(或者切人前露帕身上读到过提示)就一直盯提示到到场 GA_BREAK_UNTIL; 都没有就只读 BREAK_READS 帧
    BREAK_READS = 3
    BREAK_CENTER_MIN = 0.90
    BREAK_RING_MIN = 0.92        # 框架 check_f_break 同一个阈值
    GA_BREAK_UNTIL = 2.00
    BREAK_SEEN_FRESH = 3.00      # 切人前读到的提示, 这么久之内算数
    BREAK_RESEND = 0.30          # 按完这么久提示还在 = 被吞了(硬直/上一个动作没收), 再按
    BREAK_PRESSES = 5
    BREAK_GONE_FRAMES = 3        # 提示单帧会掉到 0.85~0.88(特效闪), 连这么多帧不见才算处决起手了
    BREAK_ANIM = 1.60            # 嘉贝谐度破坏派生 96F; 视频按 F 后倒计时停 ~1.4s
    BREAK_ROUNDS = 2             # 等完动画提示还在 = 那一下没处决上, 再来一轮
    # 强化E: 罪火满了图标高亮, 窗口很短 —— 高频盯着, 亮了当帧按(嘉仇守 09-04)。
    # 按成普通 E1 会把 E 打进冷却, 这一轮恶魔位格就没了, 所以到点没亮【也不按】
    FORTE_WAIT = 8.0
    FORTE_POLL = 0.04
    FORTE_FILL_INTERVAL = 0.20   # 等的时候补普攻的节奏, 跟轮询各走各的
    # 按出去没有: 看 E 图标外圈(find_e_forte)。10-16 录屏到场到 R 那 1.1s 外圈 2K/1080 都一帧不落地亮着,
    # 教学视频恶翼扬升出手那一帧图标就换成不带圈的恶魔 E。回路条(is_forte_full)不能用: 嘉贝身上跟着背景闪,
    # 1080p 一直读 False, 而且恶魔位格里是净炼火、也是满的
    E3_GONE_FRAMES = 2
    E3_RESEND_AFTER = 0.40       # 按完这么久外圈还亮着 = 被吞了, 再按
    E3_PRESSES = 4
    # E3 -> R: 恶翼扬升派生 29F、动作结束 61F; 嘉仇守实机 0.55
    GA_E_TO_R = 0.55
    GA_R_WINDOW = 2.00
    GA_R_ANIM_MIN = 2.50         # 嘉 R 藏 HUD 201F = 3.35s
    # ---- 爆发段(R 的 14 秒强化状态): 点普攻打恶魔连段, 每轮出脚(恶魔 A4)后闪避断后摇 ----
    # 嘉仇守 09-04 实机定的「21 下 -> 闪 -> 19 下 -> 闪 -> 5 下」(0.16s 一下)换成秒:
    # 第一脚 21x0.16+0.10 = 3.46s(从大招 HUD 回来 +LIB_TO_BURST 算), 第二脚离第一脚 0.12+19x0.16+0.40 = 3.56s, 尾巴 0.92s。
    # 视频: 4A踢+闪 3.6s / 3A踢+闪 3.51s / a3展翅 1.22s / 重击3A 1.6s(游戏倒计时), 对得上。
    # 闪早了会把恶魔 A4-2 那一脚的伤害取消(hitbox 44~56F), 闪晚了只是后摇没断, 所以往晚了压
    LIB_TO_BURST = 0.10
    BURST_A_INTERVAL = 0.16
    BURST_DODGE_1 = 3.50
    BURST_DODGE_2 = 3.56
    BURST_TAIL = 0.92
    BURST_PRE_DODGE = 0.10       # 闪避前这么久不再点(别让最后一下普攻跟闪避粘在一起)
    DODGE_HOLD = 0.07            # 闪避 = 右键
    DODGE_TO_A = 0.12            # 闪完接普攻不能贴着发, 会把闪避顶掉(嘉仇守)
    GA_CON_CAP = 3.00            # 变奏莫之前协奏要真满: 莫宁变奏才自带升空进观测模式

    # ==================== 莫宁 ====================
    # ---- 地面(启动轴 / 宏散过): 普攻到静质量能满 -> 按住出位势转换 -> 升空 ----
    # 静质量能是回路条上的橙色格(mor_mass_fill): A1 后 0.21, A2 第 1 帧 0.62, A3 第 1 帧满(爱琳莫 10-12 录屏)。
    # A1A2 每 0.08s 点(按住 0.04s); 读到 A2 以后 A3 是跳斩, 半空里点普攻会变成下落攻击把它打断(爱琳莫 10-11 升空失败的根因),
    # 所以 A2 读到后隔 MOR_GND_A3_FROM 才点、之后每 MOR_GND_TAP_GAP 一下, 每帧看条, 满了马上按住
    MOR_GND_A2_FILL = 0.58
    MOR_GND_A3_FROM = 0.62       # A2 派生 45F = 0.75s, 输入缓存早一点
    MOR_GND_TAP_GAP = 0.30       # 爱琳莫循环 A3 那下用的间隔
    MOR_GND_FULL = 0.97          # 33/34 格
    MOR_GND_CAP = 3.00           # 一直不满也到点按住(按出普通重击也给 20 静质量能)
    MOR_RISE_CAP = 2.50          # 按住 -> E 位变成空中 E 的上限(爱琳莫 L2_RISE_CAP)
    MOR_AIR_CONFIRM = 2
    # ---- R ----
    MOR_R_WINDOW_GND = 1.50      # 升空后马上按
    MOR_R_WINDOW_INTRO = 3.00    # 变奏入场 80F 前不响应输入: 从到场起一直按
    MOR_R_ANIM_MIN = 3.50        # 莫宁 R 藏 HUD 288F = 4.8s, 中途会闪
    # ---- R 演完: E -> 按住 -> 重击·反演 -> 协奏满 -> Q -> 变奏露 ----
    # 视频: HUD 回来那一帧 E 已经在冷却(16.0), 4.45s 后按 Q, 再 0.3s 切露帕
    MOR_E_CAP = 1.20
    MOR_Q_CAP = 0.80
    # 出炮 + 等协奏都撞上限、协奏还没满: 多半是按住被打断(倒地起来游戏不认还按着的左键), 松开重新按住再来一遍。
    # 观测普攻循环本来就是"按住等条件", 重按只是从观测 A1 重新起手
    MOR_REHOLD = 1
    MOR_DEAD_HOLD_CON = 0.02     # 出炮撞上限时协奏比按下时涨不到这么多 = 根本没在打, 不等协奏直接重按
    # 变奏进来到循环轴开始(macro_exit -> 框架 -> drive)不超过这么久, 才当成"刚变奏入场"走 R 起手
    MOR_INTRO_FRESH = 2.00
    MOR_ROUTE_READS = 4          # 不是刚变奏进来时读几帧 E 图标, 判她在不在观测模式

    # ---- 下面这些给抄来的爱琳莫方法用(名字不改) ----
    # E 图标 16x16 灰度字形(只取圆内, 去均值归一化), 从 10-11 两段 2K 录屏截的; 2K/1080p 读数一致。
    # 空中/地面两个模板互相点积 0.03, 实测在场时对的那个 0.76~1.00、错的那个 <0.1
    MOR_E_AIR_GLYPH = 'k6vbq9urEqz/q7erc6pvq/+r/6sDq0isSKxIrEis26t3qSuqA6tvqw6oJKw2rEisSKxarCerQChvq5OrS6tLqw6o56j/q7+px6NLnEism6lzqhKs/6sSrHSkQChHLlUvATGRMUQwTKW9IfagGClvq9ur/6t6qEAo3yz9I7cca51MpTgqXzD4L/suB6p9K9ur26sDqySsp6JZFZOr56j/q1kVIDChLgUwwS8SrDasSKz/q3qow6g8KdylWRUnq5Slsy6LL0culTAsIW0jWqxsrNurryX/Lb0hzCnYHUSnBTDUL9cuFzAXJ0QwTSJsrH6sk6suG90i7SpgJ98sVjCRMSMu8CfJKtgdHDHfqmysfqzbq5+o3SIsIb+pQy8TMWcvRKckpkisqSwDLVqsbKxsrLuqa53JKmcv8SzDqBgsnCALqf+rOzAkpmysbKwSrDKoLjFkKCSsSKzJLQwgSKznqK8vJKxsrGysYSwspPQo0CgDLQYsx6NarEckPyZIrEisw6hLnMktVqh0pDej3KXpK5SlK6qlKm0j0CjCCueoJKw='
    MOR_E_GND_GLYPH = 'ParDrCytw6zDrFqsPaparJWtLK3DrOGrLK1orpWtw6zhqz2qO6GaJbsrXxnrIxYq6yOYqOGraK49qsOs6yOhIEKkWqyMp18Zla3hq8abXxlorv+tFiqhIOkqFipqqVqsmKg7ocOsWqyhIOsjO6GhID2qGS3PMEKkaqlqqaEgRyy7K18ZaqmVrf+tRywZLSytPaoEMbsrO6FarEcsoSDDrCytmKhyKJWtla3Gm20xLK0Pq7AsmiU/J+sjRCkKMgQxBDHdMl8ZRyyhIOeluyssrQ+rgi07oeGrRyyhIHQy4atarG0xPydHLMOs6yMWKsOsPaqwLEQpPapHLD2qODHGm2qpdDLDrEcsLK2Mp0Qpw6wPq7AsjKdfGRYqFiqMp90yqDJfGTuhPycPq+eluyuVrekqvi6aJQ+rcig/J6Eg6yOhILAs4auaJekqsCw/J7AsxpsPq2qpQqRHLEcsXxlarA+rQqTpKjuhxpsZLZio4asPqzuhoSAsrZiojKfpKkKk56WCLUQpaqmYqEKkWqyhIOkqQqRCpBYqoSDrI0QpmKg='
    MOR_E_GLYPH_OK = 0.75
    # 相对动能(空中白格): 每格 min(R,G,B) 峰值 没亮 115~155 / 亮 189~207 / 满了发光 250+
    MOR_FORTE_LIT = 185
    V6_TAP_HOLD = 0.04
    V6_TAP_INTERVAL = 0.08
    # E 后按住到重击·反演出手: 这条轴 E 是在条清空时按的(R 演完马上 E), 浮游炮 +0.59 + 观测 A1~A3 才凑满,
    # 比爱琳莫(条 0.21 才 E, ~1.0s 出炮)晚一截, 上限放宽
    V6_CANNON_CAP = 4.0
    V6_CANNON_MIN = 0.4
    # 出炮后按着等协奏满的上限(爱琳莫 10-11 实机: 出炮到协奏读满 1.5s 左右)
    V6_CON_CAP = 3.0
    # 0.99 哨兵 + 面积比在这个范围连这么多帧 = 满。下限不能贴着 1.0(爱琳莫 10-12 实机: 面积比 0.995~1.009 当满, 6 次都不是变奏)
    V6_CON_SOFT_RATIO = (1.01, 1.15)
    V6_CON_SOFT_FRAMES = 3

    # ==================== 露帕 ====================
    # ---- 第一格(变奏进来): R -> 破敌 -> 空中 Q -> 空中 A 到焰袭 -> 落地锐爪 -> 两个狼魂 -> 切嘉 ----
    # 强化变奏 141F 不响应输入(+全局时停), 普通变奏 60F: R 从到场起一直按到演出开始(三火 L_LU_R_WINDOW 3.0)
    LU_R_WINDOW = 3.50
    LU_R_ANIM_MIN = 1.50         # 露帕 R 藏 HUD 132F = 2.2s
    # R 第 201F 前不响应输入、HUD 回来后还有 ~1.15s。破敌出没出读不到(见类注释), 从 R 演出开始算点普攻到这一刻:
    # 视频 HUD 消失 169.4 -> 破敌 172.85(E 图标变灰、R 冷却开始走) = 3.45s; 多点两下的余量
    LU_POD_UNTIL = 3.60
    LU_POD_EVERY = 0.10
    # 破敌出没出: 狼焰条(is_forte_full)见过满、又连 2 帧不满 = 破敌清空了狼焰。10-16 录屏 L2/L3: HUD 回来 0.05s 就满,
    # 一帧不落地亮到破敌那一帧(HUD 回来 +1.30/+1.35s)清空, 2K/1080 一致。条见过满就点到它清空(被打断也接着点),
    # 最多到 LU_POD_CAP(破敌窗口是 R 第 120F 起 4s); 一直没见过满退回上面的计时
    LU_POD_MIN = 2.00            # R 第 120F 才解锁破敌, 这之前的清空不认
    LU_POD_CAP = 5.20
    # 破敌后空中 Q: 一直按到声骸进 CD(视频破敌后 0.6s 按的; 破敌动画里按的会被丢)
    LU_Q_CAP = 0.90
    # 之后一直点普攻: 空中 A1 A2 -> 焰袭 -> 落地 -> 锐爪; E 图标换成 lupa_wolf_icon2(两个狼魂)就切嘉。
    # 视频 Q -> 切嘉 3.35s, 切的时候图标还没亮(锐爪一起手就切, 第 10F 才给狼魂); 这里等图标亮, 比视频晚一点但不会丢狼魂
    LU_AIR_A_EVERY = 0.12
    LU_WOLF_THRESHOLD = 0.85     # 框架内置 Lupa 同一个阈值
    LU_WOLF_CONFIRM = 2
    LU_WOLF_CAP = 4.60           # 短按阶段的上限。10-17 实机 Q -> 图标亮 4.06~4.17s, 旧值 4.2 贴得太紧
    # 短按出锐爪只在焰袭后一小段时间里有效, 被打断就没了; 【长按】重击照样能拿狼魂: 狼焰 >=50 且有 1 狼魂 = 锐爪,
    # 没有狼魂 = 狼咬, 都 +1(帧表: 锐爪第 10F / 狼咬第 17F 到账); 赛点沸腾里普攻回狼焰快 5 倍。
    # 10-17 实机 L3: 焰袭拿到 1 个狼魂后被怪打断, 短按到上限也没出锐爪, 露2 没有强化E -> 嘉贝不是变奏进来。
    # -> 短按阶段没等到图标: 狼焰条满就长按出重击, 不满就点普攻攒, 等两个狼魂图标亮(空中长按是下落攻击; 补狼魂排在短按阶段之后, 那时焰袭早落地了)
    LU_HEAVY_HOLD = 0.60
    LU_HEAVY_SETTLE = 0.50       # 长按松手后这么久不再长按(狼焰条要几帧才扣下去, 别连出两发)
    LU_WOLF_FIX_CAP = 2.50       # 露1 补狼魂的上限
    LU2_WOLF_FIX_CAP = 2.00      # 露2 到场没见到图标时补狼魂的上限
    LU_FIX_BLIND_LIMIT = 2
    # R 按满窗口没放出来、图标暗着 = 能量不到 125(10-17 09:22 实机 L3: 怪刚换波, 变奏那一下没打中, R 按 3.5s 图标一直暗,
    # 没破敌没沸腾 -> 没狼魂 -> 没强化E -> 嘉贝不是变奏, 整圈塌掉) -> 点普攻攒到 R 图标连 LIT_FRAMES 帧亮再放。
    # 只点普攻: 普通E(围猎)冷却 12s, 换成狼舞的决意后吃不吃这个冷却技能文本没写, 别赌露2 的强化E。
    # 普攻一整套回 7~9 点(帧表, 没算共鸣效率), 变奏那一下 8 点
    LU_R_FILL_CAP = 6.00
    LU_NO_R_A = 1.50             # 攒到上限 R 还是没放出来(没破敌/没沸腾): 点这么久普攻就切
    # ---- 第二格(普通切进来): 强化E -> 协奏满 -> 变奏嘉 ----
    LU_EE_SEEN_CAP = 0.60        # 到场这么久都没见到两个狼魂的图标就不按 E(按出来会是围猎)
    LU_EE_CAP = 2.50             # 按到图标消失的上限(只在图标还在时按, 放长了只防被打断)
    LU_EE_INTERVAL = 0.06
    LU_CON_CAP = 3.00
    LU_CON_POKE_AFTER = 1.80     # 强化E 派生 120F = 2.0s 之前点普攻会被丢掉, 等协奏不点; 过了再补

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
        self.mor_intro_at = -99.0      # 宏把莫宁变奏切进来的时刻(循环轴判"刚变奏入场")
        self.break_seen_at = -99.0     # 切给嘉贝前读到处决提示的时刻
        self.lu_icon_ever = False      # 两个狼魂的图标读到过没有
        self.lu_fix_fails = 0          # 补狼魂撞上限的次数(图标从没读到过时用来停掉补狼魂)
        self._con_soft_n = 0
        self._con_soft_frame = None
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
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申(这条轴自己按处决)
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_jlm(self) -> bool:
        """本队 = 嘉贝 + 露帕 + 莫宁。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Lupa', 'Mornye'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_jlm:
            return 'team is not galbrena/lupa/mornye'
        # 【开关过自动战斗 -> 启动轴重跑】: 见 start_enable_watch。
        # 【放在 armed 判断之前、不管 armed 真假都放行】, 直到启动轴真跑了(last_opener 刷新)为止 ——
        # 只在 armed=False 那一次放行的话, 选人时三个人的 get_switch_priority 都会调进来,
        # 第二次 armed 已经是 True, 就撞上下面的 OPENER_MIN_GAP: 一圈打到一半去开关必中(2026-10-11 爱琳莫)。
        # 按过 F 且界面上配了等待秒数的话, 照样等够再跑
        restarted = getattr(self.task, '_ww_task_restarted_at', -1)
        if restarted > self.last_opener:
            if not self.opener_armed:
                self.logger.info('auto combat 重开过 — 启动轴重新武装, 从头跑')
                self.opener_armed = True
            f_press = getattr(self.task, '_f_last_press', -1)
            if f_press > self.last_opener:
                waited = time.time() - f_press
                delay = f_settings(self.task)[1]
                if waited < delay:
                    return f'F pressed {waited:.2f}s ago, waiting {delay:.2f}s for the mobs'
            return None
        if not self.opener_armed:
            return 'already armed-down (ran once this combat)'
        # 玩家按过 F 就当成"真的重开了一局"直接放行: 60 秒门槛分不清"战斗判定抖动"
        # 和"玩家重开副本", 而物理 F 键正好能分 —— 抖动不会有人按 F
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

    # ==================== 宏原语(引擎, 逐字抄自心雷穗 Hsin.py) ====================

    def macro_mark(self, name):
        self.macro_marks.append(f'{name}@{time.time() - self.macro_t0:.2f}')
        # 【看门狗】每一步都会打标记, 在这里顺手查: HUD 很久没出现 = 关卡结束/过场/窗口没了,
        # 再按下去就是在结算画面上乱按(景月守实机 2026-09-28 19:17 见过)。不读屏, 只比时间戳
        if self.hud_watch and time.time() - self.hud_seen_at > self.HUD_LOST_ABORT:
            self.hud_watch = False
            raise MacroBroken(f'队伍 HUD {time.time() - self.hud_seen_at:.1f}s 没出现(关卡结束/过场/窗口没了?)')

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
        if not self.is_in_jlm:
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
            self.macro_wait(self.DODGE_HOLD)
        finally:
            self.task.mouse_up(key='right')
        self.macro_mark(f'{tag}闪避')

    def tap_until(self, deadline, every, hold=None):
        """点普攻到 deadline(墙钟)。返回点了几下。"""
        n = 0
        while time.time() < deadline:
            self.tap(hold)
            n += 1
            self.macro_wait(min(max(0.0, every - (hold or self.A_TAP)), deadline - time.time()))
        return n

    def icon_lit(self, available):
        """HUD 在场、图标连 LIT_FRAMES 帧亮。补按之前用: 只有"还亮着"才说明上一轮没出去。"""
        for _ in range(self.LIT_FRAMES):
            self.task.next_frame()
            try:
                lit = self.hud_ok() and bool(available())
            except Exception:
                lit = False
            if not lit:
                return False
        return True

    def press_echo(self, char, cap, tag):
        """声骸 Q 按到进 CD(先见过亮)。刚切人那几帧技能栏还是上一个人的, 给 SKILL_BAR_SETTLE。被打断就补一轮。"""
        return self.press_sure(char.send_echo_key, char.echo_available, cap, tag)

    # ==================== 嘉贝莉娜 ====================

    def ga_hold(self, hold, tag, direct=False):
        """按住普攻打常态重击链, 按满 hold 秒松手。重击是固定长度的动画, 没有"打完了"的信号, 只能计时。
        direct: 走 SendInput(刚切进来的那两下, 见 GA_HOLD_SENDINPUT)。按住期间一个键都不发。"""
        si = self.attack_down(direct and self.GA_HOLD_SENDINPUT and self.game_foreground())
        self.macro_mark(f'{tag}按住{"(SI)" if si else ""}')
        try:
            self.macro_wait(hold)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}松手({hold:.2f}s)')

    def forte_signals(self):
        """强化E 就绪的两条判据, 分开返回好在日志里单独看每一条。"""
        return bool(self.is_forte_full()), bool(self.is_e_forte_full())

    def ga_cast_e3(self, tag):
        """强化E(恶翼扬升): 一边点普攻攒罪火、一边每 FORTE_POLL 盯图标, 亮了当帧按。到点没亮不按(见 FORTE_WAIT)。
        返回最后一次发键的时刻, 没按返回 None。按完怎么确认见 ga_confirm_e3"""
        start = time.time()
        next_fill = 0.0
        taps = 0
        forte = e_forte = False
        while time.time() - start < self.FORTE_WAIT:
            self.task.next_frame()
            if self.hud_ok():
                forte, e_forte = self.forte_signals()
                if forte or e_forte:
                    # 当帧发键, 先发再记账
                    self.send_resonance_key()
                    sent_at = time.time()
                    self.macro_mark(f'{tag}强化E(forte={int(forte)} e={int(e_forte)},A x{taps},{sent_at - start:.2f}s)')
                    if not e_forte:
                        # 外圈没读到就没法确认, 只按这一次(补按可能是 E2-掠袭)
                        self.record_resonance_use()
                        return sent_at
                    return self.ga_confirm_e3(tag, sent_at)
            if time.time() >= next_fill:
                self.tap()
                taps += 1
                next_fill = time.time() + self.FORTE_FILL_INTERVAL
            self.macro_wait(self.FORTE_POLL)
        self.macro_mark(f'{tag}强化E没亮(A x{taps},CAP)')
        self.logger.warning(f'{tag}: 强化E {self.FORTE_WAIT:.0f}s 内一直没亮(forte={forte} e_forte={e_forte}), 不按 —— '
                            f'按出普通 E1 会把 E 打进冷却; 这一棒没有恶魔位格')
        return None

    def ga_confirm_e3(self, tag, sent_at):
        """按完强化E 盯 E 图标外圈: 连 E3_GONE_FRAMES 帧灭了 = 出去了; E3_RESEND_AFTER 之后还亮着 = 被吞了, 再按。
        【只认外圈, 不认回路条】: 恶魔位格里回路条换成净炼火(等量, 也是满的), 照条补按出来的是 E2-掠袭。
        外圈是"罪火满、强化E 可用"的标记, 亮着时按出来的只会是恶翼扬升。返回最后一次发键的时刻"""
        presses = 1
        gone = 0
        while True:
            self.task.next_frame()
            if self.hud_ok():
                if not self.is_e_forte_full():
                    gone += 1
                    if gone >= self.E3_GONE_FRAMES:
                        self.record_resonance_use()
                        self.macro_mark(f'{tag}强化E出手(x{presses})')
                        return sent_at
                else:
                    gone = 0
                    if time.time() - sent_at >= self.E3_RESEND_AFTER:
                        if presses >= self.E3_PRESSES:
                            break
                        self.send_resonance_key()
                        sent_at = time.time()
                        presses += 1
            elif time.time() - sent_at >= self.E3_RESEND_AFTER * 3:
                break                       # HUD 一直不在(处决时停/被打飞), 别在这里耗着
            self.macro_wait(self.FORTE_POLL)
        self.record_resonance_use()
        self.macro_mark(f'{tag}强化E?(x{presses},外圈没灭)')
        self.logger.warning(f'{tag}: 强化E 按了 {presses} 次外圈还亮着(一直被吞?), 照放 R')
        return sent_at

    def ga_break(self, tag, t_in):
        """变奏入场那一下处决(E3 之前: 恶魔位格里处决会重置普攻段数)。
        外圈满着 / 切人前读到过提示 -> 盯提示到 t_in + GA_BREAK_UNTIL; 都没有 -> 只读 BREAK_READS 帧。
        按到提示消失, 从生效那一下起等 BREAK_ANIM; 等完提示还在就再来一轮(最多 BREAK_ROUNDS)。
        不走框架的 task.f_break(): 那个会连点左键、至少卡 0.5s。返回处决了没有"""
        ring = self.break_ring()
        armed = ring or time.time() - self.break_seen_at < self.BREAK_SEEN_FRESH
        deadline = t_in + self.GA_BREAK_UNTIL if armed else 0.0
        start = time.time()
        reads = 0
        while True:
            self.task.next_frame()
            if self.hud_ok() and self.break_prompt():
                break
            reads += 1
            if reads >= self.BREAK_READS and time.time() >= deadline:
                if armed:
                    self.macro_mark(f'{tag}处决提示没回来(圈{int(ring)},{time.time() - start:.2f}s)')
                    self.logger.info(f'{tag}: 外圈满着/切人前见过提示, 但到场 {self.GA_BREAK_UNTIL:.1f}s 内中间的 F 提示'
                                     f'一直没出来(离怪太远/镜头没转回来?), 不处决')
                return False
            self.macro_wait(0.02)
        waited = time.time() - start
        total = 0
        for rnd in range(self.BREAK_ROUNDS):
            hit_at, n = self.break_press(tag)
            total += n
            try:
                self.task.can_break = False
            except Exception:
                pass
            if hit_at is None:
                self.macro_mark(f'{tag}处决?(F x{total},提示一直在)')
                self.logger.warning(f'{tag}: 处决 F 按了 {total} 下提示一直没消失(一直被吞?), 不等了')
                return False
            self.wait_until_time(hit_at + self.BREAK_ANIM)
            again = False
            for _ in range(2):
                self.task.next_frame()
                if self.hud_ok() and self.break_prompt():
                    again = True
                    break
            if not again:
                self.macro_mark(f'{tag}处决(F x{total},等提示{waited:.2f}s,圈{int(ring)}->{int(self.break_ring())})')
                return True
            self.macro_mark(f'{tag}处决没上(提示还在, 第{rnd + 2}轮)')
        self.logger.warning(f'{tag}: 处决按了 {self.BREAK_ROUNDS} 轮提示还在, 不等了')
        return False

    def burst_mash(self, until):
        """点普攻到 until 之前 BURST_PRE_DODGE。落后了就以当下重新对齐, 不补点。返回点了几下。"""
        n = 0
        next_tap = time.time()
        stop = until - self.BURST_PRE_DODGE
        while time.time() < stop:
            if time.time() >= next_tap:
                self.tap()
                n += 1
                next_tap = max(time.time(), next_tap + self.BURST_A_INTERVAL)
            self.macro_wait(min(0.02, max(0.0, stop - time.time())))
        self.wait_until_time(until)
        return n

    def burst_hud_read(self, until):
        """闪避后、接普攻前读一帧 HUD, 再等到 until。
        爆发 8s + 收尾重击 1.7s 整段不读屏, 会撞上 HUD_LOST_ABORT(9s)看门狗把这一圈当成关卡结束扔掉"""
        self.task.next_frame()
        self.hud_ok()
        self.wait_until_time(until)

    def ga_burst(self, tag):
        """恶魔连段: 点普攻 -> 第一脚闪避 -> 点普攻 -> 第二脚闪避 -> 点一小段。返回时手是松的。"""
        start = time.time()
        n = self.burst_mash(start + self.BURST_DODGE_1)
        self.dodge(f'{tag}第1脚(A x{n})')
        d1 = time.time()
        self.burst_hud_read(d1 + self.DODGE_TO_A)
        n2 = self.burst_mash(d1 + self.BURST_DODGE_2)
        self.dodge(f'{tag}第2脚(A x{n2})')
        d2 = time.time()
        self.burst_hud_read(d2 + self.DODGE_TO_A)
        n3 = self.burst_mash(d2 + self.BURST_TAIL)
        self.macro_mark(f'{tag}爆发({time.time() - start:.2f}s,A x{n + n2 + n3})')

    def seg_ga_open(self, mo, tag):
        """启动轴第一格: 嘉按住重击到第三段 -> 切莫宁。"""
        if self.cur is not self:
            self.need(self.switch_retry(self, f'{tag}切嘉'), '-> galbrena')
        self.ga_hold(self.GA_OPEN_HOLD, tag, direct=True)
        self.need(self.switch_retry(mo, f'{tag}切莫'), 'galbrena -> mornye')

    def seg_ga_mid(self, lu, tag):
        """露帕第一格之后: 嘉按住重击到第三段(子弹脱手就走) -> 切回露帕。"""
        self.ga_hold(self.GA_MID_HOLD, tag, direct=True)
        self.need(self.switch_retry(lu, f'{tag}切露'), 'galbrena -> lupa')

    def seg_ga_burst(self, mo, tag):
        """嘉(变奏): Q -> A -> 处决 -> 强化E -> R -> 爆发 -> 3 段重击 -> 协奏满 -> 变奏莫。"""
        t_in = time.time()
        self.press_echo(self, self.GA_Q_CAP, f'{tag}Q')
        if self.has_intro:
            self.wait_until_time(t_in + self.GA_INTRO_NO_INPUT)
        for _ in range(self.GA_FILL_TAPS):
            self.tap()
        self.ga_break(tag, t_in)
        sent_at = self.ga_cast_e3(tag)
        if sent_at is not None:
            self.wait_until_time(sent_at + self.GA_E_TO_R)
        if self.fire_r_sure(self, f'{tag}R', self.GA_R_WINDOW):
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.GA_R_ANIM_MIN)
        else:
            self.logger.info(f'{tag}: 嘉贝 R 没放出来, 照打恶魔连段(没有强化状态)')
        self.macro_wait(self.LIB_TO_BURST)
        self.ga_burst(tag)
        self.ga_hold(self.GA_END_HOLD, f'{tag}收尾')
        full = self.read_con(1.0, settle=0.20) >= 1.0 or self.con_wait('嘉贝', self.GA_CON_CAP, f'{tag}协奏')
        if not full:
            self.logger.warning(f'{tag}: 嘉贝协奏没满就切莫宁 —— 莫宁不是变奏进来, 循环轴第一格走地面升空')
        self.need(self.switch_retry(mo, f'{tag}切莫', assume_con=full), 'galbrena -> mornye')
        self.mor_intro_at = time.time() if full else -99.0

    # ==================== 莫宁(读条 / 按住逐字抄自爱琳莫 v2.0.0) ====================

    def mor_e_air(self):
        """E 位是空中 E(分布式阵列) = 莫宁在观测模式。只读屏不按键。"""
        import base64
        import cv2
        import numpy as np
        if not hasattr(self, '_mor_glyphs'):
            self._mor_glyphs = tuple(np.frombuffer(base64.b64decode(g), dtype=np.float16).astype(np.float32)
                                     for g in (self.MOR_E_AIR_GLYPH, self.MOR_E_GND_GLYPH))
            n = 16
            yy, xx = np.mgrid[0:n, 0:n]
            self._mor_mask = ((xx - (n - 1) / 2) ** 2 + (yy - (n - 1) / 2) ** 2) <= (n / 2) ** 2
        frame = self.task.frame
        if frame is None:
            return False
        h, w = frame.shape[:2]
        s = w / 3840
        crop = frame[int(1861 * s):int(1993 * s), int(3180 * s):int(3312 * s)]
        if crop.size == 0:
            return False
        g = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (16, 16), interpolation=cv2.INTER_AREA)
        v = g.astype(np.float32)[self._mor_mask]
        v = v - v.mean()
        nrm = float(np.linalg.norm(v))
        if nrm < 1e-6:
            return False
        v = v / nrm
        return float(v @ self._mor_glyphs[0]) >= self.MOR_E_GLYPH_OK

    def v6_tap(self):
        """一下普攻: 按住 V6_TAP_HOLD 再松(task.click 只按 10ms 会被吞), 之后等到 V6_TAP_INTERVAL。"""
        self.task.mouse_down()
        self.macro_wait(self.V6_TAP_HOLD)
        self.task.mouse_up()
        self.macro_wait(max(0.0, self.V6_TAP_INTERVAL - self.V6_TAP_HOLD))

    def mor_forte_fill(self):
        """莫宁回路条(相对动能/静质量能)读数 0~1: 从左数连续亮着的格子。只读屏不按键。

        条在 HP 条上方, 36 格(中间第 17/18 格是菱形装饰, 永远亮, 跳过)。每格取 min(R,G,B) 的峰值:
        没亮 115~155、亮 189~207、满了整条发光 250+ —— 用 min 通道, 特效白闪也不会把暗格读亮。
        10-11 录屏 2K 原图和缩到 1080p 逐帧读数一致(0.05s 一帧, 293.0~297.4 全段)。
        """
        frame = self.task.frame
        if frame is None:
            return 0.0
        s = frame.shape[1] / 3840
        band = frame[int(2000 * s):int(2012 * s) + 1, int(1632 * s):int(2175 * s)]
        if band.size == 0:
            return 0.0
        col = band.min(axis=2).max(axis=0)
        w = len(col) / 36
        n = 0
        for i in range(36):
            if i in (17, 18):
                continue
            if int(col[int(i * w):int((i + 1) * w)].max()) >= self.MOR_FORTE_LIT:
                n += 1
            else:
                break
        return n / 34

    def mor_mass_fill(self):
        """莫宁地面回路条(静质量能)读数 0~1: 和 mor_forte_fill 同一条带子, 地面形态是橙色格。只读屏不按键。
        HSV 色相 3~18、饱和度 >= 120、亮度 >= 150 算亮(空中的白格饱和度低, 读不进来)。中间第 17/18 格跳过。
        10-12 00:57 录屏 2K 原图和缩到 1080p 逐帧一致: A1 后 0.21, A2 起手后 0.62, A3 后 1.0"""
        import cv2
        frame = self.task.frame
        if frame is None:
            return 0.0
        s = frame.shape[1] / 3840
        band = frame[int(2000 * s):int(2012 * s) + 1, int(1632 * s):int(2175 * s)]
        if band.size == 0:
            return 0.0
        hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
        lit = ((hsv[..., 0] >= 3) & (hsv[..., 0] <= 18) & (hsv[..., 1] >= 120) & (hsv[..., 2] >= 150)).any(axis=0)
        w = len(lit) / 36
        n = 0
        for i in range(36):
            if i in (17, 18):
                continue
            if lit[int(i * w):int((i + 1) * w)].any():
                n += 1
            else:
                break
        return n / 34

    def v6_hold_until_cannon(self, pre_mid=False, pre_high=False):
        """按住到重击·反演出手: 回路条见过 >=0.6 之后又连 2 帧 <=0.1(出手那一下消耗全部相对动能)。
        返回时手还按着(交给 v6_hold_until_con 接着按); 中途抛异常才松。
        pre_high=True: R 演出里就按住了, HUD 回来那一刻炮可能已经出手(条已清空), 不用先见到高位, 也不卡最短时间"""
        start = time.time()
        low_streak = 0
        seen_high = pre_high
        seen_mid = pre_mid
        samples = []
        how = 'CAP'
        last_sample = 0.0
        try:
            while time.time() - start < self.V6_CANNON_CAP:
                self.task.next_frame()
                fill = self.mor_forte_fill()
                # 高位见一帧就算: 浮游炮到账那一下条可能一帧内满了又被清空; min 通道读数不吃白闪。
                # 但要先见过中间值(0.15~0.9): 条是一格格涨上去的, 从 0 直接跳 1 再回 0 是整条发光动画
                # (10-11 20:10 实机循环轴: 0.00 -> 1.00 -> 0.00 被当成出炮)
                if 0.15 <= fill < 0.9:
                    seen_mid = True
                if fill >= 0.6 and seen_mid:
                    seen_high = True
                low_streak = low_streak + 1 if (seen_high and fill <= 0.1) else 0
                now = time.time() - start
                if now - last_sample >= 0.1 and len(samples) < 40:
                    samples.append(f'{now:.2f}:{fill:.2f}')
                    last_sample = now
                if low_streak >= 2 and (pre_high or now >= self.V6_CANNON_MIN):
                    how = 'fired'
                    break
                self.macro_wait(0.02)
        except Exception:
            self.release_attack()
            raise
        self.macro_mark(f'cannon({how},{time.time() - start:.2f})')
        self.logger.info(f'v6 mornye cannon {how}: forte {" ".join(samples)}')
        return how == 'fired'

    def v6_con_read(self):
        """现读协奏, 顺带判"面积已满但环没认成闭合"(框架给 0.99 哨兵、或 0.985~0.999)。
        返回 (读数, 面积比是否已满)。面积比 = 环面积 / 上次见到闭合满环时记的面积。"""
        task = self.task
        con = float(task.get_current_con())
        if not (0.985 <= con < 1.0):
            return con, False
        try:
            from src.task.BaseCombatTask import con_colors
            idx = task._ensure_ring_index()
            full = float(task.con_full_size.get(str(idx), 0) or 0)
            if full <= 0:
                return con, False
            cropped = task.get_con_box().crop_frame(task.frame)
            area, _ = task.count_rings(cropped, con_colors[idx],
                                       1500 / 3840 / 2160 * task.screen_width * task.screen_height)
            ratio = area / full
        except Exception:
            return con, False
        self._v6_con_ratio = ratio
        lo, hi = self.V6_CON_SOFT_RATIO
        return con, lo <= ratio <= hi

    def v6_ratio_tag(self, con, soft):
        """日志里 0.985~0.999 的读数后面带上面积比(/1.007), 当满了的再加 *"""
        if 0.985 <= con < 1.0:
            return f'/{getattr(self, "_v6_con_ratio", 0.0):.3f}{"*" if soft else ""}'
        return ''

    def v6_hold_until_con(self):
        """按着等莫宁协奏满(读到 1.0 一帧, 或面积已满连 V6_CON_SOFT_FRAMES 帧), 满了马上松手。上限照松。"""
        start = time.time()
        soft_n = 0
        how = 'CAP'
        samples = []
        last_sample = -1.0
        try:
            while time.time() - start < self.V6_CON_CAP:
                self.task.next_frame()
                con, soft = self.v6_con_read()
                now = time.time() - start
                if now - last_sample >= 0.1 and len(samples) < 40:
                    samples.append(f'{now:.2f}:{con:.3f}{self.v6_ratio_tag(con, soft)}')
                    last_sample = now
                if con >= 1.0:
                    how = 'full'
                    break
                soft_n = soft_n + 1 if soft else 0
                if soft_n >= self.V6_CON_SOFT_FRAMES:
                    how = 'soft'
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'con({how},{time.time() - start:.2f})')
        self.logger.info(f'v6 mornye con {how}: {" ".join(samples)}')
        return how != 'CAP'

    def mor_air_reads(self, frames):
        """HUD 在场时连 frames 帧读到空中 E(观测模式)。HUD 不在的帧不算, 最多读 frames*3 帧。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.hud_ok():
                if not self.mor_e_air():
                    return False
                hits += 1
                if hits >= frames:
                    return True
            self.macro_wait(0.02)
        return False

    def mor_ground_fill(self, tag):
        """地面普攻到静质量能满(见 MOR_GND_*)。返回最后读到的条。"""
        start = time.time()
        n = 0
        fill = 0.0
        a2_at = None
        full_n = 0
        how = 'CAP'
        next_tap = start
        while time.time() - start < self.MOR_GND_CAP:
            self.task.next_frame()
            fill = self.mor_mass_fill() if self.hud_ok() else 0.0
            if a2_at is None and fill >= self.MOR_GND_A2_FILL:
                a2_at = time.time()
            full_n = full_n + 1 if fill >= self.MOR_GND_FULL else 0
            if full_n >= 2:
                how = 'full'
                break
            now = time.time()
            if a2_at is None:
                if now >= next_tap:
                    self.v6_tap()
                    n += 1
                    next_tap = time.time()
                    continue
            elif now >= max(next_tap, a2_at + self.MOR_GND_A3_FROM):
                self.v6_tap()
                n += 1
                next_tap = time.time() + self.MOR_GND_TAP_GAP
                continue
            self.macro_wait(0.01)
        a2_txt = f'{a2_at - start:.2f}' if a2_at else '-'
        self.macro_mark(f'{tag}地面A({how},A x{n},{time.time() - start:.2f},a2@{a2_txt},mass {fill:.2f})')
        return fill

    def mor_rise(self, tag):
        """按住出位势转换: 一直按到 E 位变成空中 E(连 MOR_AIR_CONFIRM 帧), 松手。返回升没升空。"""
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}升空按住')
        t0 = time.time()
        air_n = 0
        air = False
        try:
            while time.time() - t0 < self.MOR_RISE_CAP:
                self.task.next_frame()
                air_n = air_n + 1 if (self.hud_ok() and self.mor_e_air()) else 0
                if air_n >= self.MOR_AIR_CONFIRM:
                    air = True
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}升空({"air" if air else "CAP"},{time.time() - t0:.2f})')
        return air

    def mor_r(self, mo, window, tag):
        """莫宁 R(观测模式里有谐振场 -> 强谐振场), 放出去就等演完。返回放没放出去。"""
        fired = self.fire_r_sure(mo, f'{tag}R', window)
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.MOR_R_ANIM_MIN)
        else:
            self.logger.info(f'{tag}: 莫宁 R 没放出来(能量/冷却), 照放 E 按住')
        return fired

    def mor_con_safe(self):
        """按住期间读一帧协奏(不发键)。HUD 不在 / 读失败返回 0。"""
        self.task.next_frame()
        if not self.hud_ok():
            return 0.0
        try:
            return float(self.task.get_current_con())
        except Exception:
            return 0.0

    def mor_tail(self, mo, lu, tag):
        """R 演完: E -> 按住(观测 A 循环, 满了自动重击·反演) -> 协奏满松手 -> Q -> 变奏露帕。
        E 后按住到出炮、再按着等协奏, 都是爱琳莫的写法; 松手在 v6_hold_until_con 里。"""
        self.press_sure(mo.send_resonance_key, mo.resonance_available, self.MOR_E_CAP, f'{tag}E')
        full = False
        for i in range(1 + self.MOR_REHOLD):
            if i:
                # 出炮/协奏都撞上限: 按住多半被打断了。先看协奏(可能只是读条没读到), 不满才重新按住
                self.wait_hud_up(cap=self.RETRY_HUD_CAP, tag=f'{tag}重按前HUD')
                if self.read_con(1.0, settle=0.20) >= 1.0:
                    full = True
                    break
            self.task.mouse_down()
            self.holding_attack = True
            self.macro_mark(f'{tag}按住' if i == 0 else f'{tag}重新按住#{i}')
            try:
                con0 = self.mor_con_safe()
                fired = self.v6_hold_until_cannon()
                dead = (not fired and i < self.MOR_REHOLD
                        and self.mor_con_safe() < con0 + self.MOR_DEAD_HOLD_CON)
                if not dead:
                    full = self.v6_hold_until_con()
            finally:
                self.release_attack()
            if dead:
                self.macro_mark(f'{tag}按住没在打(协奏没涨)')
                continue
            if full or fired:
                break
        self.press_echo(mo, self.MOR_Q_CAP, f'{tag}Q')
        if not full:
            full = self.read_con(1.0, settle=0.20) >= 1.0
        if not full:
            self.logger.warning(f'{tag}: 莫宁协奏没满就切露帕 —— 露帕不是变奏进来')
        self.need(self.switch_retry(lu, f'{tag}切露', assume_con=full), 'mornye -> lupa')

    def seg_mor_open(self, mo, lu, tag):
        """莫宁地面进场: 普攻到静质量能满 -> 升空 -> R -> 后面同循环。"""
        self.mor_ground_fill(tag)
        if not self.mor_rise(tag):
            # 位势转换会把静质量能清空: 条还满着 = 按住被打断、没出去, 松开重按一次。
            # 条空了说明出去了、只是 E 图标没读到, 不重按
            self.task.next_frame()
            if self.hud_ok() and self.mor_mass_fill() >= self.MOR_GND_FULL:
                self.mor_rise(f'{tag}重按')
        self.mor_r(mo, self.MOR_R_WINDOW_GND, tag)
        self.mor_tail(mo, lu, tag)

    def seg_mor_loop(self, mo, lu, tag):
        """循环轴第一格。刚被嘉变奏进来 -> 从到场起按 R; 否则先切到她(宏散过), 在观测模式里就直接 R, 在地面就走启动格。"""
        fresh = self.cur is mo and time.time() - self.mor_intro_at < self.MOR_INTRO_FRESH
        if fresh:
            self.macro_mark(f'{tag}变奏入场')
            self.mor_r(mo, self.MOR_R_WINDOW_INTRO, tag)
            self.mor_tail(mo, lu, tag)
            return
        if self.cur is not mo:
            # 宏散在嘉贝收尾之后(协奏已满)时这一下就是变奏: 69F 才进观测模式, 马上读 E 图标会读成地面
            self.need(self.switch_retry(mo, f'{tag}切莫', read_con=True), '-> mornye')
            if mo.has_intro:
                self.mor_intro_at = time.time()
                self.macro_mark(f'{tag}变奏入场(宏散过)')
                self.mor_r(mo, self.MOR_R_WINDOW_INTRO, tag)
                self.mor_tail(mo, lu, tag)
                return
        if self.mor_air_reads(self.MOR_ROUTE_READS):
            self.macro_mark(f'{tag}在观测模式')
            self.mor_r(mo, self.MOR_R_WINDOW_GND, tag)
            self.mor_tail(mo, lu, tag)
            return
        self.macro_mark(f'{tag}在地面(走启动格)')
        self.seg_mor_open(mo, lu, tag)

    # ==================== 露帕 ====================

    def lu_wolf_icon(self):
        """E 图标是两个狼魂的那张(狼舞的决意 / ·极)。读当前帧。"""
        try:
            hit = bool(self.task.find_one('lupa_wolf_icon2', threshold=self.LU_WOLF_THRESHOLD))
        except Exception:
            hit = False
        if hit:
            self.lu_icon_ever = True
        return hit

    def lu_wolves(self, tag, cap):
        """一直点普攻到 E 图标换成两个狼魂(连 LU_WOLF_CONFIRM 帧)。返回读没读到。"""
        start = time.time()
        n = 0
        hits = 0
        next_tap = start
        while time.time() - start < cap:
            if time.time() >= next_tap:
                self.tap()
                n += 1
                next_tap = time.time() + max(0.0, self.LU_AIR_A_EVERY - self.A_TAP)
            self.task.next_frame()
            hits = hits + 1 if (self.hud_ok() and self.lu_wolf_icon()) else 0
            if hits >= self.LU_WOLF_CONFIRM:
                self.macro_mark(f'{tag}两个狼魂(A x{n},{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}狼魂没读到(A x{n},CAP)')
        return False

    def lu_podi(self, lu, t_fire, tag):
        """R 演完点普攻到破敌出手(见 LU_POD_*)。t_fire = R 演出确认的时刻。返回点了几下。"""
        n = 0
        seen = False
        gone = 0
        how = 'timer'
        next_tap = time.time()
        while True:
            now = time.time()
            if not seen and now >= t_fire + self.LU_POD_UNTIL:
                break
            if now >= t_fire + self.LU_POD_CAP:
                how = 'CAP'
                break
            if now >= next_tap:
                self.tap()
                n += 1
                next_tap = time.time() + max(0.0, self.LU_POD_EVERY - self.A_TAP)
            self.task.next_frame()
            if self.hud_ok():
                if lu.is_forte_full():
                    seen = True
                    gone = 0
                elif seen and time.time() >= t_fire + self.LU_POD_MIN:
                    gone += 1
                    if gone >= 2:
                        how = 'bar'
                        break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}破敌({how},A x{n},{time.time() - t_fire:.2f}s)')
        if how == 'CAP':
            self.logger.warning(f'{tag}: 狼焰条点到 {self.LU_POD_CAP:.1f}s 还满着, 破敌可能没出(一直被打断?)')
        return n

    def seg_lu1(self, lu, tag):
        """露帕(变奏): R(能量没满就先点普攻攒满) -> 破敌 -> 空中 Q -> 空中 A / 焰袭 / 锐爪 -> 两个狼魂 -> 切嘉。"""
        fired = self.fire_r_sure(lu, f'{tag}R', self.LU_R_WINDOW)
        if not fired:
            fired = self.lu_r_fill(lu, tag)
        t_fire = time.time()
        if fired:
            self.wait_hud_back(tag=f'{tag}R演出', min_gone=self.LU_R_ANIM_MIN)
            self.lu_podi(lu, t_fire, tag)
            self.press_echo(lu, self.LU_Q_CAP, f'{tag}Q')
            if not self.lu_wolves(tag, self.LU_WOLF_CAP):
                self.lu_wolf_fix(lu, tag, self.LU_WOLF_FIX_CAP)
        else:
            self.logger.info(f'{tag}: 露帕 R 攒了 {self.LU_R_FILL_CAP:.1f}s 能量还是没放出来, 没有破敌/沸腾, 点 {self.LU_NO_R_A:.1f}s 普攻就切')
            self.tap_until(time.time() + self.LU_NO_R_A, self.LU_AIR_A_EVERY)
        self.need(self.switch_retry(self, f'{tag}切嘉'), 'lupa -> galbrena')

    def lu_r_fill(self, lu, tag):
        """R 能量没满(见 LU_R_FILL_CAP): 点普攻攒到 R 图标连 LIT_FRAMES 帧亮再放。到上限还暗着就不放。返回放出去没有。"""
        start = time.time()
        hits = taps = 0
        next_tap = start
        while time.time() - start < self.LU_R_FILL_CAP:
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.LU_AIR_A_EVERY - self.A_TAP)
            self.task.next_frame()
            try:
                lit = self.hud_ok() and bool(lu.liberation_available())
            except Exception:
                lit = False
            hits = hits + 1 if lit else 0
            if hits >= self.LIT_FRAMES:
                self.macro_mark(f'{tag}R能量攒满(A x{taps},{time.time() - start:.2f}s)')
                return self.fire_r_sure(lu, f'{tag}攒满R', self.R_RETRY_WINDOW)
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}R能量没攒满(A x{taps},CAP)')
        return False

    def lu_cast_ee(self, lu, tag):
        """强化E(狼舞的决意·极): 见到两个狼魂的图标才按, 按到图标消失(连 2 帧)。返回放没放出去。"""
        start = time.time()
        seen = False
        gone = 0
        n = 0
        while time.time() - start < self.LU_EE_CAP:
            self.task.next_frame()
            if self.hud_ok():
                if self.lu_wolf_icon():
                    seen = True
                    gone = 0
                elif seen:
                    gone += 1
                    if gone >= 2:
                        lu.record_resonance_use()
                        self.macro_mark(f'{tag}强化E出手(x{n},{time.time() - start:.2f}s)')
                        return True
                elif time.time() - start >= self.LU_EE_SEEN_CAP:
                    break
            if seen and gone == 0:
                lu.send_resonance_key()
                n += 1
            self.macro_wait(self.LU_EE_INTERVAL)
        if seen:
            self.macro_mark(f'{tag}强化E?(x{n},图标没消失)')
            return n > 0
        self.macro_mark(f'{tag}没见到两个狼魂(不按 E)')
        return False

    def lu_wolf_fix(self, lu, tag, cap):
        """补狼魂(见 LU_HEAVY_HOLD): 狼焰条满就长按出重击(锐爪/狼咬 +1 狼魂), 不满就点普攻攒;
        两个狼魂图标连 LU_WOLF_CONFIRM 帧亮就停。返回读没读到图标。
        图标一次都没读到过、又已经白补了 LU_FIX_BLIND_LIMIT 次就不再补: 那是图标读不到, 不是没狼魂, 补了每圈白打几发重击。"""
        if not getattr(self, 'lu_icon_ever', False) and getattr(self, 'lu_fix_fails', 0) >= self.LU_FIX_BLIND_LIMIT:
            self.macro_mark(f'{tag}不补狼魂(图标从没读到过)')
            return False
        start = time.time()
        hits = taps = heavies = 0
        next_tap = start
        heavy_ok_at = start
        while time.time() - start < cap:
            self.task.next_frame()
            hud = self.hud_ok()
            if hud and self.lu_wolf_icon():
                hits += 1
                if hits >= self.LU_WOLF_CONFIRM:
                    self.macro_mark(f'{tag}补到两个狼魂(重击x{heavies},A x{taps},{time.time() - start:.2f}s)')
                    return True
                self.macro_wait(0.01)
                continue
            hits = 0
            if hud and time.time() >= heavy_ok_at and lu.is_forte_full():
                self.attack_down(False)
                try:
                    self.macro_wait(self.LU_HEAVY_HOLD)
                finally:
                    self.release_attack()
                heavies += 1
                heavy_ok_at = time.time() + self.LU_HEAVY_SETTLE
                next_tap = heavy_ok_at
                continue
            if time.time() >= next_tap:
                self.tap()
                taps += 1
                next_tap = time.time() + max(0.0, self.LU_AIR_A_EVERY - self.A_TAP)
            self.macro_wait(0.01)
        self.lu_fix_fails = getattr(self, 'lu_fix_fails', 0) + 1
        self.macro_mark(f'{tag}补狼魂没成(重击x{heavies},A x{taps},CAP)')
        return False

    def seg_lu2(self, lu, tag):
        """露帕(普通切进来): 强化E -> 协奏满 -> 变奏嘉。到场没见到两个狼魂的图标就先补狼魂再按。"""
        if not self.lu_cast_ee(lu, tag) and self.lu_wolf_fix(lu, tag, self.LU2_WOLF_FIX_CAP):
            self.lu_cast_ee(lu, f'{tag}补')
        full =self.read_con(1.0, settle=0.20) >= 1.0 or \
            self.con_wait('露帕', self.LU_CON_CAP, f'{tag}协奏', poke_after=self.LU_CON_POKE_AFTER)
        if not full:
            self.logger.warning(f'{tag}: 露帕协奏没满就切嘉贝 —— 嘉贝不是变奏进来')
        self.note_break_prompt()
        self.need(self.switch_retry(self, f'{tag}切嘉', assume_con=full), 'lupa -> galbrena')

    # ==================== 一圈 ====================

    def run_cycle(self, lu, mo, opener, tag):
        """启动轴: 嘉 -> 莫(地面) ...; 循环轴: 莫(变奏) ...; 之后: 露 -> 嘉 -> 露 -> 嘉(变奏) -> 莫变奏入场、HUD 在场。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if opener:
            self.seg_ga_open(mo, tag=f'{tag}嘉0')
            self.seg_mor_open(mo, lu, tag=f'{tag}莫')
        else:
            self.seg_mor_loop(mo, lu, tag=f'{tag}莫')
        self.seg_lu1(lu, tag=f'{tag}露1')
        self.seg_ga_mid(lu, tag=f'{tag}嘉1')
        self.seg_lu2(lu, tag=f'{tag}露2')
        self.seg_ga_burst(mo, tag=f'{tag}嘉2')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def macro_enter(self, entry_char):
        lu = self.teammate('Lupa')
        mo = self.teammate('Mornye')
        if lu is None or mo is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        for char in (self, lu, mo):
            char.check_f_on_switch = False
        self.macro_start()
        self.timeline_logged = False
        self.hud_seen_at = time.time()
        self.hud_watch = True
        return lu, mo, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'jlm timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴: (切嘉) -> 一圈(嘉开场重击 + 莫宁地面升空)。返回时莫宁刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        lu, mo, previous = got
        self.logger.info(f'jlm opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            self.run_cycle(lu, mo, opener=True, tag='op')
            self.logger.info(
                f'jlm opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'jlm opener broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是刚变奏进来的莫宁(宏散过)就先切给她, 第一格按她在不在观测模式选路。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        lu, mo, previous = got
        try:
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(lu, mo, opener=False, tag=f'L{n}')
            self.logger.info(
                f'jlm loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'jlm loop broke ({e}) — timeline: {self.macro_timeline()}')
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
                self.logger.info('jlm: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        self.logger.info(f'jlm: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_jlm:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时不让框架把嘉切上来(宏自己切)。"""
        if self.is_in_jlm and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
