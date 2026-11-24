import base64
import ctypes
import threading
import time

import cv2
import numpy as np

from src import text_white_color
from src.char.BaseChar import SwitchPriority, forte_white_color
from src.Labels import Labels
from src.char.Augusta import Augusta as NativeAugusta
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


class Augusta(MacroKit, NativeAugusta):
    """奥古斯塔。日月守(奥古斯塔/尤诺/守岸人)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    猫眼石「日月守 25s 标准轴翻新」(2旋2升), 本地 E:/轴视频/【鸣潮】轮切也有大提升…日月守25s标准轴翻新教学.mp4,
    守岸人手法取自同作者的守岸人手法教学。教学段 168~252s 是慢放 + 定格, 时机按游戏倒计时量;
    循环演示 252~292s 是 1 倍速(日那段倒计时被面板挡住, 拿技能栏 CD 当钟)。代码【按角色切人】, 编队随意。
    配置按用户的: 尤诺首位声骸海之女(Q 正常按, 不用闪避断); 共鸣效率参考 日125 / 月128 / 守275。

    ================= 轴的形状 =================
    启动轴(从日开始, 三人快切; 括号里是视频游戏秒):
      日 R(誓锋不殒)                       -> 守 A1A2A3 -> E           (1.40)
      -> 月 E(原初的律动)                  (0.25) -> 日 E(斩锋, 战势回满) (0.88)
      -> 守 A2A3 -> 重击                   (1.04) -> 月 E(告终的喧响)     (0.21)
      -> 日 后撤 -> 旋切                   (0.86) -> 守 A1A2A3 -> 重击 -> Q -> R(演出里一直点 A) -> A 出手就切 -> 变奏月
      -> 月 / 日 同循环轴 -> 变奏守
    循环轴(每圈从守岸人变奏入场开始, 25 秒):
      守  A1A2A3 -> E -> A 一下(A2) -> 重击 -> Q -> R(打断重击后摇; 演出里一直点 A) -> A 出手就切(A1 可脱手, +1 回路留给下一圈) -> 变奏月
      月  Q -> R -> 跳 + 闪避 -> 弓 A x3 -> E -> 闪避 -> A -> E -> 重击(至臻的完满) -> 变奏日
      日  撤Z切: 长按出后撤 -> 一看到后撤就松开 -> 再长按出重击·鸣铁 -> 第一段出伤就松开短按出旋切
          z升拳: 旋切里预输入长按(前台走 SendInput) -> 54F 一到出鸣铁打满战势 -> 松开出升拳(战势本来就满 -> 短按跳跃出升拳)
          E2 迅击 -> E3 跃空 -> E4 落袭 -> 落地 E1 斩锋
          长按后撤(松手带出旋切) -> 长按鸣铁(出伤就松) -> R1(誓锋不殒, 断鸣铁后摇) -> R1 横劈砍完时长按 R(赫日威临)
          -> 俯首之刻 点 9 下烈阳 + 接着一直点到不朽者之肃的镜头起来 -> 镜头演完 HUD 回来
          -> 长按鸣铁到战势满 -> 有处决提示就按着鼠标按 F(处决) -> 松手 -> 处决演完跳升拳; 没提示松手 = 升拳
          -> 战势一掉就变奏守(升拳可脱手, 切走后照样打完)。鸣铁只按一次, 没打满(怪跑了 / 转火)就不升拳直接变奏守
    宏在【守岸人变奏入场、HUD 回来】时返回, 下一次框架调的就是她; 返回点不落在任何演出里。

    ================= 为什么这么打(nanoka 技能文本 + 帧表) =================
    - 日: 战势满时长按 = 后撤(清空战势, 给 16~240F 旋切许可, 许可被别的动作打断后 2s 内还能补一次旋切);
      战势不满时长按 = 鸣铁(3 段 x17.54 ≈ 52 战势)。所以 后撤 -> 鸣铁 -> 旋切 能多吃一发鸣铁(撤Z切),
      两发鸣铁凑满战势, 鸣铁期间战势满了松手就是升拳。斩锋 3 段 x33.85 一下回满战势。
      后撤长按【一定时间后松开】自己就出旋切 —— 撤Z切要的是 后撤 -> 鸣铁打断 -> 补旋切, 所以看到后撤就得松。
      战势文本上限 100、实际上限 650(帧表特殊能量): 变奏 +100 叠在残留上, 撤Z切之后可能已经满了,
      这时长按出的是后撤不是鸣铁; 满战势时短按跳跃 = 升拳(技能原文), 升拳那一下要先看战势条再决定按住还是跳。
      权柄: 变奏 +20、R1 +40、斩锋 +10; 满了 E 变成 E2/E3/E4, 落袭清空权柄 +1 威慑。
      威慑 2 层时长按 R = 赫日威临(不吃能量, 吃 2 层威慑): 一层来自落袭, 一层来自守岸人带着日的延奏 14s 内变奏出去
      (开局那层来自脱战 4s 的炽盛决意)。短按 R 松开 = 誓锋不殒(吃能量 + 25s 冷却, 这条轴一圈的长度就是它的冷却)。
      R1 最后一刀(誓锋不殒-4 第81F≈1.35s)开始长按 R2, 早了会吞掉这一刀; 视频 R1 -> R2 ≈1.3s。
      俯首之刻里只能放烈阳 / 不朽者之肃 / 闪避, 9 次烈阳后再点一下放不朽者之肃(第220F前不响应输入)。
    - 月: 变奏 78F 不响应输入; R 给 15s 月相、隐藏 HUD 234F、第239F前不响应输入;
      月相里跳 = 换弓(第1F就换), 同时按闪避把 103F 的变弓动画闪掉; 弓 E 两格充能, 第二发之后协奏满、
      左下那格换成至臻的完满(iuno_heavy)。
    - 守: A1/A2 各 1、A3 2 段实论数据, 回路满才按重击(不满按出来是化蝶); E 之后短时间点 A 直接出 A2;
      重击出手(准星旁指示暗)就可以切人/闪避断后摇; R 演完协奏就满, 切人时偷一下 A 给下一圈攒协奏。

    ================= 判据 =================
    - 日 战势: 读技能栏上面那条 34 格(aug_zs)。准星旁的鼠标指示不是战势 —— 战势满 / 权柄满 / 2 层威慑哪个能用哪个亮
      (10-18 15:12 实机升拳按住时权柄先满、指示亮, 战势才 18/34 就松了手)。
    - 日 后撤出没出: 战势条见过满之后掉了(白条没认出来时: 20 格以上一下少 12 格), 或准星旁指示见过亮之后暗
      (谁先成立用谁) = 长按被认成后撤、战势扣掉了。
      撤Z切那一下一扣就松(晚松会自己带出旋切, 10-18 实机); 后撤旋切那两下再按 AUG_DARK_TO_RELEASE 让松手带出旋切。
      斩锋后那次按到上限战势还满 = 长按按在动作里没被认, 再按一次。
    - 日 鸣铁出没出: 战势条比按住期间最少的格数多 3 格 = 第一段出伤(鸣铁要等后撤收招才起手, 按固定时长会松在起手之前)。
      撤Z切的升拳和斩锋后的 R1 都从这一刻起算。
    - 日 升拳: 起手前战势已满 = 隔 AUG_RISE_JUMP_GAP 按一下跳到战势条掉; 没满 = 按住鸣铁到战势满松手, 松手后还满就改跳
      (撤Z切那一下不验证不跳, 松手后 AUG_RELEASE_TO_E 就连按 E);
      按住 AUG_RISE_NOGAIN 秒一格没涨 = 长按按在旋切里没被认(旋切 派生54F 之前按下的长按整段不认), 松手隔一下再按, 最多 AUG_RISE_HOLDS 次。
    - 日 E 串: 派生 E 不进冷却, E 框出现斩锋的冷却数字(>=8s) = 整串打完、斩锋出手。
    - 日 R1: R 框冷却数字没了、图标亮(Augusta_lib1/lib2)才按; 冷却中就等; 冷却好了还暗 = 能量不够(见保底)。
      R2 能不能放 = 按 R1 之前 R 图标是 lib2。1 层威慑: 按出去 = R 框出现冷却数字(或 HUD 连续消失)。
      2 层威慑: R1 放出去 HUD 不藏、R 键立刻变成赫日威临可用、不出冷却数字(10-18 实机), 判不到 ——
      只短按两下, 从最后一下起 AUG_R1_TO_R2 长按 R, 进没进俯首之刻再查。
      俯首之刻里队伍 HUD 一直不在, 9 次烈阳打完 = 战势指示亮, 之后一直点到指示灭(不朽者之肃镜头起来, 只点一下会被第 9 下吞掉);
      不朽者之肃有镜头, HUD 回来再起手升拳
      (跨镜头按着的左键回来后游戏不认, 10-18 实机按着站了 2.4s)。
    - 月 形态: 左下那格的图案(iu_form, 16x16 灰度字形): 长枪 = 环(跳 = 变弓) / 星球 = 弓(跳 = 变环)。
      内置模板 iuno_jump 两个都认, 分不出形态(10-18 15:12 实机弓形态放的 R 演完又跳了一下, 变回环, 后面全打歪)。
      R 演完 HUD 一回来: 已经是弓就不跳; 是环就连按跳到读到星球, 马上闪避; 读不出形态才退回 iuno_jump 的老判法。
      弓 E1 = E 框出现充能冷却数字; 重击可放 = iuno_heavy 亮。
    - 守: 回路满 = 准星旁指示亮; 重击出手 = 指示暗。守的变奏入场可能带镜头(HUD 藏一会儿), 按 HUD 回来和入场锁里晚的那个起算。
    - 协奏判满走 con_now(): 框架 0.99 哨兵里面积已经满的也算满。

    ================= 保底 =================
    - 只花时间的(切人重试 / 等 HUD / 等冷却 / 没按出去就再按同一个键)默认开。
    - 能量(用户 2026-10-18 选的「R 不亮才补」):
      尤诺变奏入场锁过了 R 冷却好了却不亮 -> 先补一发告终 E、按 Q, 再读一次 R; 还不亮就跳上去换弓(告终给了月相)
      一直连点弓 A(不分段, 分段会让连段从 A1 重来), 每 IU_R_BLIND_EVERY 秒在两下普攻之间夹按一下 R(不停手), 亮了立刻在空中放;
      弓形态不按 E(会打掉后面要用的两格充能); 没月相才在地面连点环 A、E 亮了就按。
      弓形态放的 R 演完还是弓, 不跳;
      日该放 R1 时冷却好了却不亮(或按了放不出来) -> 点普攻攒到亮再放, 并且从这一圈起每圈升拳后处决、补一下 Q 再变奏守
      (正常的圈处决夹在鸣铁打满和升拳之间)。R 亮着就照常打, 不多按任何东西。
    - 守的 R 不亮先攒(回路满打强化重击、否则普攻); 协奏不满不交棒, 先补(E > 强化重击 > 普攻)。

    ================= 改这个文件之前先读 =================
    - 这个文件是拼出来的(team_backup/build_scripts/Augusta__Iuno__ShoreKeeper): 引擎(macro_* / quick_switch / fire_r ...)
      逐字抄自 Hsin__Rover__Suisui/Hsin.py(心雷穗 v1.1.0), 改引擎去改构建脚本的来源, 别直接改这里。
    - 守岸人数值是日月守 9-03 / 景月守 9-28 实机标定过的; 日的 AUG_ENTRY_HOLD / AUG_HEAVY_HOLD 是 9-02 用户实机给的。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 普攻一律走 tap()(按住 A_TAP 秒): task.click() 只按 10ms, 打不出来。
    - 长按期间不调 click()/mouse_down()/send_key() —— 它们先投 WM_ACTIVATE, 会把按住打断。
      例外: 收尾鸣铁走 SendInput 按住(系统层面的左键), 按着它发 F 处决(aug_end_rise)。
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
    # 面积已经满了但环没认成闭合(特效压在环上)时框架报 0.99 哨兵: 面积比在这个范围连这么多帧就当满(心卜穗 10-09)
    CON_SOFT_RATIO = (1.01, 1.15)
    CON_SOFT_FRAMES = 3
    CON_POLL = 0.03
    CON_MIN_SETTLE = 0.20

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 保底 ====================
    SWITCH_RETRIES = 2           # 切人超时后先等 HUD 回来再切, 最多再试这么多次(被击飞/硬直时游戏不放人)
    SWITCH_RETRY_TIME_OUT = 3.00
    # 队伍 HUD 连续这么久没见过 = 关卡结束/过场/窗口没了, 整圈放弃。
    # 最长的一段是日 R1 镜头 -> 俯首之刻(右边头像不显示) -> 不朽者之肃: 视频 286.5 -> ~295s, 9 秒左右, 所以比别的轴放宽;
    # 不朽者之肃没点出来、等俯首之刻 7s 到时自己放的话要 13.5s(10-18 14:52 实机两次在这儿判成关卡结束, 启动轴断了)
    HUD_LOST_ABORT = 16.00
    DRIVE_HUD_CAP = 1.50         # 框架调进来时 HUD 这么久都不在就什么都不做(结算画面上框架还会一直调进来)
    TAIL_INTRO_TTL = 4.00        # "带着变奏上场"只认刚切进来这么久之内的
    # R / E / Q 到上限没出去、图标【还亮着】就等 HUD 回来再按一轮; 图标不亮(没能量/在冷却)不补
    R_TRIES = 3
    R_BLIND_EVERY = 3            # R 读成暗时每这么多轮攒能量盲按一次(防读数不准)
    R_BLIND_WINDOW = 0.40
    R_BUILD_SLICE = 0.60         # 攒能量每轮的连点时长
    R_RETRIES = 2
    R_RETRY_WINDOW = 1.50
    KEY_RETRY_CAP = 1.00
    RETRY_HUD_CAP = 2.00         # 补按前等 HUD 回来的上限(被打飞落地)
    LIT_FRAMES = 2               # 图标连这么多帧亮才补
    DODGE_HOLD = 0.07            # 闪避 = 右键
    # 处决: 只在怪身上的 F 提示(屏幕中间, 不是拾取 F)在的时候按
    BREAK_READS = 3
    BREAK_CENTER_MIN = 0.90
    BREAK_RESEND = 0.30          # 按完这么久提示还在 = 被吞了, 再按
    BREAK_PRESSES = 5
    BREAK_GONE_FRAMES = 3        # 提示单帧会闪掉, 连这么多帧不见才算处决起手了

    # ==================== 守岸人 ====================
    # A1A2A3 的连点窗口, 到这儿按 E。E 要在 A3 的延迟子飞道生成之后(圈圈特效), 子飞道可脱手, 切走后照样命中 +2:
    # 教学守1 在场 1.40 游戏秒, 切走时 2 格、回来已经 4 格。10-19 实机改成 1.15 按早了, 子飞道没出, 守2 进场只有 2 格
    # (之后 A2-1 +1、A3 子飞道 +2 才满, 守2 重击晚 0.7s、守3 进场 0 格)。1.38 时守2 进场 4 格(10-18 21:55)
    SK_A3_WINDOW = 1.38
    SK_E_CAP = 0.80              # 按 E 到进 CD 的上限
    SK_INTRO_LOCK = 1.43         # 变奏入场第86F前不响应输入: 3A 从入场锁和 HUD 回来里晚的那个起算
    SK_OP2_FORTE_CAP = 2.00      # 启动轴第二棒: A2A3 到回路满的上限, 超时照按重击
    SK_FORTE_CAP = 3.00          # 启动轴第三棒: A1A2A3 到回路满的上限
    SK_L_FORTE_CAP = 2.50        # 循环: E 之后到回路满的上限(景月守实机 1.6s 不够)
    # 循环守 E 之后普攻一下接重击: E 派生40F(0.67s) 之后点出来的是 A2, 等 A2-1(第9F)出伤再长按重击。从 E 进 CD 起算(按下 ~0.12s 后)
    SK_L_E_TO_HEAVY = 0.75
    # 守 R 演出里一直点 A(演出里的点击被丢, 一直点才能在演完那一刻出 A1), HUD 回来再点这么久才切月: A1 可脱手, 切走后守在原地打完, +1 回路留给下一圈。
    # 10-19 01:26 实机 HUD 回来才点一下、0.16s 后就切 -> A 没出来
    SK_R_END_A_AFTER = 0.15
    SK_FORTE_CONFIRM = 2         # 回路满要连着几帧读到, 单帧会被特效骗
    # 回路 5 格。实测(10-19 录屏 + 教学回路条): A1 +1、A2-1 +1, A2-2 平时不给; A3 的延迟子飞道 A3 起手后 30F/39F 两下各 +1。
    # 守岸人手法教学: 看到 A3 的圈圈特效就按 E/重击 —— 圈圈 = 子飞道已经生成, 重击掐掉 A3 它照样命中, 落在清条之后就是下一棒的格数。
    # 10-18 20:15 实机守2 进场就满、点 0.42s 就重击, A3 还没起手(A2 派生 0.50s) -> 守3 进场 0 格, 补满花了 2.3s
    # 启动轴守2(E 派生直接出 A2): 教学进场 4 格, A2-1 就满, 进场后 1.02 游戏秒重击出手, 守3 进场 2 格。
    # 10-19 实机守2 A3 子飞道在进场后 1.05s 命中; 重击按下到出手 ~0.30s, 所以 0.68s 按 -> ~0.98s 出手, 落在子飞道生成和命中之间。
    # 旧值 0.80 出手在 ~1.10s, 子飞道已经进条, 守3 进场 0 格
    SK_OP2_A_WINDOW = 0.68
    # 启动轴守3: 带守2 留的 2 格进场, A1 A2-1 到 4 格, A3 子飞道第一下才满, 满了就重击
    SK_OP3_A_WINDOW = 1.15
    HEAVY_MIN_HOLD = 0.10       # 长按之后先按这么久再盯指示(按下那一瞬指示还没变)
    SK_HEAVY_HOLD = 0.45         # 读不到鼠标指示时的兜底按住时长
    SK_HEAVY_CAP = 1.60          # 长按硬上限。指示暗了 = 重击出手, 立刻松手
    HEAVY_RETRIES = 2            # 按住期间指示一直亮着(没打出去)就再按, 最多这么多次
    SK_HEAVY_TO_QR = 0.30        # 重击松手 -> Q(9-02 用户实机)
    SK_Q_CAP = 0.60
    SK_LIB_WINDOW = 1.80         # R 的重试窗口, 要盖过强化重击的收招(86F)
    SK_R_ENSURE_CAP = 8.00       # R 不亮时攒能量的上限(打不到怪时防卡死)
    SK_R_ANIM_MIN = 1.00         # 守 R 演出中途 HUD 会闪, 这么久之内的"回来"不认
    SK_E_DERIVE = 0.67           # E-地面 派生 40F
    SK_CON_CAP = 1.00            # R 演完边 A 边等协奏满的上限(正常一读就满)
    SK_CON_RESCUE_CAP = 6.0      # 还不满就按 E > 强化重击 > 普攻 补, 这是补救上限
    SK_CON_RESCUE_SLICE = 0.80
    # 切给守岸人之后: 带镜头的变奏等 HUD 回来; 没镜头就只看一小段它会不会藏 HUD
    SK_INTRO_ANIM_CAP = 6.0
    SK_INTRO_NOANIM = 0.35
    SK_SWITCH_ANIM_HINT = 1.00   # 切她的 quick_switch 超过这么久才判到到场 = 镜头已经在里面演完
    SK_TO_TIME_OUT = 4.00        # 日 -> 守: 升拳没落地游戏不放人
    SK_ANCHOR_TTL = 3.00         # 入场演完的时刻离现在超过这么久就不作数(宏散过、从别处接进来的)
    SK_L_E_CAP = 1.00

    # ==================== 尤诺 ====================
    IU_INTRO_LOCK = 1.30         # 变奏 78F 不响应输入
    IU_E_BAR_SETTLE = 0.80       # 刚切进来技能栏还是上一个人的, 这么久都没见过尤诺的 E 亮才当它在 CD 上
    # 启动轴第一棒原初: 短连按这么久就切(视频在场 0.25s; 21:55 实机在场 0.30s)。
    # 守1 的 E -> 切回守2 要落在 E 派生 77F(1.28s)之内, 第一下 A 才是 A2; 21:55 实机这段 1.40s
    IU_OP_E1_WINDOW = 0.12
    IU_OP_E1_INTERVAL = 0.10
    IU_OP_E_CAP = 1.20           # 启动轴第二棒告终: 按到进 CD 的上限(视频在场 0.21s)
    IU_FIX_E_CAP = 2.40          # 能量保底那发告终 E 的上限
    IU_Q_CAP = 2.00              # Q 从到场起一直按到进 CD(要盖过入场锁)
    IU_R_WINDOW = 2.00
    IU_R_WAIT_CAP = 3.00         # R 还在冷却就等它转好(视频到场时 R 冷却还剩 ~1s)
    # 10-18 14:57 实机地面连点环 A 攒了 8.1s 才亮, 刚好卡在旧上限 8s 上放弃了
    IU_R_BUILD_CAP = 10.00
    # R 不亮时攒能量: 环 A 一整串约 7 能量 / 2.8s; 弓 A 一整串约 9.4 / 2.6s, 变弓那一箭 3.5(帧表大招回收)。
    # 告终 E 给 15s 月相, 所以补完告终就跳上去换弓打
    # (10-18 实机循环 #1: 补告终 E 之后还差一截, 旧写法 0.6s 一段地点、每 3 段盲按, 攒了 6.6s)
    IU_MOON_TTL = 13.0           # 补完告终这么久之内当月相还在(左下那格被至臻的完满盖住、形态读不到时用)
    # 防 R 亮度读不准: 每这么久在两下普攻之间夹按一下 R, 不停手(10-18 15:12 实机旧写法每 2s 停下来连按 0.4s R, 站着发愣)
    IU_R_BLIND_EVERY = 1.00
    IU_R_BLIND_SEE = 0.80        # 夹按的那下 R 之后这么久内 HUD 连着不在 = 放出去了
    IU_BUILD_E_GAP = 0.30
    IU_R_ANIM_MIN = 2.00         # R 隐藏 HUD 234F = 3.9s, 中途闪的"回来"不认
    IU_R_GONE_WAIT = 0.50        # R 判放出去了 HUD 却还在: 等它藏这么久, 不藏就当没镜头
    # R 演完的跳: HUD 一回来就连按(R 第239F前不响应输入, 比 HUD 晚 5F, 早按的被丢)。
    # 10-18 实机 HUD 211.3 就回来了, 旧写法等 HUD 连读两帧 + 0.10s 才跳, 211.72 才按出去 —— 站了 0.4s
    IU_JUMP_INTERVAL = 0.08
    IU_JUMP_CAP = 1.80
    IU_R_INPUT_LAG = 0.09        # HUD 回来 -> 第一下能被接的跳(5F); 弓 A 窗口从这儿起算
    IU_BOW_A_WINDOW = 2.70       # 弓 A x3, 从跳起算到按 E。视频 跳 267.6 -> 弓E1 270.45
    # R 是攒能量时在弓形态放的: 弓 A 在攒能量里打过了, R 又把回路补满, 演完直接 E1。
    # 10-19 01:58 L1/L2 照常点满 2.7s 弓 A(x23)才 E1, 回路满着多 A 那几下白拖时间
    IU_E1_CAP = 0.60             # 连按 E 等充能冷却数字出现的上限, 比 E 的 80F 锁短, 连按不会连出第二发
    # 从第一下按 E 算: 越限弦引 箭矢第50F出手(闪早了把这一箭闪掉), 第80F派生 —— 过了 80F 再闪就只是闪一下, 断不到后摇。
    # 10-18 20:40 实机从连按 E 结束(+0.61)才开始算, 闪在 E 起手后 ~1.5s, E 早收招了
    IU_E1_TO_DODGE = 0.90
    IU_E1_CD_LAG = 0.20          # 读到充能冷却数字时 E 大约已经起手这么久
    IU_DODGE_TO_A = 0.12
    IU_A_TO_E2 = 0.45            # 闪避反击那一下出伤再接第二发 E。视频 E1 270.45 -> E2 ~272.0
    IU_E2_PRESS = 0.40
    IU_HEAVY_ICON_CAP = 1.60     # 第二发 E 后等至臻的完满图标。视频 E2 ~272.0 -> 图标亮 273.0
    IU_E2_RESCUE_CAP = 2.00      # 图标一直不亮: E 和 A 交替补(没充能时按 E 是空操作)
    # R 是攒能量时在弓形态放的(没打弓 A): E2 之后至臻的完满图标要再补几下 A 才亮(10-19 02:16~02:44 九圈都是干等 1.6s 才补)。
    # 改成 E2 连按完就按住攻击: 游戏自己连普攻, 图标一亮就出强化重击; 强化E3 第80F 前攻击打断不了, 箭不会丢。图标亮过再灭 = 重击出手, 这么久封顶
    IU_E2_HOLD_CAP = 4.00
    # 左下那格(4K 中心, 半边长)的图案分辨尤诺现在是哪个形态: 长枪 = 环(跳 = 变弓) / 星球 = 弓(跳 = 变环)。
    # 内置模板 iuno_jump 两个都认(阈值 0.6), 分不出形态: 10-18 15:12 实机弓形态放的 R 演完还是弓,
    # 读到"可跳"又跳了一下, 变回环, 后面弓 A / E / 重击全打歪。
    # 16x16 灰度、圆内像素, 2K 录屏 11 帧平均; 同类 0.99~1.00、互相 0.26, 2K 和 1080 一样; 过渡/发光帧 0.6~0.8
    IU_SLOT_C = (2871, 1930.5)
    IU_SLOT_HALF = 68
    IU_FORM_MIN = 0.80
    IU_FORM_MARGIN = 0.30
    IU_SPEAR_GLYPH = ('TktJRD47Ojs8PUFFRkdMXEhEPDk4PUhCPTs7PUVERExEQD83NkZYUkg+P6I4P0RHP1g3OEk5Tm1gSnBSPTlESGREQJXxunA/',
                      'OWpvSUc5Pkh+R4+dj1Y6O1ufPFxSQjlGfD09PDs8VaHGRjxpPmk6QoI8PDw9XaHkljk8fjtkPECoOkU8ndHWuqs6PZk7PDpA',
                      '2EVYOseVo6OHO4nuSjs6QfB/XVrilsDKPDpu4Dw6OkXAyVCa1eeRQjg5RrE7PDxIafRfqqxWODo4ODlgO05AR0GmqJxPb3BL',
                      'Njc2RWQ/Q0RIYK3L6Kx4bm5mfG88QUNER1c8UIe41cCceko4QEFDTg==')
    IU_PLANET_GLYPH = ('NDU1MzEvMDAwMjc8PTw7OjMxLCkqKywsLS0uMTc6OjowKycoQCk/UCoqKiwtNDg4KicnSH+FbExBNCkqKioxMyYmd+zCuoVz',
                       'WjpJV259LTEmNt3FndAoKCdBRCgnvV8wRlTDup3gNicmJzRERN1LLrBK57qVu7MrJiYmUdCCKSsyS52bK2rDwUsmS9WgJiYq',
                       'YD5KyoqArqvg0+6lrjcmK7tKJFOwxdjj9sCh1c6ENCya7uzp+frevpu53OKReTAsL1JsfWtEXcLt3umTYEYnLSUiIiIiIiMj',
                       'KTI8UjUlKiwpJCIhISEiJjhCMSMjKCssKiklIiIiJiwjIiMlKCorKw==')
    IU_FORM_LOST = 0.60          # 跳了之后那格一直读不清这么久 = 当换好了(至臻的完满盖上来), 再跳会在变弓动画后变回环
    IU_FORM_WAIT = 0.35          # HUD 回来这么久一个形态都没读清(那格是至臻的完满等): 弓形态放的 R 不跳, 否则跳两下
    # 换弓: 告终 E 的动作(派生98F = 1.63s)里跳被丢, 上限要盖过它
    IU_TO_BOW_CAP = 2.40
    IU_GZ_ANIM = 1.60            # 读到告终冷却 -> 动作能被跳打断(读冷却比按下晚一两帧)
    IU_HEAVY_MIN = 0.20
    IU_HEAVY_CAP = 0.60          # 重击(至臻的完满)按住上限; 图标灭 = 出手
    IU_CON_CAP = 3.00            # 切日之前协奏还没满就边 A 边补 E 等这么久
    IU_CON_E_EVERY = 0.50

    # ==================== 奥古斯塔 ====================
    # ---- 后撤 ----
    # 变奏入场一进场就按住: 入场锁(68F)里的输入本来就被丢, 她一能动按着的左键就是后撤。
    # 指示读不到时按满这么久松手(9-02 用户实机: "这下对了, 长按减0.3s" 2.20 -> 1.90)
    AUG_ENTRY_HOLD = 1.90
    AUG_BACKSTEP_CAP = 0.90      # 不是变奏入场的那几次后撤(启动轴 / 斩锋后)的上限
    # 9-03 实机: 指示 0.31~0.32s 就暗, 那时松手只有后撤; 0.60 松手才带出旋切 —— 指示暗比真正出手早 ~0.3s。
    # 后撤旋切那两下要的就是松手带旋切; 撤Z切那一下反过来, 指示一暗就松(release_delay=0)。
    # 指示在权柄满时也亮(后撤吃掉战势它还亮着), 所以同时看战势条: 见过满之后掉了 = 后撤出了
    AUG_BACKSTEP_MIN = 0.60
    AUG_DARK_TO_RELEASE = 0.30
    AUG_DARK_CONFIRM = 2
    # 斩锋回满时白条常常没认出来(10-18 18:24 实机 30 格直接掉到 0, 判成 cap 多按了 0.3s、松手在后撤收招之后, 旋切没带出来):
    # 条从 AUG_BACK_DROP_FROM 格以上一下少了 AUG_BACK_DROP 格也算后撤吃掉了战势
    AUG_BACK_DROP_FROM = 20
    AUG_BACK_DROP = 12
    # 斩锋后那次后撤: 松手(条掉了 + AUG_DARK_TO_RELEASE)自己带出旋切(18:24 录屏松手 0.1s 后就冲出去了),
    # 旋切里的输入被丢, 所以紧跟着补点一下只管"松手没带出来"的情况; 鸣铁从松手起算
    AUG_BACK2_TO_SPIN = 0.10
    AUG_BACK2_TO_HOLD = 1.00
    # ---- 撤Z切 ----
    AUG_ZQ_REPRESS = 0.08        # 后撤松手 -> 再按住(出鸣铁)
    # 鸣铁按到战势条涨了(第一段出伤)才松, 松了鸣铁照样打完, 旋切的补施放有 2s。
    # 鸣铁要等后撤收招(46F)才起手, 起手后 22F 第一段出伤: 18:24 录屏后撤扣条 -> 第一段 1.08s;
    # 旧写法固定按 0.80 松手, 循环 #2 松在鸣铁起手之前, 整发鸣铁没出(战势一格没涨), 升拳多按了一轮。
    AUG_ZQ_CAP = 1.40
    # 战势条读不到时的固定按住时长: 盖过鸣铁起手(18:24 按下 +0.63 起手、+1.0 第一段)
    AUG_ZQ_HOLD = 1.05
    # 条读不到时升拳按住从按鸣铁起算: 18:24 L1 按鸣铁 195.00 -> 旋切 54F 197.21(+2.21)。条读不到升拳没法重按, 宁晚
    AUG_ZQ_BLIND_TO_HOLD = 2.35
    AUG_ZQ_TO_SPIN = 0.05
    # 鸣铁第一段出伤 -> 按住升拳。旋切在鸣铁派生41F 才起手(点早了也排到那时), 旋切 派生54F 之前按下的长按整段不认:
    # 18:24 实机按早了 0.32s 没被认, 多花 1.5s; 20:15 L1 用 1.20 还是没被认(条平在 18 格), 再留 0.2s
    AUG_ZQ_RISE_TO_HOLD = 1.40
    # 上面两次没被认都是 PostMessage 的按住(按下沿落在旋切里被丢)。游戏在前台时改走 SendInput, 在旋切里提前这么久按住,
    # 跟手按的预输入一样等旋切 54F 一到就出鸣铁。20:58 录屏 PostMessage 按下后 0.87s 战势才涨第一格, 鸣铁在按下 ~0.5s 后才起手。
    # 提前按的那次按住, 判"没涨"和上限都加上这段
    AUG_ZQ_PRE_LEAD = 0.35
    # 鸣铁按下后这么久之内的战势读数不看: 后撤扣条的动画 0.35s 里读数乱跳(19,15,13,18,24,22...)。
    # 20:15 实机启动轴读到 6->17 当成涨了, 按 0.19s 就松手, 鸣铁没出、回路不满, 升拳又多按了一轮。第一段出伤最早也在 1.0s
    AUG_ZQ_READ_FROM = 0.55
    ZS_MEDIAN = 3                # 战势格数取最近 3 帧的中值: 单帧会读错(20:15 条平在 18 读出过一帧 14)
    AUG_DARK_TO_SPIN = 0.45      # 后撤松手 -> 短按旋切(9-02 实机: 按住 0.60 松手后 0.45 再点)
    # 旋切 派生54F(0.90s)之前按下的长按整段不认, 松手只出一个 A1(+5 格):
    # 10-18 15:12 实机旋切 237.95、按住 238.27~239.42 没出鸣铁, 239.8 战势 18 -> 24 格
    AUG_SPIN_TO_HOLD = 0.95
    # ---- 战势条(技能栏上面那条 34 格, 4K 坐标) ----
    # 准星旁的鼠标指示不是战势: 战势满显示鼠标/空格、权柄满显示 E+鼠标、2 层威慑显示 R, 哪个能用哪个亮。
    # 10-18 15:12 实机升拳按住时权柄先满(239.0 指示亮), 战势才 18/34 就松了手。所以战势直接读这条:
    # 亮的格子是紫色; 满了整条变成粉白、格子上方竖起跳动的高条, 一直保持到被后撤/升拳吃掉。
    # 15:09 / 14:52 / 14:06 三段 DVR 在 2K 和缩到 1080 上验过没有误报(白背景 + 空条那几帧没有高条, 不认)
    ZS_BOX = (1620, 1940, 2185, 1996)
    ZS_TICK_L = (1630.5, 13.59)        # 左半 17 格中心: 起点 + 间距 * i
    ZS_TICK_R = (1954.05, 13.65)       # 右半 17 格(从中间往外亮)
    ZS_ROW = (1984, 1991)              # 格子本体
    ZS_TALL_ROW = (1965, 1977)         # 满了才竖起来的高条
    ZS_TICK_HW = 1.6                   # 每格取中心左右这么宽(4K 像素)
    ZS_GAP_DX = 6.8                    # 格子之间的缝(高条对比的基线)
    ZS_TALL_DIFF = 15                  # 高条: 格子列比两边缝亮这么多(G 通道)
    ZS_WHITE_MIN = 28                  # 白格这么多、紫格不超过 ZS_PURPLE_MAX = 粉白条
    ZS_PURPLE_MAX = 3
    ZS_TALL_MIN = 0.20                 # 高条占比; 满着的时候在 0.06~0.88 之间跳, 白背景上是 0
    ZS_CONFIRM = 2                     # 满/不满都要连这么多帧(放掉的那一下会单帧闪)
    # ---- 升拳 ----
    # 起手前已满: 跳 = 升拳(这时长按出的是后撤)。没满: 按住出鸣铁(3 段各 +6 格), 满了就松手 = 升拳。
    # 一次鸣铁不一定够(起手 18 格 + 18 = 满; 只有 6 格就要两次), 所以最多按 AUG_RISE_HOLDS 次;
    # 按住 AUG_RISE_NOGAIN 秒一格没涨 = 长按没被认(按在动作里), 松手(出 A1), 隔 AUG_RISE_REHOLD 再按
    AUG_RISE_HOLDS = 3
    AUG_RISE_HOLD_CAP = 1.50     # 一次按住的上限(鸣铁 3 段 = 认出重击 + 派生41F)
    AUG_RISE_NOGAIN = 1.05
    AUG_RISE_REHOLD = 0.42       # A1 派生22F(0.37s)
    AUG_RISE_VERIFY = 0.45       # 满了松手后这么久战势还满 = 升拳没出, 改跳
    AUG_RISE_AFTER = 0.40        # 没满松手后鸣铁还在出伤, 这段里满了就跳
    # 旋切/鸣铁的动作里跳会被丢, 所以隔一会儿再按; 战势条掉了 = 升拳吃掉了战势, 立刻停 —— 多跳一下会打断升拳(9-02 实机)
    AUG_RISE_JUMP_GAP = 0.20
    AUG_RISE_JUMP_CAP = 1.20
    AUG_RISE_PRE_READ = 0.15     # 起手前读战势最多读这么久(HUD 不在的帧不算)
    # 升拳(重击升龙 -100 战势在第1F, 6F/12F 出伤, 22F 起能被技能接)。判到战势掉了已经晚 2 帧, 再等这么久开始连按 E
    # (早按的被丢, 连按会补上)。18:24 实机 0.75 站着看升拳落地
    AUG_RISE_TO_E = 0.30
    # 撤Z切后那一下升拳(鸣铁按到满松手, 游戏必出升拳) -> 从松手起算这么久开始连按 E, 不等战势条掉。
    # 战势条掉得慢: 10-19 日志 91 圈松手后 0.04~0.67s 才读到掉, 0.45s 没读到的 5 圈都去跳了一下(录屏: 升拳松手 0.12s 就出了), 整串晚 0.45s;
    # 连按从松手后 0.34~0.49s 开始的圈松手->斩锋都是 3.71~3.86s(早按的被丢), 再晚开始的斩锋跟着晚
    AUG_RELEASE_TO_E = 0.35
    # ---- E 串 ----
    AUG_E_CHAIN_CAP = 4.50       # E2(0.77) + E3(0.73) + E4 下落落地(1.23) + 斩锋, 留余量
    AUG_E_CHAIN_INTERVAL = 0.10
    AUG_E1_CD_MIN = 8.0          # 斩锋冷却 15s; 读到这么大的数 = 刚放的
    AUG_E1_TO_HOLD = 0.60        # 斩锋第三段第37F出伤, 早按住会把这一段打掉、战势不满出不了后撤
    # ---- 普通重击(鸣铁) -> R1 ----
    # 旋切之后那发鸣铁: 按到战势条涨了(第一段出伤)松手, 再等 AUG_HEAVY_RISE_TO_R(三段 22F/28F/33F 打完)接 R1 断后摇。
    # 按住 AUG_RISE_NOGAIN 一格没涨 = 按在旋切里没被认, 松手隔一下再按一次。条读不到按满 AUG_HEAVY_HOLD
    AUG_HEAVY_CAP = 1.50
    AUG_HEAVY_RISE_TO_R = 0.20
    AUG_HEAVY_HOLD = 1.00        # 战势条读不到时的固定按住时长(9-02 实机 1.00~1.10 可用): 短了没打出来, 长了出升拳
    AUG_R1_WAIT_CAP = 8.00       # 等 R 冷却转好的上限(视频冷却刚好在这一刻转好, 宏快了就在这儿等)
    AUG_R_DARK_CONFIRM = 0.60    # 冷却好了还暗这么久 = 能量不够
    AUG_R_FILL_CAP = 5.00        # 能量不够时点普攻攒到 R 亮的上限
    AUG_R1_WINDOW = 1.20
    AUG_R1_PRESS_GAP = 0.15      # 短按 R 的间隔(松开才放; R1 镜头 ~0.15s 起)
    AUG_R1_TAP = 0.05
    AUG_R1_CD_MIN = 15.0         # R 冷却 25s; 读到这么大的数 = 刚放的
    # 2 层威慑时 R1 判不到(10-18 实机: 连按 11 下, HUD 一直在、R 框没出冷却数字), 只短按这么多下
    AUG_R1_TWO_PRESSES = 2
    # 带 2 层威慑、冷却好了, R 框外圈: 白粉圈 = 誓锋不殒能放; 橙圈 + 顶上三角 = 只能放赫日威临(誓锋不殒能量不够, 赫日威临不吃能量所以照样亮)。
    # 10-19 03:00 录屏: 白粉圈 粉 0.04~0.24 / 橙 <0.05, 橙圈 橙 0.07~0.30 / 粉 <0.03, 2K 和 1080 读数一样。
    # 02:55:30 L1 冷却好了一直是橙圈, 两下 R 没出誓锋不殒, 站了 1.8s 长按 R 直接放了赫日威临
    AUG_R1_PINK_MIN = 0.03
    AUG_R1_COLOR_READS = 4       # R1 前读几帧外圈
    # 白粉圈时按的 R1: 放出去 ~0.3s 外圈就暗了(随后变橙)。最后一下之后这么久还是白粉 = 两下都被吞了, 再按一轮
    AUG_R1_VERIFY = 0.60
    # ---- R2(赫日威临) ----
    # R1 最后一下按下 -> 开始长按 R。长按 ~0.46s 才认出来(18:24 录屏), R2 镜头起在 R1 起手 +1.70,
    # 横劈(誓锋不殒-4 第81F)砍完站了 0.4s; 往前挪到认出来时刚过派生88F(1.47s)。没进俯首之刻 aug_majesty 会重按
    AUG_R1_TO_R2 = 1.05
    AUG_R2_HOLD = 1.20           # 长按 R 多久(9-03 实机: 0.6 是在赌)
    AUG_R2_MISS_FRAMES = 8       # 长按完队伍 HUD 连着这么多帧在场 = 没进俯首之刻(俯首之刻里右边头像不显示)
    AUG_R2_FORTE_CAP = 8.0       # 俯首之刻点普攻到指示亮的上限(俯首之刻 7s, 到时自己放不朽者之肃)
    # 不朽者之肃: 第220F(3.67s)前不响应输入, 镜头藏 HUD(10-18 实机 点下去 0.3s 藏、3.6s 回来)
    # 9 次烈阳的指示亮时第 9 下还在出手, 这时只点一下会被丢: 10-18 14:52~14:57 三次都是点一下就停,
    # 站着等到俯首之刻 7s 到时自己放。所以一直点到指示灭(镜头起来)为止
    AUG_FINAL_SPAM_CAP = 3.00
    AUG_FINAL_GONE_WAIT = 1.00   # 点完这么久 HUD 都没藏 = 没放出来(或没镜头), 直接接升拳
    AUG_FINAL_ANIM_MIN = 2.00    # 镜头中途闪回来的 HUD 不认
    AUG_R2_TAIL_CAP = 6.00       # 等镜头演完的上限
    # 能量不够过的圈(升拳 -> 处决 -> Q -> 切): 升拳出伤再处决(18:24 实机 0.75 站着)。
    # 正常收尾是升拳战势一掉就切: 升拳可脱手; 游戏真不放人 quick_switch 也会一直按到放
    AUG_RISE_LAND = 0.40
    AUG_BREAK_ANIM = 1.60        # 日 谐度破坏 派生94F
    # 收尾鸣铁打满之后还按着鼠标: 有处决提示就点 F, 再按住这么久让处决先起手(10-19 01:10 录屏 F 之后 ~0.15s 处决起手)再松手
    AUG_END_F_HOLD = 0.15
    # 收尾鸣铁按住期间每帧都看处决提示, 见过这么多帧就算有: 鸣铁的打击特效盖着提示, 模板置信度在 0.6~0.97 之间跳
    # (10-19 01:58 录屏 op 收尾: 79.35~79.60 连着 0.3s 低于 0.90, 打满那几帧 0.70~0.86) —— 只在打满那刻读三帧会漏掉
    AUG_END_SEEN_MIN = 2
    # 打满后 F 点几下(每下 0.05s): 第一下被吞第二下补上, 处决起手后多点的那下被丢
    AUG_END_F_TAPS = 2
    AUG_END_F_GAP = 0.08
    # 收尾鸣铁: 已经打到这么多格(18 + 两段)再读到整条白, 就是第三段打满的那一下闪白
    AUG_END_WHITE_FROM = 28
    AUG_Q_CAP = 1.20             # 能量保底补的那下 Q
    AUG_CON_CAP = 3.00
    AUG_CON_POKE_AFTER = 0.80    # 升拳在空中, 这么久之内不点普攻(空中点普攻是下落攻击)
    # ---- 启动轴快切 ----
    AUG_OP_R_STAY = 0.50         # R1 按下 -> 切守(视频 0.6s 切走, 要的是 +40 权柄和冷却开始转)
    AUG_OP_E_CAP = 1.00
    # 斩锋判到(冷却出现) -> 切守。斩锋三段第37F 出伤 ≈ 判到后 0.52s, 而且三段都可脱手(切走照样打完);
    # 0.70 时守1 E -> 切回守2 要 1.40s, 出了 E 派生窗口(见 IU_OP_E1_WINDOW)
    AUG_OP_E_STAY = 0.55
    # 后撤判到松手 -> 0.10s 补点旋切 -> 松手后这么久就切守。旋切起手以后切走, 日会留在原地把旋切打完:
    # 10-18 20:58 录屏 234.87 切守, 234.8~235.1 日还在原地出圈; 视频也是守进场时日还在转(这一下日在场 0.86s)
    AUG_OP_SPIN_TO_SWITCH = 0.30
    AUG_OP_SPIN_STAY = 0.15      # 后撤没判到(cap): 点旋切之后再站这么久

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
        self.sk_intro_done_at = 0.0    # 守岸人入场演完(HUD 回来)的时刻, 循环段 3A 从这儿起算
        self.sk_arrived_at = 0.0       # 判到守岸人到场的时刻
        self.aug_energy_short = False  # 这一场里日放 R1 时能量不够过 -> 每圈升拳后补 Q
        self.iu_gz_at = 0.0            # 尤诺能量保底那发告终 E 放出去的时刻(月相 15s)
        self.iu_bow = False            # 尤诺这一棒的 R 是不是攒能量时在弓形态放的
        self.iu_r_lit = False          # iu_to_bow 跳的时候 R 亮了(没换弓就返回)
        self._zs_flash = 0             # 战势条判据的状态(aug_zs)
        self._zs_fullp = 0
        self._zs_latch = False
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
    def is_in_rys(self) -> bool:
        """本队 = 奥古斯塔 + 尤诺 + 守岸人。按类名判。"""
        members = 0
        for char in self.task.chars:
            if char and char.name in {'Iuno', 'ShoreKeeper'}:
                members += 1
        return members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_rys:
            return 'team is not augusta/iuno/shorekeeper'
        # 【开关过自动战斗 -> 启动轴重跑】: 见 start_enable_watch。放在 armed 判断之前、不管 armed 真假都放行,
        # 直到启动轴真跑了(last_opener 刷新)为止 —— 选人时三个人的 get_switch_priority 都会调进来(2026-10-11 爱琳莫)。
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
        # 玩家按过 F 就当成"真的重开了一局"直接放行: 60 秒门槛分不清"战斗判定抖动"和"玩家重开副本"
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
        if not self.is_in_rys:
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

    # ==================== 宏原语(本队补的) ====================

    def dodge(self, tag):
        """闪避(右键)。"""
        self.task.mouse_down(key='right')
        try:
            self.macro_wait(self.DODGE_HOLD)
        finally:
            self.task.mouse_up(key='right')
        self.macro_mark(f'{tag}闪避')

    def spam_until(self, deadline, tag='A'):
        """连点到某个绝对时刻为止。已经过点就立刻返回并记一笔 late —— 看到 late 去查前一格。"""
        left = deadline - time.time()
        if left <= 0:
            self.macro_mark(f'{tag}(late{-left:.2f}s)')
            return 0
        return self.macro_spam(left, self.A_INTERVAL, tag=tag)

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

    def wait_lit(self, read, cap, tag):
        """等 read() 连 LIT_FRAMES 帧为真(HUD 在场时), 最多 cap 秒。超时也返回(判据只管时机)。"""
        start = time.time()
        hits = 0
        while time.time() - start < cap:
            self.task.next_frame()
            if self.hud_ok():
                try:
                    hits = hits + 1 if read() else 0
                except Exception:
                    hits = 0
                if hits >= self.LIT_FRAMES:
                    return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(cap{cap:.1f}s)')
        return False

    def icon_lit(self, available):
        """HUD 在场、图标连 LIT_FRAMES 帧亮。补按之前用: 只有"还亮着"才说明上一轮没出去。"""
        return self.lit_now(available, self.LIT_FRAMES)

    def press_echo(self, char, cap, tag, ready_grace=None):
        """声骸 Q 按到进 CD(先见过亮)。被打断就补一轮。"""
        return self.press_sure(char.send_echo_key, char.echo_available, cap, tag, ready_grace=ready_grace)

    def con_full_now(self, settle=0.20):
        """协奏连 CON_FULL_CONFIRM 帧读到满才算。read_con 返回的是这段里的最大值, 一帧读到 1.0 就过了:
        10-18 20:40 实机守 R 演完 HUD 刚回来 0.1s 就切月, 月没拿到变奏、R 不亮。settle 至少 CON_MIN_SETTLE, 给够连读的帧。"""
        start = time.time()
        hits = 0
        while True:
            self.task.next_frame()
            self.hud_ok()
            try:
                con = self.con_now()
            except Exception:
                con = 0.0
            hits = hits + 1 if con >= self.CON_ENOUGH else 0
            if hits >= self.CON_FULL_CONFIRM:
                return True
            if time.time() - start >= max(settle, self.CON_MIN_SETTLE):
                return False
            self.macro_wait(self.CON_POLL)

    def warn_no_con(self, full, who, nxt):
        """协奏没满就交棒时记一笔 —— 下一个人拿不到变奏入场。"""
        if not full:
            self.logger.warning(f'【协奏没满就交棒】{who} -> {nxt}: {nxt}拿不到变奏入场')
            self.macro_mark(f'{who}协奏不满!')
        return full

    def r_state(self, char):
        """char(必须在场)的 R: 'lit' 能放 / 'cd' 冷却中 / 'dark' 冷却好了但不亮(能量不够) / None HUD 不在。读当前帧。"""
        if not self.hud_ok():
            return None
        if self.read_cd('liberation') > 0.05:
            return 'cd'
        try:
            if char is self:
                lit = bool(self.liberation_available()) or bool(self.check_majesty())
            else:
                lit = bool(char.liberation_available())
        except Exception:
            lit = False
        return 'lit' if lit else 'dark'

    def wait_r_ready(self, char, cap, tag, dark_confirm):
        """等 R 能放: 冷却中就等(不按任何东西); 冷却好了连 dark_confirm 秒不亮 = 能量不够。
        返回 'lit' / 'dark' / 'cap'。"""
        start = time.time()
        lit = 0
        dark_since = None
        first_cd = None
        while time.time() - start < cap:
            self.task.next_frame()
            st = self.r_state(char)
            if st == 'lit':
                lit += 1
                if lit >= self.LIT_FRAMES:
                    break
            else:
                lit = 0
            if st == 'cd':
                dark_since = None
                if first_cd is None:
                    first_cd = self.read_cd('liberation')
            elif st == 'dark':
                dark_since = dark_since or time.time()
                if time.time() - dark_since >= dark_confirm:
                    self.macro_mark(f'{tag}R不亮(冷却好了,{time.time() - start:.2f}s)')
                    return 'dark'
            self.macro_wait(0.03)
        else:
            self.macro_mark(f'{tag}R等冷却CAP({cap:.1f}s)')
            return 'cap'
        waited = time.time() - start
        if waited > 0.15:
            self.macro_mark(f'{tag}R等冷却({waited:.2f}s,cd{first_cd if first_cd is not None else 0:.1f})')
        return 'lit'

    def forte_lit(self, char=None):
        """准星旁的鼠标指示(回路满; 日是战势满 / 权柄满 / 2 层威慑任一个)。读当前帧。"""
        char = self if char is None else char
        try:
            return bool(char.is_mouse_forte_full())
        except Exception:
            return False

    def aug_zs_raw(self):
        """日的战势条当前帧 -> (紫格数, 白格数, 高条占比, 最后亮的两格都紫)。读不到返回 None。HUD 在不在调用方管。"""
        try:
            x1, y1, x2, y2 = self.ZS_BOX
            box = self.task.box_of_screen_scaled(3840, 2160, x1, y1, x2, y2, hcenter=True)
            crop = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if crop.shape[0] < 8 or crop.shape[1] < 100:
                return None
            crop = crop.astype(np.int32)
            s = box.width / float(x2 - x1)
            ya = int(round((self.ZS_ROW[0] - y1) * s))
            yb = max(ya + 1, int(round((self.ZS_ROW[1] - y1) * s)))
            ta = int(round((self.ZS_TALL_ROW[0] - y1) * s))
            tb = max(ta + 1, int(round((self.ZS_TALL_ROW[1] - y1) * s)))
            hw = max(1, int(round(self.ZS_TICK_HW * s)))
            go = int(round(self.ZS_GAP_DX * s))
            last = crop.shape[1] - 1
            ticks = [self.ZS_TICK_L[0] + self.ZS_TICK_L[1] * i for i in range(17)] + \
                    [self.ZS_TICK_R[0] + self.ZS_TICK_R[1] * i for i in range(17)]
            pur = []
            nw = tall = 0
            for x in ticks:
                cx = int(round((x - x1) * s))
                b, g, r = crop[ya:yb, max(0, cx - hw):cx + hw + 1].reshape(-1, 3).mean(0)
                pur.append(b - g >= 45 and r - g >= 15 and g >= 110)
                lo, hi = min(b, g, r), max(b, g, r)
                nw += lo >= 200 and hi - lo <= 55
                col = crop[ta:tb, max(0, cx - hw):cx + hw + 1, 1].mean()
                gap = (crop[ta:tb, max(0, cx - go), 1].mean() + crop[ta:tb, min(last, cx + go), 1].mean()) / 2
                tall += col - gap >= self.ZS_TALL_DIFF
            return sum(pur), nw, tall / float(len(ticks)), pur[32] and pur[33]
        except Exception:
            return None

    def aug_zs(self):
        """战势 -> (满, 格数), 读不到返回 None。满: True / False / None(白条但还没认出来, 接着读)。
        有状态, 每帧只调一次; HUD 不在的帧别调。
        认满: 粉白条连 ZS_CONFIRM 帧带高条(之后一直白着就一直算满), 或 34 格全紫连 ZS_CONFIRM 帧。
        满着的时候高条会连着 4 帧都很低(15:12 实机 236.28~236.38), 这时候刚开始读只能先算 None。
        吃掉战势那一下(后撤/升拳)条立刻不白了, 当帧就是 False。"""
        raw = self.aug_zs_raw()
        if raw is None:
            return None
        purple, nw, tall, tail = raw
        white = nw >= self.ZS_WHITE_MIN and purple <= self.ZS_PURPLE_MAX
        self._zs_flash = getattr(self, '_zs_flash', 0) + 1 if white and tall >= self.ZS_TALL_MIN else 0
        self._zs_fullp = getattr(self, '_zs_fullp', 0) + 1 if tail and purple >= 32 else 0
        if self._zs_flash >= self.ZS_CONFIRM:
            self._zs_latch = True
        elif not white:
            self._zs_latch = False
        if getattr(self, '_zs_latch', False) or self._zs_fullp >= self.ZS_CONFIRM:
            return True, 34
        return (None if white else False), purple

    @staticmethod
    def zs_text(zs):
        if zs is None:
            return '战势?'
        return '战势满' if zs[0] else ('战势白?' if zs[0] is None else f'战势{zs[1]}/34')

    def aug_zs_read(self, cap):
        """读 cap 秒内的战势: 满/不满连 ZS_CONFIRM 帧一样就返回 (满, 格数); HUD 不在的帧不算。
        到 cap 还没定: 返回最后一次读数(可能是 (None, n)), 一帧都没读到返回 None。"""
        start = time.time()
        last = None
        same = 0
        while time.time() - start < cap:
            self.task.next_frame()
            zs = self.aug_zs() if self.hud_ok() else None
            if zs is not None:
                if zs[0] is None:
                    same = 0
                elif last is not None and zs[0] is last[0]:
                    same += 1
                else:
                    same = 1
                last = zs
                if same >= self.ZS_CONFIRM:
                    return zs
            self.macro_wait(0.01)
        return last

    def aug_zs_wait(self, full, cap):
        """等战势条连 ZS_CONFIRM 帧读成 full(True 满 / False 不满; 白条没认出来的帧两边都不算)。等到了返回 True。"""
        start = time.time()
        hits = 0
        while time.time() - start < cap:
            self.task.next_frame()
            zs = self.aug_zs() if self.hud_ok() else None
            if zs is not None:
                hits = hits + 1 if zs[0] is full else 0
                if hits >= self.ZS_CONFIRM:
                    return True
            self.macro_wait(0.01)
        return False

    def wait_forte_full(self, char, cap, tag, after=0.0):
        """边点普攻边等鼠标指示亮(回路满), 连 SK_FORTE_CONFIRM 帧才算; 满了再接着点 after 秒。超时也返回。
        点击不走 tap(): 按下到抬起那 A_TAP 秒里照样读帧(10-18 实机回路条满了 ~0.3s 才判到)。"""
        start = time.time()
        hits = n = 0
        next_click = 0.0
        down_at = None
        full_at = None
        try:
            while (time.time() - start < cap) if full_at is None else (time.time() - full_at < after):
                now = time.time()
                if down_at is None and now >= next_click:
                    self.task.mouse_down()
                    self.holding_attack = True
                    down_at = now
                    n += 1
                elif down_at is not None and now - down_at >= self.A_TAP:
                    self.task.mouse_up()
                    self.holding_attack = False
                    down_at = None
                    next_click = time.time() + self.A_INTERVAL - self.A_TAP
                self.task.next_frame()
                if full_at is None:
                    full = self.hud_ok() and self.forte_lit(char)
                    hits = hits + 1 if full else 0
                    if hits >= self.SK_FORTE_CONFIRM:
                        full_at = time.time()
                        if after <= 0:
                            break
                self.macro_wait(0.01)
        finally:
            if down_at is not None:
                self.task.mouse_up()
                self.holding_attack = False
        how = f'满+{after:.2f}' if full_at and after > 0 else ('满' if full_at else 'cap')
        took = (full_at if full_at else time.time()) - start
        self.macro_mark(f'{tag}({how},A x{n},{took:.2f}s)')
        return full_at is not None

    # ==================== 守岸人 ====================

    def sk_hold_heavy(self, sk, hold, tag):
        """长按打重击, 鼠标指示【见过亮之后一暗】就松手; 没亮过按满 hold 兜底; SK_HEAVY_CAP 硬上限。
        返回 '指示暗' / '一直暗' / 'cap'。"""
        self.attack_down(False)
        self.macro_mark(f'{tag}_down')
        start = time.time()
        why = 'cap'
        seen = False
        try:
            self.macro_wait(min(self.HEAVY_MIN_HOLD, hold))
            while time.time() - start < max(self.SK_HEAVY_CAP, hold):
                self.task.next_frame()
                self.hud_ok()
                lit = self.forte_lit(sk)
                if lit:
                    seen = True
                elif seen:
                    why = '指示暗'
                    break
                if not seen and time.time() - start >= hold:
                    why = '一直暗'
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}_up({why},{time.time() - start:.2f}s)')
        return why

    def sk_heavy(self, sk, tag):
        """守岸人重击: 按住期间指示一直亮着没暗(没打出去)就再按, 最多 HEAVY_RETRIES 次。没亮过(普通重击)不补。"""
        for i in range(1 + self.HEAVY_RETRIES):
            if self.sk_hold_heavy(sk, self.SK_HEAVY_HOLD, tag if i == 0 else f'{tag}重按#{i}') != 'cap':
                return True
        return False

    def sk_a_then_heavy(self, sk, window, cap, tag):
        """点普攻 window 秒(把 A3 打出来) -> 回路满(指示亮)就重击; 还没满接着点到满, 从开始算 cap 封顶照按。"""
        start = time.time()
        self.spam_until(start + window, tag=f'{tag}A')
        if not self.lit_now(lambda: self.forte_lit(sk), self.SK_FORTE_CONFIRM):
            self.wait_forte_full(sk, max(0.30, cap - (time.time() - start)), f'{tag}补满')
        self.sk_heavy(sk, f'{tag}重击')

    def sk_build_energy(self, sk, tag, rounds):
        """守岸人攒能量的一轮: 回路满了就打强化重击(能量多), 否则普攻一小段。"""
        if self.forte_lit(sk):
            self.sk_heavy(sk, f'{tag}攒能量重击#{rounds}')
        else:
            self.macro_spam(self.R_BUILD_SLICE, self.A_INTERVAL, tag=f'{tag}攒能量A#{rounds}')

    def sk_qr(self, sk, tag):
        """重击之后: Q -> R(不亮先攒) -> 等演出结束。"""
        self.macro_wait(self.SK_HEAVY_TO_QR)
        self.press_echo(sk, self.SK_Q_CAP, f'{tag}Q', ready_grace=0.15)
        if self.ensure_r(sk, tag, self.SK_LIB_WINDOW, self.SK_R_ENSURE_CAP,
                         lambda rounds: self.sk_build_energy(sk, tag, rounds)):
            self.sk_r_anim_a(tag)

    def sk_r_anim_a(self, tag):
        """守 R 演出(全局时停 + 隐藏 HUD 179F): 一直点 A, HUD 回来后再点 SK_R_END_A_AFTER 秒, 切人前 A 一定出手了。
        HUD 回来的判法同 wait_hud_back: 先见过消失, 消失后 SK_R_ANIM_MIN 之内的"回来"不认, 回来要连读 HUD_BACK_CONFIRM 帧。"""
        start = time.time()
        gone_at = back_at = None
        back_hits = n = 0
        next_click = 0.0
        while time.time() - start < self.LIB_ANIM_TIME_OUT:
            if back_at is not None and time.time() - back_at >= self.SK_R_END_A_AFTER:
                break
            if time.time() >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + self.A_INTERVAL - self.A_TAP
            if back_at is None:
                self.task.next_frame()
                if not self.hud_ok():
                    if gone_at is None:
                        gone_at = time.time()
                    back_hits = 0
                elif gone_at is not None:
                    if time.time() - gone_at >= self.SK_R_ANIM_MIN:
                        back_hits += 1
                        if back_hits >= self.HUD_BACK_CONFIRM:
                            back_at = time.time()
                elif time.time() - start >= self.HUD_NO_ANIM_GRACE:
                    back_at = time.time()
            self.macro_wait(0.01)
        end = back_at if back_at is not None else time.time()
        if gone_at is not None:
            self.add_freeze_duration(gone_at, end - gone_at)
        self.macro_mark(f'{tag}R演出({"ok" if back_at else "timeout"},{"anim" if gone_at else "NOANIM"},{end - start:.2f}s)'
                        f'+R后A(A x{n})')

    def sk_ensure_con(self, sk, tag):
        """协奏没满就别交棒 —— 用能给协奏的技能补: E(20) > 强化重击(6) > 普攻(1~4)。"""
        start = time.time()
        rounds = 0
        while time.time() - start < self.SK_CON_RESCUE_CAP:
            rounds += 1
            if self.con_full_now():
                self.macro_mark(f'{tag}补协奏(满,{rounds}轮,{time.time() - start:.2f}s)')
                return True
            if self.read_cd('resonance') <= 0 and self.press_res_until_cd(sk, 0.40, f'{tag}补协奏E', ready_grace=0.10):
                self.macro_wait(self.SK_E_DERIVE)
                continue
            if self.forte_lit(sk):
                self.sk_heavy(sk, f'{tag}补协奏重击')
                continue
            self.macro_spam(self.SK_CON_RESCUE_SLICE, self.A_INTERVAL, tag=f'{tag}补协奏A')
        self.macro_mark(f'{tag}补协奏(cap,{rounds}轮,{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 补了 {time.time() - start:.1f}s 协奏还是没满, 认赔交棒(打不到怪?)')
        return False

    def sk_hand_to_iuno(self, sk, iu, tag):
        """守 -> 月: 协奏满了(连着几帧)才交棒, 不满先补。切人时点 A(偷一下普攻给下一圈攒协奏)。"""
        full = self.con_full_now(settle=0.25) or \
            self.wait_con_full('守岸人', self.SK_CON_CAP, f'{tag}协奏') or self.sk_ensure_con(sk, tag)
        self.warn_no_con(full, '守', '月')
        self.need(self.switch_retry(iu, f'{tag}切月', assume_con=full, click_during=True), 'shorekeeper -> iuno')

    def switch_to_sk(self, sk, tag, assume_con=False):
        """切给守岸人并等她的变奏入场演完。返回时 HUD 在场、场上是她。

        带镜头的变奏一切进来 HUD 就藏了, quick_switch 读不到她到场会超时 ——
        所以不管切人判没判到, 都等 HUD 回来再认一次场上是谁; 真没切过去就再切(最多 SWITCH_RETRIES 次)。
        """
        for attempt in range(1 + self.SWITCH_RETRIES):
            got = self.switch_to_sk_once(sk, tag if attempt == 0 else f'{tag}重切#{attempt}', assume_con)
            if got is not None:
                return True
            self.wait_hud_up(cap=2.0, tag=f'{tag}重切前HUD')
        return False

    def switch_to_sk_once(self, sk, tag, assume_con):
        """切一次守岸人。返回 True/False(到场了, HUD 回没回来), None = 守岸人没上场。"""
        src = self.cur
        sw_at = time.time()
        arrived = self.quick_switch(sk, click_during=False, assume_con=assume_con, time_out=self.SK_TO_TIME_OUT)
        arrived_at = time.time()
        anim_in_switch = arrived and arrived_at - sw_at >= self.SK_SWITCH_ANIM_HINT
        start = time.time()
        gone_seen = False
        back = False
        n = 0
        next_click = 0.0
        while time.time() - start < self.SK_INTRO_ANIM_CAP:
            now = time.time()
            if now >= next_click:
                self.tap()
                n += 1
                next_click = time.time() + self.A_INTERVAL - self.A_TAP
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
            self.handoff(src if src is not None else self, sk, assume_con)
            self.macro_mark(f'{tag}>Sho(演出里到场)')
            arrived_at = start
        # 镜头演完的变奏: 入场锁在镜头里; 没镜头: 入场锁从到场起算
        self.sk_arrived_at = sw_at if anim_in_switch else arrived_at
        self.sk_intro_done_at = time.time()
        return back

    def seg_op_sk1(self, sk, iu, tag):
        """启动轴守第一棒: A1A2A3 -> E -> 切月。"""
        self.macro_spam(self.SK_A3_WINDOW, self.A_INTERVAL, tag=f'{tag}3A')
        self.press_res_until_cd(sk, self.SK_E_CAP, f'{tag}E')
        self.need(self.switch_retry(iu, f'{tag}切月'), 'shorekeeper -> iuno')

    def seg_op_sk2(self, sk, iu, tag):
        """启动轴守第二棒: A2 -> A3 起手过半 -> 重击(指示暗 = 出手) -> 切月。"""
        self.sk_a_then_heavy(sk, self.SK_OP2_A_WINDOW, self.SK_OP2_FORTE_CAP, f'{tag}A2A3')
        self.need(self.switch_retry(iu, f'{tag}切月'), 'shorekeeper -> iuno')

    def seg_op_sk3(self, sk, iu, tag):
        """启动轴守第三棒: A1A2A3 到圈圈特效 -> 重击 -> Q -> R -> A 一下 -> 变奏月。"""
        self.sk_a_then_heavy(sk, self.SK_OP3_A_WINDOW, self.SK_FORTE_CAP, f'{tag}3A')
        self.sk_qr(sk, tag)
        self.sk_hand_to_iuno(sk, iu, tag)

    def seg_sk_loop(self, sk, iu, tag):
        """守·循环(变奏入场): A1A2A3 -> E -> A 一下(A2) -> 重击 -> Q -> R(演出里一直点 A) -> A 出手就变奏月。"""
        if not sk.has_intro:
            self.logger.warning('守岸人没拿到变奏入场(上一棒协奏没满)')
            self.macro_mark(f'{tag}无变奏')
        now = time.time()
        done = self.sk_intro_done_at
        if 0 < now - done < self.SK_ANCHOR_TTL:
            anchor = max(done, self.sk_arrived_at + (self.SK_INTRO_LOCK if sk.has_intro else 0.0))
        else:
            anchor = now
        self.spam_until(anchor + self.SK_A3_WINDOW, tag=f'{tag}3A')
        self.press_res_until_cd(sk, self.SK_L_E_CAP, f'{tag}E', poke=True)
        # E 之后普攻一下接重击; 回路还没满(上一圈没带格)就接着点到满再重击, 不满按出来是化蝶
        self.sk_a_then_heavy(sk, self.SK_L_E_TO_HEAVY, self.SK_L_FORTE_CAP, f'{tag}E后')
        self.sk_qr(sk, tag)
        self.sk_hand_to_iuno(sk, iu, tag)

    # ==================== 尤诺 ====================

    def iu_jump_icon(self):
        """左下那格的"可跳"图标(内置模板 iuno_jump): 有月相就亮。长枪(环)和星球(弓)两个图案都认, 分形态用 iu_form。"""
        try:
            return bool(self.task.find_feature('iuno_jump', box='box_extra_action', threshold=0.6))
        except Exception:
            return False

    def iu_heavy_icon(self):
        """左下那格的至臻的完满图标(内置模板 iuno_heavy)。"""
        try:
            return bool(self.task.find_feature('iuno_heavy', box='box_extra_action', threshold=0.6))
        except Exception:
            return False

    @staticmethod
    def iu_glyph_vec(a):
        v = a.astype(np.float32).flatten()
        v = v - v.mean()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    def iu_form(self):
        """尤诺左下那格的图案 -> 'ring'(长枪, 跳 = 变弓) / 'bow'(星球, 跳 = 变环) / '?'(别的图案、过渡帧)。
        读当前帧, HUD 在不在调用方管。"""
        try:
            ref = getattr(self, '_iu_form_ref', None)
            if ref is None:
                yy, xx = np.mgrid[:16, :16]
                mask = ((xx - 7.5) ** 2 + (yy - 7.5) ** 2) <= 7.6 ** 2

                def tpl(chunks):
                    raw = base64.b64decode(''.join(chunks))
                    return self.iu_glyph_vec(np.frombuffer(raw, np.uint8).reshape(16, 16)[mask])

                ref = self._iu_form_ref = (mask, tpl(self.IU_SPEAR_GLYPH), tpl(self.IU_PLANET_GLYPH))
            mask, spear, planet = ref
            cx, cy = self.IU_SLOT_C
            h = self.IU_SLOT_HALF
            box = self.task.box_of_screen_scaled(3840, 2160, cx - h, cy - h, cx + h, cy + h, hcenter=True)
            icon = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width, :3]
            if icon.shape[0] < 8 or icon.shape[1] < 8:
                return '?'
            g = cv2.resize(cv2.cvtColor(np.ascontiguousarray(icon), cv2.COLOR_BGR2GRAY), (16, 16),
                           interpolation=cv2.INTER_AREA)
            v = self.iu_glyph_vec(g[mask])
            sp, pl = float(v @ spear), float(v @ planet)
            self._iu_form_score = (sp, pl)
            if sp >= self.IU_FORM_MIN and sp - pl >= self.IU_FORM_MARGIN:
                return 'ring'
            if pl >= self.IU_FORM_MIN and pl - sp >= self.IU_FORM_MARGIN:
                return 'bow'
            return '?'
        except Exception:
            return '?'

    def iu_form_read(self, cap):
        """连 2 帧读到同一个形态就返回它; cap 秒内没定返回 '?'(HUD 不在的帧不算)。"""
        start = time.time()
        last = None
        same = 0
        while time.time() - start < cap:
            self.task.next_frame()
            if self.hud_ok():
                f = self.iu_form()
                same = same + 1 if f == last else 1
                last = f
                if f != '?' and same >= 2:
                    return f
            self.macro_wait(0.01)
        return '?'

    def seg_op_iu_e1(self, iu, aug, tag):
        """启动轴月第一棒: E(原初的律动) -> 切日。
        原初不进冷却, 只把 E 换成告终的喧响(5s), 所以不能"按到进 CD": 那样会把告终也连着按出去。
        等技能栏换成她的(E 亮)再短连按一小段 —— 原初的动作里后面几下被丢掉。"""
        self.wait_lit(iu.resonance_available, self.IU_E_BAR_SETTLE, f'{tag}E亮')
        self.spam_key(iu.send_resonance_key, self.IU_OP_E1_WINDOW, interval=self.IU_OP_E1_INTERVAL, tag=f'{tag}E')
        self.need(self.switch_retry(aug, f'{tag}切日'), 'iuno -> augusta')

    def seg_op_iu_e2(self, iu, aug, tag):
        """启动轴月第二棒: E(告终的喧响, 进 8s 冷却)按到进 CD 就切日。"""
        self.press_key_until_cd(iu.send_resonance_key, iu.resonance_available, self.IU_OP_E_CAP, f'{tag}E',
                                ready_grace=self.IU_E_BAR_SETTLE)
        self.need(self.switch_retry(aug, f'{tag}切日'), 'iuno -> augusta')

    def read_r_state(self, char, tries=6):
        """r_state 读到 HUD 在场的一帧为止, 最多 tries 帧。"""
        state = None
        for _ in range(tries):
            self.task.next_frame()
            state = self.r_state(char)
            if state is not None:
                break
            self.macro_wait(0.03)
        return state

    def iu_fire_r(self, iu, tag, state):
        """尤诺的 R: 冷却中就等它转好再按; 亮就按; 不亮(能量)走 iu_build_r。返回放出去没有。"""
        if state == 'cd':
            state = self.wait_r_ready(iu, self.IU_R_WAIT_CAP, f'{tag}', dark_confirm=0.40)
        if state == 'lit' and self.fire_r_sure(iu, f'{tag}R', self.IU_R_WINDOW):
            return True
        return self.iu_build_r(iu, tag)

    def iu_to_bow(self, tag, iu=None):
        """攒能量之前先换弓(月相里跳 = 换弓): 弓 A 回能量比地面环 A 快, 变弓那一箭也给。看左下那格:
        已经是弓(星球) -> 不跳; 环(长枪) -> 隔 IU_JUMP_INTERVAL 连按跳(告终 E 的动作里跳被丢, 上限 IU_TO_BOW_CAP),
        连 2 帧读到星球 = 换好了, 闪避断变弓动画;
        读不出形态(那格是至臻的完满 / 没月相): 刚放过告终(月相还在)就跳两下 —— 变弓动画里的第二下被丢, 否则在地面攒。
        跳的时候每帧读 iu 的 R, 连 LIT_FRAMES 帧亮就不等换弓直接返回(self.iu_r_lit = True), 环放的 R 演完 iu_jump 会再换。
        返回是不是弓。"""
        self.iu_r_lit = False
        form = self.iu_form_read(0.20)
        if form == 'bow':
            self.macro_mark(f'{tag}上天(已经是弓)')
            return True
        if form == '?':
            if time.time() - self.iu_gz_at > self.IU_MOON_TTL:
                self.macro_mark(f'{tag}上天(读不到形态,没月相,地面攒)')
                return False
            # 跳两下只管一次: 刚补的告终还在动作里就先等它过(Q 在告终动作里照样放得出, 补完马上就到这儿; 干跑 iulow2,formblind),
            # 等的时候 R 亮了就直接放
            lit = 0
            while time.time() < self.iu_gz_at + self.IU_GZ_ANIM:
                self.task.next_frame()
                if iu is not None and self.hud_ok():
                    lit = lit + 1 if self.r_state(iu) == 'lit' else 0
                    if lit >= self.LIT_FRAMES:
                        self.iu_r_lit = True
                        self.macro_mark(f'{tag}上天(读不到形态,等告终时R亮了)')
                        return False
                self.macro_wait(0.01)
            self.task.jump(after_sleep=0)
            self.macro_wait(self.IU_JUMP_INTERVAL)
            self.task.jump(after_sleep=0)
            self.dodge(f'{tag}上天后')
            self.macro_mark(f'{tag}上天(读不到形态,跳x2)')
            return True
        start = time.time()
        n = bow = lit = 0
        why = 'CAP'
        next_jump = 0.0
        while time.time() - start < self.IU_TO_BOW_CAP:
            if bow == 0 and time.time() >= next_jump:
                self.task.jump(after_sleep=0)
                n += 1
                next_jump = time.time() + self.IU_JUMP_INTERVAL
            self.task.next_frame()
            if self.hud_ok():
                bow = bow + 1 if self.iu_form() == 'bow' else 0
                if bow >= 2:
                    why = '换弓'
                    break
                if iu is not None:
                    # 告终 E 的出伤一打完能量就可能够了(18:24 实机跳了 1.8s 才换上弓、再点 0.5s 才放 R)
                    lit = lit + 1 if self.r_state(iu) == 'lit' else 0
                    if lit >= self.LIT_FRAMES:
                        why = 'R亮了'
                        self.iu_r_lit = True
                        break
            self.macro_wait(0.01)
        if why != 'R亮了':
            self.dodge(f'{tag}上天后')
        self.macro_mark(f'{tag}上天({why},x{n},{time.time() - start:.2f}s)')
        return why == '换弓'

    def iu_build_r(self, iu, tag):
        """R 不亮: 先换弓(iu_to_bow, 跳的时候就亮了直接放), 一直连点 A(弓 A / 环 A), 每帧读 R, 连 LIT_FRAMES 帧亮就放。
        E 只在不是弓的时候按(原初 -> 告终给能量); 弓形态按 E 是越限弦引, 会把后面 E1/E2 要用的两格充能打掉(10-18 15:12 实机)。
        每 IU_R_BLIND_EVERY 秒在两下普攻之间夹按一下 R(防亮度读不准), 不停手; 按完 IU_R_BLIND_SEE 秒内队伍 HUD 连着不在 = 放出去了。
        打不到怪时 IU_R_BUILD_CAP 封顶。返回放出去没有; self.iu_bow 记下放的时候是不是弓。"""
        start = time.time()
        n = e = lit = blind = gone = 0
        next_tap = next_e = 0.0
        next_blind = start + self.IU_R_BLIND_EVERY
        blind_at = None
        self.macro_mark(f'{tag}R没亮(攒能量)')
        bow = self.iu_bow = self.iu_to_bow(tag, iu)
        form = 'bow' if bow else '?'
        names = {'ring': '环', 'bow': '弓'}
        if self.iu_r_lit:
            self.macro_mark(f'{tag}攒能量(跳的时候亮了,{time.time() - start:.2f}s)')
            if self.fire_r_sure(iu, f'{tag}R', self.IU_R_WINDOW):
                return True
        while time.time() - start < self.IU_R_BUILD_CAP:
            if gone == 0 and time.time() >= next_tap:
                self.tap()
                n += 1
                next_tap = time.time() + self.A_INTERVAL - self.A_TAP
            self.task.next_frame()
            state = self.r_state(iu)
            if state is None:
                gone += 1
                if blind_at is not None and gone >= self.LIB_GONE_CONFIRM:
                    iu.record_liberation_use()
                    self.lib_press_at = blind_at
                    self.task.skip_combat_check = True
                    self.iu_bow = form == 'bow'
                    self.macro_mark(f'{tag}R盲按_fired(第{blind}下,{names.get(form, "形态?")},A x{n},E x{e},'
                                    f'{time.time() - start:.2f}s)')
                    self.logger.info(f'{tag}: 尤诺 R 攒了 {time.time() - start:.1f}s 能量, 盲按放出去的(亮度没读到)')
                    return True
                self.macro_wait(0.01)
                continue
            gone = 0
            if blind_at is not None and time.time() - blind_at > self.IU_R_BLIND_SEE:
                blind_at = None
            f = self.iu_form()
            if f != '?':
                form = f
            lit = lit + 1 if state == 'lit' else 0
            if lit >= self.LIT_FRAMES:
                self.iu_bow = form == 'bow'
                self.macro_mark(f'{tag}攒能量(亮,{names.get(form, "形态?")},A x{n},E x{e},{time.time() - start:.2f}s)')
                if self.fire_r_sure(iu, f'{tag}R', self.IU_R_WINDOW):
                    return True
                lit = 0
                continue
            if form != 'bow' and f != 'bow' and time.time() >= next_e:
                try:
                    ready = bool(iu.resonance_available())
                except Exception:
                    ready = False
                if ready:
                    iu.send_resonance_key()
                    e += 1
                    next_e = time.time() + self.IU_BUILD_E_GAP
            if time.time() >= next_blind:
                iu.send_liberation_key()
                blind += 1
                blind_at = time.time()
                next_blind = blind_at + self.IU_R_BLIND_EVERY
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}攒能量(CAP,{names.get(form, "形态?")},A x{n},E x{e},盲按x{blind})')
        self.logger.warning(f'{tag}: 尤诺 R 攒了 {self.IU_R_BUILD_CAP:.1f}s 能量还是没放出去, 不放了')
        return False

    def iu_jump(self, tag, fired, bow=False):
        """R 演完换弓。HUD 回来后看左下那格:
          星球(弓) -> 已经是弓, 不跳(10-18 15:12 实机: 弓形态放的 R 演完还是弓, 再跳就变回环, 后面全打歪);
          长枪(环) -> 隔 IU_JUMP_INTERVAL 连按跳, 连 2 帧读到星球 = 换了, 立刻闪避把变弓动画闪掉(动画里再按的跳被丢);
          跳了之后那格一直读不清(至臻的完满盖上来了)超过 IU_FORM_LOST 秒 = 当换了, 不再跳;
          HUD 回来 IU_FORM_WAIT 秒都读不出形态(那格是至臻的完满) -> bow(R 是弓形态放的)不跳;
          否则(环放的 R / 没放出来但有"可跳"图标)跳两下就走 —— 第二下在变弓动画里被丢。
          不能像以前那样跳到"可跳"图标灭: iuno_jump 长枪星球都认, 换了弓也不灭, 会一直站到上限(干跑 formblind)。
        返回估计的起跳时刻(弓 A 窗口从这儿算)。"""
        start = time.time()
        gone_at = back_at = None
        if not fired:
            back_at = start
        n = hud_off = bow_n = 0
        ring = False
        why = 'CAP'
        next_jump = 0.0
        first_jump = lost_at = None
        while time.time() - start < self.LIB_ANIM_TIME_OUT:
            self.task.next_frame()
            hud = self.hud_ok()
            now = time.time()
            if back_at is not None and fired and n == 0 and not ring:
                # 认成"回来"之后又连着不在 = 演出中途闪的那一下, 接着等
                hud_off = 0 if hud else hud_off + 1
                if hud_off >= self.LIB_GONE_CONFIRM:
                    back_at = None
                    hud_off = 0
            if back_at is None:
                if not hud:
                    gone_at = gone_at or now
                    self.macro_wait(0.01)
                    continue
                if (now - start < self.IU_R_GONE_WAIT) if gone_at is None else (now - gone_at < self.IU_R_ANIM_MIN):
                    self.macro_wait(0.01)
                    continue
                back_at = now
            if hud:
                f = self.iu_form()
                bow_n = bow_n + 1 if f == 'bow' else 0
                if bow_n >= 2:
                    why = '换弓' if n else '已经是弓'
                    break
                if f == 'ring':
                    ring = True
                    lost_at = None
                elif f == '?' and first_jump is not None:
                    lost_at = lost_at or now
                    if now - lost_at >= self.IU_FORM_LOST:
                        why = '换弓?(读不清)'
                        break
                if not ring and bow_n == 0 and now - back_at >= self.IU_FORM_WAIT:
                    if bow or (not fired and not self.iu_jump_icon()):
                        why = '读不到形态,' + ('R在弓形态放的' if bow else '没见过可跳图标')
                        break
                    self.task.jump(after_sleep=0)
                    self.macro_wait(self.IU_JUMP_INTERVAL)
                    self.task.jump(after_sleep=0)
                    n += 2
                    why = '读不到形态,跳x2'
                    break
            if ring and bow_n == 0 and now >= next_jump:
                self.task.jump(after_sleep=0)
                n += 1
                first_jump = first_jump or now
                next_jump = time.time() + self.IU_JUMP_INTERVAL
            if now - back_at >= self.IU_JUMP_CAP:
                break
            self.macro_wait(0.01)
        if back_at is None:
            back_at = time.time()
        if gone_at is not None:
            self.add_freeze_duration(gone_at, back_at - gone_at)
            self.macro_mark(f'{tag}R演出({back_at - gone_at:.2f}s)')
        if n:
            self.dodge(f'{tag}跳后')
        self.macro_mark(f'{tag}跳({why},x{n},HUD回来后{time.time() - back_at:.2f}s)')
        return back_at + self.IU_R_INPUT_LAG

    def iu_bow_e1(self, iu, tag):
        """弓 E1(越限弦引): 连按 E 到 E 框出现充能冷却数字。两格充能, 第一发之后图标还亮着、只多了回充数字,
        所以只能看数字从无到有。读不到就按满上限当它出去了。返回 E 起手的估计时刻(第一下按的时候)。"""
        start = time.time()
        n = 0
        next_e = 0.0
        prev = None
        why = 'CAP'
        seen_at = None
        trace = []
        while time.time() - start < self.IU_E1_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                n += 1
                next_e = time.time() + 0.08
            self.task.next_frame()
            if self.hud_ok():
                cd = self.read_cd('resonance')
                if len(trace) < 20:
                    trace.append(f'{time.time() - start:.2f}:{cd:.1f}')
                if cd > 5.0 and (prev is None or prev < 1.0):
                    why = 'CD'
                    seen_at = time.time()
                    break
                prev = cd
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({why},x{n},{time.time() - start:.2f}s)')
        if why == 'CAP':
            self.logger.info(f'{tag}: bow E charge CD never showed up — {" ".join(trace)}')
            return start
        # 第一下按出去 E 就起手(E 能打断弓 A); 读到数字比较晚说明前几下被丢了
        return max(start, seen_at - self.IU_E1_CD_LAG)

    def iu_wait_heavy_icon(self, iu, tag):
        """第二发弓 E: 连按 IU_E2_PRESS, 然后等至臻的完满图标亮(协奏满了才有); 一直不亮就 E/A 交替补。"""
        self.spam_key(iu.send_resonance_key, self.IU_E2_PRESS, interval=0.08, tag=f'{tag}E2')
        start = time.time()
        lit = 0
        while time.time() - start < self.IU_HEAVY_ICON_CAP:
            self.task.next_frame()
            if self.hud_ok():
                lit = lit + 1 if self.iu_heavy_icon() else 0
                if lit >= 2:
                    self.macro_mark(f'{tag}重击图标亮({time.time() - start:.2f}s)')
                    return True
            self.macro_wait(0.02)
        start = time.time()
        n = clicks = lit = 0
        next_e = 0.0
        while time.time() - start < self.IU_E2_RESCUE_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                n += 1
                next_e = time.time() + 0.30
            else:
                self.tap()
                clicks += 1
            self.task.next_frame()
            if self.hud_ok():
                lit = lit + 1 if self.iu_heavy_icon() else 0
                if lit >= 2:
                    self.macro_mark(f'{tag}补E2(重击图标亮,x{n},A x{clicks},{time.time() - start:.2f}s)')
                    return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}补E2(CAP,x{n},A x{clicks})')
        return False

    def iu_heavy(self, iu, tag, cap=None):
        """重击(至臻的完满): 按住到图标灭(见过亮才认灭), 最短 IU_HEAVY_MIN、最长 cap(默认 IU_HEAVY_CAP)。
        图标还没亮就按住 = 游戏自己连普攻, 亮了直接出强化重击。"""
        cap = self.IU_HEAVY_CAP if cap is None else cap
        self.attack_down(False)
        start = time.time()
        seen = False
        why = 'cap'
        try:
            self.macro_wait(self.IU_HEAVY_MIN)
            while time.time() - start < cap:
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

    def iu_ensure_con(self, iu, tag):
        """切日之前协奏要满: 边 A 边隔一会儿按一下 E(弓 E 还有充能就是一大笔协奏, 没有是空操作)。"""
        start = time.time()
        hits = 0
        best = 0.0
        next_e = start + self.IU_CON_E_EVERY
        next_a = 0.0
        while time.time() - start < self.IU_CON_CAP:
            now = time.time()
            if now >= next_e:
                iu.send_resonance_key()
                next_e = time.time() + self.IU_CON_E_EVERY
            elif now >= next_a:
                self.tap()
                next_a = time.time() + self.A_INTERVAL - self.A_TAP
            self.task.next_frame()
            self.hud_ok()
            try:
                con = self.con_now()
            except Exception:
                con = 0.0
            best = max(best, con)
            hits = hits + 1 if con >= self.CON_ENOUGH else 0
            if hits >= self.CON_FULL_CONFIRM:
                self.macro_mark(f'{tag}补协奏(满,{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}补协奏(CAP,con{best:.2f})')
        return False

    def seg_iuno(self, iu, tag):
        """月(变奏入场): Q -> R -> 跳闪换弓 -> 弓 A x3 -> E -> 闪 -> A -> E -> 重击 -> 变奏日。
        R 不亮攒能量、在弓形态放的 R: 演完已经是弓、回路是满的, 不再打弓 A, 直接 E; E2 之后直接按住攻击, 游戏自己普攻到出强化重击。"""
        t_in = getattr(iu, 'last_switch_in_time', 0.0) or time.time()
        if not iu.has_intro:
            self.logger.warning('尤诺没拿到变奏入场(上一棒协奏没满)')
            self.macro_mark(f'{tag}无变奏')
        # 入场锁过了再读 R(锁里技能栏也已经换成她的)
        self.wait_until_time(t_in + self.IU_INTRO_LOCK)
        state = self.read_r_state(iu)
        short = state == 'dark'
        if short:
            # 能量保底(用户 10-18 选「R 不亮才补」): 先补一发告终 E 再照常打
            self.macro_mark(f'{tag}R不亮(补告终E)')
            if self.press_key_until_cd(iu.send_resonance_key, iu.resonance_available, self.IU_FIX_E_CAP,
                                       f'{tag}补E', ready_grace=self.IU_E_BAR_SETTLE):
                self.iu_gz_at = time.time()
        else:
            self.macro_mark(f'{tag}R({state})')
        self.press_echo(iu, self.IU_Q_CAP, f'{tag}Q', ready_grace=0.15)
        if short:
            # 补完 E 和 Q 再读: 还不亮就直接攒(10-18 实机旧写法当它亮了连按 2s R, 白等)
            state = self.read_r_state(iu)
            self.macro_mark(f'{tag}补完R({state})')
        self.iu_bow = False
        fired = self.iu_fire_r(iu, tag, state)
        if not fired:
            self.logger.warning(f'{tag}: 尤诺 R 没放出来 —— 没有月相, 跳不出弓, 这一段会打歪')
        jump_at = self.iu_jump(tag, fired, bow=self.iu_bow)
        if self.iu_bow:
            self.wait_until_time(jump_at)
            self.macro_mark(f'{tag}弓A(R前攒能量打过,直接E1)')
        else:
            self.spam_until(jump_at + self.IU_BOW_A_WINDOW, tag=f'{tag}弓A')
        e1_at = self.iu_bow_e1(iu, f'{tag}E1')
        self.wait_until_time(e1_at + self.IU_E1_TO_DODGE)
        self.dodge(f'{tag}E1后{time.time() - e1_at:.2f}s')
        self.macro_wait(self.IU_DODGE_TO_A)
        self.tap()
        self.macro_wait(self.IU_A_TO_E2)
        if self.iu_bow:
            self.spam_key(iu.send_resonance_key, self.IU_E2_PRESS, interval=0.08, tag=f'{tag}E2')
            why, seen = self.iu_heavy(iu, f'{tag}按住A到重击', cap=self.IU_E2_HOLD_CAP)
        else:
            self.iu_wait_heavy_icon(iu, tag)
            why, seen = self.iu_heavy(iu, f'{tag}重击')
        if why == 'cap' and seen and self.lit_now(self.iu_heavy_icon, 2):
            # 松手后图标还亮 = 没打出去, 重按一次
            self.iu_heavy(iu, f'{tag}重击重按')
        full = self.con_full_now(settle=0.25) or self.iu_ensure_con(iu, tag)
        self.warn_no_con(full, '月', '日')
        self.need(self.switch_retry(self, f'{tag}切日', assume_con=full), 'iuno -> augusta')

    # ==================== 奥古斯塔 ====================

    def aug_backstep(self, cap, tag, min_hold=0.0, release_delay=None):
        """长按出后撤: 战势被扣掉 = 长按刚被认成后撤, 这时离真正出手还有 ~0.3s,
        再按 release_delay(默认 AUG_DARK_TO_RELEASE, 松手带出旋切)才松手, 且总时长不少于 min_hold;
        撤Z切传 0: 一扣就松, 只要后撤不要旋切。返回原因('条掉了' / '指示暗' / 'cap')。
        两条判据谁先成立用谁: 战势条见过满(或 AUG_BACK_DROP_FROM 格以上)之后连 ZS_CONFIRM 帧少了一截;
        准星旁指示见过亮之后暗(权柄也满的时候指示一直亮, 只能靠条)。一个都没成立按满 cap。"""
        release_delay = self.AUG_DARK_TO_RELEASE if release_delay is None else release_delay
        self.attack_down(False)
        self.macro_mark(f'{tag}后撤按住')
        start = time.time()
        seen = bar_seen = False
        dark = drop = 0
        hi = 0
        why = 'cap'
        try:
            self.macro_wait(self.HEAVY_MIN_HOLD)
            while time.time() - start < cap:
                self.task.next_frame()
                if self.hud_ok():
                    zs = self.aug_zs()
                    if zs is not None and zs[0] is not False:
                        # 白条还没认出来(None)也算见过满: 斩锋回满后 0.3s 就被后撤吃掉时, 高条可能一直没跳起来(干跑 forteblind)
                        bar_seen = True
                        drop = 0
                    elif zs is not None and zs[0] is False:
                        fell = bar_seen or (hi >= self.AUG_BACK_DROP_FROM and zs[1] <= hi - self.AUG_BACK_DROP)
                        if not bar_seen and not fell:
                            hi = max(hi, zs[1])
                        drop = drop + 1 if fell else 0
                        if drop >= self.ZS_CONFIRM:
                            why = '条掉了'
                            break
                    if self.forte_lit():
                        seen = True
                        dark = 0
                    elif seen:
                        dark += 1
                        if dark >= self.AUG_DARK_CONFIRM:
                            why = '指示暗'
                            break
                self.macro_wait(0.02)
            if why != 'cap' and release_delay > 0:
                self.macro_wait(min(release_delay, cap - (time.time() - start)))
            self.wait_until_time(start + min(min_hold, cap))
        finally:
            self.release_attack()
        note = '' if seen or bar_seen else (f',从{hi}格掉' if why == '条掉了' else ',没见过满')
        self.macro_mark(f'{tag}后撤松手({why}{note},{time.time() - start:.2f}s)')
        return why

    def zs_median(self, vals, n):
        """vals 里追加 n, 攒够 ZS_MEDIAN 个才返回中值(之前返回 None)。"""
        vals.append(n)
        del vals[:-self.ZS_MEDIAN]
        if len(vals) < self.ZS_MEDIAN:
            return None
        return sorted(vals)[len(vals) // 2]

    def aug_hold_rise(self, cap, tag, blind_hold, nogain=None, read_from=0.0):
        """按住出重击·鸣铁, 战势条涨了(第一段出伤, +6 格)就松手 —— 松了鸣铁照样打完。返回 (原因, 涨的那一刻):
        '涨了' / '没涨'(按了 nogain 秒条读得到却一格没涨 = 长按按在动作里没被认, 松手出的是 A1) / 'cap' /
        '读不到'(按了 blind_hold 秒条一帧都没读到, 退回固定时长)。涨 = 比按住期间最少的格数多 3 格以上(或满), 连 ZS_CONFIRM 帧;
        刚按下时条可能还在往下掉(后撤扣的), 所以跟最少的比; read_from 秒之前的读数不看, 格数取中值。"""
        self.attack_down(False)
        self.macro_mark(f'{tag}按住')
        start = time.time()
        lo = last = None
        vals = []
        up = 0
        up_at = rise_at = None
        why = 'cap'
        try:
            self.macro_wait(self.HEAVY_MIN_HOLD)
            while time.time() - start < cap:
                now = time.time()
                if lo is None and now - start >= blind_hold:
                    why = '读不到'
                    break
                self.task.next_frame()
                zs = self.aug_zs() if self.hud_ok() and now - start >= read_from else None
                if zs is not None and zs[0] is False:
                    n = self.zs_median(vals, zs[1])
                    if n is None:
                        zs = None
                    else:
                        last = n
                        lo = n if lo is None else min(lo, n)
                if zs is not None and zs[0] is not None:
                    if zs[0]:
                        last = 34
                    rising = lo is not None and (zs[0] or last - lo >= 3)
                    if rising:
                        up_at = up_at or now
                        up += 1
                        if up >= self.ZS_CONFIRM:
                            why = '涨了'
                            rise_at = up_at
                            break
                    else:
                        up = 0
                        up_at = None
                if nogain is not None and lo is not None and up == 0 and now - start >= nogain:
                    why = '没涨'
                    break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}松手({why},{"?" if lo is None else lo}->{"?" if last is None else last}格,'
                        f'{time.time() - start:.2f}s)')
        return why, rise_at

    def aug_spin(self, tag):
        """短按普攻 = 旋切(后撤的许可 / 被打断后 2s 的补施放)。"""
        self.tap()
        self.macro_mark(f'{tag}旋切')

    def aug_rise_jump(self, tag, how):
        """满战势升拳 = 短按跳跃。隔 AUG_RISE_JUMP_GAP 按一下(动作里的跳被丢), 战势条连 ZS_CONFIRM 帧不满(升拳吃掉了战势)就停。
        条读不到的帧退回准星旁指示。"""
        start = time.time()
        n = off = 0
        why = 'cap'
        next_jump = 0.0
        while time.time() - start < self.AUG_RISE_JUMP_CAP:
            if time.time() >= next_jump:
                self.task.jump(after_sleep=0)
                n += 1
                next_jump = time.time() + self.AUG_RISE_JUMP_GAP
            self.task.next_frame()
            if self.hud_ok():
                zs = self.aug_zs()
                full = self.forte_lit() if zs is None else zs[0] is not False
                if full:
                    off = 0
                else:
                    off += 1
                    if off >= self.ZS_CONFIRM:
                        why = '战势掉了'
                        break
            self.macro_wait(0.01)
        self.macro_mark(f'{tag}跳升拳({how},{why},x{n},{time.time() - start:.2f}s)')
        return why

    def aug_rise_hold(self, direct=False, lead=0.0):
        """按住出鸣铁, 战势满了就松手。返回 (原因, 按住期间最少格数, 最后格数, 按了多久, 走没走 SendInput):
        '满' / '没涨'(AUG_RISE_NOGAIN 秒涨不到 3 格 = 长按没被认, 松手出的是 A1) / 'cap'。格数读不到是 None。
        direct = 前台走 SendInput; lead = 在动作里提前按住了多久, 判"没涨"和上限都往后挪这么多。"""
        si = self.attack_down(direct)
        start = time.time()
        lo = last = None
        vals = []
        why = 'cap'
        try:
            self.macro_wait(self.HEAVY_MIN_HOLD)
            while time.time() - start < self.AUG_RISE_HOLD_CAP + lead:
                self.task.next_frame()
                zs = self.aug_zs() if self.hud_ok() else None
                if zs is not None and zs[0]:
                    why = '满'
                    break
                n = self.zs_median(vals, zs[1]) if zs is not None and zs[0] is False else None
                if n is not None:
                    lo = n if lo is None else min(lo, n)
                    last = n
                    if time.time() - start >= self.AUG_RISE_NOGAIN + lead and last - lo < 3:
                        why = '没涨'
                        break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        return why, lo, last, time.time() - start, si

    def aug_rise(self, tag, lead=0.0, to_e=None):
        """z升拳。起手前战势已满 -> 跳(这时长按出的是后撤)。没满 -> 按住出鸣铁, 满了就松手 = 升拳;
        松手后战势还满 = 升拳没出, 改跳。一次没满(鸣铁不够 / 长按按在动作里没被认)就隔一下再按, 最多 AUG_RISE_HOLDS 次;
        没满松手后鸣铁还在出伤, 那段里满了也跳。战势条一帧都读不到: 按满一次上限就算了。返回原因。
        lead > 0 = 调用方在旋切里提前 lead 秒叫的: 第一次按住走 SendInput(前台才行), 之后的重按照旧。
        to_e = 满了松手就不验证、不跳, 等到松手后 to_e 秒返回 '松手接E'(调用方接着连按 E)。"""
        zs = self.aug_zs_read(self.AUG_RISE_PRE_READ)
        if zs is not None and zs[0] is not False:
            # 白条没认出来(None)也当满: 空条 + 白背景只在 14:11 录屏里出现过一次, 长按出后撤的代价更大
            return self.aug_rise_jump(tag, '已满' if zs[0] else '白条')
        for i in range(self.AUG_RISE_HOLDS):
            t = f'{tag}升拳' + ('' if i == 0 else f'#{i + 1}')
            pre = lead if i == 0 else 0.0
            self.macro_mark(f'{t}按住({self.zs_text(zs)}{f",预输入{pre:.2f}s" if pre > 0 else ""})')
            why, lo, last, held, si = self.aug_rise_hold(direct=pre > 0, lead=pre)
            released = time.time()
            self.macro_mark(f'{t}松手({why},{"?" if lo is None else lo}->{"?" if last is None else last}格,{held:.2f}s'
                            f'{",SI" if si else ""})')
            if why == '满' and to_e is not None:
                # 读到条掉了只落日志; 没读到也不跳(10-19 那 5 圈升拳其实都出了, 跳只会把 E 拖后)
                seen = self.aug_zs_wait(False, max(0.0, released + to_e - time.time()))
                self.macro_mark(f'{t}出了' if seen else f'{t}松手(条还没掉,不等,接E)')
                self.wait_until_time(released + to_e)
                return '松手接E'
            if why == '满':
                if self.aug_zs_wait(False, self.AUG_RISE_VERIFY):
                    self.macro_mark(f'{t}出了')
                    return '升拳'
                return self.aug_rise_jump(tag, '松手没出')
            if lo is None:
                self.macro_mark(f'{t}(战势条读不到,不再按)')
                return why
            if self.aug_zs_wait(True, self.AUG_RISE_REHOLD if why == '没涨' else self.AUG_RISE_AFTER):
                return self.aug_rise_jump(tag, '松手后满')
            zs = self.aug_zs_read(0.06)
            if zs is not None and zs[0] is not False:
                return self.aug_rise_jump(tag, '再按前满')
        self.macro_mark(f'{tag}升拳没出({self.AUG_RISE_HOLDS}次没满)')
        self.logger.warning(f'{tag}: 按了 {self.AUG_RISE_HOLDS} 次鸣铁战势都没满, 这一下没有升拳')
        return 'cap'

    def aug_e_chain(self, tag):
        """E2 迅击 -> E3 跃空 -> E4 落袭 -> 落地 E1 斩锋: 一直连按 E, 到 E 框出现斩锋的冷却数字为止。
        返回斩锋出手(读到数字)的时刻, 撞上限返回 None。"""
        start = time.time()
        n = 0
        while time.time() - start < self.AUG_E_CHAIN_CAP:
            self.send_resonance_key()
            n += 1
            step_end = time.time() + self.AUG_E_CHAIN_INTERVAL
            while time.time() < step_end:
                self.task.next_frame()
                if self.hud_ok() and self.read_cd('resonance') >= self.AUG_E1_CD_MIN:
                    took = time.time() - start
                    self.macro_mark(f'{tag}E串(斩锋出手,x{n},{took:.2f}s)')
                    if took < 1.5:
                        # 正常 E2~E4 要 2.7s 左右; 这么快就出斩锋 = 权柄没满, 没有落袭(这一圈 R2 少一层威慑)
                        self.logger.warning(f'{tag}: E 串 {took:.2f}s 就出了斩锋 —— 权柄没满, 没打出迅击/跃空/落袭')
                    return time.time()
                self.macro_wait(0.02)
        self.macro_mark(f'{tag}E串(CAP,x{n})')
        self.logger.warning(f'{tag}: 连按 E {self.AUG_E_CHAIN_CAP:.1f}s 没读到斩锋冷却(被打断/读不到), 照往下打')
        return None

    def aug_r_icon_info(self):
        """R 图标的诊断读数(lib1/lib2 匹配分、亮度、冷却), 写日志用。"""
        try:
            lib1 = self.task.find_one('Augusta_lib1', threshold=0.3)
            lib2 = self.task.find_one('Augusta_lib2', threshold=0.3)
            bright = self.current_liberation()
            return (f'lib1={lib1.confidence if lib1 else 0:.2f} lib2={lib2.confidence if lib2 else 0:.2f} '
                    f'亮度={bright:.3f} 冷却={self.read_cd("liberation"):.1f}')
        except Exception as e:
            return f'读失败({e})'

    def aug_r_ring(self):
        """R 框外圈颜色(读当前帧): (橙色占比, 粉紫占比)。框是 box_liberation 往外扩到包住外圈。
        白粉圈 = 誓锋不殒能放; 橙圈 = 只能放赫日威临; 冷却中的紫圈也算粉紫(只在冷却好了之后读)。"""
        b = self.task.get_box_by_name('box_liberation')
        cx, cy = b.x + b.width / 2, b.y + b.height / 2
        crop = self.task.frame[int(cy - b.height * 0.75):int(cy + b.height * 0.75),
                               int(cx - b.width * 0.7):int(cx + b.width * 0.7)]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, sat, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        orange = ((h >= 5) & (h <= 22) & (sat >= 140) & (v >= 140)).mean()
        pink = ((h >= 125) & (h <= 170) & (sat >= 40) & (v >= 150)).mean()
        return float(orange), float(pink)

    def aug_r1_pink(self):
        """R 框是白粉圈(誓锋不殒能放)。读不了返回 None。"""
        try:
            orange, pink = self.aug_r_ring()
        except Exception:
            return None
        return pink >= self.AUG_R1_PINK_MIN and pink >= orange

    def aug_r1_pink_wait(self):
        """R1 前读 AUG_R1_COLOR_READS 帧外圈: 连 2 帧白粉 = True; 一直读不了也当 True(不拦); 否则 False(橙圈)。"""
        hits = reads = 0
        for i in range(self.AUG_R1_COLOR_READS):
            if i:
                self.task.next_frame()
            if not self.hud_ok():
                continue
            pk = self.aug_r1_pink()
            if pk is None:
                continue
            reads += 1
            hits = hits + 1 if pk else 0
            if hits >= 2:
                return True
        return reads == 0

    def aug_r1_left_pink(self, r1_at):
        """白粉圈时按的 R1 放没放出去: 到 r1_at + AUG_R1_VERIFY 之前外圈连 2 帧不是白粉 = 放了(True);
        到点最后连 3 帧还是白粉 = 两下都被吞了(False); 读不清 = None(当放了)。"""
        off = on = 0
        while time.time() < r1_at + self.AUG_R1_VERIFY:
            self.task.next_frame()
            pk = self.aug_r1_pink() if self.hud_ok() else None
            if pk is False:
                off += 1
                on = 0
                if off >= 2:
                    return True
            elif pk:
                on += 1
                off = 0
            self.macro_wait(0.01)
        return False if on >= 3 else None

    def aug_r2_icon_ready(self):
        """R 图标是不是赫日威临那张(Augusta_lib2, 2 层威慑)。读当前帧。
        lib1 / lib2 是同一个按钮位置上的两张图, 阈值 0.5 时互相都能匹配上, 要比谁更像(9-03 实机)。"""
        try:
            lib2 = self.task.find_one('Augusta_lib2', threshold=0.5)
            if not lib2:
                return False
            lib1 = self.task.find_one('Augusta_lib1', threshold=0.5)
            return not (lib1 and lib1.confidence > lib2.confidence)
        except Exception:
            return False

    def aug_r1(self, tag, window=None, blind=False):
        """短按 R(松开才放) = 誓锋不殒。每 AUG_R1_PRESS_GAP 按一下, 到队伍 HUD 连续消失(镜头)
        或 R 框出现冷却数字为止。返回最后一下按下的时刻(放出去了), 没放出去返回 None。
        blind: 带 2 层威慑时 R1 放出去既不藏 HUD 也不出冷却数字(R 键立刻变成赫日威临可用), 两条判据都不成立 ——
        只按 AUG_R1_TWO_PRESSES 下, 没判到也返回最后一下的时刻。"""
        window = self.AUG_R1_WINDOW if window is None else window
        if blind:
            window = (self.AUG_R1_PRESS_GAP + self.AUG_R1_TAP) * (self.AUG_R1_TWO_PRESSES - 1) + self.AUG_R1_TAP + 0.08
        self.wait_hud_up(tag=f'{tag}前HUD')
        key = self.get_liberation_key()
        start = time.time()
        n = gone = 0
        last = None
        next_press = 0.0
        how = None
        while time.time() - start < window:
            if gone == 0 and time.time() >= next_press:
                self.task.send_key(key, down_time=self.AUG_R1_TAP)
                last = time.time()
                n += 1
                next_press = last + self.AUG_R1_PRESS_GAP
            self.task.next_frame()
            if not self.hud_ok():
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    how = '镜头'
                    break
            else:
                gone = 0
                if n and self.read_cd('liberation') >= self.AUG_R1_CD_MIN:
                    how = '冷却'
                    break
            self.macro_wait(0.02)
        self.task.skip_combat_check = True
        if how is None and blind and last is not None:
            self.record_liberation_use()
            self.lib_press_at = last
            self.macro_mark(f'{tag}_按了(没判据,x{n})')
            return last
        if how is None:
            self.macro_mark(f'{tag}_NOFIRE(x{n})')
            return None
        self.record_liberation_use()
        self.lib_press_at = last
        self.macro_mark(f'{tag}_fired({how},x{n},{time.time() - start:.2f}s)')
        return last

    def aug_fill_r(self, tag, r1=False):
        """能量不够: 点普攻攒到 R 亮(连 LIT_FRAMES 帧); r1 = 带 2 层威慑时要攒到外圈变白粉(誓锋不殒能放), 橙圈的"亮"不算。
        从这一圈起每圈升拳后处决、补 Q(作者的充能补救)。"""
        if not self.aug_energy_short:
            self.logger.warning(f'{tag}: 日放 R1 时能量不够 —— 先点普攻攒, 从这一圈起升拳后补 Q 再变奏守')
        self.aug_energy_short = True
        start = time.time()
        n = lit = 0
        next_tap = 0.0
        while time.time() - start < self.AUG_R_FILL_CAP:
            if time.time() >= next_tap:
                self.tap()
                n += 1
                next_tap = time.time() + self.A_INTERVAL - self.A_TAP
            self.task.next_frame()
            ok = self.r_state(self) == 'lit' and (not r1 or self.aug_r1_pink() is not False)
            lit = lit + 1 if ok else 0
            if lit >= self.LIT_FRAMES:
                self.macro_mark(f'{tag}攒能量({"白粉圈" if r1 else "亮"},A x{n},{time.time() - start:.2f}s)')
                return True
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}攒能量(CAP,{"还是橙圈," if r1 else ""}A x{n})')
        return False

    def aug_r2(self, r1_at, tag):
        """R1 横劈那一刀开始长按 R = 赫日威临(吃 2 层威慑)。按住期间不发任何别的键。"""
        self.wait_until_time(r1_at + self.AUG_R1_TO_R2)
        key = self.get_liberation_key()
        self.task.send_key_down(key)
        self.macro_mark(f'{tag}R2按住')
        try:
            self.macro_wait(self.AUG_R2_HOLD)
        finally:
            self.task.send_key_up(key)
        self.macro_mark(f'{tag}R2松手({self.AUG_R2_HOLD:.2f}s)')

    def aug_majesty(self, tag):
        """俯首之刻: 点普攻(烈阳)到战势指示亮(9 次烈阳打完) -> 接着点到指示灭(不朽者之肃镜头起来, AUG_FINAL_SPAM_CAP 封顶)
        -> 镜头演完 HUD 回来(收尾在 aug_end)。
        队伍 HUD 连 AUG_R2_MISS_FRAMES 帧在场:
          - 之前没见过它消失 = 没进俯首之刻(长按没被认出来), R2 图标还亮就再长按一次, 否则返回 False;
          - 见过消失 = 俯首之刻已经结束(指示读不到, 点着点着把不朽者之肃点出来了 / 7s 到时自己放了), 直接返回去收尾。"""
        start = time.time()
        n = lit = hud_run = gone = 0
        gone_seen = False
        retried = False
        why = 'cap'
        next_tap = 0.0
        while time.time() - start < self.AUG_R2_FORTE_CAP:
            if time.time() >= next_tap:
                self.tap()
                n += 1
                next_tap = time.time() + self.A_INTERVAL - self.A_TAP
            self.task.next_frame()
            if self.hud_ok():
                hud_run += 1
                gone = 0
                if hud_run >= self.AUG_R2_MISS_FRAMES:
                    if gone_seen:
                        why = 'HUD回来'
                        break
                    if not retried and self.aug_r2_icon_ready():
                        # 长按没被认出来(按在 R1 收招里被丢了), R2 图标还亮着: 再长按一次
                        retried = True
                        hud_run = 0
                        self.macro_mark(f'{tag}R2没进(图标还亮,重按)')
                        self.aug_r2(time.time() - self.AUG_R1_TO_R2, f'{tag}重')
                        continue
                    self.macro_mark(f'{tag}没进俯首之刻(A x{n},{time.time() - start:.2f}s)')
                    return False
            else:
                hud_run = 0
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    gone_seen = True
            if self.forte_lit():
                lit += 1
                if lit >= self.AUG_DARK_CONFIRM:
                    why = '指示亮'
                    break
            else:
                lit = 0
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}烈阳({why},A x{n},{time.time() - start:.2f}s)')
        if why == 'HUD回来':
            # 不朽者之肃已经演完了, 现在就是普通状态, 直接收尾(aug_end)
            self.logger.info(f'{tag}: 俯首之刻里没读到 9 次烈阳的指示, HUD 回来了才知道已经结束 —— 直接收尾')
            return True
        final = time.time()
        n2 = off = 0
        why2 = 'CAP'
        next_tap = 0.0
        while time.time() - final < self.AUG_FINAL_SPAM_CAP:
            if time.time() >= next_tap:
                self.tap()
                n2 += 1
                next_tap = time.time() + self.A_INTERVAL - self.A_TAP
            self.task.next_frame()
            if self.forte_lit():
                off = 0
            else:
                off += 1
                if off >= self.LIB_GONE_CONFIRM:
                    why2 = '指示灭'
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}不朽者之肃({why2},A x{n2},{time.time() - final:.2f}s)')
        final = time.time()
        # 镜头演完、HUD 回来再起手升拳: 跨镜头按着的左键回来后不认(10-18 实机 HUD 回来后按着站了 2.4s, 升拳没出)
        gone = 0
        while time.time() - final < self.AUG_FINAL_GONE_WAIT:
            self.task.next_frame()
            gone = 0 if self.hud_ok() else gone + 1
            if gone >= self.LIB_GONE_CONFIRM:
                break
            self.macro_wait(0.01)
        if gone >= self.LIB_GONE_CONFIRM:
            self.wait_hud_back(tag=f'{tag}不朽者之肃演出', time_out=self.AUG_R2_TAIL_CAP, min_gone=self.AUG_FINAL_ANIM_MIN)
        else:
            self.macro_mark(f'{tag}不朽者之肃没镜头?')
        return True

    def aug_burst(self, tag):
        """鸣铁之后: 等 R 冷却 -> R1 -> (2 层威慑) R2 -> 俯首之刻。返回时 HUD 在场, 收尾的鸣铁/处决/升拳在 aug_end。"""
        state = self.wait_r_ready(self, self.AUG_R1_WAIT_CAP, tag, dark_confirm=self.AUG_R_DARK_CONFIRM)
        if state == 'dark':
            self.aug_fill_r(tag)
        two = self.aug_r2_icon_ready()
        self.logger.info(f'{tag}: R1 前 R 图标 {self.aug_r_icon_info()} -> {"赫日威临(2层威慑)" if two else "誓锋不殒"}')
        if two:
            # 2 层威慑: R1 放出去既不藏 HUD 也不出冷却数字(见 aug_r1), 看 R 框外圈:
            # 橙圈 = 誓锋不殒能量不够(这时长按 R 直接放赫日威临、R1 整个跳过), 先点普攻攒到白粉圈;
            # 白粉圈时按的 R1 放出去外圈会暗, 还是白粉 = 两下都被吞了, 再按一轮。进没进俯首之刻由 aug_majesty 查
            pink = self.aug_r1_pink_wait()
            if not pink:
                self.macro_mark(f'{tag}R框橙圈(誓锋不殒能量不够)')
                pink = self.aug_fill_r(tag, r1=True)
            r1_at = self.aug_r1(f'{tag}R1', blind=True)
            if pink and self.aug_r1_left_pink(r1_at) is False:
                self.macro_mark(f'{tag}R1没出(外圈还是白粉,再按)')
                r1_at = self.aug_r1(f'{tag}R1补', blind=True)
            self.aug_r2(r1_at, tag)
            if not self.aug_majesty(tag):
                self.wait_hud_back(tag=f'{tag}R1演出')
            return True
        r1_at = self.aug_r1(f'{tag}R1')
        if r1_at is None and state != 'cap':
            if self.icon_lit(lambda: self.r_state(self) == 'lit'):
                # 图标还亮着 = 键被硬直吞了, 不是能量问题: 等 HUD 回来再按一轮, 不记"能量不够"
                self.wait_hud_up(cap=self.RETRY_HUD_CAP, tag=f'{tag}R1补按前HUD')
            else:
                # 按了之后反而不亮: 能量差一点(亮度判不准), 攒一下再按
                self.aug_fill_r(tag)
            two = self.aug_r2_icon_ready()
            r1_at = self.aug_r1(f'{tag}R1补')
        if r1_at is None:
            self.logger.warning(f'{tag}: R1 没放出来, 不按 R2 (长按会直接把赫日威临放掉、跳过誓锋不殒)')
            return False
        if not two:
            self.macro_mark(f'{tag}R2图标没亮(威慑不够)')
            self.logger.info(f'{tag}: 按 R1 之前 R 图标不是赫日威临 —— 威慑不到 2 层(落袭没打出来/守岸人没带延奏变奏), 这一圈没有 R2')
            self.wait_hud_back(tag=f'{tag}R1演出')
            return True
        self.aug_r2(r1_at, tag)
        if not self.aug_majesty(tag):
            self.wait_hud_back(tag=f'{tag}R1演出')
        return True

    def aug_break(self, tag):
        """有处决提示就处决(日自己处决, 能量回给她)。返回处决了没有。"""
        for _ in range(self.BREAK_READS):
            self.task.next_frame()
            if self.hud_ok() and self.break_prompt():
                break
        else:
            return False
        hit_at, n = self.break_press(tag)
        try:
            self.task.can_break = False
        except Exception:
            pass
        if hit_at is None:
            self.macro_mark(f'{tag}处决?(F x{n},提示一直在)')
            return False
        self.wait_until_time(hit_at + self.AUG_BREAK_ANIM)
        self.macro_mark(f'{tag}处决(F x{n})')
        return True

    def aug_end_break_held(self, seen):
        """收尾鸣铁打满、鼠标还按着: 按住期间见过处决提示(seen 帧 >= AUG_END_SEEN_MIN), 或打满后 BREAK_READS 帧里读到,
        就点 F AUG_END_F_TAPS 下, 最后一下之后再按住 AUG_END_F_HOLD 让处决先起手(松早了游戏先出升拳)。
        返回第一下 F 的时刻, 没提示返回 None。处决起手后怪身上的提示变成处决标记, 模板照样认, 所以不靠提示消失判起手。"""
        if seen < self.AUG_END_SEEN_MIN:
            for i in range(self.BREAK_READS):
                if i:
                    self.task.next_frame()
                if self.hud_ok() and self.break_prompt():
                    break
            else:
                return None
        f_at = last = None
        for i in range(self.AUG_END_F_TAPS):
            if i:
                self.macro_wait(self.AUG_END_F_GAP)
            self.task.send_key('f', down_time=0.05)
            last = time.time()
            f_at = f_at or last
        self.wait_until_time(last + self.AUG_END_F_HOLD)
        return f_at

    def aug_end_jump(self, tag):
        """处决演完: 战势还满就跳出升拳, 不满就算了(不再按鸣铁)。"""
        zs = self.aug_zs_read(self.AUG_RISE_PRE_READ)
        if zs is not None and zs[0] is False:
            self.macro_mark(f'{tag}处决后战势没满({zs[1]}/34,不升拳)')
            return '战势没满'
        return self.aug_rise_jump(tag, '处决后')

    def aug_end_rise(self, tag):
        """收尾: 按住鸣铁打到战势满 -> 按着鼠标看处决提示, 有就按 F -> 松手; 处决了就等它演完再跳升拳, 没处决松手就是升拳。
        鸣铁里战势一满、手是松的游戏就自己出升拳(10-19 实机: 第一段出伤就松, 0.65s 内战势 18 -> 0), 所以 F 要在松手前按。
        按住走 SendInput: PostMessage 的按住被 send_key 投的 WM_ACTIVATE 打断, 游戏不在前台就先处决再按鸣铁。
        只按一次鸣铁: 没打满(怪跑了 / 转火 / 长按没被认)就不升拳, 照常切守。返回原因。"""
        zs = self.aug_zs_read(self.AUG_RISE_PRE_READ)
        if zs is not None and zs[0] is not False:
            if self.aug_break(tag):
                return '处决+' + self.aug_end_jump(tag)
            return self.aug_rise_jump(tag, '已满' if zs[0] else '白条')
        direct = self.game_foreground()
        if not direct:
            self.aug_break(tag)
        t = f'{tag}收尾升拳'
        self.macro_mark(f'{t}按住({self.zs_text(zs)}{",满了看处决" if direct else ""})')
        si = self.attack_down(direct)
        start = time.time()
        lo = last = hi = None
        vals = []
        why = 'cap'
        f_at = full_at = None
        seen = 0
        try:
            self.macro_wait(self.HEAVY_MIN_HOLD)
            while time.time() - start < self.AUG_RISE_HOLD_CAP:
                self.task.next_frame()
                hud = self.hud_ok()
                if hud and self.break_prompt():
                    seen += 1
                zs = self.aug_zs() if hud else None
                # 第三段打满那一帧条先整条闪白(还没认成满), 前两段已经打到 AUG_END_WHITE_FROM 就直接算满, 省掉认满的那两三帧
                if zs is not None and (zs[0] or zs[0] is None and hi is not None and hi >= self.AUG_END_WHITE_FROM):
                    why = '满'
                    break
                if zs is not None and zs[0] is False:
                    hi = zs[1] if hi is None else max(hi, zs[1])
                n = self.zs_median(vals, zs[1]) if zs is not None and zs[0] is False else None
                if n is not None:
                    lo = n if lo is None else min(lo, n)
                    last = n
                    if time.time() - start >= self.AUG_RISE_NOGAIN and last - lo < 3:
                        why = '没涨'
                        break
                self.macro_wait(0.02)
            if why == '满':
                full_at = time.time()
                if si:
                    f_at = self.aug_end_break_held(seen)
        finally:
            self.release_attack()
        brk = (f'点了F x{self.AUG_END_F_TAPS}' if f_at else '没提示') if si else 'PostMessage按住不按F'
        self.macro_mark(f'{t}松手({why},{"?" if lo is None else lo}->{"?" if last is None else last}格,'
                        f'{time.time() - start:.2f}s{",SI" if si else ""},提示x{seen}'
                        f'{f",满后{time.time() - full_at:.2f}s,{brk}" if full_at else ""})')
        if why != '满':
            # 松手后鸣铁还在出伤, 打满了游戏自己出升拳; 没满就不再按鸣铁。怪就在跟前(按住期间见过提示)就照样处决, 不升拳
            self.macro_mark(f'{tag}没打满,不再按鸣铁')
            if seen >= self.AUG_END_SEEN_MIN and self.aug_break(tag):
                return f'没打满({why})+处决'
            return f'没打满({why})'
        if f_at is not None:
            if self.aug_zs_wait(False, self.AUG_RISE_VERIFY):
                return '升拳(F没接上)'
            self.wait_until_time(f_at + self.AUG_BREAK_ANIM)
            self.macro_mark(f'{tag}处决(鸣铁满了按着F)')
            return '处决+' + self.aug_end_jump(tag)
        if self.aug_zs_wait(False, self.AUG_RISE_VERIFY):
            self.macro_mark(f'{t}出了')
            return '升拳'
        return self.aug_rise_jump(tag, '松手没出')

    def aug_end(self, sk, tag):
        """R 之后收尾: 按住鸣铁到战势满 -> (有提示)按着鼠标按 F 处决 -> 松手 -> 升拳 -> 战势一掉就变奏守(aug_end_rise, 没打满不重按)。
        升拳 6F/12F 出伤、可脱手, 切走后日在原地打完, 所以判到战势掉了就切。
        能量不够过的圈走作者的充能补救: 升拳 -> 落地 -> 处决 -> 补 Q -> 变奏守(会拖一下轴)。"""
        if self.aug_energy_short:
            self.aug_rise(f'{tag}收尾')
            self.macro_wait(self.AUG_RISE_LAND)
            self.aug_break(tag)
            self.press_echo(self, self.AUG_Q_CAP, f'{tag}补Q', ready_grace=0.15)
            con = self.con_full_now(settle=0.25) or \
                self.con_wait('奥古斯塔', self.AUG_CON_CAP, f'{tag}协奏', poke_after=self.AUG_CON_POKE_AFTER)
        else:
            con = self.con_full_now(settle=0.10)
            why = self.aug_end_rise(tag)
            self.macro_mark(f'{tag}升拳同时切守({why})')
            if not con:
                # 升拳的两下也给协奏, 落地前不点普攻(空中点普攻是下落攻击)
                con = self.con_full_now(settle=0.20) or \
                    self.con_wait('奥古斯塔', self.AUG_CON_CAP, f'{tag}协奏', poke_after=self.AUG_CON_POKE_AFTER)
        self.warn_no_con(con, '日', '守')
        self.need(self.switch_to_sk(sk, f'{tag}切守', assume_con=con), 'augusta -> shorekeeper')

    def seg_aug(self, sk, tag):
        """日(变奏入场): 撤Z切 -> z升拳 -> E 串 -> 后撤旋切 -> 鸣铁 -> R1 -> R2 -> 升拳 -> 变奏守。"""
        if not self.has_intro:
            self.logger.warning('奥古斯塔没拿到变奏入场(上一棒协奏没满) —— 战势没回满, 第一下长按出的是鸣铁不是后撤')
            self.macro_mark(f'{tag}无变奏')
        # 撤Z切: 后撤一出就松(晚松自己带出旋切), 再按住出鸣铁打断后撤, 第一段出伤就松开点一下补旋切
        self.aug_backstep(self.AUG_ENTRY_HOLD, f'{tag}1', release_delay=0.0)
        self.macro_wait(self.AUG_ZQ_REPRESS)
        zq_at = time.time()
        why, rise_at = self.aug_hold_rise(self.AUG_ZQ_CAP, f'{tag}鸣铁1', self.AUG_ZQ_HOLD,
                                          read_from=self.AUG_ZQ_READ_FROM)
        self.macro_wait(self.AUG_ZQ_TO_SPIN)
        self.aug_spin(f'{tag}1')
        # z升拳: 旋切跟在鸣铁派生后面, 从鸣铁第一段出伤起算;
        # 条读不到 = 不知道鸣铁什么时候起手, 按"鸣铁出了、旋切排在派生"估; 读得到但没涨(鸣铁没出) = 旋切就是点的那下
        # 前台: 在旋切里提前 AUG_ZQ_PRE_LEAD 用 SendInput 按住(预输入); 不在前台 SendInput 会点到别的窗口, 照旧时机
        lead = self.AUG_ZQ_PRE_LEAD if rise_at is not None and self.game_foreground() else 0.0
        if rise_at is not None:
            self.wait_until_time(rise_at + self.AUG_ZQ_RISE_TO_HOLD - lead)
        elif why == '读不到':
            self.wait_until_time(max(time.time() + self.AUG_SPIN_TO_HOLD, zq_at + self.AUG_ZQ_BLIND_TO_HOLD))
        else:
            self.macro_wait(self.AUG_SPIN_TO_HOLD)
        if self.aug_rise(f'{tag}1', lead=lead, to_e=self.AUG_RELEASE_TO_E) != '松手接E':
            self.macro_wait(self.AUG_RISE_TO_E)
        # E2 E3 E4 E1
        e1_at = self.aug_e_chain(tag)
        self.wait_until_time((e1_at or time.time()) + self.AUG_E1_TO_HOLD)
        # 后撤(松手带出旋切) -> 鸣铁
        why = self.aug_backstep(self.AUG_BACKSTEP_CAP, f'{tag}2', min_hold=self.AUG_BACKSTEP_MIN)
        if why == 'cap':
            zs = self.aug_zs_read(0.10)
            if zs is not None and zs[0] is not False:
                # 按住到上限战势还满 = 长按按在斩锋的动作里没被认, 松手出了一下普攻; 再按一次
                self.macro_mark(f'{tag}2后撤没出({self.zs_text(zs)},重按)')
                why = self.aug_backstep(self.AUG_BACKSTEP_CAP, f'{tag}2重', min_hold=self.AUG_BACKSTEP_MIN)
        released = time.time()
        if why == 'cap':
            # 没判到后撤, 松手可能已经在后撤收招之后、带不出旋切: 隔一下点旋切, 鸣铁从点的那下起算
            self.macro_wait(self.AUG_DARK_TO_SPIN)
            self.aug_spin(f'{tag}2')
            hold_at = time.time() + self.AUG_SPIN_TO_HOLD
        else:
            self.wait_until_time(released + self.AUG_BACK2_TO_SPIN)
            self.aug_spin(f'{tag}2补')
            hold_at = released + self.AUG_BACK2_TO_HOLD
        self.wait_until_time(hold_at)
        self.aug_heavy2(tag)
        self.aug_burst(tag)
        self.aug_end(sk, tag)

    def aug_heavy2(self, tag):
        """旋切之后那发鸣铁: 按到第一段出伤松手, 没被认(按在旋切里)就隔 AUG_RISE_REHOLD 再按一次;
        出伤后等 AUG_HEAVY_RISE_TO_R 让三段打完, 再交给 R1 断后摇。"""
        rise_at = None
        for i in range(2):
            why, rise_at = self.aug_hold_rise(self.AUG_HEAVY_CAP, f'{tag}鸣铁2' + ('' if i == 0 else '重'),
                                              self.AUG_HEAVY_HOLD, nogain=self.AUG_RISE_NOGAIN)
            if why != '没涨':
                break
            self.macro_wait(self.AUG_RISE_REHOLD)
        if rise_at is not None:
            self.wait_until_time(rise_at + self.AUG_HEAVY_RISE_TO_R)

    def seg_op_aug_r(self, sk, tag):
        """启动轴第一下: 日 R(誓锋不殒, +40 权柄、冷却开始转) -> 切守。R 不亮就跳过。"""
        if self.lit_now(lambda: self.r_state(self) == 'lit', 2):
            r1_at = self.aug_r1(f'{tag}R1')
            if r1_at is not None:
                self.wait_until_time(r1_at + self.AUG_OP_R_STAY)
        else:
            self.macro_mark(f'{tag}R没亮(跳过)')
            self.logger.warning(f'{tag}: 开局日的 R 不亮, 跳过 —— 少 40 权柄, 后面迅击可能出不来')
        self.need(self.switch_retry(sk, f'{tag}切守'), 'augusta -> shorekeeper')

    def seg_op_aug_e(self, sk, tag):
        """启动轴: 日 E(斩锋, 战势回满) -> 三段打完 -> 切守。"""
        if self.press_res_until_cd(self, self.AUG_OP_E_CAP, f'{tag}E'):
            self.macro_wait(self.AUG_OP_E_STAY)
        self.need(self.switch_retry(sk, f'{tag}切守'), 'augusta -> shorekeeper')

    def seg_op_aug_spin(self, sk, tag):
        """启动轴: 日 后撤 -> 旋切 -> 旋切一起手就切守(日留在原地打完)。"""
        why = self.aug_backstep(self.AUG_BACKSTEP_CAP, tag, min_hold=self.AUG_BACKSTEP_MIN)
        released = time.time()
        if why == 'cap':
            self.macro_wait(self.AUG_DARK_TO_SPIN)
            self.aug_spin(tag)
            self.macro_wait(self.AUG_OP_SPIN_STAY)
        else:
            self.wait_until_time(released + self.AUG_BACK2_TO_SPIN)
            self.aug_spin(f'{tag}补')
            self.wait_until_time(released + self.AUG_OP_SPIN_TO_SWITCH)
        self.need(self.switch_retry(sk, f'{tag}切守'), 'augusta -> shorekeeper')

    # ==================== 一圈 / 启动轴 / 循环轴 ====================

    def run_opener(self, iu, sk):
        """日 R -> 守 -> 月 -> 日 -> 守 -> 月 -> 日 -> 守(变奏月) -> 月(变奏) -> 日(变奏) -> 守变奏入场。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        if self.cur is not self:
            self.need(self.switch_retry(self, 'op切日'), 'opener: switch to augusta')
        self.seg_op_aug_r(sk, 'op日R')
        self.seg_op_sk1(sk, iu, 'op守1')
        self.seg_op_iu_e1(iu, self, 'op月1')
        self.seg_op_aug_e(sk, 'op日E')
        self.seg_op_sk2(sk, iu, 'op守2')
        self.seg_op_iu_e2(iu, self, 'op月2')
        self.seg_op_aug_spin(sk, 'op日撤')
        self.seg_op_sk3(sk, iu, 'op守3')
        self.seg_iuno(iu, 'op月')
        self.seg_aug(sk, 'op日')
        self.logger.info(f'op cycle timeline: {self.macro_timeline(marks)}')
        self.timeline_logged = True

    def run_loop(self, iu, sk, tag):
        """守(变奏入场, HUD 在场) -> 月 -> 日 -> 守变奏入场、HUD 回来。"""
        marks = len(self.macro_marks)
        self.timeline_logged = False
        self.seg_sk_loop(sk, iu, f'{tag}守')
        self.seg_iuno(iu, f'{tag}月')
        self.seg_aug(sk, f'{tag}日')
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
        # 中途被停(关自动战斗/切出窗口)也要把时间轴写下来
        if not self.timeline_logged and self.macro_marks:
            self.logger.info(f'rys timeline (没跑完): {self.macro_timeline()}')
            self.timeline_logged = True
        self.hud_watch = False
        self.task.skip_combat_check = previous
        self.mark_busy(False)

    def perform_opener(self, entry_char=None):
        """启动轴。返回时守岸人刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        iu, sk, previous = got
        self.logger.info(f'rys opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            self.aug_energy_short = False
            self.run_opener(iu, sk)
            self.logger.info(
                f'rys opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'rys opener broke ({e}) — timeline: {self.macro_timeline()}')
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
                self.need(self.switch_to_sk(sk, 'L接守'), 'loop: hand the field to shorekeeper')
            self.loop_count += 1
            n = self.loop_count
            self.run_loop(iu, sk, f'L{n}')
            self.logger.info(
                f'rys loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'rys loop broke ({e}) — timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        finally:
            self.macro_exit(previous)
        return True

    def perform_tail(self, entry_char):
        """宏散掉后尤诺/奥古斯塔带着变奏上场: 从这一棒接着打到守岸人, 然后回到正常循环。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        iu, sk, previous = got
        try:
            if entry_char is iu:
                self.seg_iuno(iu, 'T月')
            self.seg_aug(sk, 'T日')
            self.logger.info(f'rys tail timeline: {self.macro_timeline()}')
            self.timeline_logged = True
        except MacroBroken as e:
            self.logger.warning(f'rys tail broke ({e}) — timeline: {self.macro_timeline()}')
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
                self.logger.info('rys: 队伍 HUD 不在(结算/过场/窗口没了?), 宏不动')
            return None
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        if (entry is not None and entry.name in ('Iuno', 'Augusta') and getattr(entry, 'has_intro', False)
                and time.time() - getattr(entry, 'last_switch_in_time', 0.0) < self.TAIL_INTRO_TTL):
            self.logger.info(f'rys: {entry.name} came in with an intro, running from there')
            return self.perform_tail(entry)
        self.logger.info(f'rys: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_rys:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时不让框架把她切上来(宏自己切)。"""
        if self.is_in_rys and self.opener_pending():
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        return super().on_combat_end(chars)
