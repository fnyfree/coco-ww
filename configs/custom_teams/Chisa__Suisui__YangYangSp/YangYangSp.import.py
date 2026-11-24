import base64
import ctypes
import math
import os
import threading
import time

import cv2
import numpy as np

from src.Labels import Labels
from src.char.BaseChar import SwitchPriority
from src.char.YangYangSp import YangYangSp as NativeYangYangSp
from src.combat.MacroKit import MacroKit, send_input_left

# ==================== 物理 F 键监视 ====================
# 跟 Changli__Jinhsi__Verina/Changli.py 里那份逐字相同, 说明见那边。
# 几个队伍文件都能启动这个线程, 但每个 task 只启一次(_f_watch_started) —— 所以宏在跑时
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
# 【玩家手动开关自动战斗, 必须从启动轴重跑】(2026-09-22 用户明确要求:
# "以后做的轴, 都要确保不管什么时候开关自动战斗, 都要从一开始启动")。
#
# 为什么不会自己重跑: 手动停任务是【任务停止把战斗循环打断】, 异常一路往上抛,
# `AutoCombatTask` 的 combat_end() 整个跳过 -> `on_combat_end()` 不执行 ->
# 角色实例上的标记全部留着, 其中就有 `opener_armed=False`。重开时【队伍识别没变化的话
# load_chars() 也不会跑】(角色对象被粘性复用), 于是 reset_state() 里那句
# `opener_armed = True` 根本轮不到执行 —— 直接跳到循环轴。
# 实机日志的形状: 一连串 `opener not pending (already armed-down (ran once this combat))`。
#
# 【为什么用线程而不是在 do_perform 里判】: do_perform 只在任务【已经启用】时才会被调到,
# 站在里面永远看不到"刚才关过"。只有独立线程能观察到 enabled 的 True->False->True。
# 判据跟物理 F 键那条路完全一致: 记一个时间戳, 调用方比 `> last_opener` 就放行。
ENABLE_POLL_INTERVAL = 0.2




class YangYangSp(MacroKit, NativeYangYangSp):
    """秧秧·玄翎。秧千穗(千咲/穗穗/秧秧·玄翎)里她兼任【整条轴的持宏者】。

    ================= 轴的出处 =================
    B 站 Sy1is 的秧千穗宏, 2026-09-26 那一版(6 张宏面板截图, 启动宏/循环宏各 3 张),
    加上用户按这套宏跑的实录 E:/轴视频/Wuthering Waves 2026.09.25 - 20.45.45.110.mp4
    (2560x1440 OBS 直录, 【视频时间就是墙钟】; 大招/处决期间游戏倒计时会停)。
    队伍编成 1 穗穗 / 2 千咲 / 3 秧秧·玄翎。
    上一版(09-22 视频宏 + 自己写的 2羽1苍 手动段)备份在
    team_backup/2026-09-26_Chisa__Suisui__YangYangSp_0922宏表版。

    ================= 一大轮的形状 =================
        启动宏(58 对, 表长 40.2s) -> 循环宏(54 对, 38.5s) -> 循环宏 -> ...
    【2羽1苍 这一版写进了宏里】, 两条宏末尾是同一段 9 格:
        变奏切秧秧(等 800) -> E(换苍剑式) -> 长按 2300(1苍) -> Q -> R(5000)
        -> E(换羽剑式) -> 长按 4400(1羽) -> 点一下 -> 长按 4300(第2羽) -> F
    F 那一格见 'F?': 有处决就按(处决取消羽的后摇), 没有就等 YU_NO_BREAK_TAIL 让后摇演完。
    每条宏跑完就交回框架, 下一次 do_perform 还是秧秧, 接着跑循环宏(前三格 W/E/A 还在她身上)。
    跟 09-22 那版逐格比, 前 49 格(到变奏切秧秧为止)顺序完全一样, 只改了几个数:
    千咲大招 3700->3500、穗穗大招 4350->4300、千咲电锯长按 1150->1100、秧秧长按后 650->600、
    循环宏末尾千咲那下 A 400->300、启动宏千咲空中 A 前 550->350, 以及两处切人 50->55/60/80。

    ================= 烟岚窗口(整条轴最卡时间的一段) =================
    穗穗芳菲信 600 满档延奏 -> 飞仙翾舞1 给【当时的前台角色】挂烟岚前置 -> 持有者【满协奏切人】时,
    变奏出场的那个人拿到【烟岚】(攻击最高 +50%, 14 秒, 离场即移除)。
    翾舞1 在【穗穗大招后切秧秧那一下】之后约 6.06s(游戏时间, 帧算 5.97、实机校到 6.02~6.11, 见 YANLAN_XUANWU),
    所以那一刻场上必须是千咲, 千咲还得在那之后打满协奏、变奏切秧秧, 秧秧才是带着烟岚打
    2羽1苍 的(帧数据表穗穗块"特殊能量"、延奏那几行)。早切走白给, 晚了翾舞1 落在秧秧身上。
    实录读倒计时: 这两次切人之间两轮分别是 6.13s / 6.11s —— 宏表本身就是贴着上限写的。
    上一版引擎在这段里多花了约 1.7s(09-23 实机日志换算成游戏时间是 7.8s, 烟岚从来没吃到过):
      千咲强化E 那格 +0.85(等收招 + 先点一下 A 再读图标 + 回头等 0.25s 确认、每轮都补按一次)
      两次"切人后等入场" +0.4, 千咲大招前先点 A 再读能量 +0.35
    所以表里用 'WIN' 标出这一段, 段内这几格换成快速版(见 TIGHT_*), 并在变奏切秧秧那一刻把
    这段花掉的游戏时间打进日志(`烟岚窗口(x.xxs)`, 超了 YANLAN_WINDOW 报警告)。

    ================= 为什么叫"2羽1苍" =================
    秧秧·玄翎有【苍剑式】和【羽剑式】两种形态, 按 E 切换; 重击要【苍翎】满 2 点才放得出来,
    而且两种形态的重击是两个不同技能、各自带一个 25 秒 CD 的暴伤 buff:
      · 重击·苍剑式 -> 巨大气旋, 获得【绝息】, 本次重击暴伤 +160%
      · 重击·羽剑式 -> 命中后自动接空中攻击·释羽风落, 获得【流岚】15 秒,
        并进入【聆风祀声】12 秒 —— 这期间羽剑式普攻替换成普攻·湮象风华(3 段, 算重击伤害)
    苍翎上限 2, 变奏入场 +1 / 共鸣解放 +1 / 剑式流转(流响耗尽时按 E)+1, 重击消耗全部。
    所以一轮的苍翎账是:
        变奏入场+1 -> E 流转+1(满2) -> 重击·苍剑式(1苍, 清空)
        -> R 共鸣解放+1 -> E 流转+1(满2) -> 重击·羽剑式 -> 释羽风落(1羽, 清空, 进聆风祀声)
        -> 聆风祀声里接普攻·湮象风华(第2羽, 不吃苍翎; 第2、3段在低空施放, 实录里就是贴地连挥)
    【第2羽不是可打可不打的收尾】: 重击·羽剑式给的【流岚】(160% 暴伤)要到
    "普攻·湮象风华第3段结束"才移除 —— 少打这几下等于把 buff 剩下的部分扔了。
    宏里这一段【全按时间走, 不读判据】: 1苍 那下长按 2300 本身就盖住了重击 + 收招,
    R 那格 5000 = 演出约 4.2s + 收招 0.8s(实录 HUD 回来到按 E 隔 0.75s, 对上 M_R_TO_E)。

    ================= 两条宏怎么读出来的 =================
    宏面板是一串"按键 + 延时": 键帽【下面带 ▼ = 按下, 上面带 ▲ = 抬起】, 所以
    【按下后面的延时 = 按住多久, 抬起后面的延时 = 松开到下一个键的间隔】。
    启动宏 116 项 = 58 对, 循环宏 108 项 = 54 对, 脚本校验过每一对都是同一个键先按后抬。
    【转录对过表】: 实录右侧头像栏(在场的那个没有数字牌)逐帧扫出来的切人时间线,
    启动宏 16 次、循环宏 15 次切人【按顺序全部对上】, 相邻切人的间隔误差都在 0.1s 以内;
    只有一点累积漂移(宏工具每一步多花几毫秒, 八秒下来慢 0.45s), 跟转录无关。
    【剑式的奇偶也对得上】, 这是第二重验证: 秧秧默认苍剑式, 按一次 E 换一次形态。
    启动宏里她有 3 个 E(第23格 + 末尾两个): 第23格换成羽剑式 -> 末尾第一个 E 换回苍剑式打 1苍
    -> 第二个 E 换成羽剑式打 1羽。循环宏里 4 个(第2、23格 + 末尾两个), 偶数 —— 每轮结束都停在
    羽剑式, 下一轮末尾照样是"先苍后羽"。【宏表要是抄错一个 E, 这个奇偶就崩了】。

    ================= 每一格为什么在那(改表之前先读这一节) =================
    【穗穗】两个形态, 两条【不同】的资源条:
      · 浣尘时(初始): 普攻命中回【水云息】(上限120), E(共鸣技能·浣尘时)向前突进 +40。
        水云息【满】时 E 会换成【醒春潮】—— 放出去就进濯雨时。
      · 带协奏满切进来(变奏技能·泠泠漱玉声)也直接进濯雨时, 同样消耗所有水云息。
      · 濯雨时(15 秒): 普攻·濯雨时/重击命中回【芳菲信】(上限600),
        E(共鸣技能·濯雨时)突进回 100 点。15 秒后自动退出。
      · R(康衢之谣) = 在场上铺【山河水境】30 秒, 【它不消耗芳菲信】。
      · 【芳菲信是"切走"那一下结算的】: 延奏技能·渌水盈盈 退出濯雨时、消耗所有芳菲信,
        按消耗量解锁 200/400/600 三档 —— 【600 才有第3次翾舞和烟岚】。
        所以"三段回路满"这道闸的真正位置是【切走之前】; 宏里 R 之后紧接着就切秧秧,
        卡在 R 前面是等价的。
    【千咲】核心资源是【锯环残响】(上限100), 普攻和共鸣技能命中时攒:
      · R(即刻·归无) 回 40 点残响, 并进入【万缕·汇终】15 秒 —— 锯环·疾攻/终结 +120% 倍率。
      · 残响【满】且【在地面】时, E 换成【齿轨轮回】, 放出去【进入电锯模式】。
        这就是"强化E" —— 没放出来, 后面那串长按全是普通普攻, 整轮千咲的输出作废。
      · 电锯模式里: 【长按】普攻从锯环·疾攻【第2段】起手(短按是第1段), 按住会自动接第3段;
        残响被消耗到 0 之后, 下一次按普攻 = 【锯环·终结】(清空残响, 全队护盾)。
      · 所以宏里那一串读起来是: Q -> R(开 15 秒窗口) -> E(进电锯) -> 切走让别人打
        -> 切回来 长按A 1150ms(疾攻 2->3 段) -> 短按A(锯环·终结) -> 切走。
        【1150 不是随手写的数】: 它要盖住疾攻第2段到第3段那一整段。
    【秧秧·玄翎】见上面"2羽1苍"那一节的苍翎账。

    ================= 保底的边界(2026-09-22 实机教训, 加保底之前先读这一节) =================
    【宏表本身是作者实机验证过的, 保底的职责是"别让打断把整轮废掉", 不是"纠正宏"】。
    分界线只有一条: **这个保底会不会改变游戏状态**。
      · 只花时间的 -> 安全, 可以默认开:
        切人确认(补按数字键)、等入场、等收招、大招连发(R 在 CD/演出里按是空操作)、
        纯读屏的判据(等图标灭)。最坏的后果是这一轮慢一点。
      · 会改状态的 -> 【默认关掉】, 出问题时再按需打开:
        补按 E(秧秧每按一次 E 就翻一次剑式!)、额外点普攻(耗流响/推连段/攒资源)、
        多放一次技能(吃 CD、吃资源)。这类保底一旦判据误判, 【就把本来对的流程搞坏了】,
        而且外表看不出来。
    【踩过的那次】: 给 1苍 加了"没打出来就再走一轮 E+长按"的重试, 看起来很合理 ——
    可秧秧的剑式是有奇偶性的(每条宏里她的 E 个数算好了, 一轮正好翻回原位),
    多按那一次 E 直接把形态翻掉, 于是后面那个"1羽"打出来的其实是【苍剑式重击】,
    绝息/流岚全错位。**少打一发, 远好过打错一发。**

    ================= 打断了怎么办(每一格的判据和兜底) =================
    总开关 SAFETY 关掉 = 全部退化成纯照宏打, 下面这些判据一个都不跑。
      格子                判据                          没成立怎么办
      切人                in_team() 读到目标 index      补按数字键, 最多多等 SWITCH_EXTRA;
                                                        累计 MACRO_ABORT_DESYNC 次【放弃整轮】
      穗穗 R (R3)         suisui_forte3 (芳菲信 600)    没满就接着点普攻攒, 到 FORTE3_WAIT 照放
      任何大招 (R!/R3)    HUD 消失 = 演出开始            连发到出手, 还不出再补救一轮
      千咲 E* (齿轨轮回)  E 图标白像素 >= 0.13          烟岚窗口里: 读一次, 不亮才短攒 TIGHT_CHI_FILL_SAW,
                                                        然后按一次就走, 不回头确认(结果留给下一格)
      千咲长按A           上一格 E* 按下去时亮没亮       没亮就先补一发 E, 再长按
      变奏切秧秧 (^)      协奏真满(1.0)                  没满就接着点普攻, 上限 CON_GATE_WAIT_INTRO
      秧秧 R (RY)         HUD 消失 = 演出开始            连发到出手; 演完再等 M_R_TO_E 收招才按 E
      秧秧 1苍/1羽        【不读判据, 按表里的时长】     M_YU_TRACE 把重击就绪图标读数打进日志
      处决 (F?)           f_break 提示                   没提示就等 YU_NO_BREAK_TAIL, 期间提示亮了照按
    【切人之后紧跟着长按的格子要额外等一下】: 见 HOLD_AFTER_SWITCH(烟岚窗口里不等)。

    ================= 宏里的 1/2/3 =================
    是【作者的队伍槽位】(从实录右侧头像栏读的: 1 穗穗 / 2 千咲 / 3 秧秧·玄翎)。
    这里【按角色翻译回槽位】(char.index + 1), 用户自己的队伍顺序不一样也能跑。

    ================= 这一版的判断都在哪 =================
    两条宏【逐格按时间回放】, 保底挂在格子里(切人确认、大招盯 HUD 补按、资源闸), 关掉 SAFETY
    就是纯照宏打。每条宏都在末尾的处决/后摇之后交回框架。
    【别把返回点挪进任何演出里】—— 演出期间读不到目标, 框架会判脱战、重进战斗、重跑启动轴。
    处决那格是等 HUD 回来才返回的(try_f_break), 满足这一条。
    """

    # ==================== 全局节奏 ====================
    A_INTERVAL = 0.12            # 0.12 是实测的地板, 更密会让空中角色打出下落攻击
    KEY_INTERVAL = 0.10
    # 【普攻必须真的按住几十毫秒才打得出来】: task.click() 那条路按不住 ——
    # BaseWWTask.click 会把不带坐标的调用的 down_time 【无条件覆盖成 0.01】, 底层
    # genshin.do_click 又是"按下-立刻抬起-再睡 down_time"。所以这个文件里所有的"点一下"
    # 都走 tap(), 那是 mouse_down -> 等 -> mouse_up
    A_TAP = 0.06

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    SWITCH_SETTLE = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True

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
    # ---- 变奏那一格的协奏闸 ----
    # 【用户 2026-09-22 实机: "千咲强化E之后攻击也有可能被打断, 要确保持续攻击到
    # 协奏满变奏秧秧"】。两条宏的末行都是 '>YY^', 而 macro_switch 的 con_full
    # 【只喂给框架记账, 一帧屏都不读】—— 表里写了 ^ 它就当变奏记, 协奏到底满没满
    # 根本没人看过。强化E 之后到末行之间隔着 5 格(切秧秧-A-A-切千咲-A~-A-...),
    # 那几下但凡被怪打断, 协奏就是不满的, '>YY^' 于是变成一次【普通平切】:
    #   · 秧秧拿不到变奏入场, 羽挟苍空那 +1 苍翎没有 —— 末尾第一步 1苍 直接打空
    #   · 框架那边却按变奏记了账(has_intro / 变奏冻结), 后面一路错下去
    # 实机对得上: 21:08 那轮 `>Yan^(0.34,晚0.29)`, 21:09 那轮 `>Yan^(0.48,晚0.43)`,
    # 紧跟着的(当时的)手动段两轮都是 `1苍 打了 1 轮都没出手 — 这一轮的绝息(160% 暴伤)丢了`。
    # 【等的时候必须接着点普攻】: 协奏是打中涨的, 干等等不来(wait_con_at_least 自带)。
    # 【门槛必须是 1.0 不能是 0.99】: get_current_con 只有环真闭合才返回 1, 面积够了
    # 但环没闭合会被钳成 0.99 —— 0.99 正是"还差一点"的哨兵值, 拿它当门槛等于没等。
    # 【等不到也照切】: 但这时 con_full 传 False, 让框架按平切记账 —— 宁可记成没变奏,
    # 也好过骗它一次(记错了下游全歪, 而少记一次最多是这一轮保守一点)
    CON_GATE_BEFORE_INTRO = True
    # 【2.0 -> 3.5】(2026-09-22 加了循环宏"千咲变奏交穗穗"那一格之后)。
    # 【上限只影响攒不满的那一格, 满了是立刻放行的】—— 所以放宽不会拖慢已经满的格子:
    # 实机 `>YY^` 那一格是 `Ch等协奏(1.00,ok,A x0,0.01s)`, 一帧就过, 上限多大都一样。
    # 而新加的 `>SUI^` 那一格千咲只打了 E + 1 下 A, 协奏缺口大得多, 2 秒多半不够。
    # 【怎么调】: CAP 的时候 wait_con_at_least 会把整条涨幅轨迹打进日志
    #   (`con only reached 0.78 (< 1.00) in 3.5s — 0.00:0.31 0.11:0.33 ...`),
    # 看那条轨迹的斜率就知道还差几秒, 别凭感觉加
    CON_GATE_WAIT = 3.5          # 等协奏满的上限(秒), 0 或关掉 = 退回老行为
    # 【交给秧秧那一格单独给一个更长的上限】。它是整条轴最不能丢的一次变奏:
    # 秧秧靠变奏技能·羽挟苍空拿 +1 苍翎, 丢了就只剩 E 给的那 1 点, 【凑不满 2 点,
    # 末尾的 1苍 必定打空】, 而重击就绪图标在 1 点时就亮着, 判据完全看不出来。
    # 交给别人那几格丢了只是少一点增益, 不至于让整段作废。
    # 【这一格也是烟岚窗口的终点】: 等协奏的时间算在 6 秒里。但没满就切 = 不是变奏,
    # 烟岚照样拿不到, 还把 +1 苍翎也丢了 —— 所以超了也要等, 不为窗口提前放行
    CON_GATE_WAIT_INTRO = 5.0
    # 【变奏那格的协奏提前读】: 上一格的空等(表里 A 之后等 300ms)快结束时先读一次,
    # 已经满了就直接切, 不在按切人键之前再花一次读屏(0.03~0.05s, 烟岚窗口里每一点都算)。
    # 纯读屏, 不改状态; 协奏只涨不掉, 早 0.1 秒读到满, 切的那一刻一定还是满的
    CON_PEEK_LEAD = 0.10

    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 宏末尾那段(2羽1苍) ====================
    # 【这一段现在写在宏表里, 按表里的时长走】。09-22 那版是自己写的手动段: 靠重击就绪图标
    # 判 1苍/1羽 出没出手、按到图标灭、从第 3 下点按开始探处决……那些常量和方法这次一起删了,
    # 备份里还有(team_backup/2026-09-26_Chisa__Suisui__YangYangSp_0922宏表版),
    # 当时的实机数据也写在那份注释里。留下来的只有【宏表给不了、实机标定过】的几样:
    M_R_WINDOW = 2.00            # 秧秧大招连发的窗口(传给 fire_lib)
    # 【1苍 那下长按提前这么多松手, 后面的 Q、R 跟着提前】(2026-09-27, 用户: 苍出伤到 R 之间感觉能快 0.15s)。
    # 9月26日.mp4 逐帧: 苍重击大伤害数字出来 -> 声骸进 CD 0.47s -> R 演出 0.64s;
    # 作者实录同一段是 0.59s / 0.74s —— 照表按已经比作者紧 0.12s。
    # 帧数据表: 苍重击最后一段判定在第 56F, 派生帧 80F(0.4s 后, 声骸要到这里才按得出来);
    # 我们 Q 落在派生帧之后约 0.1s, 这 0.1s 可以挪掉。
    # 【不给到 0.15】: 秧秧·玄翎的声骸输入缓存只有 0.1s(大招 0.3s) —— 比派生帧早 0.1s 以上按的 Q
    # 直接丢; 早于派生帧那部分 Q 只是被缓存到派生帧才出, 并不会更快, 反而 R(表里紧跟 0.1s)会贴上来,
    # 大招优先级 10 能把刚出手的声骸打断。R 演完会读一次声骸 CD, 日志里出现 `Q没出` 就往回调
    YY_CANG_TRIM = 0.10
    # 【R 演出结束到按 E 的间隔 —— 这一格丢了整个 R 之后就全错】(2026-09-22 实机)。
    # HUD 回来 != 能出招, 收招还在演, E 按在这段里会被整个吃掉; E 又不像 R 那样会重试,
    # 剑式没切成羽, 后面那一路长按打出来的就是苍剑式的普攻。
    # 0.80 三处来源对上: 判据驱动旧版实机标定的 LIB_TO_E = 0.8; 09-22 实录逐帧 0.8s;
    # 09-26 这版宏 R 那格 5000 = 演出约 4.2s + 收招, 实录 HUD 回来到按 E 隔 0.75s
    M_R_TO_E = 0.80
    # 【等落地再按 E】(判据驱动旧版的 WAIT_DOWN_BEFORE_E): 大招收尾人可能还在空中,
    # 重击·羽剑式的条件里明写着"处于地面时"
    M_WAIT_DOWN_BEFORE_E = True
    # 按 E 的按下时长(秒)。【别用框架默认的 0.01】: 收招那一下可能吃不到
    M_E_KEY_DOWN = 0.05
    # 秧秧那三下长按期间每隔一会儿读一次重击就绪图标, 【只打日志不打断】。这一段完全按时长走,
    # 实机要是打歪了, trace 是唯一能看出"重击什么时候出手"的东西
    # (09-22 实机读数: 亮 = 苍翎 >= 1, 重击出手清空苍翎就灭; 湮象风华换了图案也读成灭)
    M_YU_TRACE = True
    M_YU_TRACE_EVERY = 0.30
    # 【这一段的左键走 SendInput】(2026-09-26 实机: 有时候打不出第二个羽)。
    # 框架的 mouse_down 在前台也是 PostMessage 投 WM_LBUTTONDOWN —— 按下那一下要是落在
    # 动作里(E 的剑式切换 / 1羽 的落地)被游戏丢掉, 后面一直按着也不算数, 这一整段长按就白按了。
    # 作者的宏是系统级输入, 游戏读得到"现在按着", 不怕这个。
    # 实机读数对得上: 15:27 那轮 1羽 那段(4.4s)重击就绪图标全程亮, 第2羽 那段(4.3s)0.96s 才灭
    # —— 跟 09-22 实测"1羽 在按下后 0.97s 打出来"一样, 只是挪到了第二段: 第一段长按整段没起效,
    # 1羽 在第二段才打出来, 于是就只剩一个羽。
    # 只在游戏窗口在前台时走(SendInput 发给前台), 不在前台自动退回 PostMessage; 关掉 = 全退回
    YY_TAIL_SENDINPUT = True
    # 【没处决就等羽的后摇演完再往下走】(用户 2026-09-26 给的数: 0.5s)。
    # 处决的作用就是取消后摇 —— 没打成的话后摇还在, 直接进下一条宏, 第一格的 W/E 会被吞。
    # 【等的期间照样探处决】: 提示晚几帧才亮的话照按, 按了就不用等完。
    # 【不点普攻】: 后摇期间的点击被丢掉, 后摇一完那一下又会起一段普攻, 挡在 W/E 前面
    YU_NO_BREAK_TAIL = 0.50
    YU_BREAK_POLL = 0.03
    # 【打了处决也要等一下收招, 再交回去跑下一条宏】(2026-09-26 20:53 实机)。
    # 处决那格是等 HUD 回来才返回的, 可 HUD 回来时秧秧还在处决的收招里, 循环宏开头那下
    # W/E 会被吞 —— 而那个 E 是剑式切换, 吞了剑式就反了: 下一轮中段那个"换羽剑式"的
    # 强化E 图案从钩子(苍->羽)变成羽翼(羽->苍), 切千咲前那下 600ms 长按在苍剑式下
    # 打出了苍剑重击(用户看到的"提前放了个苍"), 后面 2羽1苍 全错位。
    # 对得上的还有: 同一局没处决那条路(等 0.5s 后摇)接的循环宏开头都正常;
    # 上一份录屏里处决后开头那个 E 也晚了 0.2~0.3s 才生效。作者手动接循环宏也隔了约 0.5s
    # 【0.50 -> 0】处决改成从第一下 F 起等满 YU_BREAK_ANIM, 不再是"HUD 回来 + 这么久"
    YU_AFTER_BREAK_SETTLE = 0.0

    # ==================== 秧秧的强化E(剑式流转)闸: 'EY' ====================
    # 【帧数据表秧秧块"特殊能量"】: 流响上限 100、开局 100, 普攻【打中】才扣, 大招清空;
    # 流响为 0 时 E 才是强化E(剑式流转: 回 100 流响 + 1 点苍翎), 不为 0 按出来的是
    # 普通E —— 只换剑式, 【不给苍翎】。苍翎满 2 才放得出苍剑/羽剑重击。
    # 【循环宏第 23 格那个"换羽剑式"的 E 按设计是强化E】(作者实录里那一下图标带 "^^")。
    # 它给的那 1 点苍翎, 加上穗穗大招后变奏入场的 1 点, 让切千咲前那下 600ms 长按打出
    # 羽剑重击 —— 羽剑重击命中后接羽强化空中攻击, 解锁 12 秒羽强化A, 这个解锁一直带进
    # 2羽1苍, 【第二个羽就是靠它】。
    # 2026-09-26 录屏(9月26日.mp4)对出来的: 循环#1 这一格按 E 前流响条空、图标有粉环
    # -> 两个羽都出了; 循环#2、#3 这一格流响还剩一半(开局那几下普攻没怎么打中, 怪在别处)
    # -> 普通E、少 1 点苍翎 -> 600ms 长按没出重击 -> 第二个羽没了。
    # 宏是按时间走的, 打没打中它不知道, 所以这里加一道闸: 按 E 前先看图标,
    # 流响没打空就接着点普攻打空, 再按 E。跟穗穗的回路闸、千咲的攒残响是同一类。
    # 【启动宏同一格也是强化E, 也加】: 作者实录启动宏那一格(9.65s)流响已经空了、图标带粉环,
    # 按完流响回满 —— 我第一次核对时时间窗取错了(看成了穗穗在场那一段), 当成了普通E。
    # 09-26 19:12 录屏: 启动宏这一格流响还剩两成多 -> 普通E -> 穗穗变奏后苍翎只有 1 点
    # -> 600ms 长按没出羽剑重击 -> 启动宏每次少一个羽(循环宏加了闸之后两个羽都在)。
    # 判据: E 图标强化态外面有一圈粉/暗红的环、图案右下角有 "^^"。录屏逐帧量的
    # (1080p, 以 box_resonance 中心为圆心, 半径按框高算):
    #   环在 0.64 倍框高处, 真环覆盖 0.73~1.00, 环外(0.9 倍)0~0.04; 普通E 全是 0;
    #   切人瞬间背景整片染红时环里环外一起红(0.25~0.46) —— 所以要求"环外不红";
    #   但作者那一帧强化E 也碰上了染红, 那种靠 "^^" 认(真 0.17~0.41, 普通 0)
    YY_EX_GATE = True
    YY_EX_SETTLE = 0.15          # 刚切进来技能栏还没换过来, 先只读不点这么久(秒)
    # 没亮就点普攻打流响的上限(秒), 到了照按一次 E(剑式奇偶不能乱)。
    # 【2.0 -> 3.5】09-26 18:52/18:54 两局实机截图: 这一格起手流响还剩 65~70%, 点满 2 秒
    # 分别打掉了约 55% / 65% —— 18:54 那局到上限那一刻图标刚亮(截图判据读数 环0.56/^^0.43),
    # 差一帧确认就按了普通E。打空一般要 2~2.5 秒, 只有流响没空时才会用到这个上限
    YY_EX_FILL = 3.50
    YY_EX_FILL_INTERVAL = 0.12
    YY_EX_CONFIRM = 2            # 连读这么多帧都成立才认
    # 【羽强化A 不扣流响】(帧数据表"核心回收"列: 苍A1 -12、羽A3 -26 …… 羽强化A1/A2/A3 那几行是空的)。
    # 切千咲前那下 600ms 长按打出羽剑重击 -> 羽强化空中攻击 -> 解锁 12 秒羽强化A, 这段时间里
    # 点左键打出来的都是羽强化A: 【一滴流响都扣不掉】, 而且第三段会把解锁用掉 —— 第二个羽靠的正是它。
    # 09-26 21:52/21:54 实机: 变奏后流响剩两成, 末尾那道闸点了 27 下普攻流响纹丝不动(前后截图一样),
    # 最后按普通E -> 没苍; 而那 27 下把羽强化A 打掉了 -> 只剩一个羽。
    # 所以:
    #   · 【流响剩得多才在 600ms 长按之前打】('AX'): 那时还是羽A, 打中就扣; 见 YY_DRAIN_ABOVE
    #   · 【变奏后那个 E 不点普攻】('EY0'): 只读图标, 亮就按强化E, 不亮也只能按普通E
    YY_DRAIN_CAP = 1.50          # 600 长按前点普攻打流响的上限(秒), 在烟岚窗口里, 不能给太长
    # 【'AX' 只在流响条还剩超过这个比例时才点普攻, 而且只打到这条线为止】(2026-09-26 晚, 用户:
    # 跑下来总是慢很多、最后一个循环打不完)。原来那版是"不亮就打到空", 实机 20 轮账是这样的:
    #   · 进这一格时流响条剩 0.11~0.29(截图量的, 12 轮), 【轮轮都要点 5~12 下】, 花 0.85~1.64s
    #   · 【更贵的是后面】: 只要这里点过普攻, 千咲最后那格等协奏就从中位 0.8s 变成 2.4s
    #     (点过的 20 轮 1.6~4.9s; 唯一一轮进来就已经空的 22:17 是 0.14s, 烟岚窗口 6.7s)。
    #     为什么会连带没完全坐实; 录屏上看到的是: 作者千咲回场点一下就出锯环·终结(协奏 +45,
    #     一下打满), 我们那几轮千咲回场后锯环残响条停了 0.9 秒才开始往下掉
    #   · 合计每轮多 2.5~3 秒, 烟岚窗口从中位 7.1s 涨到 10.2s(9.3~13.5), 等于轮轮都吃不到烟岚
    # 而不打的时候(AX 之前那 21 轮)变奏后的强化E 18 轮是亮的 —— 正常剩的那两三成, 后面自己就耗掉了。
    # 所以正常那两三成不管它; 前面的普攻被打断/没打中、剩得明显多时才打, 打到这条线就停
    # 【0.35 -> 0.03】(2026-09-27 晚, 用户: 秧秧前面没 A 到怪, 流响没耗完, 该放苍的时候发呆/放成羽)。
    # 千咲开电锯提速之后(CHI_E_QUICK)秧秧连段接上了, 正常轮进这一格流响就是 0(09-27 实机轮轮 0.00),
    # 上面那条"剩两三成后面自己会耗掉"已经不成立: 22:02 那轮进来剩 0.11 没打, 变奏后还是没空;
    # 21:57 那轮 0.48 打到 0.29 就停了, 变奏后也没空。变奏后流响没空的下场见 YY_SKIP_CANG。
    # 现在只要还剩一点、E 图标也没亮(亮 = 流响已空)就打空; 正常轮进来就是 0, 一下不点, 不多花时间
    YY_DRAIN_ABOVE = 0.03
    # 【点了这么久流响一点没降 = 打不到怪, 别再点】(09-27 22:31 那轮怪飞天: 0.92 -> 0.99, 点了 10 下 1.5s)。
    # 这时候点下去全是空挥, 再点只是把千咲那格往后推。
    # 【别给短了】: 正常打流响头 0.7 秒条也只掉 0.04(9月26日录屏 0.29 -> 0.25 -> 1.3s 才空, 条有动画),
    # 所以是 1 秒内一点(0.02)都没掉才算打不到
    YY_DRAIN_NO_PROGRESS = 1.00
    # 流响条在画面上的位置(相对整帧; 1080p 录屏和实机截图上量的): 条从左往右涨, 空了整条换成蓝羽毛
    LX_BAR_X = (0.4245, 0.5678)
    LX_BAR_Y = (0.897, 0.9245)
    YY_EX_READ_ONLY = 0.15       # 'EY0' 只读不点时读多久(秒)
    # 【变奏后流响没空 -> 这一轮的苍放弃, 不按 E、不长按, 直接 Q R】(2026-09-27 晚, 用户: 前面秧秧没
    # A 到怪流响没耗完, 该放苍的时候发呆或者干脆放了羽)。21:57 / 22:02 两轮截图: 变奏后 E 图标是
    # 【灰暗的斜剑】—— 羽剑式下普通 E(常态切换剑式)按不出来(帧数据表: 中断优先级只有 1), 补按也没用;
    # 这时点左键是羽强化A, 不扣流响还吃掉第二个羽; 1苍 那下长按在羽剑式、苍翎只有 1 点, 打出来的是
    # 羽强化A(用户看到的"放了羽"), 人飞在空中 -> R 按了 36~38 下才出、Q 没出, R 后那下 E 又把剑式
    # 翻成苍 -> 1羽 打成苍。
    # 放弃这一个苍之后账是齐的: 苍翎 = 变奏 1 + 大招 1 = 2, 剑式还是羽, R 清空流响;
    # R 后那格 'E羽' 读到已经是羽就不按(见 'E苍'/'E羽'), 1羽 / 第2羽 照常出。
    # 长按那 2.2 秒换成等 YY_SKIP_CANG_WAIT(让变奏入场动作 80F 演完, Q 不被吞)
    YY_SKIP_CANG = True
    YY_SKIP_CANG_WAIT = 0.40
    YY_EX_RING_K = (0.60, 0.64, 0.68)
    YY_EX_OUT_K = (0.87, 0.93)
    YY_EX_RING_MIN = 0.50
    YY_EX_OUT_MAX = 0.15
    YY_EX_RING_MIN_FLOOD = 0.60  # 背景染红时: 环够红 + "^^" 亮也算
    YY_EX_CHEV_MIN = 0.15
    # 【排查用, 定稿就关】'EY' 每一次判定(亮/第一下没亮/到上限)和按完 E 0.2 秒后, 把 E 图标和
    # 流响条那一小块存成 png 到 logs/yy_ex/(每张一两百 KB)。判据在实机上认没认对、E 按没按出去,
    # 靠它对照。按完那一下还会读一次图标: 强化E 真出去了环会灭、流响回满
    # 【2026-09-28 定稿分享, 关掉】排查时改回 True
    YY_EX_SNAPSHOT = False

    # ==================== 秧秧剑式核对('E苍' / 'E羽' / 'EY' / 'EY0') ====================
    # 【按完 E 认一下剑式, 反了补一次 E】(2026-09-27, 用户: 偶然在苍之前放出羽 / 该放苍放成羽)。
    # 09-27 00:27~09:13 的截图对出来 5 轮剑式反了, 都是【那一下 E 没生效】:
    #   · 第 23 格(换羽, 点普攻打流响后紧接着按): 01:12 按完还是钩子(E 没出去);
    #     00:33 / 09:10 钩子没了, 图案却是月牙剑(还在苍剑式)
    #   · 变奏后那格(换苍): 01:17 / 09:05 按完还是斜剑(还在羽剑式)
    # 这 5 轮末尾都是 `Q没出` —— 1苍 那格打成了羽重击, 人在空中放不出声骸。
    # 认法: E 图标中间的图案有四种 —— 月牙剑(苍, 普通) / 钩子(苍, 强化) / 斜剑(羽, 普通) /
    # 羽翼(羽, 强化)。模板是这几天截图里挑的(按完 E 0.2s 那张 + 判定亮那张), 16x16 灰度,
    # 取相关系数最大的一类: 102 张判对 102 张, 苍/羽 两边分差最小 0.063。
    # 分差不够 YY_FORM_MARGIN / 读不到 就当不知道, 不补。
    # 【补错一下剑式就真反了】, 所以: 按完 YY_FORM_WRONG_AFTER 以后(秧秧 E 的输入缓存 0.3s,
    # 被缓存住的那一下这时也该出来了)连着两次读到反才补, 只补 YY_FORM_FIX 次, 补完还不对只报不按
    YY_FORM_CHECK = True
    YY_FORM_MARGIN = 0.08
    YY_FORM_MIN_SCORE = 0.60
    YY_FORM_WRONG_AFTER = 0.45
    YY_FORM_WINDOW = 0.80        # 按完之后最多看这么久(秒), 补按之后重新计
    YY_FORM_FIX = 1
    # 8 张 16x16 灰度模板(uint8, base64): 月牙剑 x2, 钩子 x2, 斜剑 x2, 羽翼 x2
    YY_GLYPHS = (
        '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'
        'AAA='
    )

    # ==================== 烟岚窗口 ====================
    # 表里 'WIN' 那一格之后的第一个切人(穗穗大招后切秧秧)起算, 到 '>YY^' 按下为止,
    # 游戏时间(扣掉大招演出的时停)要在这个秒数以内。【只用来打日志】: 超了报警告, 不改流程。
    # 为什么是 6 秒、为什么这么紧, 见类文档"烟岚窗口"一节
    YANLAN_WINDOW = 6.0
    # 【翾舞1 那一刻】(帧数据表穗穗块): 秧秧变奏入场第 1F 触发穗穗延奏, 延奏动作 79F(1.32s)演完
    # 生成飞仙, 飞仙第 273F(4.55s)翾舞1 —— 算上切人键到入场约 0.1s, 是切人键后 5.97s(游戏时间,
    # 千咲大招的时停不算)。那一刻在场的人拿烟岚前置, 所以要千咲在场、并且在这之后才变奏切秧秧。
    # 用户 09-27: "穗穗延奏后的6s必须要是千咲在场, 然后让秧秧吃到BUFF"。作者实录两轮 6.13/6.11s
    # 正好卡在它后面。【变奏那一格不到 YANLAN_MIN 就让千咲多站一会儿】(早切走 = 前置还没挂上);
    # 晚了(翾舞1 时场上是秧秧)救不回来, 只在日志里标出来(`翾舞1:千咲` / `翾舞1:不是千咲`)
    # 【5.97 -> 6.06, 6.05 -> 6.15】(09-27 22:29 录屏 `9月27日 (1).mp4`): 秧秧变奏后血条上方有没有
    # 烟岚那个图标(粉色六边形、三角图案, 排最左)能直接看出来 —— 启动轮 6.31s、循环#1 6.20s、
    # 循环#2(千咲最后一段从 6.02s 才开始)都吃到了; 作者 6.11/6.13s 也吃到了。
    # 所以翾舞1 落在 6.02 ~ 6.11s 之间(按帧算的 5.97 偏早一点), 取中 6.06; 千咲至少站到 6.15s 再走
    YANLAN_XUANWU = 6.06
    YANLAN_MIN = 6.15
    # 下面三个是窗口里那几格的快速版, 【只在 'WIN' 和 '>YY^' 之间生效】。
    # 切人后紧跟长按的两格(千咲电锯 1100 / 秧秧 600)不额外等入场: 切人确认本身就要
    # 0.09~0.11s, 按下沿落下的时刻已经跟宏表一样(切人键后 100ms 按左键)。
    # 实机要是又出现"切千咲愣一下再打", 往上加 —— 每 0.1 秒吃掉窗口 0.2 秒(两格)
    TIGHT_HOLD_AFTER_SWITCH = 0.0
    # 千咲大招: 【先直接连发 R】这么久(不先读能量、不先点 A, 跟宏一样 Q 后马上按),
    # 没进才边点边攒能量, 攒能量的上限压短。
    # 09-22/23 实机日志: 攒得满的多是 0.10~0.35s(偶尔 1.4~1.9s), 攒不满的点满 3 秒照样
    # NOFIRE —— 上限给长只是在窗口里白站。没放出来的话电锯那一格还有 CHI_R_RETRY 补一次
    TIGHT_R_FIRST_WINDOW = 0.40
    TIGHT_LIB_FILL = 1.00
    # 千咲强化E: 等收招 TIGHT_CHI_R_TO_E -> 读一次图标 -> 不亮才短攒这么久 -> 按一次就走。
    # 【不先点 A、也不回头确认】: 上一版这格先点一下 A 再读、按完等 0.25s 看图标暗没暗、
    # 实机每轮都补按一次, 合计 0.85s —— 而进电锯后 E 换成解弦之眼, 不在 CD 上照样亮,
    # 那个"确认"本来就分不清。按下去之前亮 = 残响满, 就当进了电锯; 没亮的话 A~ 那格补一发
    TIGHT_CHI_FILL_SAW = 0.50
    # 大招 HUD 回来到按强化E。实录里作者是 HUD 回来后约 0.17s 按的(切秧秧在 0.22s);
    # 我们读到 HUD 回来本身就晚一两帧, 再加上读一次图标 0.03s, 所以这里给 0.12 才对得上。
    # 窗口外那份 CHI_R_TO_E(0.20)是先点一下 A 的老流程用的
    TIGHT_CHI_R_TO_E = 0.12

    # ==================== 保底 ====================
    # 总开关。关掉 = 两条宏纯照表回放(切人不确认、大招不补按), 跟人按宏一模一样
    SAFETY = True
    # 【切人一格错, 后面每一格都发在错的人身上】—— 这是整条轴最该有的一道保底。
    # 剩下的 gap 照等, 时序一点不变; 被游戏拒绝(上一个动作/入场动画没演完)就一直补按,
    # 最多再等 SWITCH_EXTRA(游戏的换人 CD 约 1 秒)
    SWITCH_EXTRA = 1.50
    SWITCH_REPRESS = 0.05        # 没到场时补按数字键的间隔
    # 连续读到目标 index 这么多帧才算到场(特效会盖住槽位)。
    # 【这道确认是有代价的】: 宏表给切人格的 gap 多数只有 50ms, 而"按下 -> 游戏换人 -> 连读
    # 两帧确认"比这长, 所以每个切人格都会晚一点。干跑量到的是【每格晚 0.13s, 一轮 16 个切人格
    # 累计 1.7s】(启动宏 24.96s vs 表 23.29s, 循环宏 23.27s vs 表 21.48s)。
    # 一轮 28 秒里多出来的 1.7 秒会挤占绝息/流岚那两个 25 秒 CD 的 buff 窗口, 实机要盯。
    # 嫌慢有三条路, 按代价从小到大: 压成 1(特效误判的概率上去一点) / MACRO_SPEED 调到
    # 0.93 左右把整条表按比例缩回去 / 直接关 SAFETY 纯照宏打
    SWITCH_CONFIRM = 2
    # 【变奏那一格的 gap 从"到场"算, 不从"按键"算】(2026-09-26 实机, 用户: 有时候循环轴千咲
    # 变奏秧秧后, 秧秧的 E 没接上苍)。表里 '>YY^' 的 800 是羽挟苍空的入场动作, 默认秧秧
    # 按键后 0.1s 就到场。可 15:49 / 15:54 两轮游戏拖了 0.69 / 0.57s 才让她上来, E 照样在
    # "按键后 0.85s"发出去 = 到场后才 0.2~0.3s, 落在入场动作里被吞: 剑式没切、没 +1 苍翎,
    # 1苍 打空, 后面 1羽 也跟着变成苍剑式。正常轮是到场后 0.75~0.80s 按的 E。
    # 现在 = max(按键 + 按住 + gap, 到场 + 按住 + gap - SWITCH_NORMAL_LATENCY):
    # 正常到场(0.1s)时跟宏表一模一样, 游戏拖慢了才往后推。只管带 '^' 的格子 ——
    # 普通切人格的 gap 只有 50ms, 不是动作锁
    INTRO_GAP_FROM_ARRIVAL = True
    SWITCH_NORMAL_LATENCY = 0.10     # 实机日志: 切人键到确认到场 0.09~0.11s
    # 切人确认慢于这个秒数就把逐帧读数打进日志: 一串旧 index = 游戏在拒绝切人
    # (上一个动作没演完), 一串 x = HUD 被特效/时停盖住读不到
    SWITCH_TRACE_SLOW = 0.30
    # 【切人没确认就别假装切过去了】: 累计这么多次就放弃这一轮宏交回框架 ——
    # 乱着打完一整轮不如早点重来
    MACRO_ABORT_DESYNC = 2
    E_CD_CAP = 1.60              # 'E#'(按到进 CD)那类格子的上限
    # 'E#' 的"先见亮才认暗"那道门等多久。【默认的 0.15 太短】: 切人后技能栏要过几帧
    # 才换成新角色, 那几帧读到的是【上一个人】的 CD —— 0.15 秒内没见过"可用"就会被
    # 当成"本来就在 CD 上"直接跳过, E 一次都不发。而穗穗的 E 恰好紧跟在切人后 100ms。
    # 干跑里复现过: SuE_没见过亮(x5,0.45s)
    E_READY_GRACE = 0.40
    E_SPAM_INTERVAL = 0.06
    R_EXTRA_CAP = 2.00           # 大招没出手时补救的上限

    # ---- 切人之后紧跟着的长按, 要多等一下 ----
    # 【用户 2026-09-22 实机: "切千咲的时候会愣一下再打"】。
    # macro_switch 确认的是"in_team() 读到目标 index"= 人已经在场了, 但【在场 != 能出招】:
    # 入场动作还要演几百毫秒。宏表里切人格的 gap 只有 50ms, 紧跟着的 1150ms 长按,
    # 【按下沿正好落在入场动作里被丢掉】 —— 而长按又不能补按(PostMessage 重发会把
    # 已经按住的状态打断, 见 tap/A_TAP 那段), 所以只能等游戏先把入场演完再按下去。
    # 对千咲尤其要命: 锯环·疾攻【长按才从第2段起手】, 按下沿丢了就退化成第1段。
    # 只对"上一格是切人 + 这一格按住时间 >= HOLD_SETTLE_MIN"的格子生效, 短按不受影响。
    # 【从确认到场那一刻算, 只补差额】: 切人格自己的 gap 已经等过的部分不再重复等
    # (循环宏穗穗那下 650 长按前面隔着 1000ms, 原来照样再等 0.2s)。
    # 烟岚窗口里换成 TIGHT_HOLD_AFTER_SWITCH
    HOLD_AFTER_SWITCH = 0.20     # 等多久(秒)
    HOLD_SETTLE_MIN = 0.30       # 按住多久才算"长按", 短于这个的不等

    # ---- 千咲: 大招之后那发强化E ----
    # 【用户 2026-09-22 实机: "千咲的流程也不对, 没放出强化E"】。两个原因叠在一起:
    #  ① 宏表里千咲 R 那格后面紧跟着 E(gap=0), 而大招演出刚结束时【收招还在演】,
    #     E 按在这段里被整个吃掉 —— 跟秧秧的 M_R_TO_E 是同一个坑;
    #  ② 强化E 要【回路能量满】才放得出来, 满没满是打出来的, 表给不了保证。
    # 判据用 E 图标的白像素占比, 阈值是判据驱动旧版标定的: 亮起前基线 0.0000,
    # 亮起后持续在 0.158~0.211, 噪声见过 0.0942 —— 所以 0.13 卡在噪声和信号中间。
    # 【到点没亮也照按一次】: 按了不亏, 落在普通E 上也是在攒回路
    CHI_E_STAR = True
    # 【0.60 -> 0.15】(2026-09-22 实机, 用户: "为什么每次千咲放强化E都要卡一下才放")。
    # 实录里 R 演完之后 E 图标【0.12 秒就读到亮了】—— 残响早就满了, 那 0.6 秒纯粹是干站着。
    # 而且 0.6 还不保险: 18:23 那轮按下去照样被吞("强化E 按了但图标一直亮着")。
    # 【改成连发盖住收招】之后这个数就不需要大了, 留一点只是别在 HUD 刚回来那一帧就发
    # 【0.35 -> 0.20】(2026-09-22 用户第三次提: "正常流程千咲都是R完后按强化E,
    # 实机跑下来感觉还是不流畅")。实测 R 演出结束到按下 E 的间隔是 0.49~0.67 秒,
    # 拆开= 这个等待 + fill_saw_resonance 读一次屏(0.05) + cast_enhanced_e 又读一遍(0.12)。
    # 后面那一遍已经去掉了(见 'E*' 分支), 这里再压一点。
    # 【别压到 0】: R 演出结束(HUD 回来)之后收招还在演, 发出去的键会被丢掉 ——
    # 而这一下是单发, 丢了这一轮就没电锯了。日志里 `强化E(没亮` 变多就往上加
    CHI_R_TO_E = 0.20
    # 连发窗口: 发一次 -> 读图标 -> 没暗就再发。收招期间发的会被游戏丢掉, 单发必丢,
    # 只有连发盖得住(跟 fire_r 是同一个道理)
    # 【连发窗口默认 0 = 只发一次】。连发能盖住收招, 但齿轨轮回一放出去 E 就换回
    # 解弦之眼(有 CD), 多按的那几下会真的放出去、吃掉残响 —— 又是"改状态"那一类。
    # 收招靠 CHI_R_TO_E 等过去就够了; 真被吞了这一轮少一次电锯, 下一轮照常
    CHI_E_FIRE_WINDOW = 0.0
    CHI_E_FIRE_INTERVAL = 0.10
    CHI_E_LIT_BOX = 'box_resonance'
    CHI_E_LIT_COLOR = {'r': (200, 255), 'g': (200, 255), 'b': (200, 255)}
    CHI_E_LIT_THRESHOLD = 0.13
    # 【白像素占比的上限】(2026-09-26 21:14 录屏): 千咲大招演完 HUD 回来那一刻, E 还在冷却(图标上
    # 写着 1.3, 残响没满、没有齿轨轮回), 下一帧 E/Q 两个图标整块闪白 —— 白占比 0.599, 单帧读到就判成
    # "亮"按了 E, 什么都没放出来。真亮起来是 0.13~0.30(旧版标定 0.158~0.211, 当天日志最高 0.304),
    # 超过这个数的一律当闪白、不算亮
    CHI_E_LIT_MAX = 0.45
    # 【还要"稳定亮够这么久"才算】: 闪白是渐亮渐暗的, 起落两段各有一小截落在 [阈值, 上限] 里
    # (录屏逐帧: 21.52~21.53 两帧 0.23/0.45, 21.63~21.70 四帧 0.42->0.13), 读屏 30~50ms 一次,
    # "连读两帧"照样会中。真亮的齿轨轮回是稳定停在 0.15~0.30 的, 闪白每段在范围里最多 ~0.08s
    CHI_E_LIT_HOLD = 0.12
    # 烟岚窗口里读图标的时间上限(秒), 亮够 CHI_E_LIT_HOLD 就提前走。
    # 【0.25 -> 0.45】(9月26日.mp4 逐帧): 大招 HUD 回来那一帧强化E 就已经亮了(白 0.15, 跟作者一样),
    # 但紧接着(+0.15~+0.27s)E 图标整块闪粉一次, 白占比掉到 0, 再过 0.1s 才回到亮 —— "稳定亮 0.12s"
    # 最早在 HUD 回来 +0.47s 左右才成立, 0.25 的窗口正好错过, 于是轮轮都去点两下普攻"攒残响"
    # (日志 `攒残响(0.25s,A x2)` 20 多轮一模一样), 按 E 要到 +0.62s。残响其实早就满了
    TIGHT_CHI_E_READ = 0.45
    # 【开电锯要按作者那个点按, 不能等"稳定亮 0.12s"】(2026-09-27, 用户转述: "千咲电锯开慢了,
    # 切回秧秧的时候从1a开始打了"; 合轴时秧秧切出去模型还在场上演, 回来得及时连段是接着的)。
    # 实机日志: 大招 HUD 回来到按强化E 0.46~0.50s(21 轮), 作者实录是 0.17s, 慢 0.3s。
    # 录屏上秧秧这一格(千咲强化E 之后回来的两下普攻)流响 65%->55%, 作者同一格 65%->40%。
    # 9月26日.mp4 逐帧(千咲大招演完 4 次): HUD 回来头 2~6 帧强化E 图标都是白 0.15, 之后要么
    # 往上走到 0.2~0.34 再闪粉(白跌到 0), 要么直接闪粉 —— "稳定亮 0.12s"最早要到 +0.47s 才成立。
    # 而 09-26 21:14 那次残响没满(E 在 CD 上)的闪白是 0.23 -> 0.45 -> 0.60 -> 0.42 -> 0.13。
    # 判据: 先读到一次 [阈值, CHI_E_QUICK_FIRST_MAX](锚点), 隔 >= 0.03s 再读一次, 没往下掉
    # (>= 锚点 - CHI_E_QUICK_DROP)也没超 CHI_E_QUICK_MAX -> 算亮, 在 TIGHT_CHI_R_TO_E 那一刻按
    # (= 作者的 +0.17s)。录屏上按 6 种读屏时机模拟: 4 次大招 24 种组合认出 21 种, 那次闪白 0 种。
    # 千咲 E 的输入缓存只有 0.1s(附页2), 所以不再往前提。没认出来的退回上面那条慢路径
    CHI_E_QUICK = True
    CHI_E_QUICK_FIRST_MAX = 0.19
    CHI_E_QUICK_MAX = 0.30
    CHI_E_QUICK_DROP = 0.03
    CHI_E_LIT_CONFIRM = 2        # 连续这么多次过阈值才认, 挡单点噪声
    CHI_E_LIT_POLL = 0.03
    CHI_E_LIT_MAX_WAIT = 1.20    # 等图标亮的上限(秒)
    # 按完之后回头确认: 齿轨轮回放出去了, 强化态就被消耗掉, 图标应该暗下去。
    # 【还亮着 = 那一下被吞了】(入场锁/收招/硬直), 重按一次。
    # 【别用 has_cd 判】: 强化段很可能只消耗强化态、不进基础冷却 —— 旧版注释里写着
    # 秧秧那边实测 7/7 都判错就是这个原因
    # 【发完回头确认一次, 还亮着就补按一次】。
    # 【为什么千咲可以补按而秧秧不行】(2026-09-22 想清楚的分界): 秧秧【每按一次 E 就翻
    # 一次剑式】, 一轮的奇偶被打乱之后 1羽 会变成苍剑重击, 绝息/流岚全错位 —— 所以她那边
    # 补按是禁止的。千咲不一样: 齿轨轮回放出去之后 E 换成解弦之眼, 多按那一下【只是多放
    # 一个有伤害的技能 + 吃一次 CD】, 不会让后面的动作打错。
    # 而"没进电锯"的代价是【整轮千咲的输出作废】(后面那串 1150ms 长按全退化成普通普攻,
    # 外表还看不出来) —— 两边一权衡, 这里该补
    CHI_E_CONFIRM = True         # 关掉 = 发一次就走, 不读图标确认
    CHI_E_CONFIRM_WAIT = 0.25    # 发完隔多久回头读图标(秒)
    CHI_E_RETRIES = 1            # 还亮着就补按几次
    # 【长按锯环·疾攻之前, 确认人真的在电锯模式里】。E*(齿轨轮回)要是被吞了, 这一下
    # 1150ms 长按打出来的是【普通普攻】, 整轮千咲的输出作废, 而且外表看不出区别 ——
    # 所以宁可在这里多花一点时间补一发 E
    # 【打开 —— 这是强化E 的第二次机会】(2026-09-22 用户: "千咲R后强化E能量不够会继续A,
    # 但是会有后面亮了不开的情况")。E* 那一格的 CHI_FILL_SAW 到点就得走人, 而残响常常是
    # 【在那之后才攒满的】—— 图标亮了却没人按。这一格(电锯长按)正好在后面, 拿来补一发。
    # 【按千咲/秧秧的分界线, 这里补按是安全的】: 见 CHI_E_CONFIRM 上面那段 ——
    # 千咲多按一次 E 只是多放个解弦之眼, 不像秧秧会翻剑式
    CHI_SAW_GATE = True
    # 【R 没放出来就在电锯那一格补一次】(2026-09-22 用户: "有时候强化E先好, R没好,
    # 后面R好了就不开R了, 这不行")。
    # R(即刻·归无)开的【万缕·汇终】15 秒给锯环·疾攻/终结 +120% 倍率 —— 它就是为后面
    # 那串电锯输出准备的; R! 那一格能量没好就过去的话, 电锯等于白打。
    # 补在 A~(电锯长按)之前, 而且【排在补 E 前面】: R 还回 40 点残响, 顺带把 E 的条件也垫上。
    # 代价是多一次 2.6 秒的演出, 但比整串电锯没 buff 划算
    CHI_R_RETRY = True
    # ---- 残响不够就先点普攻攒够再开强化E ----
    # 【用户 2026-09-22 实机要求】: "实战情况多样, 千咲在本该开强化E的时候回路能量不够,
    # 开不了强化E, 需要多A几下打够能量开强化E"。
    # 【这一条属于"改状态"里的例外】: 多点几下普攻攒的就是【锯环残响】—— 那正是这一格
    # 需要的资源, 不像补按 E 那样会翻形态/吃CD。跟穗穗的回路闸是同一类。
    # 判据还是 E 图标的白像素占比(亮 = 残响满, 齿轨轮回就绪)
    # 【2.50 -> 3.50】(2026-09-22 用户第二次提: "强化E 因为回路能量不够开不出来的
    # 情况还是时有发生")。21:08 那份日志里这一格两轮都是秒满(`Ch攒残响(0.14s,A x1)`
    # / `(0.15s,A x1)`), 所以【不是判据的问题, 是有时候残响真的差一截】——
    # 差多少取决于前面那几下 A 打没打中, 上限给死在 2.5 秒就等于赌前面没被打断。
    # 多等的代价只是把后面整条宏往后推, 而开不出电锯这一轮千咲的输出是整个作废的
    # 【3.50 -> 3.00】同上: 这一格紧跟在大招那一格后面, 两个闸门是【串起来加】的,
    # 最坏情况下千咲连着站桩十几秒。残响本来就比大招能量好攒(实机两轮都是 0.15s 秒过)
    # 【3.00 -> 4.00】: 实机常常是"到点了才刚亮"。这一格拖长不算亏 —— 期间一直在点普攻,
    # 攒的就是残响本身; 真到上限还不亮, 后面 A~ 那一格还有一次机会(CHI_SAW_GATE)
    CHI_FILL_SAW = 4.00          # 点普攻攒残响的上限(秒), 0 = 关掉
    CHI_FILL_INTERVAL = 0.15     # 千咲普攻 2 段带派生, 点太密反而互相吞
    CHI_FILL_TRACE = True        # 把 E 图标读数记进日志(只读不改流程), 调阈值靠它
    # 【补救那一发等得短一些】: E* 那一格已经等过 CHI_E_LIT_MAX_WAIT(1.2s) 都没亮,
    # 说明多半是【残响真的没攒满】而不是那一下被吞了 —— 补按救不回来, 等满一样白等。
    # 给 0.4s: 够覆盖"被吞"那种(图标本来就亮着, 一读就中), 不够的那种也不拖太久
    CHI_SAW_RETRY_WAIT = 0.40
    # 【电锯长按前看千咲 E 图标的图案, 判强化E 到底放没放出去】(2026-09-27 晚, 用户: 正常流程下
    # 应该打出强化E 的时候没有打出)。快路径(CHI_E_QUICK)按完就走、不回头确认, 原来这一格只看
    # "按之前亮没亮", 按了没生效(被收招吞)这里发现不了 —— 23:02/23:06/23:07/23:08 几轮都是 `强化E(快)`
    # 之后千咲等协奏 3~5 秒(没进电锯就没有锯环·终结)。
    # `9月27日 (1).mp4` 逐帧: 强化E 放出去以后 E 就变成解弦之眼(三角图案), 循环轮前面用过 E,
    # 回场时带 5s 左右的冷却数字; 启动轮冷却转完了, 是亮着的三角 —— 所以【不能只看亮不亮】。
    # 强化E 没放出去的话, 回场时还是齿轨轮回(圆锯图案)。录屏上取的模板(每种 2 张, 16x16 灰度):
    # 解弦之眼(亮/冷却)的 263 帧"齿轨轮回分 - 其他分"全 <= -0.06, 齿轨轮回稳定显示时到 +0.5。
    # 回场头 5 帧图标会闪白(0.16/0.55/0.33), 所以连着两次读到同一种才算, 最多读 CHI_SAW_CHECK_CAP。
    # 还是齿轨轮回 -> 补按一次 E; 是解弦之眼 -> 不补(放出去了, 或者残响没满、补也补不出强化E),
    # 顺带省掉原来"上一格没亮就等 0.4s 再按"的白等
    CHI_SAW_CHECK = True
    # 【千咲最后等协奏时, E 图标变回齿轨轮回就按一次】(2026-09-28, 用户: 流程不顺时千咲强化E 怎么补救)。
    # 强化E 这一轮没出来(残响没满 / 被吞), 千咲最后那格只能靠普通攻击攒协奏 —— 帧数据表: 普攻一整套
    # A1~A3 协奏合计约 12, 每秒约 5; 锯环·终结(电锯A4)一下 +45。09-27 七次 `等协奏→秧(…,CAP)` 基本都是
    # 这一类。等的过程中残响被普攻攒满、图标变回齿轨轮回, 就按一次进电锯, 接下来的点击是锯环, 残响
    # 耗到 0 出锯环·终结, 协奏一下就满。只按一次; 图案判法同 CHI_SAW_CHECK
    CHI_CON_E2 = True
    CHI_SAW_CHECK_CAP = 0.20
    CHI_GLYPH_MARGIN = 0.10
    # 6 张 16x16 灰度模板(uint8, base64): 齿轨轮回 x2, 解弦之眼亮 x2, 解弦之眼冷却 x2
    CHI_GLYPHS = (
        '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'
        'AAAAAAAAAyRHSVNINgcAAAAA'
    )

    # ---- 大招前的能量闸(所有 R!/R3 那类格子共用) ----
    # 【用户 2026-09-22 实机: "每次千咲的R能量都不够, 肯定是前面少打了什么"】。
    # 把两条宏里千咲到自己 R 为止的动作拉出来对比, 差别是实打实的:
    #   启动宏: E + A x7   (1.95 / 3.55 / 3.80 / 4.10 / 4.50长按 / 7.50 / 8.25空中A)
    #   循环宏: E + A x5   (2.20 / 3.65 / 5.56 / 5.81 / 6.11)
    # 循环轮本来就少两下, 而且那 5 下【全都紧跟在切人后】(切人格 gap 只有 50ms),
    # 入场动画还要再吞掉一部分 —— 所以启动轴的 R 放得出来, 循环轴放不出来。
    # 实机日志: ChR补救_NOFIRE(x40/x41) 出现 5 次 —— 连按 40 下都放不出来, 就是能量没满。
    # 【大招是能量门控不是 CD 门控】: 判据用 liberation_available()(读图标亮不亮),
    # 不是 has_cd。能量靠普攻/技能命中攒, 【干等等不来, 必须动手点】——
    # 跟穗穗的回路闸、千咲的攒残响是同一类"攒的正是这一格要用的资源", 安全
    # 【2.50 -> 4.50】(2026-09-22 用户第二次提: "循环轴里千咲 R 能量没好 ... 还是
    # 时有发生")。上面那段分析(循环轮比启动轮少两下 A)是对的, 但 2.5 秒的上限不够兜:
    # 21:08 那一轮 `Ch能量(CAP,A x19)` —— 点满 19 下 A 到上限还是没亮, 照按之后
    # ChR_fired(x12) 侥幸出去了; 21:09 那一轮穗穗更惨, `Su能量(CAP,A x18)` 之后
    # `SuR_NOFIRE(x25)` + `SuR补救_NOFIRE(x41)` 白烧 3.5 秒没放出来, 紧接着
    # 切人连着两次失步, 整轮宏放弃。
    # 【多等 2 秒 vs 白烧 3.5 秒再崩一轮】: 这一格等的时候手是在点普攻的, 等的就是
    # 能量本身; 而上限一到就"照按", 后面 fire_r 那两轮连发是纯粹的空转 —— 
    # 能量没满按多少下都不出手。所以上限往大了给才是省时间的那一边
    # 【4.50 -> 3.00】(2026-09-22 用户实机: "循环轴里秧秧切千咲, 千咲就开始不动了")。
    # 4.5 是上一版按"多等 2 秒好过白烧 3.5 秒"调上去的, 方向对但【加错了地方】:
    # 真正白烧的是后面 fire_r 那 2 秒补救(能量不够时必然按不出来), 那一段现在按
    # filled 跳掉了; 而闸门本身等得越久, 千咲站桩就越久 —— 实机看到的就是"不动"。
    # 3.0 是两头的折中: 上一版 2.5 那次 `Ch能量(CAP,A x19)` 之后照按【是放出来的】
    # (ChR_fired x12), 说明"差一点"的情况 2.5~3 秒就够; 而差得多的情况(x24+x40 全
    # 打空)再给 1.5 秒也救不回来 —— 那是宏表少给了 A, 不是闸门等得不够
    LIB_FILL = 3.00              # 图标不亮时点普攻攒能量的上限(秒), 0 = 关掉
    LIB_FILL_INTERVAL = 0.12

    # ---- 穗穗大招前的回路闸 ----
    # 【用户 2026-09-22 实机报的】: "穗穗在启动轴里没有打满回路就开R了"。
    # 宏是照表按时间走的, 表里给穗穗攒回路的那几下 A 够不够, 取决于打没打中、有没有被
    # 怪打断 —— 宏本身给不了任何保证。满不满读 suisui_forte3 这张图(芳菲信 600 那一档),
    # 阈值和确认帧数都是从判据驱动旧版搬过来的(它那边叫 FORTE3_THRESHOLD/FORTE3_CONFIRM)。
    # 【等的时候必须点普攻】: 回路就是普攻攒出来的, 干等永远等不满。
    # 【这一格会把后面整条宏往后推】—— 等多久推多久, 跟切人确认是一个性质
    FORTE3_GATE = True
    # 【3.0 -> 6.0】(2026-09-27, 用户: 穗穗有时候被打断太久, 三段回路没满也开大了)。
    # 09-27 实机循环轮里 4 次 `三段回路 3.0s 没攒满(点了 23 下 A)`。三段回路 = 芳菲信 600,
    # 【延奏清掉的芳菲信 > 599 才有烟岚前置】—— 没满就放大招, 这一轮的烟岚一定没有。
    # 这一格在烟岚窗口之前, 多等不挤窗口, 只是整轮往后推
    FORTE3_WAIT = 6.0            # 等的上限(秒), 超了照放不误: 少一档好过把整轮宏卡死
    FORTE3_THRESHOLD = 0.75      # 旧版标定的值, 注释写着"原来 0.6 太松会误判"
    FORTE3_CONFIRM = 2           # 连读这么多帧都命中才认, 单帧误判不算
    # 一下普攻都没点就读到"满", 而且快于这个秒数 -> 记一笔(只报不拦)。
    # 【循环轮里这是常态, 不是误判】(09-27 22:30 两轮 `满(0.09s)`/`满(0.10s)`, 录屏上那两轮秧秧
    # 都吃到了烟岚 —— 烟岚要延奏清掉的芳菲信 > 599, 说明回路是真满): 穗穗前两段上场已经攒满了
    FORTE3_SUSPECT = 0.30

    # ---- 穗穗的强化E(醒春潮) ----
    # 【用户 2026-09-22 实机: "启动轴里穗穗的强化E 有时候回路条没有满, 就会放个普通E"】。
    # 表里这一格原来是 'E#'(按到技能进 CD), 而【普通E 一样会进 CD】—— 那个判据根本
    # 分不出放出去的是哪一个: 按完就走, 冷却却是实打实吃掉了, 这一轮穗穗进不了濯雨时。
    # 后果不止少一次强化E: 她没进濯雨时, 资源条显示的就还是【水云息】而不是【芳菲信】,
    # 后面 R3 那一格的 wait_forte3 读的是错的那条, 会误判成"回路满"直接放大招
    # (实机那条 `0.03s 一下没点就读到"回路满"` 的警告, 根子就在这里)。
    # 【判据换成 suisui_e1】: 那张图是 E 图标的强化态图案(标注框就在技能栏那个位置),
    # 亮着 = 醒春潮就绪。原生 Suisui.try_e() 用的就是它, 而且是【按到它消失为止】——
    # 消失才等于强化E 真的放出去了。
    # 【没亮就先点普攻攒回路】: 回路是普攻攒出来的, 干等等不来 —— 跟千咲攒残响、
    # 穗穗大招前那道回路闸是同一类"攒的正是这一格要用的资源", 不翻形态也不吃 CD。
    SUI_E1_GATE = True
    SUI_E1_FILL = 2.50           # 图标不亮时点普攻攒回路的上限(秒), 0 = 关掉
    SUI_E1_FILL_INTERVAL = 0.12
    # 【上限到了还不亮就干脆不按】(跟千咲那边"照按一次"相反, 因为诉求正好相反):
    # 用户要的就是"别放普通E"。而且这时候按不按结果差不多 —— 两种都进不了濯雨时,
    # 不按至少把 CD 留着, 穗穗后面还会上场。翻成 False 就退回"照按一次"
    SUI_E1_SKIP_IF_NOT_READY = True
    SUI_E1_CAST_CAP = 1.50       # 按到图标消失的上限(秒)
    SUI_E1_KEY_INTERVAL = 0.05
    SUI_E1_THRESHOLD = 0.80      # find_one 的默认值, 原生 try_e 也没改过

    # 处决。【只在两条宏的最后一格按】('F?')—— 处决带全局时停、动画期间 HUD 整个不在,
    # 按在别处会骗过大招/重击的 HUD 判据
    F_BREAK = True
    F_BREAK_ANIM = 5.00          # 按完等 HUD 回来的上限(处决动画时长因角色而异)
    # 【处决不走框架的 f_break()】(2026-09-27, 用户: 循环轴里最后一个羽打完衔接下一轮总是有点慢)。
    # f_break() 是"按 F + 点左键"每 0.2s 一轮, 至少 0.5s、提示还在就一直按(09-27 实机 1.5~1.7s),
    # 后面再等 HUD 0.36s + YU_AFTER_BREAK_SETTLE 0.5s —— 第2羽 打完到下一轮第一格要 2.3~2.6s;
    # 那一路左键还会让秧秧在处决后多砍几刀。帧数据表秧秧的谐度破坏是 90F(1.5s)。
    # 现在: 只按 F(提示没了就停, 最多 F_BREAK_PRESSES 下), 从第一下 F 起等满 YU_BREAK_ANIM。
    # 等短了循环宏开头那个 E 会被吞 —— 那一格有剑式核对(见 YY_FORM_CHECK), 吞了会补
    F_BREAK_PRESSES = 3
    YU_BREAK_ANIM = 1.60

    MACRO_SPEED = 1.00           # 整条宏的速度系数, 1.0 = 照表

    # ==================== 两条宏(逐条从宏面板截图抄下来的) ====================
    # 每行 = (动作, 按住ms, 松开后等ms, 注释)。动作:
    #   'A'      左键(普攻)。按住 ms 就是宏里那个按住时长, 1100/650/600 那几格是长按
    #   'AY'     秧秧 2羽1苍 那段的左键(1苍 2300 / 1羽 4400 / 点一下 / 第2羽 4300): 按满时长,
    #            前台时走 SendInput(YY_TAIL_SENDINPUT); 长按期间把重击就绪图标读数打进日志
    #   'AYC'    同 'AY', 只用在 1苍 那一格: 提前 YY_CANG_TRIM 松手
    #   'W'      向前走一下(循环宏第一格, 调位置用)
    #   'E'/'R'/'Q'   共鸣技能 / 共鸣解放 / 声骸技能, 【发给当时在场的那个人】。
    #            秧秧的 E【一律单发】: 每按一次翻一次剑式, 多一下奇偶就错
    #   'R!'     大招: 【连发到 HUD 消失为止】, 再等演出结束。不是"发一次然后盯着",
    #            原因见 macro_key 里那段注释(宏表里 Q 那格的 gap 是 0)
    #   'R3'     穗穗大招: 先等三段回路满(没满就接着点普攻), 再走 'R!' 那一套
    #   'RY'     秧秧大招: 连发到出手, 演完再等 M_R_TO_E(收招)—— 后面那个 E 是单发, 丢不起
    #   'E*'     千咲的强化E(齿轨轮回): 先等大招收招演完, 再看 E 图标亮没亮, 按一次
    #   'E1'     穗穗的强化E(醒春潮): 回路没满就先点普攻攒, 满了按到 suisui_e1 熄灭;
    #            【攒不满就不按】—— 按下去是普通E, 见 SUI_E1_GATE
    #   'E#'     按到这个技能进 CD 为止(穗穗循环轮那个 E, 紧跟切人会被入场吞)
    #   'EY'     秧秧的强化E(剑式流转, +1 苍翎): 图标没亮(流响没打空)就先点普攻打空再按,
    #            只按一次 E, 见 YY_EX_GATE
    #   'E苍'/'E羽'  秧秧的普通 E, 按完核对剑式是不是换到了苍/羽, 没换过去补一次(见 YY_FORM_CHECK)。
    #            'EY'(换羽) / 'EY0'(换苍) 按完也核对
    #   'EY0'    同 'EY' 但【不点普攻】(变奏后那个 E: 这时点出来的是羽强化A, 不扣流响还吃掉第二个羽)
    #   'AX'     秧秧那下 600ms 长按: 按住之前看一眼流响条, 剩得多才先点普攻打, 见 YY_DRAIN_ABOVE
    #   'A~'     千咲的电锯长按(锯环·疾攻 第2段起手 -> 自动接第3段): 上一格强化E 没亮就先
    #            补一发 E。其余跟 'A' 一样(含切人后的入场等待)
    #   'SPACE'  跳(走框架配置的跳跃键)
    #   'F?'     处决: 有提示就按; 没有就等 YU_NO_BREAK_TAIL(羽的后摇), 期间提示亮了照按
    #   'WIN'    不按键, 只是标记: 下一个切人起算烟岚窗口, 到 '>YY^' 为止(见 YANLAN_WINDOW)
    #   '>SUI'/'>CHI'/'>YY'  切人; 结尾带 '^' = 这一下是变奏入场, 切之前先把协奏打满
    OPENER_MACRO = (
        # 开局场上就是穗穗(1 号位), 第一格是她的 3A
        ('A',      50,  250, '穗A1'),
        ('A',      50,  600, '穗A2'),
        ('A',      50,  200, '穗A3'),
        ('>YY',    50,  150, '切秧秧'),
        ('A',      50,  100, '秧A'),
        ('>CHI',   50,   55, '切千咲'),
        ('E',      50,  250, '千E'),
        ('A',      50,  250, '千A'),
        ('>SUI',   50,   80, '切穗穗'),
        ('E1',     50,  200, '穗强化E(醒春潮): 等回路满再按, 按到图标灭'),
        ('>YY',    50,   50, '切秧秧'),
        ('A',      50,  400, '秧A'),
        ('A',     100,  200, '秧A'),
        ('>CHI',   50,   50, '切千咲'),
        ('A',      50,  200, '千A'),
        ('A',      50,  250, '千A'),
        ('A',      50,  350, '千A'),
        ('A',     100,  450, '千A'),
        ('>SUI',   50,  600, '切穗穗'),
        ('A',      50,  800, '穗A'),
        ('A',      50,  250, '穗A'),
        ('>YY',    50,   50, '切秧秧'),
        ('EY',     50,  250, '秧强化E(换羽剑式, +1 苍翎): 流响没打空就先点普攻打空, 见 YY_EX_GATE'),
        ('>CHI',   50,  200, '切千咲'),
        ('A',      50,  350, '千A'),
        ('SPACE',  50,  100, '跳'),
        ('A',      50,   10, '空中A'),
        ('>SUI',   50,   50, '切穗穗'),
        ('A',      50,  850, '穗A'),
        ('Q',      25,    0, '穗声骸'),
        ('R3',     50, 4300, '穗大招(先等三段回路满, 再连发到出手)'),
        ('WIN',     0,    0, '烟岚窗口: 从下面这个切人起, 游戏时间 6s 内要变奏切回秧秧'),
        ('>YY',    50,  800, '切秧秧(穗穗延奏)'),
        ('A',      50,  250, '秧A'),
        ('A',      50,  200, '秧A'),
        ('>CHI',   50,    0, '切千咲'),
        ('Q',      50,    0, '千声骸'),
        ('R!',     50, 3500, '千大招(连发到出手)'),
        ('E*',     50,    0, '千强化E(等收招, 看图标, 按一次就走)'),
        ('>YY',    50,   50, '切秧秧'),
        ('A',      50,  400, '秧A'),
        ('A',      50,  450, '秧A'),
        ('>CHI',   50,   50, '切千咲'),
        ('A~',   1100,   50, '千电锯长按A(锯环·疾攻2->3段)'),
        ('A',      50,    0, '千A(锯环·终结)'),
        ('>YY',    50,   50, '切秧秧'),
        ('AX',    600,  600, '秧长按A(流响剩得多才先打, 见 YY_DRAIN_ABOVE)'),
        ('>CHI',   50,   50, '切千咲'),
        ('A',      50,  300, '千A'),
        ('>YY^',   50,  800, '变奏切秧秧(烟岚窗口到此为止), 800 = 羽挟苍空的入场动作'),
        # ---- 2羽1苍 ----
        ('EY0',    50,   50, '秧强化E(换苍剑式, +1 苍翎凑满 2): 只读不点, 见 YY_DRAIN_CAP'),
        ('AYC',  2300,   50, '秧长按: 1苍(重击·苍剑式), 提前 YY_CANG_TRIM 松手'),
        ('Q',      50,   50, '秧声骸'),
        ('RY',     50, 5000, '秧大招(+1 苍翎), 演完等收招'),
        ('E羽',    50,   50, '秧E(换羽剑式, +1 苍翎凑满 2), 按完核对剑式'),
        ('AY',   4400,   50, '秧长按: 1羽(重击·羽剑式 -> 释羽风落 -> 聆风祀声)'),
        ('AY',     50,   50, '秧A(点一下)'),
        ('AY',   4300,   50, '秧长按: 第2羽(湮象风华)'),
        ('F?',     50,    0, '处决(没有就等羽的后摇)'),
    )

    # 跟启动宏的差: 开头三格是秧秧自己的收尾(W/E/A, 启动宏那边是穗穗 3A), 中段的穿插顺序
    # 不一样; 从穗穗 Q 那一格往后【逐格相同】, 只差穗穗那下 A 的间隔(650 / 850)
    LOOP_MACRO = (
        # 开头这三格还在秧秧身上: 上一条宏刚打完 2羽, 人还是她
        ('W',      50,    0, '向前走(调位置)'),
        ('E苍',    50,  150, '秧E(换苍剑式), 按完核对剑式'),
        ('A',      50,  100, '秧A'),
        ('>SUI',   50, 1000, '切穗穗'),
        ('A',     650,    0, '穗长按A'),
        ('>CHI',   50,   50, '切千咲'),
        ('A',      50,    0, '千A'),
        ('>YY',    50,   50, '切秧秧'),
        ('A',      50,  400, '秧A'),
        ('A',      50,  450, '秧A'),
        ('>CHI',   50,   60, '切千咲'),
        ('E',      50,  200, '千E'),
        ('A',      80,   55, '千A'),
        # 【这一格【不是】变奏入场, 别改成 '>SUI^'】(2026-09-22 改错过一次):
        # 千咲到这里只打过 A x1 + E + A x1, 协奏物理上攒不起来, 加了 '^' 实机是
        # `等协奏(0.22,CAP,A x26,3.58s)` —— 白等 3.58 秒、多点 26 下 A。
        # 要保的是【最后千咲切秧秧】那一格(见本表的 '>YY^')
        ('>SUI',   50,   50, '切穗穗'),
        ('E#',     50,  575, '穗E(按到进CD, 紧跟切人会被入场吞)'),
        ('A',      50,  600, '穗A'),
        ('A',      50,  250, '穗A'),
        ('>CHI',   50,   50, '切千咲'),
        ('A',      50,  200, '千A'),
        ('A',      50,  250, '千A'),
        ('A',      50,   50, '千A'),
        ('>YY',    50,   50, '切秧秧'),
        ('EY',     50,  250, '秧强化E(换羽剑式, +1 苍翎): 流响没打空就先点普攻打空, 见 YY_EX_GATE'),
        ('>SUI',   50,   50, '切穗穗'),
        ('A',      50,  650, '穗A'),
        ('Q',      25,    0, '穗声骸'),
        ('R3',     50, 4300, '穗大招(先等三段回路满, 再连发到出手)'),
        ('WIN',     0,    0, '烟岚窗口: 从下面这个切人起, 游戏时间 6s 内要变奏切回秧秧'),
        ('>YY',    50,  800, '切秧秧(穗穗延奏)'),
        ('A',      50,  250, '秧A'),
        ('A',      50,  200, '秧A'),
        ('>CHI',   50,    0, '切千咲'),
        ('Q',      50,    0, '千声骸'),
        ('R!',     50, 3500, '千大招(连发到出手)'),
        ('E*',     50,    0, '千强化E(等收招, 看图标, 按一次就走)'),
        ('>YY',    50,   50, '切秧秧'),
        ('A',      50,  400, '秧A'),
        ('A',      50,  450, '秧A'),
        ('>CHI',   50,   50, '切千咲'),
        ('A~',   1100,   50, '千电锯长按A(锯环·疾攻2->3段)'),
        ('A',      50,    0, '千A(锯环·终结)'),
        ('>YY',    50,   50, '切秧秧'),
        ('AX',    600,  600, '秧长按A(流响剩得多才先打, 见 YY_DRAIN_ABOVE)'),
        ('>CHI',   50,   50, '切千咲'),
        ('A',      50,  300, '千A'),
        ('>YY^',   50,  800, '变奏切秧秧(烟岚窗口到此为止), 800 = 羽挟苍空的入场动作'),
        # ---- 2羽1苍(跟启动宏末尾逐格相同) ----
        ('EY0',    50,   50, '秧强化E(换苍剑式, +1 苍翎凑满 2): 只读不点, 见 YY_DRAIN_CAP'),
        ('AYC',  2300,   50, '秧长按: 1苍(重击·苍剑式), 提前 YY_CANG_TRIM 松手'),
        ('Q',      50,   50, '秧声骸'),
        ('RY',     50, 5000, '秧大招(+1 苍翎), 演完等收招'),
        ('E羽',    50,   50, '秧E(换羽剑式, +1 苍翎凑满 2), 按完核对剑式'),
        ('AY',   4400,   50, '秧长按: 1羽(重击·羽剑式 -> 释羽风落 -> 聆风祀声)'),
        ('AY',     50,   50, '秧A(点一下)'),
        ('AY',   4300,   50, '秧长按: 第2羽(湮象风华)'),
        ('F?',     50,    0, '处决(没有就等羽的后摇)'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.opener_armed = True
        self.last_opener = -1
        self.macro_t0 = 0.0
        self.macro_marks = []
        self.cur = None
        self.holding_attack = False
        self.si_holding = False        # 左键是不是 SendInput 按下去的(松开也得走 SendInput)
        self.yy_ex_verify_pending = None  # 'EY' 按完 E 还没回头检查(挪到紧跟的长按里做)
        self.hold_credit = 0.0         # 上一格核对剑式超出宏表的时间, 由紧跟的长按扣回来
        self.skip_cang = False         # 变奏后流响没空, 这一轮不放苍(见 YY_SKIP_CANG)
        self.win_log = []              # 烟岚窗口里每次切人: (切给谁, 按键时的窗口游戏时间)
        self.poking = False
        self.switch_press_at = 0.0
        self.lib_press_at = 0.0
        self.loop_count = 0
        self.macro_desync = 0
        self.last_was_switch = False
        self.chi_in_saw = False        # 千咲进没进电锯模式(E* 那一格的结果)
        self.chi_r_fired = False       # 千咲的 R 这一轮放出去没有(R! 那一格的结果)
        self.switch_key_at = 0.0       # 最近一次宏里切人键按下的时刻
        self.switch_arrived_at = 0.0   # 最近一次宏里切人确认到场的时刻
        self.tight = False             # 在不在烟岚窗口里('WIN' 到 '>YY^')
        self.win_armed = False         # 'WIN' 之后还没碰到切人(窗口起点待定)
        self.win_t0 = 0.0              # 烟岚窗口起点(穗穗大招后切秧秧那一下的按键时刻)
        # 【这里也要启一次】: reset_state() 只在 load_chars() 时跑, 而角色对象会被
        # 粘性复用 —— 开关自动战斗时很可能一次都不跑到
        try:
            start_enable_watch(self.task, self.logger)
        except Exception:
            pass

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        start_f_watch(self.task, self.logger)
        start_enable_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_yangqiansui(self) -> bool:
        """本队 = 千咲 + 穗穗 + 秧秧·玄翎。【按类名判】。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Chisa', 'Suisui'}:
                team_members += 1
        return team_members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。

        【这里不查任何"某个技能可用"】: 场下角色的可用性读的是上次在场时 OCR 到的 CD 残值,
        拿它当门会开场愣一下。防重入靠 opener_armed + OPENER_MIN_GAP。
        """
        if not self.is_in_yangqiansui:
            return 'team is not chisa/suisui/yangyangsp'
        if not self.opener_armed:
            # 【玩家开关过自动战斗就放行】。手动停任务不走 combat_end(), 这个标记会跨局
            # 留在 False; 重开时角色对象又常常被粘性复用, reset_state() 根本不跑。
            # 见文件顶部 start_enable_watch 那段
            restarted = getattr(self.task, '_ww_task_restarted_at', -1)
            if restarted > self.last_opener:
                self.logger.info('auto combat 重开过 — 启动轴重新武装, 从头跑')
                self.opener_armed = True
                return None
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

    def macro_spam(self, window, interval=None, tag='A', sample=None, sample_every=2,
                   max_clicks=None):
        """连点一个窗口。

        打出几段由 window 决定, 不由单次点击的时机决定 —— 动画期间的点击会被丢掉,
        连点保证总有一下落在动画刚结束的瞬间。时间轴上记首尾和点了几下, 对着录像
        能反推一段动画多长: 窗口里打出 n 段, 一段就约等于 window/n。
        """
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        # 次数上限: 光靠时间兜底的话, interval 被调成 0 或者机器卡一下就会瞬间
        # 灌进去几千次点击
        cap = int(window / max(interval, 0.01)) + 2
        # 【可以再封一层】: 用在【空中】那几格 —— 多余的点击会变成下落攻击,
        # 落地会打断/改变后面的动作。封顶【不缩短窗口】, 所以不花时间
        max_clicks = cap if max_clicks is None else min(cap, max_clicks)
        self.macro_mark(f'{tag}_start')
        self.spam_samples = []
        while time.time() - start < window and n < max_clicks:
            self.click()
            n += 1
            if sample is not None and n % sample_every == 0:
                self.spam_samples.append(f'{time.time() - start:.2f}:{1 if sample() else 0}')
            # 最后一段等待按剩余时间裁剪, 别整个 interval 等下去 —— 那是白送的延迟
            remaining = window - (time.time() - start)
            if remaining <= 0:
                break
            # 【减掉 A_TAP】: click 现在自己按住 A_TAP 秒, 不扣的话这段连点的周期
            # 从 interval 变成 interval + A_TAP, 一个窗口里少点三分之一下
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
        """本类里所有的 self.click() 都改成按住 A_TAP —— 引擎里那些"顺手点"也一样。

        BaseChar.click 强制 down_time=0.01, 在这条轴上等于没点。参数照收不用:
        这条轴的点击【一律点屏幕中心】, 没有带坐标的调用。
        """
        self.tap()

    def wait_con_at_least(self, char, level, tag='con', cap=None, interval=None, poke=True,
                          on_poll=None):
        """边打边等协奏圈涨到 level 以上(0~1)。到上限也往下走, 不把轴卡死。

        跟 wait_con_for_intro 的区别: 那个是"等满才交棒"(门槛是 CON_ENOUGH=0.99,
        【那个值是"环还没闭合"的哨兵, 不是真满】—— 要真满就把 level 传 1.0), 这个是
        【等到某个门槛才做下一个动作】—— 用户 2026-08-21: "达妮娅有时候被打断,
        能不能确保协奏圈超过75%才开R2Q切人"。

        【为什么是门槛不是满】: R2 和 Q 自己还会把圈推上去一截, 所以起手时到 75%
        就够她离场时是满的。等满再开 R2 反而是把这一截白等掉。

        【读协奏不便宜】(能量环连通域分析), 所以轮询间隔别调太小。
        【等的期间必须继续打】: 圈是靠打中涨的, 干站着等只会等到超时。
        """
        cap = self.CON_WAIT_TIME_OUT if cap is None else cap
        interval = self.CON_POLL_INTERVAL if interval is None else interval
        start = time.time()
        n = 0
        con = 0.0
        hit = False
        samples = []
        next_click = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < cap:
            try:
                con = float(char.get_current_con())
            except Exception:
                con = 0.0
            if len(samples) < self.CON_SAMPLE_MAX:
                samples.append(f'{time.time() - start:.2f}:{con:.2f}')
            if con >= level:
                hit = True
                break
            now = time.time()
            if poke and now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            if on_poll is not None:
                on_poll()
            self.macro_wait(interval)
        self.macro_mark(f'{tag}({con:.2f},{"ok" if hit else "CAP"},A x{n},'
                        f'{time.time() - start:.2f}s)')
        if not hit:
            self.logger.info(f'{tag}: con only reached {con:.2f} (< {level:.2f}) in '
                             f'{cap:.1f}s — {" ".join(samples)}')
        return hit

    # ==================== 本条轴额外用到的原语 ====================

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

    def fire_r(self, char, tag, window):
        """先等 HUD 在场再 fire_lib。fire_lib 里的 self.sleep 会把 skip_combat_check 放回 False,
        这里重新置上(宏里后面还有好几格高频切人)。"""
        self.wait_hud_up(tag=f'{tag}前HUD')
        fired = self.fire_lib(char, tag=tag, window=window)
        self.task.skip_combat_check = True
        return fired

    def fire_r_tight(self, char, tag):
        """烟岚窗口里的大招: 【先直接连发】TIGHT_R_FIRST_WINDOW, 没进才边点边攒能量再补。

        跟作者的宏一样, Q 之后第一时间就按 R, 不先读能量、不先点 A: 能量满的话第一发就进;
        差的那一点要是 Q 打中才给的, 连发会在能量到账的那一刻进去, 也不用点 A。
        只有连发这一段都没进, 才是真差几下普攻 —— 那时再点 A 攒(TIGHT_LIB_FILL 封顶)。
        """
        if self.fire_r(char, tag, self.TIGHT_R_FIRST_WINDOW):
            return True
        if self.fill_liberation(char, f'{char.name[:2]}能量', cap=self.TIGHT_LIB_FILL):
            return self.fire_r(char, f'{tag}补救', self.R_EXTRA_CAP)
        self.macro_mark(f'{tag}_能量不够(电锯那格再补)')
        return False

    # ==================== 各格 ====================

    def macro_enter(self, entry_char):
        chi = self.teammate('Chisa')
        sui = self.teammate('Suisui')
        if chi is None or sui is None:
            return None, None, None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        # 处决全程交给玩家手动按, 框架切人时那一发一律关掉(内置非治疗角色默认会按)
        self.check_f_on_switch = False
        chi.check_f_on_switch = False
        sui.check_f_on_switch = False
        self.macro_desync = 0
        self.macro_start()
        return chi, sui, previous

    def macro_exit(self, previous):
        # 长按跨了好几个方法, 中途抛异常的话鼠标会一直按着。这里是唯一保证松手的地方
        self.release_attack()
        self.poking = False
        self.tight = False
        self.win_armed = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)


    # ==================== 宏回放 ====================

    def try_f_break(self, tag):
        """处决提示亮着就按 F, 按完等 HUD 回来。返回按了没有。

        【为什么不能直接 task.f_break()】: 它的门是 `can_break or check_f_break()`, 而
        check_f_break 里【屏幕中央那一路找图有 1 秒频率限制】; 宏跑的时候 skip_combat_check=True,
        战斗判定线程也不刷新 can_break —— 两条腿一起瘸, 症状是"处决从来不放"。
        所以这里自己补中央那一路, 命中就把 can_break 置上, 再让 f_break() 走它的短路分支。
        【is_pick_f 那道门不能省】: F 同时是拾取/交互键。
        """
        if not self.F_BREAK:
            return False
        task = self.task
        ready = bool(getattr(task, 'can_break', False))
        if not ready:
            try:
                task.next_frame()
                ready = self.f_prompt()
            except Exception as e:
                self.logger.info(f'{tag}: f_break probe failed {e}')
                return False
        if not ready:
            return False
        # 【只按 F, 不走 task.f_break()】: 那个会连点左键、至少卡 0.5s, 见 YU_BREAK_ANIM
        start = time.time()
        presses = 0
        while presses < self.F_BREAK_PRESSES:
            task.send_key('f', down_time=0.05)
            presses += 1
            self.macro_wait(0.10)
            task.next_frame()
            try:
                if not self.f_prompt():
                    break
            except Exception:
                break
        task.can_break = False
        self.macro_wait(max(0.0, start + self.YU_BREAK_ANIM - time.time()))
        task.next_frame()
        if not task.in_team()[0]:
            # 演出还在(HUD 不在), 等它回来 —— 后面那几格都要读 HUD
            self.wait_hud_back(tag=f'{tag}处决anim', time_out=self.F_BREAK_ANIM)
        self.macro_mark(f'{tag}处决(F x{presses},{time.time() - start:.2f}s)')
        return True

    def f_prompt(self):
        """屏幕中间有处决提示(不是拾取/交互的 F)。"""
        task = self.task
        hit = task.find_one('f_break', box=task.box_of_screen(0.2, 0.2, 0.75, 0.8,
                                                               hcenter=True, vcenter=True),
                            target_height=720)
        return bool(hit) and not task.is_pick_f()

    def watch_hud_gone(self, window, tag, poke=False):
        """在 window 里盯着队伍 HUD 有没有消失过(= 全局时停/演出开始), 【用满 window】。

        不提前返回 —— 这一格的长度是宏表定的, 出手了就得原样走完, 提前走会把后面每一格都推歪。
        poke: HUD 还在(= 这一格的技能还没出手)时顺手点普攻。【真出手了一下都不会点】,
        因为出手那一刻 HUD 就没了; 没出手的话这段时间正好拿来攒回路。
        """
        end = time.time() + window
        gone = 0
        seen = False
        clicks = 0
        next_click = 0.0
        while time.time() < end:
            self.task.next_frame()
            if self.task.in_team()[0]:
                gone = 0
                if poke and not seen and time.time() >= next_click:
                    self.tap()
                    clicks += 1
                    next_click = time.time() + self.A_INTERVAL
            else:
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    seen = True
            left = end - time.time()
            if left <= 0:
                break
            self.macro_wait(min(0.02, left))
        self.macro_mark(f'{tag}({"出手" if seen else "没出"},A x{clicks})')
        return seen

    def macro_key(self, act, hold, gap):
        """把宏表里的一格发出去(不含切人)。技能键【发给当时在场的那个人】。

        走 char.send_*_key 而不是硬编码 'E'/'R'/'Q': 玩家改过键位也认。
        record_*_use 是框架自己的 CD 记账, 不记的话宏散掉之后内置逻辑会以为技能还能用。

        返回【这一格的 gap 是不是已经在这里面用掉了】—— 带保底的那两格要自己管时间。
        """
        # 【char 要在所有分支之前取】: 'A~' 那一支也要用它。原来这一行在 A/W/SPACE 后面,
        # 2026-09-22 加 'A~' 时插在了它前面 -> UnboundLocalError, 实机当场停任务
        char = self.cur if self.cur is not None else self
        # 上一格核对剑式超出的时间, 只给紧跟着的这一格(长按)扣, 见 after_form_check
        credit, self.hold_credit = self.hold_credit, 0.0
        if act == 'AX':
            # 600 长按之后就是羽强化A, 再也扣不动流响 —— 剩得多的话只能趁现在打, 见 YY_DRAIN_ABOVE
            if self.SAFETY and self.YY_EX_GATE:
                self.drain_liuxiang(f'{char.name[:2]}流响')
            act = 'A'
        if act == 'A~':
            # 千咲的电锯长按。【先确认在电锯模式】: E*(齿轨轮回)被吞的话这一下退化成普通普攻。
            # 【入场等待要提到补 E 前面】: 这一格紧跟在切人后, 补的那发 E 一样会被入场动画吞。
            # 等完把 last_was_switch 清掉, 免得下面的 'A' 分支再等一次
            self.settle_before_hold(hold)
            self.last_was_switch = False
            # 【先补 R 再补 E】: R 回 40 点残响, 顺带把 E 的条件垫上
            if (self.SAFETY and self.CHI_R_RETRY and not self.chi_r_fired
                    and self.lib_ready_now(char, 2)):
                self.logger.info(f'{char.name}: R! 那一格没放出来, 电锯前补一次 '
                                 f'(万缕·汇终要盖住后面这串锯环·疾攻)')
                self.macro_mark(f'{char.name[:2]}电锯补R')
                if self.fire_r(char, f'{char.name[:2]}R补', self.LIB_FIRE_WINDOW):
                    self.chi_r_fired = True
                    self.wait_hud_back(tag=f'{char.name[:2]}R补anim',
                                       time_out=self.LIB_ANIM_TIME_OUT)
            state = self.chi_saw_state() if (self.SAFETY and self.CHI_SAW_CHECK) else None
            if self.SAFETY and self.CHI_SAW_GATE and state == 'gear':
                # 回场时图标还是齿轨轮回 = 上一格那下强化E 没放出去, 现在补(见 CHI_SAW_CHECK)
                self.logger.info(f'{char.name}: 电锯前 E 图标还是齿轨轮回 — 上一格强化E 没生效, 补按')
                char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}电锯补E(还是齿轨轮回)')
                self.chi_in_saw = True
            elif self.SAFETY and self.CHI_SAW_GATE and state == 'e1':
                if not self.chi_in_saw:
                    self.macro_mark(f'{char.name[:2]}电锯前E是解弦之眼(不补)')
            elif self.SAFETY and self.CHI_SAW_GATE and not self.chi_in_saw:
                self.logger.info(f'{char.name}: 上一格没进电锯模式 — 长按前补一发 E(齿轨轮回)')
                self.macro_mark(f'{char.name[:2]}电锯补E')
                self.chi_in_saw = self.cast_enhanced_e(char, tag=f'{char.name[:2]}补',
                                                       cap=self.CHI_SAW_RETRY_WAIT)
            act = 'A'
            self.chi_in_saw = False      # 这一串打完接着就是锯环·终结, 电锯模式随之结束
        if act == 'A':
            # 【切人之后紧跟着的长按要多等一下】, 见 HOLD_AFTER_SWITCH
            self.settle_before_hold(hold)
            self.tap(hold)
            return False
        if act == 'AYC' and self.skip_cang:
            # 变奏后流响没空, 这一轮不放苍(见 YY_SKIP_CANG): 不长按, 等变奏入场演完就接 Q
            self.skip_cang = False
            self.macro_mark(f'{char.name[:2]}跳过1苍')
            self.macro_wait(self.YY_SKIP_CANG_WAIT)
            return True
        if act == 'AYC':
            # 1苍: 重击早就出手了, 松手时刻只决定后面 Q/R 什么时候按, 见 YY_CANG_TRIM
            hold = max(0.0, hold - self.YY_CANG_TRIM)
            act = 'AY'
        if act == 'AY' and credit > 0:
            # 上一格(E)核对剑式多用的时间从这下长按里扣: 重击是跟着 E 的动作出的, 不跟按下时刻,
            # 松手时刻照宏表不变
            hold = max(hold * 0.5, hold - credit)
        if act == 'AY':
            # 秧秧的长按(1苍/1羽/第2羽)。按满表里的时长, 期间只读图标打日志
            self.hold_traced(hold, f'{char.name[:2]}左键{hold:.2f}')
            return False
        if act == 'RY':
            return self.cast_yy_lib(char, hold, gap)
        if act == 'F?':
            return self.break_or_wait_tail()
        if act == 'W':
            # 向前走一下(循环宏第一格)。WASD 是游戏默认键位, 框架没把它放进 key_config
            self.task.send_key('w', down_time=hold)
            self.macro_mark('走W')
            return False
        if act == 'SPACE':
            self.task.send_key(self.task.key_config.get('Jump Key'), down_time=hold)
            self.macro_mark('跳')
            return False
        if act == 'E+':
            # 强化E1: 在同样长的一格里连发, 盖过变奏入场的 QTE 锁。见 E_SPAM_INTERVAL
            if not self.SAFETY:
                char.send_resonance_key(down_time=hold)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}强化E1')
                return False
            self.spam_key(char.send_resonance_key, hold + gap, interval=self.E_SPAM_INTERVAL,
                          tag=f'{char.name[:2]}强化E1', poke=False)
            char.record_resonance_use()
            return True
        if act == 'E#':
            # 按到这个技能进 CD 为止。【先见过亮才认暗】(press_key_until_cd), 刚切进来那几帧
            # 技能栏还是上一个人的, 不带这道门会当场假成功
            if not self.SAFETY:
                char.send_resonance_key(down_time=hold)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}E')
                return False
            entry = time.time()
            self.press_res_until_cd(char, self.E_CD_CAP, tag=f'{char.name[:2]}E',
                                     ready_grace=self.E_READY_GRACE)
            char.record_resonance_use()
            # 【判据成立也要把这一格的时间走完】: 提前走等于把后面每一格都往前挪,
            # 宏的节奏就不是宏表那个节奏了(切人确认也是这么处理的)
            left = (hold + gap) - (time.time() - entry)
            if left > 0:
                self.macro_wait(left)
            return True
        if act == 'E1':
            # 穗穗的强化E(醒春潮)。判据是 suisui_e1 这张图, 见 SUI_E1_GATE 那段注释
            if not self.SAFETY or not self.SUI_E1_GATE:
                char.send_resonance_key(down_time=hold)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}E')
                return False
            entry = time.time()
            tag = char.name[:2]
            if self.fill_sui_e1(char, tag):
                self.cast_sui_e1(char, tag)
                char.record_resonance_use()
            elif self.SUI_E1_SKIP_IF_NOT_READY:
                # 【回路没满就不按】—— 按下去是普通E, 白吃一次 CD 还是进不了濯雨时
                self.macro_mark(f'{tag}强化E跳过(回路没满)')
            else:
                char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                char.record_resonance_use()
                self.macro_mark(f'{tag}E(普通, 回路没满)')
            # 【判据成立也要把这一格的时间走完】: 提前走等于把后面每一格都往前挪
            left = (hold + gap) - (time.time() - entry)
            if left > 0:
                self.macro_wait(left)
            return True
        if act in ('R!', 'R3'):
            if act == 'R3':
                # 【穗穗专用的回路闸】: 三段回路没满就先接着点普攻, 满了立刻往下走
                self.wait_forte3(f'{char.name[:2]}回路')
            tag = f'{char.name[:2]}R'
            if self.tight and self.SAFETY:
                # 烟岚窗口里: 先直接连发, 没进才攒能量, 见 fire_r_tight
                fired = self.fire_r_tight(char, tag)
                if char.name == 'Chisa':
                    self.chi_r_fired = fired
                if fired:
                    self.wait_hud_back(tag=f'{tag}anim', time_out=self.LIB_ANIM_TIME_OUT)
                else:
                    self.macro_mark(f'{tag}_没出手(不补演出gap)')
                return True
            # 【能量不够就先点普攻攒】。放在回路闸后面: 那一段本来就在点 A, 能量顺带也涨了,
            # 先让它跑完再读, 免得白等一轮
            # 【接住返回值】: 它是"图标到底亮没亮", 下面决定还要不要走补救轮
            filled = self.fill_liberation(char, f'{char.name[:2]}能量')
            if not self.SAFETY:
                char.send_liberation_key(down_time=hold)
                char.record_liberation_use()
                self.macro_mark(f'{char.name[:2]}R')
                return False
            # 【连发到 HUD 消失, 不是"发一次然后盯着"】(2026-09-22 实机改的)。
            # 宏表里穗穗/千咲的 Q 那格【gap 就是 0】, R 紧跟着 Q 按下去会落在声骸动作里
            # 被游戏丢掉 —— 演出期间的按键是被丢弃的、不排队, 盖住整段只能靠连发。
            # 旧写法是"发一次 + watch_hud_gone 盯满整格", 症状是【白烧掉一整格 4.35 秒】
            # 才发现没出手再去补救(实录 SuR(没出,A x22) / ChR(没出,A x19))。
            # 启动轮之所以碰巧是好的, 是因为回路闸在前面等了 1.3 秒, 正好给了 Q 演完的时间
            fired = self.fire_r(char, tag, self.LIB_FIRE_WINDOW)
            if not fired and filled:
                # 【只有"能量闸说满了却没出手"才值得补救】—— 那种是按键被吞(收招/入场锁),
                # 多按几下能救回来。
                # 【能量闸自己就说没满的, 补救是白烧】: 它刚盯着 liberation_available()
                # 点了几秒图标都没亮 = 能量真的不够, 这时大招按 100 下也不出手。
                # 实机 21:31 那一轮正是这样: `ChR(x24, icon lit=False)` 之后
                # `ChR补救(x40, icon lit=False)` —— 整整 2 秒按了 40 下, 一次都没可能中。
                # 这 2 秒是千咲"站着不动"里最没道理的一段
                self.logger.info(f'{char.name}: 大招连发 {self.LIB_FIRE_WINDOW:.1f}s 没出手 — 再补救')
                fired = self.fire_r(char, f'{tag}补救', self.R_EXTRA_CAP)
            elif not fired:
                self.macro_mark(f'{tag}_能量不够(跳过补救)')
                self.logger.info(f'{char.name}: 能量闸就没攒满, 大招这一格直接放弃 — '
                                 f'补救那 {self.R_EXTRA_CAP:.1f}s 按不出来, 留给后面的格子')
            if char.name == 'Chisa':
                self.chi_r_fired = fired
            if fired:
                self.wait_hud_back(tag=f'{tag}anim', time_out=self.LIB_ANIM_TIME_OUT)
                # 【演出结束就走, 不补足 gap】(2026-09-22 实机: 千咲强化E "还是卡一下")。
                # 表里这一格的 gap 写的【就是演出时长】(千咲 3700 / 穗穗 4350), 是作者
                # 拿秒表估的; 而 wait_hud_back 是等 HUD 真的回来 —— 它比 gap 准。
                # 演出实测 3.49s 而 gap 给 3.75s, 补足那 0.26s 是纯站着, 紧跟着的强化E
                # 就被这 0.26 秒往后推。其余格子照旧补足(那些 gap 是节奏, 不是演出时长)
                return True
            # 【没出手就不补 gap】: 这一格的 gap(千咲 3700 / 穗穗 4350)写的【就是演出时长】,
            # 上面 fired 那一支的注释已经说了。既然大招压根没放出来, 也就没有演出要等 ——
            # 补足它只是让千咲多站 0.5 秒, 后面每一格跟着往后推
            self.macro_mark(f'{tag}_没出手(不补演出gap)')
            return True
        if act == 'E*':
            # 千咲的强化E。【先等收招】: 上一格是她的大招, HUD 回来 != 能出招
            if not self.SAFETY or not self.CHI_E_STAR:
                char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}E')
                return False
            entry = time.time()
            quick = False
            if self.tight and self.CHI_E_QUICK:
                # 收招那 TIGHT_CHI_R_TO_E 不干等, 趁 HUD 刚回来图标还没闪先认一次, 见 CHI_E_QUICK
                quick, w = self.chi_e_quick_lit(self.TIGHT_CHI_R_TO_E)
            self.macro_wait(max(0.0, (self.TIGHT_CHI_R_TO_E if self.tight else self.CHI_R_TO_E)
                                - (time.time() - entry)))
            if self.tight:
                # 烟岚窗口里的快速版: 读一次 -> 不亮才短攒 -> 按一次就走, 不回头确认。
                # 为什么这么做见 TIGHT_CHI_FILL_SAW。结果(按之前亮没亮)留给 A~ 那一格
                # 【稳定亮够 CHI_E_LIT_HOLD 才算】, 单帧/两帧会被技能栏闪白骗(见 CHI_E_LIT_MAX)
                ready = quick
                if not ready:
                    ready, w = self.chi_e_stable_lit(self.TIGHT_CHI_E_READ)
                if not ready and self.TIGHT_CHI_FILL_SAW > 0:
                    ready = self.fill_saw_resonance(char, tag=char.name[:2],
                                                    cap=self.TIGHT_CHI_FILL_SAW)
                self.send_e_once(f'{char.name[:2]}强化E({"快" if quick else "亮" if ready else "没亮"},'
                                 f'白{w:.3f},{time.time() - entry:.2f}s)', char)
                self.chi_in_saw = ready
                if not ready:
                    self.logger.info(f'{char.name}: 残响没攒满就按了强化E — 电锯那一格会补一发')
                left = (hold + gap) - (time.time() - entry)
                if left > 0:
                    self.macro_wait(left)
                return True
            # 残响不够就先点普攻攒够 —— 干等是等不来的。
            # 【它返回的就是"够没够"】, 所以下面直接按, 不再走 cast_enhanced_e 的
            # "等图标亮"那一遍: 同一个图标刚读过, 再确认一次纯粹多花 0.12 秒读屏,
            # 而用户要的就是"R 完就按"
            ready = self.fill_saw_resonance(char, tag=char.name[:2])
            self.send_e_once(f'{char.name[:2]}强化E({"亮" if ready else "没亮"})', char)
            self.chi_in_saw = ready and self.confirm_saw_mode(char, char.name[:2])
            if not ready:
                self.logger.info(f'{char.name}: 残响没攒满就按了强化E — 这一轮多半没进电锯')
            left = (hold + gap) - (time.time() - entry)
            if left > 0:
                self.macro_wait(left)
            return True
        if act in ('EY', 'EY0'):
            if not self.SAFETY or not self.YY_EX_GATE:
                char.send_resonance_key(down_time=hold)
                char.record_resonance_use()
                self.macro_mark(f'{char.name[:2]}E')
                return False
            entry = time.time()
            tag = f'{char.name[:2]}强化E'
            ready, clicks = self.wait_yy_ex(tag, fill=0.0 if act == 'EY0' else None)
            if act == 'EY0' and not ready and self.YY_SKIP_CANG:
                self.skip_cang = True
                self.macro_mark(f'{tag}流响没空,不按E,这一轮不放苍')
                self.logger.warning(f'{tag}: 变奏后流响没空(前面普攻没打中?) —— 普通E 在羽剑式按不出来, '
                                    f'这一轮放弃 1苍, 直接 Q R, 保住后面两个羽; 见 YY_SKIP_CANG')
                self.after_form_check(entry, hold, gap)
                return True
            self.send_e_once(f'{tag}({"亮" if ready else "没亮"},A x{clicks},'
                             f'{time.time() - entry:.2f}s)', char)
            self.fix_yy_form(tag, '羽' if act == 'EY' else '苍', char)
            if self.YY_EX_SNAPSHOT:
                self.yy_ex_snapshot('after')
            self.after_form_check(entry, hold, gap)
            return True
        if act in ('E苍', 'E羽'):
            entry = time.time()
            if self.already_form(act[1]):
                # 已经是这个剑式了(上一轮/这一轮前面错过一次), 再按反而翻走 —— 不按
                self.macro_mark(f'{char.name[:2]}E(已是{act[1]},不按)')
                self.after_form_check(entry, hold, gap)
                return True
            char.send_resonance_key(down_time=hold)
            char.record_resonance_use()
            self.macro_mark(f'{char.name[:2]}E')
            self.fix_yy_form(f'{char.name[:2]}E', act[1], char)
            self.after_form_check(entry, hold, gap)
            return True
        if act == 'E':
            char.send_resonance_key(down_time=hold)
            char.record_resonance_use()
        elif act == 'R':
            char.send_liberation_key(down_time=hold)
            char.record_liberation_use()
        elif act == 'Q':
            char.send_echo_key(down_time=hold)
            char.record_echo_use()
        else:
            raise ValueError('unknown macro action ' + repr(act))
        self.macro_mark(f'{char.name[:2]}{act}')
        return False

    def macro_switch(self, target, hold, con_full, gap):
        """宏里的数字键。返回【这一格的 gap 是不是已经用掉了】。

        SAFETY 关掉 = 按一下就走, 人按宏就是这样, 也继承了宏的风险: 上一个动作还没演完时
        游戏会拒绝切人, 这一下就白给, 后面每一格都发在错的人身上。
        SAFETY 开着 = 【在这一格自己的 gap 里盯到场】: 正常 0.1 秒就到, 剩下的 gap 照等,
        时序一点不变; 没到就按 SWITCH_REPRESS 补按, 最多再多等 SWITCH_EXTRA。
        con_full 只喂给框架记账(has_intro / 变奏冻结时间), 不读屏 —— 读一次协奏要几十毫秒
        到近一秒, 塞进宏的间隙里就把时序推歪了。
        """
        if target is None:
            return False
        if target is self.cur:
            self.macro_mark(f'>{target.name[:3]}(already)')
            return False
        source = self.cur if self.cur is not None else self
        slot = target.index + 1
        entry = time.time()
        self.switch_key_at = entry
        self.task.send_key(slot, down_time=hold)
        if not self.SAFETY:
            self.handoff(source, target, con_full)
            self.switch_arrived_at = time.time()
            self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}')
            return False
        soft = entry + hold + gap
        hard = soft + self.SWITCH_EXTRA
        last = time.time()
        hits = 0
        arrived = False
        trace = []
        while time.time() < hard:
            self.task.next_frame()
            in_team, index, _count = self.task.in_team()
            if len(trace) < 40:
                trace.append(f'{time.time() - entry:.2f}:{index if in_team else "x"}')
            hits = hits + 1 if (in_team and index == target.index) else 0
            if hits >= self.SWITCH_CONFIRM:
                arrived = True
                break
            if time.time() - last >= self.SWITCH_REPRESS:
                self.task.send_key(slot, down_time=hold)
                last = time.time()
            self.macro_wait(0.02)
        cost = time.time() - entry
        late = time.time() - soft
        self.switch_arrived_at = time.time()
        if cost > self.SWITCH_TRACE_SLOW:
            self.logger.info(f'slow switch -> {target.name} ({cost:.2f}s): {" ".join(trace)}')
        if arrived:
            self.handoff(source, target, con_full)
            self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}'
                            f'({cost:.2f}{f",晚{late:.2f}" if late > 0.05 else ""})')
            if con_full and self.INTRO_GAP_FROM_ARRIVAL:
                # 变奏格的 gap 是入场动作锁, 从到场算(见 INTRO_GAP_FROM_ARRIVAL)
                by_arrival = self.switch_arrived_at + hold + gap - self.SWITCH_NORMAL_LATENCY
                if by_arrival > soft + 0.02:
                    self.macro_mark(f'入场锁从到场算(+{by_arrival - soft:.2f})')
                    soft = by_arrival
        else:
            # 【没切过去就照实记】: 读一次场上到底是谁, 后面几格至少发在对的人身上
            actual = self.on_field_char()
            self.macro_desync += 1
            if actual is not None and actual is not target:
                if actual is not source:
                    self.handoff(source, actual, False)
                self.cur = actual
                self.macro_mark(f'>{target.name[:3]}超时({cost:.2f},场上是{actual.name[:3]})')
            else:
                # 读不到(演出/时停里 HUD 整个不在)——只能先按目标记, 但照样算一次失步
                self.handoff(source, target, con_full)
                self.macro_mark(f'>{target.name[:3]}超时({cost:.2f},读不到场上是谁)')
            self.logger.warning(f'macro switch to {target.name} never confirmed in '
                                f'{cost:.2f}s — 游戏在拒绝切人(被打断/上一个动作没演完?)')
        left = soft - time.time()
        if left > 0:
            self.macro_wait(left)
        return True

    def run_macro(self, table, tag):
        """逐条回放一张宏表。中间【一次读屏都没有】, 时间到就走下一格。"""
        chi = self.teammate('Chisa')
        sui = self.teammate('Suisui')
        if chi is None or sui is None:
            return False
        chars = {'YY': self, 'CHI': chi, 'SUI': sui}
        planned = sum(h + g for _, h, g, _ in table) / 1000.0 * self.MACRO_SPEED
        start = time.time()
        marks = len(self.macro_marks)
        self.chi_in_saw = False        # 每轮重新记账: 电锯模式不跨轮
        self.chi_r_fired = False
        self.skip_cang = False
        self.tight = False
        self.win_armed = False
        con_peeked = False
        finished = False
        row = 0
        try:
            finished = self.play_rows(table, tag, chars, marks)
            row = len(table)
        finally:
            if not finished:
                # 【任务被停/出错也要留时间轴】: 手动停任务是异常一路抛上来的, 不写的话
                # 这一轮跑到哪、哪一格出的问题就全没了(09-26 18:27/18:29 两局就是这样)
                self.logger.info(f'{tag}(没跑完): {self.macro_timeline(marks)}')
        if finished:
            self.logger.info(f'{tag}: {len(table)} keys in {time.time() - start:.2f}s '
                             f'(table says {planned:.2f}s) — {self.macro_timeline(marks)}')
        return finished

    def play_rows(self, table, tag, chars, marks):
        """run_macro 的主体: 逐格回放。返回是不是整张表跑完了(失步放弃返回 False)。"""
        con_peeked = False
        for i, (act, hold_ms, gap_ms, _note) in enumerate(table):
            hold = hold_ms / 1000.0 * self.MACRO_SPEED
            gap = gap_ms / 1000.0 * self.MACRO_SPEED
            nxt = table[i + 1][0] if i + 1 < len(table) else ''
            if act == 'WIN':
                # 只是标记, 不按键不占时间: 窗口起点是【下一个切人键按下】那一刻
                self.tight = True
                self.win_armed = True
                continue
            if act.startswith('>'):
                con_full = act.endswith('^')
                target = chars.get(act[1:].rstrip('^'))
                if con_full and con_peeked:
                    self.macro_mark('协奏已满(提前读)')
                elif con_full and self.CON_GATE_BEFORE_INTRO and self.CON_GATE_WAIT > 0:
                    # 【变奏那一格之前先把协奏打满】, 见 CON_GATE_BEFORE_INTRO。
                    # 读到的是【现在场上那个人】的协奏圈 —— 变奏是他交出去的
                    holder = self.cur if self.cur is not None else self
                    # 【先把缓存打掉再读】: BaseChar.get_current_con() 开头是
                    # `if self.current_con == 1: return 1` —— 上一轮留下的 1 会让
                    # 整个闸门【一帧屏都不读就放行】, 等于没加。缓存只在"满协奏切出"
                    # 和重读队伍时才清, 中途切人失败那一类根本走不到那里。
                    # 清掉不影响记账: 下面拿到的 con_full 才是喂给 switch_out 的那个值
                    holder.current_con = 0
                    # 【读之前先刷一帧】: wait_con_at_least 内部不调 next_frame(),
                    # 读的是 task.frame 那张缓存帧 —— 上一格如果是纯 macro_wait(比如
                    # 末尾那格前面的 A 之后等 400ms), 读到的就是 400 毫秒前的画面。
                    # 实机见过 `等协奏(1.00,ok,A x0,0.01s)`: 0.01 秒、一下没点就"满",
                    # 快得不像真读了一次协奏环
                    self.task.next_frame()
                    to_me = target is self
                    watch = None
                    if (self.SAFETY and self.CHI_CON_E2 and holder is chars.get('CHI')):
                        self._chi_e2_done = False
                        self._chi_e2_next = 0.0
                        watch = lambda h=holder: self.chi_con_e2(h)
                    con_full = self.wait_con_at_least(
                        holder, self.CON_FULL_LEVEL,
                        tag=f'{holder.name[:2]}等协奏{"→秧" if to_me else ""}',
                        cap=self.CON_GATE_WAIT_INTRO if to_me else self.CON_GATE_WAIT,
                        on_poll=watch)
                    if not con_full:
                        # 【文案要跟着目标走】: 带 ^ 的格子现在有两个(交秧秧的、交穗穗的),
                        # 后果不一样 —— 只有交给秧秧那次丢的是羽挟苍空
                        who = target.name if target is not None else '下一个人'
                        extra = ('(羽挟苍空没了, 末尾的 1苍 会打空; 烟岚也拿不到)'
                                 if target is self else '')
                        self.logger.warning(
                            f'{tag}: {holder.name} 协奏没打满就到变奏格了 — 这一下按平切记账, '
                            f'{who} 拿不到变奏入场{extra}')
                if self.tight and act.endswith('^') and self.win_t0 > 0 and self.SAFETY:
                    # 翾舞1 之前千咲不能走(前置还没挂上), 见 YANLAN_MIN
                    early = self.YANLAN_MIN - self.time_elapsed_accounting_for_freeze(self.win_t0)
                    if early > 0:
                        self.macro_mark(f'等翾舞1({early:.2f})')
                        self.macro_wait(early)
                consumed = self.macro_switch(target, hold, con_full, gap)
                self.last_was_switch = True
                if self.win_armed:
                    self.win_armed = False
                    self.win_t0 = self.switch_key_at
                    self.win_log = []
                    self.macro_mark(f'烟岚窗口起(按键@{self.win_t0 - self.macro_t0:.2f})')
                elif self.tight:
                    self.win_log.append((act[1:].rstrip('^'), self.time_elapsed_accounting_for_freeze(
                        self.win_t0) - (time.time() - self.switch_key_at)))
                    if act.endswith('^'):
                        self.close_yanlan_window(tag, con_full)
            else:
                consumed = self.macro_key(act, hold, gap)
                self.last_was_switch = False
            con_peeked = False
            if not consumed:
                if (nxt.startswith('>') and nxt.endswith('^') and self.SAFETY
                        and self.CON_GATE_BEFORE_INTRO and self.CON_GATE_WAIT > 0
                        and gap >= self.CON_PEEK_LEAD + 0.05):
                    # 下一格是变奏: 趁这段空等提前读一次协奏, 见 CON_PEEK_LEAD
                    end = time.time() + gap
                    self.macro_wait(gap - self.CON_PEEK_LEAD)
                    con_peeked = self.peek_con_full(self.cur if self.cur is not None else self)
                    left = end - time.time()
                    if left > 0:
                        self.macro_wait(left)
                else:
                    self.macro_wait(gap)
            if self.macro_desync >= self.MACRO_ABORT_DESYNC:
                self.tight = False
                self.macro_mark(f'{tag}_放弃(失步x{self.macro_desync})')
                self.logger.warning(
                    f'{tag}: 切人累计 {self.macro_desync} 次没确认, 这一轮宏放弃 —— '
                    f'交回框架, 下一次从循环宏开头重来 (被打断了?)')
                return False
        return True

    def peek_con_full(self, holder):
        """读一次 holder 的协奏满没满(真满 1.0, 不是 0.99 那个哨兵值)。只读不点。

        先把 BaseChar 的缓存打掉、再刷一帧, 理由跟 run_macro 里协奏闸那段一样。
        """
        holder.current_con = 0
        self.task.next_frame()
        try:
            return float(holder.get_current_con()) >= self.CON_FULL_LEVEL
        except Exception:
            return False

    def close_yanlan_window(self, tag, con_full):
        """变奏切秧秧那一下按出去了: 算烟岚窗口花掉的游戏时间, 打进日志。只记不改流程。

        起点和终点都是【切人键按下】的时刻(协奏闸在按键之前, 所以等协奏的时间算在里面)。
        游戏时间 = 墙钟扣掉窗口里大招演出的时停(wait_hud_back 记进 freeze 的那几段)。
        """
        self.tight = False
        wall = self.switch_key_at - self.win_t0
        game = (self.time_elapsed_accounting_for_freeze(self.win_t0)
                - (time.time() - self.switch_key_at))
        # 翾舞1(YANLAN_XUANWU)那一刻场上是谁: 窗口里最后一次切给千咲 ~ 这一下变奏
        chi_in = max([t for who, t in self.win_log[:-1] if who == 'CHI'] or [-1.0])
        on_chi = 0 <= chi_in <= self.YANLAN_XUANWU <= game
        self.macro_mark(f'烟岚窗口({game:.2f}s,翾舞1:{"千咲" if on_chi else "不是千咲"})')
        msg = (f'{tag}: 烟岚窗口 游戏时间 {game:.2f}s (墙钟 {wall:.2f}s; 千咲最后一段 '
               f'{chi_in:.2f}~{game:.2f}s, 翾舞1 约 {self.YANLAN_XUANWU:.2f}s, 实录两轮 6.13/6.11s)')
        if not con_full:
            self.logger.warning(msg + ' — 千咲协奏没打满, 这一下不是变奏, 烟岚拿不到')
        elif not on_chi:
            self.logger.warning(msg + ' — 翾舞1 那一刻场上不是千咲, 烟岚前置没挂在她身上, '
                                      '秧秧这一轮吃不到烟岚; 看时间轴里哪一格晚了')
        else:
            self.logger.info(msg)


    # ==================== 各格用到的读屏/按键小工具 ====================

    def yy_ex_ready(self):
        """秧秧的 E 现在是不是强化E(流响打空了)。读不到返回 None。判据见 YY_EX_GATE。"""
        return self.yy_ex_read()[0]

    def yy_ex_read(self):
        """同 yy_ex_ready, 顺带返回读数 (结果, 环, 环外, "^^")。环外/"^^" 没算到的是 -1。"""
        try:
            box = self.task.get_box_by_name('box_resonance')
            frame = self.task.frame
            cx, cy, bh = box.x + box.width / 2, box.y + box.height / 2, box.height
            fh, fw = frame.shape[:2]
            ang = [i * 2 * math.pi / 48 for i in range(48)]

            def cover(k):
                n = 0
                for a in ang:
                    x = int(round(cx + k * bh * math.cos(a)))
                    y = int(round(cy + k * bh * math.sin(a)))
                    if not (0 <= x < fw and 0 <= y < fh):
                        continue
                    b, g, r = (int(v) for v in frame[y, x][:3])
                    # 【放宽过一次】: 实机 2560x1440 上那圈暗红环按 r-g>=30/r>=b/r>=60 只覆盖 0.56
                    # (录屏里 0.77), 放宽到下面这组后 0.94; 录屏 18 个样本 + 236 帧普通段仍零误判
                    if r - g >= 25 and r + 15 >= b and r >= 50:
                        n += 1
                return n / len(ang)

            ring = max(cover(k) for k in self.YY_EX_RING_K)
            if ring < self.YY_EX_RING_MIN:
                return False, ring, -1.0, -1.0
            out = max(cover(k) for k in self.YY_EX_OUT_K)
            if out <= self.YY_EX_OUT_MAX:
                return True, ring, out, -1.0
            # 背景整片染红: 看 "^^"(图案右下角那两道白色折角)
            x0, x1 = int(cx + 0.10 * bh), int(cx + 0.42 * bh)
            y0, y1 = int(cy + 0.12 * bh), int(cy + 0.48 * bh)
            patch = frame[y0:y1, x0:x1, :3]
            if patch.size == 0:
                return False, ring, out, -1.0
            chev = float((patch.min(axis=2) >= 190).mean())
            return (ring >= self.YY_EX_RING_MIN_FLOOD and chev >= self.YY_EX_CHEV_MIN), ring, out, chev
        except Exception as e:
            self.logger.info(f'yy_ex_ready probe failed ({e})')
            return None, -1.0, -1.0, -1.0

    def yy_ex_snapshot(self, kind):
        """把 E 图标(连外面那圈环)和流响条存成一张小 png, 排查用。开关 YY_EX_SNAPSHOT。"""
        if not self.YY_EX_SNAPSHOT:
            return
        try:
            frame = self.task.frame
            box = self.task.get_box_by_name('box_resonance')
            fh, fw = frame.shape[:2]
            cx, cy, h = int(box.x + box.width / 2), int(box.y + box.height / 2), int(box.height * 1.1)
            icon = frame[max(0, cy - h):cy + h, max(0, cx - h):cx + h, :3]
            bar = frame[int(fh * 0.875):int(fh * 0.93), int(fw * 0.41):int(fw * 0.58), :3]
            # 图标保持原尺寸(看得清那圈环), 流响条放大到同样高
            bar = cv2.resize(bar, (icon.shape[0] * bar.shape[1] // max(1, bar.shape[0]), icon.shape[0]))
            img = cv2.hconcat([icon, bar])
            folder = os.path.join('logs', 'yy_ex')
            os.makedirs(folder, exist_ok=True)
            name = f'{time.strftime("%H%M%S")}_{int(time.time() * 1000) % 1000:03d}_{kind}.png'
            ok, buf = cv2.imencode('.png', img)
            if ok:
                buf.tofile(os.path.join(folder, name))
                self.logger.info(f'yy_ex snapshot saved: {folder}/{name}')
        except Exception as e:
            self.logger.info(f'yy_ex snapshot failed ({e})')

    def yy_ex_after_check(self, tag):
        """按完 E 之后读一次图标并存图(排查用)。强化E 按出去了: 环灭、流响回满。只记录不补按。"""
        self.task.next_frame()
        ready, ring, out, chev = self.yy_ex_read()
        self.yy_ex_snapshot('after')
        if ready:
            self.macro_mark(f'{tag}按后环还亮')
            self.logger.warning(f'{tag}: 按完 E 0.2s 图标还是强化态(环{ring:.2f}/环外{out:.2f}) — '
                                f'这一下 E 多半没按出去, 剑式可能没切(后面苍/羽会错位)')
        else:
            self.macro_mark(f'{tag}按后环灭')

    def yy_form(self):
        """秧秧现在是哪个剑式: 读 E 图标中间的图案(见 YY_FORM_CHECK)。返回 (苍/羽/None, 分差)。"""
        try:
            if not self.task.in_team()[0]:
                return None, 0.0
            tv = getattr(self, '_yy_glyph_vecs', None)
            if tv is None:
                raw = base64.b64decode(''.join(self.YY_GLYPHS))
                arr = np.frombuffer(raw, np.uint8).reshape(8, 16, 16).astype(np.float32)
                tv = self._yy_glyph_vecs = [self.glyph_vec(a) for a in arr]
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
            score = [float(v @ t) for t in tv]
            cang, yu = max(score[:4]), max(score[4:])
            margin = abs(cang - yu)
            if max(cang, yu) < self.YY_FORM_MIN_SCORE or margin < self.YY_FORM_MARGIN:
                return None, margin
            return ('苍' if cang > yu else '羽'), margin
        except Exception as e:
            self.logger.info(f'yy_form probe failed ({e})')
            return None, 0.0

    def fix_yy_form(self, tag, target, char):
        """按完秧秧的 E 之后核对剑式, 不是 target 就补一次 E(见 YY_FORM_CHECK)。只在读得准时动手。"""
        if not (self.SAFETY and self.YY_FORM_CHECK):
            return
        pressed = time.time()
        fixes = wrong = 0
        seen = None
        while True:
            self.macro_wait(0.03)
            self.task.next_frame()
            form, margin = self.yy_form()
            now = time.time()
            if form == target:
                if fixes:
                    self.macro_mark(f'{tag}剑式补成{target}')
                return
            if form is not None:
                seen = form
                if now - pressed >= self.YY_FORM_WRONG_AFTER:
                    wrong += 1
            if wrong >= 2:
                if fixes >= self.YY_FORM_FIX:
                    self.macro_mark(f'{tag}剑式还是{form}(不再补)')
                    self.logger.warning(f'{tag}: 补了 E 剑式还是{form}(该是{target}) —— 不再按, '
                                        f'这一轮后面的苍/羽会错位')
                    return
                self.yy_ex_snapshot('form')
                self.macro_mark(f'{tag}剑式是{form}→补E')
                self.logger.warning(f'{tag}: 按完 E 剑式还是{form}(该是{target}, 分差{margin:.2f}) '
                                    f'—— 这一下 E 没生效, 补一次')
                char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
                char.record_resonance_use()
                fixes += 1
                wrong = 0
                pressed = time.time()
                continue
            if now - pressed >= self.YY_FORM_WINDOW:
                if seen is None:
                    self.macro_mark(f'{tag}剑式读不到')
                return

    def already_form(self, target):
        """按秧秧的普通 E 之前: 连着两次(隔 >= 0.03s)都读到已经是 target 剑式才算。读不准就当不是。"""
        if not (self.SAFETY and self.YY_FORM_CHECK):
            return False
        first = None
        for _ in range(4):
            self.task.next_frame()
            form, _margin = self.yy_form()
            if form != target:
                return False
            if first is None:
                first = time.time()
            elif time.time() - first >= 0.03:
                return True
            self.macro_wait(0.02)
        return False

    def after_form_check(self, entry, hold, gap):
        """E 那一格收尾: 宏表的时间还没用完就等完; 核对剑式用超了, 超出的记给紧跟的长按去扣。"""
        left = (hold + gap) - (time.time() - entry)
        if left > 0:
            self.macro_wait(left)
        else:
            self.hold_credit = min(0.5, -left)

    def liuxiang_level(self):
        """秧秧流响条还剩多少(0~1)。读不到返回 None。

        条是一格格粉色竖条从左往右排, 没满的部分是灰点; 取【从最左边连着的粉色】能延伸到哪。
        流响空了整条换成蓝羽毛 -> 0。截图上量过 300 多张: 强化E 亮的时候 95/99 读 0,
        按完强化E 回满读 0.99~1.00, 进 'AX' 那一格读 0.11~0.29 跟肉眼一致。
        """
        try:
            frame = self.task.frame
            fh, fw = frame.shape[:2]
            x0, x1 = int(fw * self.LX_BAR_X[0]), int(fw * self.LX_BAR_X[1])
            y0, y1 = int(fh * self.LX_BAR_Y[0]), int(fh * self.LX_BAR_Y[1])
            hsv = cv2.cvtColor(frame[y0:y1, x0:x1, :3], cv2.COLOR_BGR2HSV)
            pink = ((hsv[..., 0] >= 140) & (hsv[..., 0] <= 168)
                    & (hsv[..., 1] >= 80) & (hsv[..., 2] >= 150))
            cols = [i for i, v in enumerate(pink.mean(axis=0)) if v >= 0.15]
            width = x1 - x0
            if not cols or cols[0] > width * 0.05:
                return 0.0
            gap = max(2, int(width * 0.03))   # 竖条之间的空隙
            right = cols[0]
            for c in cols[1:]:
                if c - right > gap:
                    break
                right = c
            return (right + 1) / width
        except Exception as e:
            self.logger.info(f'liuxiang_level probe failed ({e})')
            return None

    def drain_liuxiang(self, tag):
        """'AX' 那一格: 流响还剩超过 YY_DRAIN_ABOVE 才点普攻打, 打到这条线就停。

        剩得不多就一下不点, 直接去长按。先等技能栏换过来(YY_EX_SETTLE), 不然读到的是上一个人的条。
        读不到(HUD 被盖住)就当没事, 照表走。
        """
        start = time.time()
        self.macro_wait(max(0.0, self.YY_EX_SETTLE - (start - self.switch_arrived_at)))
        first = level = None
        clicks = high = 0
        next_click = first_click = 0.0
        while True:
            self.task.next_frame()
            level = self.liuxiang_level() if self.task.in_team()[0] else None
            if level is None:
                # HUD 一时被特效盖住: 给 0.1 秒再读, 还读不到就照表走
                if time.time() - start < self.YY_EX_SETTLE + 0.10:
                    self.macro_wait(0.02)
                    continue
                break
            if first is None:
                first = level
            if level <= self.YY_DRAIN_ABOVE:
                break
            if self.yy_ex_read()[0]:
                # E 图标已经是强化态 = 流响空了(条上残留的是特效/没刷新)
                level = 0.0
                break
            # 【连着两帧都高才动手】: 特效盖在条上时会整条读成满(录屏上见过 0.28 -> 1.00 -> 0.28),
            # 读错成高就是白打一串普攻、连带千咲那格慢下来; 读错成低最多是这一轮照老样子不打
            high += 1
            if high < 2:
                self.macro_wait(0.02)
                continue
            if high == 2:
                self.yy_ex_snapshot('first')
            now = time.time()
            if now - start >= self.YY_DRAIN_CAP:
                self.yy_ex_snapshot('cap')
                break
            if clicks and now - first_click >= self.YY_DRAIN_NO_PROGRESS and level > first - 0.02:
                self.macro_mark(f'{tag}打不动')
                self.logger.info(f'{tag}: 点了 {now - first_click:.2f}s 流响 {first:.2f} -> {level:.2f} '
                                 f'没降, 多半打不到怪 —— 不点了')
                break
            if now >= next_click:
                if not clicks:
                    first_click = now
                self.click()
                clicks += 1
                next_click = now + self.YY_EX_FILL_INTERVAL
            self.macro_wait(0.02)
        if first is None:
            self.macro_mark(f'{tag}读不到(不打)')
        elif not clicks:
            seen = f'{first:.2f}' if level is None or level == first else f'{first:.2f}/{level:.2f}'
            self.macro_mark(f'{tag}{seen}(不打)')
        else:
            got = '' if level is None else f'{level:.2f}'
            self.macro_mark(f'{tag}{first:.2f}->{got}(A x{clicks},{time.time() - start:.2f}s)')

    def wait_yy_ex(self, tag, fill=None, mark_ok=False):
        """等秧秧的强化E 可用(= 流响打空): 先只读(刚切进来技能栏还没换过来), 没亮再边点普攻边读。

        fill: 点普攻的上限(秒), 默认 YY_EX_FILL; 0 = 只读不点(读 YY_EX_READ_ONLY 秒)。
        返回 (亮没亮, 点了几下)。读不到(None)按"亮"处理 —— 退回原来的照表走。
        """
        fill = self.YY_EX_FILL if fill is None else fill
        window = fill if fill > 0 else self.YY_EX_READ_ONLY
        start = time.time()
        settle = max(0.0, self.YY_EX_SETTLE - (start - self.switch_arrived_at))
        streak = clicks = 0
        next_click = 0.0
        trace = []
        last_trace = 0.0
        snapped = False
        while True:
            # 【先读再点】: 已经亮着的话一下都不点, 直接去按 E(原来先点一下再读, 白打一下普攻)
            self.task.next_frame()
            hud = self.task.in_team()[0]
            ready, ring, out, chev = self.yy_ex_read() if hud else (False, -1.0, -1.0, -1.0)
            if ready is None:
                return True, clicks
            if len(trace) < 40 and time.time() - last_trace >= 0.09:
                last_trace = time.time()
                trace.append(f'{last_trace - start:.2f}:{ring:.2f}/{out:.2f}/{chev:.2f}'
                             f'{"" if hud else "x"}')
            streak = streak + 1 if ready else 0
            if streak >= self.YY_EX_CONFIRM:
                if clicks:
                    self.macro_mark(f'{tag}打空流响({time.time() - start:.2f}s,A x{clicks})')
                    self.logger.info(f'{tag}: 环/环外/^^ trace — {" ".join(trace)}')
                else:
                    self.macro_mark(f'{tag}读数{ring:.2f}/{out:.2f}/{chev:.2f}')
                if mark_ok and not clicks:
                    self.macro_mark(f'{tag}(已空)')
                self.yy_ex_snapshot('ok')
                return True, clicks
            now = time.time()
            elapsed = now - start
            if not ready and not snapped and hud and elapsed >= settle:
                snapped = True
                self.yy_ex_snapshot('first')
            if elapsed >= settle + window:
                break
            if fill > 0 and elapsed >= settle and not ready and now >= next_click:
                self.click()
                clicks += 1
                next_click = now + self.YY_EX_FILL_INTERVAL
            self.macro_wait(0.02)
        self.yy_ex_snapshot('cap')
        self.macro_mark(f'{tag}没亮(CAP,A x{clicks})')
        if fill > 0:
            self.logger.info(f'{tag}: {fill:.1f}s 点了 {clicks} 下普攻流响还没打空(多半没打中) — '
                             f'照往下走, 这一轮多半少 1 点苍翎; 环/环外/^^ trace — {" ".join(trace)}')
        else:
            self.logger.info(f'{tag}: 流响没打空, 这时点普攻打出来的是羽强化A(不扣流响还会吃掉第二个羽), '
                             f'不点; 环/环外/^^ trace — {" ".join(trace)}')
        return False, clicks

    def send_e_once(self, tag, char=None):
        """发一次 E(按住 M_E_KEY_DOWN)。

        【秧秧的 E 绝对不能连发】: 剑式切换每按一次换一个形态, 连发两下等于换回去了。
        千咲那边也用它: 强化E 多按一下会真的放出解弦之眼, 吃掉 CD。
        """
        char = self if char is None else char
        char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
        char.record_resonance_use()
        self.macro_mark(tag)

    def chi_e_stable_lit(self, window):
        """只读不点: window 秒内千咲 E 图标能不能稳定亮够 CHI_E_LIT_HOLD。返回 (亮没亮, 最后读数)。"""
        start = time.time()
        lit_since = None
        w = -1.0
        while True:
            self.task.next_frame()
            w = self.e_icon_white() if self.task.in_team()[0] else -1.0
            now = time.time()
            if not self.chi_e_lit(w):
                lit_since = None
            elif lit_since is None:
                lit_since = now
            if lit_since is not None and now - lit_since >= self.CHI_E_LIT_HOLD:
                return True, w
            if now - start >= window:
                return False, w
            self.macro_wait(0.02)

    def chi_e_glyph(self):
        """千咲 E 图标的图案: 'gear'(齿轨轮回, 强化E 还在) / 'e1'(解弦之眼, 亮或冷却) / None(读不准)。"""
        try:
            tv = getattr(self, '_chi_glyph_vecs', None)
            if tv is None:
                raw = base64.b64decode(''.join(self.CHI_GLYPHS))
                arr = np.frombuffer(raw, np.uint8).reshape(6, 16, 16).astype(np.float32)
                tv = self._chi_glyph_vecs = [self.glyph_vec(a) for a in arr]
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
            score = [float(v @ t) for t in tv]
            gear, e1 = max(score[:2]), max(score[2:])
            if max(gear, e1) < 0.6:
                return None
            if gear - e1 >= self.CHI_GLYPH_MARGIN:
                return 'gear'
            if e1 - gear >= self.CHI_GLYPH_MARGIN:
                return 'e1'
            return None
        except Exception as e:
            self.logger.info(f'chi_e_glyph probe failed ({e})')
            return None

    def chi_con_e2(self, char):
        """千咲等协奏的时候每 0.2s 看一眼 E 图标, 变回齿轨轮回就按一次(见 CHI_CON_E2)。"""
        now = time.time()
        if self._chi_e2_done or now < self._chi_e2_next:
            return
        self._chi_e2_next = now + 0.20
        self.task.next_frame()
        if not self.task.in_team()[0] or self.chi_e_glyph() != 'gear':
            return
        self.macro_wait(0.03)
        self.task.next_frame()
        if self.chi_e_glyph() != 'gear':
            return
        self._chi_e2_done = True
        char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
        char.record_resonance_use()
        self.macro_mark(f'{char.name[:2]}等协奏时补强化E')
        self.logger.info(f'{char.name}: 等协奏时 E 图标变回齿轨轮回 —— 按一次进电锯, 靠锯环·终结打满协奏')

    def chi_saw_state(self):
        """千咲回场、电锯长按之前: 连着两次读到同一种 E 图案才算, 最多读 CHI_SAW_CHECK_CAP。"""
        start = time.time()
        prev = None
        while time.time() - start < self.CHI_SAW_CHECK_CAP:
            self.task.next_frame()
            g = self.chi_e_glyph() if self.task.in_team()[0] else None
            if g is not None and g == prev:
                return g
            prev = g
            self.macro_wait(0.015)
        return None

    def chi_e_quick_lit(self, window):
        """大招 HUD 刚回来那几帧认强化E(理由和判据见 CHI_E_QUICK): 锚点读数在
        [阈值, CHI_E_QUICK_FIRST_MAX], 隔 >= 0.03s 的读数在 [锚点 - CHI_E_QUICK_DROP, CHI_E_QUICK_MAX]
        就算亮。只读不点。返回 (亮没亮, 最后读数)。
        """
        start = time.time()
        anchor = None        # (读数, 时刻)
        w = -1.0
        while time.time() - start < window:
            self.task.next_frame()
            w = self.e_icon_white() if self.task.in_team()[0] else -1.0
            now = time.time()
            held = anchor is not None and anchor[0] - self.CHI_E_QUICK_DROP <= w <= self.CHI_E_QUICK_MAX
            if held and now - anchor[1] >= 0.03:
                return True, w
            if not held:
                first = self.CHI_E_LIT_THRESHOLD <= w <= self.CHI_E_QUICK_FIRST_MAX
                anchor = (w, now) if first else None
            self.macro_wait(0.01)
        return False, w

    def chi_e_lit(self, w):
        """千咲 E 图标的白像素占比算不算"强化E 亮了": 在 [阈值, 上限] 之间; 超过上限是闪白。"""
        return self.CHI_E_LIT_THRESHOLD <= w <= self.CHI_E_LIT_MAX

    def e_icon_white(self):
        """E 技能图标框里的白像素占比。读不到时返回 -1, 不让它把轴卡住。"""
        try:
            box = self.task.get_box_by_name(self.CHI_E_LIT_BOX)
            return self.task.calculate_color_percentage(self.CHI_E_LIT_COLOR, box)
        except Exception as e:
            self.logger.info(f'e icon probe failed ({e})')
            return -1.0

    def fill_liberation(self, char, tag, cap=None):
        """大招图标不亮(能量没满)就接着点普攻攒, 亮了立刻停手。返回攒够没有。

        【大招是能量门控不是 CD 门控】—— 判据是 liberation_available()(读图标),
        不是 has_cd('liberation')(那读的是 CD 数字, 大招没有数字)。
        上限到了照放不误: fire_r 连发两轮都不出手最多是这一轮少一个大招, 不该把宏卡死。
        """
        cap = self.LIB_FILL if cap is None else cap
        if cap <= 0:
            return True
        start = time.time()
        n = 0
        next_click = 0.0
        while time.time() - start < cap:
            # 【先点再读】, 理由见 wait_forte3 里那段
            now = time.time()
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.LIB_FILL_INTERVAL
            self.task.next_frame()
            try:
                if self.task.in_team()[0] and char.liberation_available():
                    if n:
                        self.macro_mark(f'{tag}({time.time() - start:.2f}s,A x{n})')
                    return True
            except Exception:
                pass
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(CAP,A x{n})')
        self.logger.info(f'{tag}: 大招能量 {cap:.1f}s 没攒满(点了 {n} 下 A) — 照按')
        return False

    def confirm_saw_mode(self, char, tag):
        """发完强化E 回头确认进没进电锯: 齿轨轮回放出去 -> 强化态被消耗 -> E 图标暗下去。

        还亮着 = 那一下被收招/硬直吞了, 补按(见 CHI_E_CONFIRM 上面那段: 千咲补按是安全的)。
        【判据不是百分百可靠】: 进电锯之后 E 换成解弦之眼, 它不在 CD 上的话图标照样亮 ——
        所以补按次数卡死在 CHI_E_RETRIES, 别指望靠它按到"确认成功"。
        """
        if not self.CHI_E_CONFIRM:
            return True
        for attempt in range(self.CHI_E_RETRIES + 1):
            self.macro_wait(self.CHI_E_CONFIRM_WAIT)
            self.task.next_frame()
            w = self.e_icon_white()
            if w < self.CHI_E_LIT_THRESHOLD:
                self.macro_mark(f'{tag}进电锯(白{w:.3f})')
                return True
            if attempt < self.CHI_E_RETRIES:
                self.logger.info(f'{tag}: 发完 E 图标还亮着(白{w:.3f}) — 多半被收招吞了, 补按')
                self.send_e_once(f'{tag}强化E补', char)
        self.macro_mark(f'{tag}电锯没确认')
        self.logger.warning(f'{tag}: 强化E 发完图标一直亮 — 可能没进电锯(后面那串长按会'
                            f'退化成普通普攻), 也可能只是解弦之眼不在CD照样亮')
        return False

    def fill_saw_resonance(self, char, tag, cap=None):
        """千咲: E 图标不亮(= 锯环残响没满)就接着点普攻攒, 亮了立刻停手。

        常态攻击命中会回残响, 所以【干等没用, 必须动手】。上限 CHI_FILL_SAW 到了照往下走:
        这一轮少一次电锯, 好过把整条宏卡在这一格。
        """
        cap = self.CHI_FILL_SAW if cap is None else cap
        if cap <= 0:
            return True
        start = time.time()
        n = 0
        lit_since = None
        next_click = 0.0
        trace = []
        next_trace = 0.0
        while time.time() - start < cap:
            # 【先点再读】, 理由见 wait_forte3 里那段
            now0 = time.time()
            if now0 >= next_click:
                self.click()
                n += 1
                next_click = now0 + self.CHI_FILL_INTERVAL
            self.task.next_frame()
            w = -1.0
            if self.task.in_team()[0]:
                w = self.e_icon_white()
                # 稳定亮够 CHI_E_LIT_HOLD 才算(闪白见 CHI_E_LIT_MAX)
                if not self.chi_e_lit(w):
                    lit_since = None
                elif lit_since is None:
                    lit_since = time.time()
                if lit_since is not None and time.time() - lit_since >= self.CHI_E_LIT_HOLD:
                    if n:
                        self.macro_mark(f'{tag}攒残响({time.time() - start:.2f}s,A x{n})')
                    return True
            now = time.time()
            # 【只读不改流程】: 实机出现"肉眼看着亮了但没开"时, 靠这条 trace 判断
            # 到底是没亮、还是读数没过 CHI_E_LIT_THRESHOLD
            if self.CHI_FILL_TRACE and now - start >= next_trace and len(trace) < 30:
                next_trace = (now - start) + 0.25
                trace.append(f'{now - start:.2f}:{w:.3f}')
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}攒残响(CAP,A x{n})')
        self.logger.info(f'{tag}: 锯环残响 {cap:.1f}s 没攒满(点了 {n} 下 A) — '
                         f'后面 A~ 那一格还会补一发 E; 白像素 trace: {" ".join(trace)}')
        return False

    def cast_enhanced_e(self, char, tag, cap=None):
        """等 E 图标亮(= 回路能量满、强化态就绪)再按。返回读到亮没有。

        【到点没亮也照按一次】: 按了不亏 —— 落在普通E 上也是在攒回路, 而"不按"是白白
        放掉一次机会。没亮时把 trace 打进日志, 实机调阈值就靠它。
        """
        cap = self.CHI_E_LIT_MAX_WAIT if cap is None else cap
        start = time.time()
        streak = 0
        lit = False
        trace = []
        while time.time() - start < cap:
            self.task.next_frame()
            if self.task.in_team()[0]:
                w = self.e_icon_white()
                if len(trace) < 24:
                    trace.append(f'{time.time() - start:.2f}:{w:.3f}')
                streak = streak + 1 if self.chi_e_lit(w) else 0
                if streak >= self.CHI_E_LIT_CONFIRM:
                    lit = True
                    break
            self.macro_wait(self.CHI_E_LIT_POLL)
        if not lit:
            # 等不到亮也照按一次: 落在普通E 上也是在攒残响, 不按是白丢一次机会
            char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
            char.record_resonance_use()
            self.macro_mark(f'{tag}强化E(没亮,{time.time() - start:.2f}s)')
            self.logger.info(f'{tag}: E 图标没读到亮(残响没满?), 照按一次 — '
                             f'trace {" ".join(trace)}')
            return False
        if not self.CHI_E_CONFIRM:
            char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
            char.record_resonance_use()
            self.macro_mark(f'{tag}强化E(不确认)')
            return True
        # 【连发到图标暗为止】, 不是"按一次然后回头确认"。
        # 齿轨轮回放出去 -> 强化态被消耗 -> 图标暗; 还亮着就是那一下被收招/硬直吞了,
        # 而演出期间的按键是被丢弃的、不排队 —— 单发必丢, 只有连发盖得住
        char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
        char.record_resonance_use()
        if self.CHI_E_FIRE_WINDOW <= 0:
            # 单发。【不回头确认也不重按】—— 重按是"改状态"那一类, 见类文档"保底的边界"
            self.macro_mark(f'{tag}强化E(亮,{time.time() - start:.2f}s)')
            return True
        fire_start = time.time()
        n = 1
        while time.time() - fire_start < self.CHI_E_FIRE_WINDOW:
            char.send_resonance_key(down_time=self.M_E_KEY_DOWN)
            char.record_resonance_use()
            n += 1
            self.macro_wait(self.CHI_E_FIRE_INTERVAL)
            self.task.next_frame()
            w = self.e_icon_white()
            if w < self.CHI_E_LIT_THRESHOLD:
                self.macro_mark(f'{tag}进电锯(x{n},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}强化E没确认(x{n})')
        self.logger.warning(f'{tag}: 强化E 连发 {n} 次图标一直亮着 — 没进电锯模式, '
                            f'后面那串长按会退化成普通普攻')
        return False

    def sui_e1_lit(self):
        """穗穗的强化E(醒春潮)就绪没有 —— 读的是 E 图标那张强化态模板。"""
        return bool(self.task.find_one(Labels.suisui_e1, threshold=self.SUI_E1_THRESHOLD))

    def fill_sui_e1(self, char, tag):
        """强化E 图标不亮(= 回路没满)就接着点普攻攒, 亮了立刻停手。返回亮了没有。"""
        if self.SUI_E1_FILL <= 0:
            self.task.next_frame()
            return self.sui_e1_lit()
        start = time.time()
        n = 0
        next_click = 0.0
        while time.time() - start < self.SUI_E1_FILL:
            self.task.next_frame()
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
        self.macro_mark(f'{tag}攒回路(CAP,A x{n})')
        self.logger.warning(f'{tag}: 回路 {self.SUI_E1_FILL:.1f}s 没攒满(点了 {n} 下 A) — '
                            f'强化E 这一轮开不出来')
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

    def wait_forte3(self, tag):
        """穗穗大招前的保底: 三段回路没满就接着点普攻, 满了立刻停手。返回满了没有。

        上限 FORTE3_WAIT 到了【照放不误】: 少一档伤害, 好过把整轮宏卡死在这一格。
        """
        if not self.FORTE3_GATE:
            return True
        start = time.time()
        n = 0
        next_click = 0.0
        while time.time() - start < self.FORTE3_WAIT:
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
        self.macro_mark(f'{tag}没满(CAP,A x{n})')
        self.logger.warning(f'{tag}: 三段回路 {self.FORTE3_WAIT:.1f}s 没攒满(点了 {n} 下 A), '
                            f'照放 —— 表里给穗穗的 A 不够, 或者那几下被打断了')
        return False

    # ==================== 宏里几种专用的格子 ====================

    def settle_before_hold(self, hold):
        """切人之后紧跟着的长按, 先等入场演完再按下去(见 HOLD_AFTER_SWITCH)。

        【从确认到场那一刻算, 只补差额】; 烟岚窗口里换成 TIGHT_HOLD_AFTER_SWITCH。
        """
        if not (self.SAFETY and self.last_was_switch and hold >= self.HOLD_SETTLE_MIN):
            return
        settle = self.TIGHT_HOLD_AFTER_SWITCH if self.tight else self.HOLD_AFTER_SWITCH
        left = settle - (time.time() - self.switch_arrived_at)
        if left > 0:
            self.macro_wait(left)
            self.macro_mark(f'长按前等入场({left:.2f})')

    def hold_traced(self, hold, tag):
        """按住左键 hold 秒, 【中途一下都不松】; 期间隔一会儿读一次重击就绪图标打日志。

        前台时走 SendInput(见 YY_TAIL_SENDINPUT), 时间轴里 SI = SendInput, PM = PostMessage。

        秧秧的重击要连续按住才派生, 松开重按会把蓄力打断(09-22 实机 12 轮栽 10 轮),
        所以这里只读不动 —— 读屏不发输入, 不影响按住的状态。
        """
        direct = self.attack_down(self.YY_TAIL_SENDINPUT and self.game_foreground())
        start = time.time()
        trace = []
        next_read = 0.0
        try:
            while True:
                left = hold - (time.time() - start)
                if left <= 0:
                    break
                # 快到点了就别再读屏: 一次读屏几十毫秒, 会把松手推迟
                pending = getattr(self, 'yy_ex_verify_pending', None)
                if pending and time.time() - start >= 0.20 and left > 0.10:
                    self.yy_ex_verify_pending = None
                    self.yy_ex_after_check(pending)
                if (self.M_YU_TRACE and left > 0.10
                        and time.time() - start >= next_read):
                    next_read = (time.time() - start) + self.M_YU_TRACE_EVERY
                    self.task.next_frame()
                    try:
                        lit = 1 if (self.task.in_team()[0] and self.is_mouse_forte_full()) else 0
                    except Exception:
                        lit = -1
                    trace.append(f'{time.time() - start:.2f}:{lit}')
                self.macro_wait(min(0.05, max(0.0, hold - (time.time() - start))))
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}({time.time() - start:.2f}s,{"SI" if direct else "PM"})')
        if trace:
            self.logger.info(f'{tag}: forte icon trace — {" ".join(trace)}')

    def cast_yy_lib(self, char, hold, gap):
        """秧秧的大招('RY'): 连发到出手 -> 等演出结束 -> 落地 -> 等收招 M_R_TO_E。

        后面紧跟的 E 是单发(剑式切换不能连发), 被收招吞了, 后面那一路长按打出来的就是
        苍剑式普攻 —— 所以按"HUD 回来 + M_R_TO_E"走, 不按表里的 5000 盲等。
        返回【gap 是不是已经在这里用掉了】(跟 macro_key 一致)。
        """
        tag = f'{char.name[:2]}R'
        if not self.SAFETY:
            char.send_liberation_key(down_time=hold)
            char.record_liberation_use()
            self.macro_mark(tag)
            return False
        spent = 0.0
        if self.fire_r(char, tag, self.M_R_WINDOW):
            self.wait_hud_back(tag=f'{tag}anim', time_out=self.LIB_ANIM_TIME_OUT)
            t0 = time.time()
            self.check_echo_used(char)
            spent = time.time() - t0      # 读声骸那一下算在收招的等待里, 不往后推 E
        else:
            self.logger.info(f'{tag}: 秧秧大招没出手(能量没满?), 照走 —— 少 1 点苍翎, '
                             f'1羽 可能打不出来')
        if self.M_WAIT_DOWN_BEFORE_E and self.flying():
            self.logger.info(f'{tag}: 大招收尾人还在空中, 先等落地再按 E')
            self.macro_mark(f'{tag}等落地')
            self.wait_down()
        self.macro_wait(max(0.0, self.M_R_TO_E - spent))
        return True

    def check_echo_used(self, char):
        """R 演完读一次声骸: R 前那下 Q 真放出去了, 这时应该还在 CD 里(CD 20s)。只记录不补按, 见 YY_CANG_TRIM。"""
        try:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                return
            if char.echo_available():
                self.macro_mark('Q没出')
                self.logger.warning(f'{char.name}: 大招演完声骸不在 CD —— R 前那下 Q 没放出来'
                                    f'(按早了被丢 / 被 R 顶掉?), 常出现就把 YY_CANG_TRIM 往回调')
            else:
                self.macro_mark('Q在CD')
        except Exception as e:
            self.logger.info(f'echo check failed ({e})')

    def settle_after_break(self):
        """处决完等秧秧收招(见 YU_AFTER_BREAK_SETTLE), 不点不按 —— 下一条宏第一格就是 W/E。"""
        if self.YU_AFTER_BREAK_SETTLE > 0:
            self.macro_wait(self.YU_AFTER_BREAK_SETTLE)
            self.macro_mark(f'处决后等收招({self.YU_AFTER_BREAK_SETTLE:.2f})')

    def break_or_wait_tail(self):
        """两条宏的最后一格('F?')。返回 True(时间自己管)。

        有处决提示就按 —— 处决取消羽的后摇, try_f_break 等 HUD 回来才返回, 再等 YU_AFTER_BREAK_SETTLE 收招。
        没有就等 YU_NO_BREAK_TAIL 让后摇演完, 期间一直探, 提示晚几帧才亮也照按。
        """
        if self.try_f_break('收尾'):
            self.settle_after_break()
            return True
        end = time.time() + self.YU_NO_BREAK_TAIL
        while True:
            left = end - time.time()
            if left <= 0:
                break
            self.macro_wait(min(self.YU_BREAK_POLL, left))
            if time.time() < end and self.try_f_break('收尾(后摇里)'):
                self.settle_after_break()
                return True
        self.macro_mark(f'没处决等后摇({self.YU_NO_BREAK_TAIL:.2f})')
        return True

    # ==================== 两条轴 ====================

    def perform_opener(self, entry_char=None):
        """启动轴 = 启动宏一张表跑完(含末尾的 2羽1苍 和处决)。
        返回时秧秧在场, 下一次 do_perform 还是她, 接循环宏。"""
        chi, sui, previous = self.macro_enter(entry_char)
        if chi is None:
            return False
        self.logger.info(f'yangqiansui opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            # 【启动宏第一格是穗穗的 A】: 开局场上本来就该是她(1 号位), 不是的话先切过去
            if self.cur is not sui:
                self.quick_switch(sui)
            self.run_macro(self.OPENER_MACRO, '启动宏')
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴的一步 = 循环宏一张表跑完。返回时秧秧在场(跟启动轴同一个位置)。"""
        chi, sui, previous = self.macro_enter(entry_char)
        if chi is None:
            return False
        try:
            # 循环宏前三格(W/E/A)在秧秧身上; 宏散掉之后框架可能把别人放上来了, 先切回自己
            if self.cur is not self:
                self.quick_switch(self)
            self.loop_count += 1
            n = self.loop_count
            self.run_macro(self.LOOP_MACRO, f'循环宏#{n}')
            self.logger.info(
                f'loop step #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def run_opener_from(self, entry_char):
        """跑启动轴并落防重入标记。【穗穗那份也调它】—— 启动宏第一格是她的普攻,
        正常情况下入口在她身上, 不在这里。

        标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑。
        """
        self.opener_armed = False
        self.last_opener = time.time()
        return self.perform_opener(entry_char)

    def do_perform(self):
        """本队里秧秧驱动两条轴。两条都在末尾处决(或等完后摇)后返回, 下一次框架调的还是她。"""
        if not self.is_in_yangqiansui:
            return super().do_perform()
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 走到这儿说明启动轴待跑但场上是秧秧(穗穗没被框架放上来) ——
            # perform_opener 里会先切回穗穗再开表
            if self.run_opener_from(self):
                return
            self.logger.warning('yangqiansui opener aborted, falling back to the native rotation')
            return super().do_perform()
        self.logger.info(f'yangqiansui: opener not pending ({blocked}), running the loop')
        if self.perform_loop(self):
            return
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """宏散掉之后, 框架选人时秧秧优先 —— 她一上场轴就接上了。

        【启动轴待跑时反过来给 NO】: 启动宏的第一格是穗穗的 A, 场上得是穗穗才对得上,
        秧秧抢先上场会把整张表错开一格。其余时候给 HIGH。
        """
        if self.is_in_yangqiansui:
            if self.opener_pending():
                return SwitchPriority.NO
            return SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
