import ctypes
import threading
import time

from src.char.BaseChar import SwitchPriority
from src.char.Lucy import Lucy as NativeLucy
from src.combat.MacroKit import MacroKit, MacroBroken

# ==================== 物理 F 键监视 ====================
# 跟 Changli__Jinhsi__Verina/Changli.py 里那份逐字相同(构建脚本抽的), 说明见那边。
F_VK = 0x46              # 'F'
F_POLL_INTERVAL = 0.05   # 一次按键按住的时间远大于这个, 不会漏采
F_LOG_DEBOUNCE = 1.0     # 按住不放时别刷日志
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


class Lucy(MacroKit, NativeLucy):
    """露西。露丽守(露西/丽贝卡/守岸人)里她兼任【整条轴的持宏者】, 三个人的 do_perform 都转到 drive()。

    ================= 轴的出处 =================
    B 站「露丽守-联动队 25 秒轮椅轴教学」(长风ei), 本地 E:/轴视频/。启动轴 24~128s、循环轴 129~193s。
    视频 1 倍速, 但有大量整帧定格; 下面提到的秒数都已扣掉定格(键盘浮层 + 头像数字牌逐帧扫出来的)。

    ================= 轴的形状 =================
    启动轴: 露 E1 -> R(面板选①-⑤) -> 切守岸人 -> 【一圈】
    一圈(循环轴从守岸人变奏入场、HUD 回来那一刻开始):
      守  启动: 3A -> E -> 3A -> 重击 -> 闪避 -> A 到回路满 -> 重击 -> Q -> [协奏保底] -> R
          循环: (变奏入场) A+E 4 秒 -> A 到回路满 -> 重击 -> 闪避 -> AA -> Q -> R
          -> 等演出 -> 打满协奏(不满用 E/强化重击/普攻补) -> 变奏丽贝卡
      丽  (变奏入场) 霰弹E -> 等它切回手枪 -> 切露西
      露  E(启动轴只剩 E2 / 循环轴 E1+E2) -> 按住普攻切丽贝卡
      丽  (一直按着) 手枪重击循环到强化重击指示亮 -> [启动轴多一下战术闪避] -> 松手出强化重击 -> 马上切露西
      露  普攻打到 A4 -> 切丽贝卡
      丽  Q -> R -> 机枪里连点(升两次阶) -> 大烟花 -> 满协奏变奏露西
      露  (变奏入场) A 到回路满 -> 强化E(死锁) -> 3A -> Q -> A 到重击指示亮 -> 回路重击 -> 等伤害打完
          -> 边 A 边 F 处决到 R 转好 -> R(面板) -> 满协奏变奏守岸人
    宏在【守岸人变奏入场、HUD 回来】时返回, 下一次框架调的就是她; 返回点不落在任何演出里。
    【守岸人那一段不按视频】: 按用户要求照搬 Augusta__Iuno__ShoreKeeper 实机调好的守岸人
    (流程和数值出自 2026-09-03 的备份 F:/ok-ww-备份-2026-09-03/Augusta.py)。
    【处决只在一处由宏按】: 露西回路重击打完、R 还没转好的那 ~1s 里边 A 边 F; 其余时候玩家手动。
    宏在跑时按的物理 F 不会被当成"重开一局"。

    ================= 帧表结论(鸣潮动作数据汇总.xlsx) =================
    - 露西 E1 命中后自动派生追加(46F), 追加第1F解锁 E2; E2 派生 64F。
    - 露西回路(传输协议)满 100 才能放 E3。E3 在死锁前置(骇入 88F)结束时才清空回路, 之后死锁还有 80F。
      【E3 期间队伍 HUD 不消失】, 所以"强化E 出去了"看回路条由满变空, 不看 HUD。
    - 露西根协议满解锁强化重击2, 后面整串不可脱手; 视频两次 R 都是松手后 ~5.15s 才被接受。
    - 露西大招面板: 左键每点一下按顺序选下一个选项, RAM 不够的选项点了没反应, 再点就收起面板;
      面板开着时队伍 HUD 不在。演出 ~3.4s 后才出面板。
    - 丽贝卡霰弹E 第40F 切回手枪。手枪重击循环长按攒狂热, 满了松手 = 手枪强化重击, 切走后照样打完。
    - 丽贝卡机枪期间霸体、不能切人, 过载满自动大烟花; 放人时刻游戏说了算(视频 R 后 ~6.9s),
      提前按着切人键等, 放人第一帧就走。
    - 守岸人 QTE 第86F前不响应输入; 大招第178F前不能切人、隐藏 HUD 179F; 强化重击-1 发生 23F、扣 5 核心。
      大招领域还在时她的变奏入场是强化QTE, 隐藏 HUD 192F —— 切给她之后要等 HUD 回来。

    ================= 判断 vs 固定时间 =================
    判断: 协奏满 / 重击指示亮暗 / 回路条满空 / 技能进 CD / 队伍 HUD 消失回来 / 切人到场。
    固定: 守岸人第一段 3A、露西 E 的站场时长、露西 4A、回路重击的按住和伤害结算、机枪段连点 ——
    要么没有可读信号, 要么信号本身就是结果。

    ================= 改这个文件之前先读 =================
    - 引擎那一半(macro_* / quick_switch / press_key_until_cd …)由构建脚本从
      Changli__Jinhsi__Verina/Changli.py 逐字抽出来, 那些注释里的角色和日志是别的轴的标定记录。
      fire_r 是本队自己写的: HUD 要【连续】消失几帧才算放出去。
    - 【自定义 __init__ 里跟内置版不同的字段会被打回内置值】, 要保住就在 reset_state() 里重申。
    - 按住左键期间别调 click()/mouse_down()/send_key() —— 它们先投 WM_ACTIVATE。
      丽贝卡按住期间的闪避走 dodge_keep_hold(), 直接投右键消息。
    """

    # ==================== 全局节奏(引擎用) ====================
    A_INTERVAL = 0.12
    KEY_INTERVAL = 0.10

    # ---- 切人 ----
    SWITCH_TIME_OUT = 2.5
    SWITCH_KEY_DOWN_TIME = 0.02
    SWITCH_KEY_INTERVAL = 0.0
    SWITCH_SETTLE = 0.0
    UNTRUSTED_CONFIRM = 3
    TRACE_SWITCH = True

    # ---- 大招 ----
    LIB_KEY_INTERVAL = 0.03
    LIB_READ_EVERY = 2
    LIB_ANIM_TIME_OUT = 8.0
    LIB_ANIM_TAIL = 0.15
    HUD_NO_ANIM_GRACE = 0.35
    LIB_GONE_CONFIRM = 3         # HUD 连续这么多帧不在 = 演出真的开始了
    HUD_UP_CAP = 1.0             # 按 R 之前等 HUD 在场的上限(防假成功)

    # ---- 协奏 ----
    CON_WAIT_TIME_OUT = 2.0
    CON_POLL_INTERVAL = 0.05
    CON_FULL_LEVEL = 1.0         # 0.99 是环没闭合的哨兵值, 要真满传 1.0
    CON_SAMPLE_MAX = 24

    FIELD_SETTLE_TIME_OUT = 1.5
    E_RESEND_STEP = 0.06
    SKILL_BAR_SETTLE = 0.40      # 刚切进来多久没见过技能亮就当它在 CD 上

    # ==================== 防重入 ====================
    OPENER_MIN_GAP = 60.0

    # ==================== 露西 · 启动段 E1 -> R ====================
    # 按 E1 -> 等 E 框变成金色的强化E 图标(追加第1F解锁 E2) -> R。视频: 按下 0.12s 出 CD, ~0.5s 变金。
    # E1 只在没被接受(没见到 CD 也没见到金色)时才补发: 露西有输入缓存, 多按的 E 会在追加之后直接放出 E2,
    # 启动轴的 E2 要留到后面
    O_LU_E1_RESEND = 0.45
    O_LU_E2_WAIT_CAP = 2.00      # 等变金的上限; 等不到也照放 R, 日志带金色占比 trace
    # E 框里金色像素(HSV 色相 12~38、饱和度>40、亮度>90)占比超过它算"亮"。
    # 视频实测: 金色实心 0.34~0.43; 白色 E1 / CD 中 / 丽贝卡和守岸人的 E 全是 0.000;
    # 金环只是变实心前 ~0.2s 的过渡(0.003)。游戏里的技能框可能比图标大一圈、占比被稀释, 所以取 0.10
    LU_E_GOLD_RATIO = 0.10
    LU_E_GOLD_CONFIRM = 2
    LU_R_WINDOW = 1.50

    # ==================== 露西 · 大招面板 ====================
    # 面板操作(放大视频图标看清的): 左键 = 对被标记目标执行当前选中的选项, 滚轮 = 上下切换选项。
    # 用完一项光标会自己跳到下一项, 所以正常情况下一直点左键就行; 可光标停在受限/RAM 不够的项上时
    # 左键永远没反应, 只能等面板 648F 超时 —— 实机 2026-09-14 循环轴点了 81 下 14.8s 才关。
    LU_PANEL_CLICK_INTERVAL = 0.17   # 视频手速。面板里点太密可能在选项刷新前被吞
    LU_PANEL_CAP = 16.0              # 演出 ~3.4s + 面板最长 648F(10.8s) + 收起
    LU_PANEL_BACK_CONFIRM = 2        # HUD 连续回来这么多帧才算面板收了
    LU_PANEL_OPEN = 3.40             # 从 R 放出去到面板出来(视频 3.34s)
    LU_PANEL_PLAIN = 2.20            # 面板出来后先只点左键这么久; 启动轴实机 5.4s 就收起了, 碰不到扫描
    LU_PANEL_SCAN_STEP = 0.45        # 之后每这么久滚一格再接着点(一格之间能点两下)
    LU_PANEL_SCAN_SPAN = 7           # 先往下滚这么多格, 再往上滚这么多格, 循环; 不管滚轮会不会绕回都能扫到每一项
    # 【面板里的左键必须带坐标】: PostMessage 后端不带坐标的点击落在"上一次带坐标点击的位置"(没有就是左上角),
    # 战斗里无所谓, 面板是能用鼠标点的界面, 点偏了就不算执行 —— 实机 2026-09-14 L2/L3 选项预选好了还愣 14.8s。
    # 轮流点屏幕正中(准星)和右下角"左键执行骇入"按钮(视频量的相对位置)。面板收起后这两处点下去就是普攻
    LU_PANEL_CLICK_POINTS = ((0.50, 0.50), (0.827, 0.798))
    LU_PANEL_SNAPSHOT = True         # 面板到扫描阶段还没收起就存一张画面到 logs/, 排查用

    # ==================== 守岸人(照搬日月守) ====================
    # 流程和数值逐个抄自日月守 Augusta.py(2026-09-03 备份版), 那边是实机一格一格标定的。
    # 只改了两处: 循环段那 4 秒不再夹 F(处决手动); 协奏"满"的门槛用 1.0(0.99 是环没闭合的哨兵值)。
    E_PRESS_WINDOW = 0.26        # 发一发 E 的连发窗口, 比任何派生帧都短, 不会连出第二发
    E_PRESS_INTERVAL = 0.06
    HEAVY_MIN_HOLD = 0.10        # 长按之后先按这么久再盯指示(按下那一瞬指示还没变)
    SHORE_A3_WINDOW = 1.38       # 启动段第一组三段普攻。A3 被 E 掐掉就加, E 前有空转/多出 A4 就减
    SHORE_E_TO_HEAVY = 1.43      # 从按下 E 起算的第一发重击时刻(绝对锚点, 后面整条链跟着平移)
    ANCHOR_E_ON_CD = False       # 诊断开关: 锚点改成 E 真进 CD 的时刻; 开着的话上面那个数要重标
    E_CD_DETECT_CAP = 0.60
    SHORE_HEAVY_HOLD = 0.85      # 读不到鼠标指示时的兜底按住时长
    SHORE_HEAVY2_HOLD = 0.45
    SHORE_HEAVY_CAP = 1.60       # 长按硬上限。指示暗了 = 长按已被识别, 立刻松手
    SHORE_DODGE_TO_A = 0.25      # 闪避 -> 接普攻, 要跨过闪避的派生帧
    SHORE_FORTE_WAIT_CAP = 5.0   # 边打边等回路满的上限, 超时也照按重击
    SHORE_FORTE_CONFIRM = 2      # 连着几帧读到满才算, 单帧会被特效骗
    SHORE_HEAVY_TO_QR = 0.30     # 启动段重击松手 -> Q
    SHORE_AE_WINDOW = 4.00       # 【已不用】循环段旧版: A 不停、E 定期补发的固定窗口
    # 循环段改成边打边判(用户 2026-09-15): A 不停、E 定期补发; 重击指示一亮就马上 重击 -> 闪避 再接着打;
    # 协奏读到 SHORE_LOOP_CON_QR 就直接 Q R 切人。没到就照旧 重击 -> 闪避 -> AA 之后 Q R
    SHORE_LOOP_CON_QR = 0.80
    SHORE_LOOP_MIN = 2.20        # 正常收尾前至少打这么久: QTE 86F 输入锁(1.43s) + E 出手
    SHORE_LOOP_CAP = 9.00        # 整段上限(旧版 4s A+E + 等回路满 5s)
    SHORE_CON_POLL_EVERY = 0.15  # 读协奏的间隔(能量环连通域分析不便宜)
    SHORE_E_RESEND = 0.60        # E 补发间隔(落在 CD 上是空操作)
    SHORE_A2_WINDOW = 0.80       # 循环段闪避之后两下 A: A1+A2 派生累计 0.83, 收在它前面正好两段
    SHORE_A2_TO_Q = 0.10
    SHORE_E_DERIVE = 0.67        # E-地面 派生 40F
    SHORE_LIB_WINDOW = 1.80      # R 的重试窗口, 要盖过强化重击的收招(86F)
    SHORE_LIB_MIN_CON = 0.70     # 启动段放 R 前协奏至少到这个值, 不够补一发强化重击(R 自己给 20)
    SHORE_LIB_CON_SETTLE = 0.60
    SHORE_LIB_RESCUE_CAP = 3.0
    SHORE_CON_CAP = 6.0          # R 之后边打边等协奏满的上限
    SHORE_CON_RESCUE_CAP = 8.0   # 还不满就按 E > 强化重击 > 普攻 补, 这是补救上限(打不到怪时防卡死)
    SHORE_CON_RESCUE_SLICE = 0.80
    SHORE_CON_ENOUGH = 1.0
    SHORE_CON_FULL_CONFIRM = 3   # 协奏满要连着几帧读到, 一个假的满会把交棒提前
    SHORE_CON_SETTLE = 0.50
    SHORE_CON_POLL = 0.03
    # 切给守岸人之后: 带演出的强化QTE 等 HUD 回来; 没演出就只看一小段它会不会藏 HUD,
    # 普通 QTE 的输入锁由循环段那 4 秒 A+E 盖过去(跟日月守一样)
    SK_INTRO_ANIM_CAP = 6.0
    SK_INTRO_NOANIM = 0.35
    SK_SWITCH_ANIM_HINT = 1.00   # 切给她的 quick_switch 超过这么久才判到到场 = 强化QTE 已经在里面演完

    # ==================== 丽贝卡 · 变奏入场霰弹E ====================
    # 视频逐帧: 霰弹E 出手 40F 后 E 图标从霰弹换成手枪, 出手后 ~1.2s E 上才出现 CD 数字。
    # 实机 2026-09-14 上限 1.3s 时一直没读到 CD、E 也没出来就切走了 -> 上限放宽、按键放慢
    RB_E_CAP = 2.40              # 连按 E 等它进 CD 的上限
    RB_E_INTERVAL = 0.15         # 视频手速 ~0.2s 一下
    RB_E_SETTLE = 0.25           # 到场头这么久技能栏还是守岸人的, 读数不算
    RB_E_TO_SWITCH = 0.70        # 读不到 CD、按满上限之后再等这么久才切(霰弹E 第40F 才切回手枪)

    # ==================== 露西 · E ====================
    O_LU_E2_STAY = 1.15          # 启动轴只有 E2: 派生 64F(1.07s)
    L_LU_E12_STAY = 1.95         # 循环轴 E1+E2: 46F + 64F = 110F(1.83s), 视频 2.04s
    # E 段按图标走: [E1 -> 等 E 图标变金(E2 解锁)] -> 连按 E 到金色消失(E2 放出去) -> 站满 stay -> 按住切丽贝卡。
    # 实机 2026-09-14 循环轴 E2 偶尔没出来: 旧写法固定连按 1.2s 且"回路读到满就停手", E1 出得晚或回路先满都会漏 E2。
    # 【金色消失一次就停手】: E2 放完回路可能刚好满, 再按会当场放出强化E(死锁锁切人)
    LU_E_SETTLE = 0.25           # 到场头这么久技能栏还是丽贝卡的, 读数不算
    LU_E2_GOLD_CAP = 1.60        # 按完 E1 等变金的上限(视频 ~0.5s)
    LU_E2_PRESS_CAP = 1.40       # 连按 E2 到金色消失的上限
    # 实机 2026-09-14 两种漏 E2: ① 循环轴进场时 E 框被特效染成金色, 宏以为 E2 已解锁跳过了 E1, 按一下金色没了又当成 E2 出去;
    # ② E1 读到 CD 了, 1.6s 里没判到变金。所以:
    #   - 循环轴一律先按 E1(上一圈 E2 用掉了, 进场时不可能已解锁), 只有启动轴直接按 E2;
    #   - 金色只在【E 框没有 CD 数字】时才算(真的强化E 图标上没有数字, 特效扫过去数字还在), 连续 3 帧;
    #   - E2 出去 = 金色消失且出现 CD 数字; 读不到数字时要连续暗 6 帧
    LU_E2_GOLD_CONFIRM = 3
    LU_E2_GOLD_AFTER_CD = 0.25   # E1 进 CD 之后这么久才开始认金色(追加第1F才解锁)
    LU_E2_GOLD_NO_CD_AFTER = 0.45  # CD 数字一直读不到时, 从第一发 E1 起这么久才认金色
    LU_E2_DARK_NO_CD = 6
    LU_E_INTERVAL = 0.10

    # ==================== 丽贝卡 · 长按 ====================
    RB_HOLD_SETTLE = 0.40        # 到场头这么久的指示读数不算(技能栏还是露西的)
    RB_HOLD_CONFIRM = 2
    RB_HOLD_CAP = 7.00           # 视频: 循环轴 3.5s, 启动轴(狂热从低位攒, 带两下闪避)5.3s
    O_RB_TACTICAL_DODGE = True   # 启动轴满狂热后打一下战术闪避再松手, 多的协奏让大烟花之后直接满
    RB_DODGE_TO_RELEASE = 0.70   # 手枪战术闪避派生 36F
    # 松手 -> 切露西。循环轴视频松手 0.16s 就切(强化重击切走后照样打完); 启动轴(带战术闪避)视频是 1.0s。
    # 实机 2026-09-14 启动轴也用 0.15 时, 露西进场没马上出 A、打不到 A4 —— 切人压在了强化重击起手里
    O_RB_RELEASE_TO_SWITCH = 1.00
    L_RB_RELEASE_TO_SWITCH = 0.15
    # 上面两个现在是【读不到指示时的上限】: 松手后狂热指示连续两帧暗 = 强化重击起手吃掉了狂热, 再等这么久就切。
    # 实机 2026-09-15 启动轴松手到露西到场 1.09s, 用户嫌慢; 1.0s 是为了别在强化重击起手前切走(0.15 时露西进场不出 A)
    RB_DARK_TO_SWITCH = 0.10

    # ==================== 露西 · 4A ====================
    # 从到场起连点, A4 出手时切。视频 2.16~2.33s; 帧表 A1~A3 派生累计 ~2.0s 才轮到 A4。
    # 实机 2026-09-14 用 2.10 打不到 A4
    LU_4A_WINDOW = 2.50

    # ==================== 丽贝卡 · QR ====================
    RB_Q_WINDOW = 0.10
    RB_Q_TO_R = 0.05
    # 实机 2026-09-14: 1.5s 窗口里判不到演出(R_NOFIRE), 可紧接着 HUD 就消失了 —— R 是放出去了、起得晚。
    # 宏以为没放, 跳过机枪段直接切露西, 机枪+大烟花期间不放人, 5s 超时整圈判失败退回守岸人
    RB_R_WINDOW = 3.00
    RB_R_LATE_GRACE = 1.00       # 窗口结束后这么久内 HUD 连续消失也算 R 放出去了
    # 放 R 前能量保底(用户 2026-09-15): 放完 Q 读 R, 没转好 -> 强化重击指示还亮就补一发强化重击, 否则平 A, 转好了才按 R。
    # 能量不够一般是前面那发强化重击被打断了(狂热还满着, 大招回收 12~18 那一大笔没进账)
    RB_R_READY_SETTLE = 0.30     # 到场这么久之内的 R 读数不算(技能栏还是露西的)
    RB_R_BUILD_CAP = 20.0        # 攒能量的防卡死上限(打不到怪时永远攒不满)
    RB_R_BUILD_SLICE = 1.00      # 平 A 每轮时长, 之后再读一次 R
    RB_R_HEAVY_MAX = 2           # 最多补几发强化重击
    RB_HEAVY_PRESS = 0.65        # 按住过手枪重击-2 发生帧(32F)再松, 狂热满时松手出强化重击
    RB_HEAVY_DMG = 1.35          # 强化重击伤害-5 在 78F; 这段不点不按 R —— R 优先级高, 会把它打断
    RB_MG_CLICK_INTERVAL = 0.17  # 机枪里点普攻升阶
    RB_MG_R_EVERY = 0.90         # 顺带隔一会按一下 R(同样能升阶); 大招已放, 按了不会出别的
    RB_MG_BEFORE_SWITCH = 5.30   # 从开始按 R 起算, 之后边点边按切人。游戏 ~6.9s 放人
    RB_SWITCH_TIME_OUT = 9.00    # 要盖过 R 起得晚 + 机枪 + 大烟花; 放人第一帧就走, 给长不花钱
    # 协奏保底(用户 2026-09-15): 正常情况不用等大烟花打中协奏就是满的; 前面强化重击没打中怪才会差一截。
    # 所以【QR 入场时】读一次协奏: 低于 RB_CON_LOW -> 这圈等机枪+大烟花放完、协奏真满了再切露西;
    # 不低 -> 最后一枪打完游戏一放人就切(原来的写法)。不满就切的话露西没有变奏入场, 收尾整段全乱
    RB_CON_LOW = 0.75
    RB_CON_READ = 0.15           # 入场读协奏的时长(读到的最大值)
    RB_R_END_CAP = 8.00          # 等机枪 + 大烟花放完(队伍 HUD 回来)的上限
    RB_CON_WAIT_CAP = 4.00       # HUD 回来后等协奏满(大烟花打中)的上限, 到了也照样切

    # ==================== 露西 · 收尾 ====================
    LU_FILL_SETTLE = 0.40
    LU_FILL_CONFIRM = 2
    LU_FILL_CAP = 4.00           # 变奏入场 A 到回路满的上限; 视频启动轴 ~2.1s, 循环轴 E1+E2 之后几乎是满的
    LU_FILL_SNAPSHOT_AT = 0.80   # 到场这么久还没判满就存一张画面到 logs/(排查"强化E 好了却在等")
    LU_E3_PRESS = 0.60           # 强化E 每次只连发这么久, 之后等回路清空(死锁里多按的 E 会进缓存)
    # 骇入 88F 结束才清空回路; 实机按完到判到清空 0.94~1.46s。等不到 = 按的时候被打断、强化E 没出去,
    # 重按(用户 2026-09-14: 被打断就愣住了 —— 日志 `回路清空(CAP,2.52s)` 之后直接去打 3A)
    LU_E3_CLEAR_CAP = 2.20
    LU_E3_TRIES = 4
    # 判到回路满之后连按 E, 按到【队伍 HUD 连续消失】(或 E 框出 CD 数字)就停, 最长这么久。
    # 【要盖过 A3 的「第46F前禁用E」(0.77s)】: 回路常在连点打出 A3 的那一刻满, 实机 2026-09-14 23:04 启动轴
    # 旧版只按 0.6s 全落在禁用期里 -> 判成被打断 -> 干等 2.2s 重按(用户: 都好了还等 1~2s)。
    # 实机 23:17: 强化E 一出手队伍 HUD 就没了(trace 按完第一下之后再没读数) —— 这是最快的"出去了"信号;
    # 用 E 框图标差分判过一版, 回路刚满时 E1 图标换成强化E 图标本身就差 62~86, 按两下就误判成出去了
    LU_E3_PRESS_CAP = 1.20
    LU_E3_TAIL = 1.10            # 清空之后死锁还有 ~80F, 视频量到 ~1.1s 恢复连点
    LU_E3_BLIND = 2.60           # 读不到回路条时从按 E 起算的固定等待
    LU_3A_WINDOW = 0.65
    LU_Q_WINDOW = 0.30
    LU_Q_INTERVAL = 0.14
    LU_HEAVY_FILL_CAP = 3.00     # A 到重击指示亮(根协议满)的上限; 视频 ~1.7s
    LU_HEAVY_HOLD = 1.00         # 回路重击按住时长, 视频 1.03s
    # 回路重击保底(用户 2026-09-14: 被打断就不打了): 松手后重击指示还亮 = 没打出去, 再按; 等伤害之后、放 R 之前也各查一次
    LU_HEAVY_RETRY = 3
    LU_HEAVY_VERIFY = 0.50       # 松手后等指示暗的时长; 强化重击2 起手就清空根协议, 按住 1s 早该暗了
    # 按住期间就盯指示(实机 23:31 启动轴: 指示亮了立刻按住 1s, 松手还亮 = 这下没被接受, 再等 0.5s 才重按, 拖了 ~1.6s):
    # 暗了 = 接住了(强化重击2 起手清空根协议), 从那一刻再按够 LU_HEAVY_HOLD_AFTER 松手, 让后面强化重击3 整串打完;
    # 按下 LU_HEAVY_REGRIP_AT 秒还亮 = 没接住(常见是正压在强化普攻的动作里), 松开立刻重按, 最多 LU_HEAVY_REGRIPS 次
    LU_HEAVY_HOLD_AFTER = 0.95
    LU_HEAVY_REGRIP_AT = 0.45
    LU_HEAVY_REGRIPS = 3
    LU_HEAVY_HOLD_CAP = 4.00
    # 回路重击松手之后到 R 能放之前: 头 LU_HEAVY_NO_F 秒【只点 A 不按 F】, 之后 F 一直按、A 不停, R 转好就去放 R。
    # 【回路重击绝对不能被处决打断】(用户 2026-09-14 实机循环轴见过): 整串是强化重击2(派生50F) -> 强化重击3 下落(30F)
    # -> SQL落地(结束80F), 按住 1s 之后还要 ~1.7s 才打完, F 落在这里面会直接出处决把它打断。
    # 头 LU_HEAVY_A_ONLY 秒不看 R(重击整串没打完, 这时按 R 会被吞)。实机 R 在松手后 ~3.8s 被接受
    LU_HEAVY_NO_F = 1.80
    LU_HEAVY_A_ONLY = 2.60
    LU_F_BEFORE_R = True
    LU_F_CAP = 2.50              # 边 A 边 F 等 R 转好的上限, 超时也照样去按 R
    LU_F_ANIM_CAP = 5.00         # 处决演出(队伍 HUD 消失)等回来的上限
    LU_HEAVY_MASH_INTERVAL = 0.17
    LU_R_WINDOW_FINAL = 2.50
    # 窗口结束后这么久内 HUD 连续消失也算 R 放出去了(R 在窗口最后一刻才被接受时判不到, 会跳过面板直接切人卡死)
    LU_R_LATE_GRACE = 1.00
    # 收尾的 R 必须放出来(用户 2026-09-14: 前面被打断能量没攒够, 没放 R 就切人了)。
    # 按不出来就攒能量(回路重击 > 声骸 > 普攻)再按, 循环到出手; 上限只防打不到怪时永远攒不满
    LU_R_ENSURE_CAP = 25.0
    LU_R_BUILD_SLICE = 1.20      # 每轮攒能量的连点时长
    LU_R_RETRY_WINDOW = 1.00     # 补救时每次按 R 的窗口
    LU_R_BLIND_EVERY = 3         # R 读成暗时只攒不按, 每这么多轮盲按一次(防读数不准); 按 R 那 1~2s 是不打怪的
    LU_R_BLIND_WINDOW = 0.40     # 盲按只按这么久、不等"晚到" —— 实机 2026-09-15 进保底先盲按 2.5s+等晚到 1s, 3.5s 一下 A 没点
    LU_F_BREAK = False           # 处决玩家手动按。改 True 就在面板收起后连按 F
    LU_F_WINDOW = 1.10
    LU_F_INTERVAL = 0.20
    LU_TO_SK_TIME_OUT = 4.00     # 处决动画里不放人

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
        self.loop_count = 0

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        # 内置非治疗角色默认 True, 换类时会被打回, 所以在这里重申
        self.check_f_on_switch = False
        start_f_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_lrs(self) -> bool:
        """本队 = 露西 + 丽贝卡 + 守岸人。按类名判。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Rebecca', 'ShoreKeeper'}:
                team_members += 1
        return team_members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。不查任何技能可用性(场下读数是旧的)。"""
        if not self.is_in_lrs:
            return 'team is not lucy/rebecca/shorekeeper'
        if not self.opener_armed:
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

    # ==================== 宏原语 ====================
    # 通用的在 src/combat/MacroKit.py (MacroKit); 这里只有这条轴自己的写法, 同名的盖过 MacroKit 的。

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

    # ==================== 本条轴额外用到的原语 ====================

    def fire_r(self, char, tag, window):
        """发大招到队伍 HUD【连续】消失 LIB_GONE_CONFIRM 帧。返回放出去没有。

        跟引擎 fire_lib 的差别就是这道确认: 露西强化重击的特效会让 HUD 读数闪一两帧,
        单帧判会把没放出去的 R 记成放了。发键之前先等 HUD 在场(HUD 本来就不在会假成功)。
        确认期间继续按 R 没有副作用 —— 演出里的按键被丢掉。
        """
        self.wait_hud_up(tag=f'{tag}前HUD')
        start = time.time()
        self.lib_press_at = start
        n = gone = 0
        fired = False
        while time.time() - start < window:
            char.send_liberation_key()
            n += 1
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    fired = True
                    break
            else:
                gone = 0
            self.macro_wait(self.LIB_KEY_INTERVAL)
        self.task.skip_combat_check = True
        if fired:
            char.record_liberation_use()
            self.macro_mark(f'{tag}_fired(x{n},{time.time() - start:.2f}s)')
            return True
        self.macro_mark(f'{tag}_NOFIRE(x{n})')
        try:
            lit = bool(char.liberation_available())
        except Exception:
            lit = 'err'
        # lit=True: 键被上一个动作吞了; lit=False: 能量不够
        self.logger.warning(f'{tag}: {char.name} liberation never started an animation '
                            f'after {n} presses (icon lit={lit})')
        return False

    def click_until_lit(self, read, cap, tag, settle=0.0, confirm=2, min_window=0.0,
                        interval=None, poke=True):
        """一直点普攻(poke=False 就只读不点)到某个指示连续亮 confirm 帧。返回亮了没有。

        settle: 到场头这么久的读数不算(技能栏/回路条换成新角色要几帧, 那几帧是上一个人的)。
        min_window: 至少点这么久才认亮 —— 提前满了也把这一段打完。
        HUD 不在的帧不读(演出/特效里读数是假的)。
        """
        interval = self.A_INTERVAL if interval is None else interval
        start = time.time()
        n = streak = 0
        next_click = 0.0
        lit = False
        trace = []
        while time.time() - start < cap:
            self.task.next_frame()
            now = time.time()
            if now - start >= settle and self.task.in_team()[0]:
                try:
                    full = bool(read())
                except Exception:
                    full = False
                if len(trace) < 40:
                    trace.append(f'{now - start:.2f}:{1 if full else 0}')
                streak = streak + 1 if full else 0
                if streak >= confirm and now - start >= min_window:
                    lit = True
                    break
            if poke and now >= next_click:
                self.click()
                n += 1
                next_click = now + interval
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"亮" if lit else "CAP"},A x{n},{time.time() - start:.2f}s)')
        if not lit:
            self.logger.info(f'{tag}: indicator never lit in {cap:.1f}s — {" ".join(trace)}')
        return lit

    def wait_until_dark(self, read, cap, tag, confirm=2):
        """不按任何键, 等某个指示连续暗 confirm 帧。只在 HUD 在场时读。返回暗了没有。"""
        start = time.time()
        streak = 0
        dark = False
        trace = []
        while time.time() - start < cap:
            self.task.next_frame()
            if self.task.in_team()[0]:
                try:
                    full = bool(read())
                except Exception:
                    full = True
                if len(trace) < 40:
                    trace.append(f'{time.time() - start:.2f}:{1 if full else 0}')
                streak = 0 if full else streak + 1
                if streak >= confirm:
                    dark = True
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"暗" if dark else "CAP"},{time.time() - start:.2f}s)')
        if not dark:
            self.logger.info(f'{tag}: indicator never went dark in {cap:.1f}s — {" ".join(trace)}')
        return dark

    def hold_until_clear(self, read, min_hold, cap, blind_hold, tag):
        """按住左键: 读到过亮、之后变暗、且按够 min_hold 才松; 一次都没读到亮就按 blind_hold 松。

        【先见过亮才认暗】: 按下的头几帧指示可能还没刷新。
        按住期间一下都不点(会打断长按)。
        """
        self.task.mouse_down()
        self.holding_attack = True
        start = time.time()
        seen = False
        state = 'CAP'
        try:
            while time.time() - start < cap:
                self.task.next_frame()
                held = time.time() - start
                if self.task.in_team()[0]:
                    try:
                        full = bool(read())
                    except Exception:
                        full = False
                    if full:
                        seen = True
                    elif seen and held >= min_hold:
                        state = 'cleared'
                        break
                    elif not seen and held >= blind_hold:
                        state = 'blind'
                        break
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        self.macro_mark(f'{tag}({state},{time.time() - start:.2f}s)')
        return state

    def dodge_keep_hold(self, tag):
        """左键按着的时候闪避: 直接投右键消息, 不走 click()。

        PostMessage 后端的 click()/mouse_down() 都会先投 WM_ACTIVATE, 按着的左键会被打断。
        wParam 带上 MK_LBUTTON 告诉窗口左键仍按着。拿不到后端就退回普通右键(那样左键多半会松)。
        """
        interaction = getattr(getattr(self.task, 'executor', None), 'interaction', None)
        post = getattr(interaction, 'post', None)
        pos = getattr(interaction, 'mouse_pos', None)
        try:
            import win32api
            import win32con
            if post is None or pos is None:
                raise AttributeError('no PostMessage backend')
            lparam = win32api.MAKELONG(int(pos[0]), int(pos[1]))
            post(win32con.WM_RBUTTONDOWN, win32con.MK_RBUTTON | win32con.MK_LBUTTON, lparam)
            self.macro_wait(0.03)
            post(win32con.WM_RBUTTONUP, win32con.MK_LBUTTON, lparam)
            how = 'post'
        except Exception as e:
            self.task.click(key='right', after_sleep=0)
            how = f'click:{e.__class__.__name__}'
        self.macro_mark(f'{tag}闪避({how})')

    def spam_a_f(self, window, tag):
        """一个窗口里 F 一直按、A 不停, 不读屏。F 和 A 错开帧发(同一帧两个键游戏只认后按的)。
        没处决可打时 F 什么都不触发; 处决演出里的按键被丢掉, 也不花钱。"""
        start = time.time()
        na = nf = 0
        next_a = next_f = 0.0
        while time.time() - start < window:
            now = time.time()
            if now >= next_f:
                self.task.send_key('f')
                nf += 1
                next_f = now + self.LU_F_INTERVAL
            elif now >= next_a:
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            self.macro_wait(0.02)
        self.task.can_break = False
        self.macro_mark(f'{tag}(A x{na},F x{nf},{time.time() - start:.2f}s)')

    def lu_a_f_until_r(self, tag):
        """接着边 A 边按 F, 直到 R 连续两帧读到可用就停手去放 R。

        处决触发了(队伍 HUD 连续消失 = 击破演出)就等演出结束(期间照样点), 然后【接着按】到 R 转好 ——
        演出里按 R 会被丢掉, 而且 fire_r 靠 HUD 消失判大招, 演出没过去之前不能开始按 R。
        上限不算处决演出的时间。R 一直读不到可用就按满上限, 交给 lu_ensure_r 去按/补救。
        """
        start = time.time()
        na = nf = 0
        next_a = next_f = 0.0
        ready_hits = gone = 0
        anims = 0
        why = 'CAP'
        trace = []
        while time.time() - start < self.LU_F_CAP:
            now = time.time()
            if now >= next_f:
                self.task.send_key('f')
                nf += 1
                next_f = now + self.LU_F_INTERVAL
            elif now >= next_a:
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    anims += 1
                    anim_at = time.time()
                    self.poke_until_hud_back(tag=f'{tag}处决演出', time_out=self.LU_F_ANIM_CAP, tail=0.0)
                    start += time.time() - anim_at          # 演出时间不算进上限
                    gone = ready_hits = 0
                    next_a = next_f = 0.0
            else:
                gone = 0
                try:
                    ready = bool(self.liberation_available())
                except Exception:
                    ready = False
                ready_hits = ready_hits + 1 if ready else 0
                if len(trace) < 40:
                    trace.append(f'{time.time() - start:.2f}:{1 if ready else 0}')
                if ready_hits >= 2:
                    why = 'R好了'
                    break
            self.macro_wait(0.02)
        self.task.can_break = False
        self.macro_mark(f'{tag}A+F({why},处决x{anims},A x{na},F x{nf},{time.time() - start:.2f}s)')
        if why == 'CAP':
            self.logger.info(f'{tag}A+F: liberation never read as ready in {self.LU_F_CAP:.1f}s — '
                             f'{" ".join(trace)}')

    def spam_f_break(self, window, tag):
        """连按 F + 普攻一个窗口。没有处决可打时按 F 不触发别的(本场景没有可拾取物)。"""
        start = time.time()
        nf = na = 0
        next_f = next_a = 0.0
        while time.time() - start < window:
            now = time.time()
            if now >= next_f:
                self.task.send_key('f')
                nf += 1
                next_f = now + self.LU_F_INTERVAL
            elif now >= next_a:
                # 跟 F 错开帧发: 同一帧两个键游戏只认后按的
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            self.macro_wait(0.02)
        self.task.can_break = False
        self.macro_mark(f'{tag}(F x{nf},A x{na})')

    def lu_ult_panel(self, tag):
        """露西大招演出 + 选项面板: 一直点左键到队伍 HUD 回来。调用前 R 已确认放出去(HUD 已消失)。

        演出里的点击被丢掉。面板里左键执行当前选中项、用完光标自己跳到下一项, RAM 用完再点就收起。
        面板出来 LU_PANEL_PLAIN 秒还没收起 = 光标卡在用不了的项上: 开始"滚一格 -> 点两下"地扫,
        先往下扫 LU_PANEL_SCAN_SPAN 格再往上扫同样多格, 循环到收起为止。
        """
        start = time.time()
        n = back = scrolls = 0
        next_click = 0.0
        next_scroll = start + self.LU_PANEL_OPEN + self.LU_PANEL_PLAIN
        done = False
        snapped = False
        while time.time() - start < self.LU_PANEL_CAP:
            now = time.time()
            if now >= next_scroll:
                if self.LU_PANEL_SNAPSHOT and not snapped:
                    snapped = True
                    self.save_panel_snapshot(tag)
                down = (scrolls % (2 * self.LU_PANEL_SCAN_SPAN)) < self.LU_PANEL_SCAN_SPAN
                try:
                    self.task.scroll_relative(0.5, 0.5, -1 if down else 1)
                except Exception as e:
                    self.logger.info(f'{tag}面板: scroll failed ({e})')
                scrolls += 1
                next_scroll = now + self.LU_PANEL_SCAN_STEP
                next_click = max(next_click, now + 0.08)   # 滚完稍等再点, 别跟滚轮挤在同一帧
            if now >= next_click:
                x, y = self.LU_PANEL_CLICK_POINTS[n % len(self.LU_PANEL_CLICK_POINTS)]
                self.click(x, y)
                n += 1
                next_click = now + self.LU_PANEL_CLICK_INTERVAL
            self.task.next_frame()
            if self.task.in_team()[0]:
                back += 1
                if back >= self.LU_PANEL_BACK_CONFIRM:
                    done = True
                    break
            else:
                back = 0
            self.macro_wait(0.02)
        self.add_freeze_duration(start, time.time() - start)
        self.macro_mark(f'{tag}面板({"收起" if done else "CAP"},点x{n},滚x{scrolls},{time.time() - start:.2f}s)')
        return done

    def save_panel_snapshot(self, tag):
        """把当前画面存到 working/logs/lrs_panel_<时间>_<tag>.png, 日志里记路径。失败不影响宏。"""
        try:
            import os
            import cv2
            frame = self.task.frame
            if frame is None:
                return
            name = time.strftime('lrs_panel_%m%d_%H%M%S') + f'_{tag}.png'
            path = os.path.join(os.getcwd(), 'logs', name)
            cv2.imencode('.png', frame)[1].tofile(path)
            self.logger.info(f'{tag}面板: still open, snapshot saved to {path}')
        except Exception as e:
            self.logger.info(f'{tag}面板: snapshot failed ({e})')

    def switch_to_sk(self, sk, tag):
        """切给守岸人并等她的变奏入场演完。返回时 HUD 在场、场上是她。

        带演出的强化QTE 一切进来 HUD 就藏了, quick_switch 读不到她到场会超时 ——
        所以不管切人判没判到, 都等 HUD 回来再认一次场上是谁。
        没演出的普通 QTE 不在这里等输入锁: 循环段开头那 4 秒 A+E 本来就盖着它。
        """
        src = self.cur
        sw_at = time.time()
        arrived = self.quick_switch(sk, click_during=True, read_con=True,
                                    time_out=self.LU_TO_SK_TIME_OUT)
        # 切人本身花了这么久 = 演出已经在等切人判定的时候演完了(到场要 HUD 回来才读得到), 不用再盖 QTE 锁
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
            if not self.task.in_team()[0]:
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
                return False
            self.handoff(src if src is not None else self, sk, True)
            self.macro_mark(f'{tag}>Sho(演出里到场)')
        return back

    # ==================== 各格 ====================

    def seg_lu_open(self, tag):
        """启动轴第一格: 露西 E1 -> 等 E 图标变金(强化E 解锁) -> R -> 面板。

        E1 命中后自动派生追加, 追加第1F解锁 E2, E 框换成金色图标 —— 那一刻 E1 整套打完、
        骇破偏移也挂上了, 马上接 R。
        """
        self.poking = False
        start = time.time()
        last_press = -9.0
        n = streak = 0
        seen_cd = False
        gold = False
        trace = []
        while time.time() - start < self.O_LU_E2_WAIT_CAP:
            now = time.time()
            if not seen_cd and streak == 0 and now - last_press >= self.O_LU_E1_RESEND:
                self.send_resonance_key()
                n += 1
                last_press = now
            self.task.next_frame()
            if self.task.in_team()[0]:
                ratio = self.box_gold_ratio('box_resonance')
                cd = self.read_cd('resonance')
                if cd > 0:
                    seen_cd = True
                if len(trace) < 60:
                    trace.append(f'{time.time() - start:.2f}:{ratio:.2f}/cd{cd:.1f}')
                streak = streak + 1 if ratio >= self.LU_E_GOLD_RATIO else 0
                if streak >= self.LU_E_GOLD_CONFIRM:
                    gold = True
                    break
            self.macro_wait(0.02)
        self.record_resonance_use()
        self.macro_mark(f'{tag}E1(x{n},{"变金" if gold else "CAP"},{time.time() - start:.2f}s)')
        if not gold:
            self.logger.info(f'{tag}E1: E icon never turned gold in {self.O_LU_E2_WAIT_CAP:.1f}s '
                             f'(firing R anyway) — gold/cd: {" ".join(trace)}')
        if self.fire_lucy_r(self.LU_R_WINDOW, tag):
            self.lu_ult_panel(tag)

    def gold_now(self, frames):
        """E 框连续 frames 帧读到金色(强化E 图标)。只读 HUD 在场的帧。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.task.in_team()[0]:
                if self.box_gold_ratio('box_resonance') >= self.LU_E_GOLD_RATIO:
                    hits += 1
                    if hits >= frames:
                        return True
                else:
                    return False
            self.macro_wait(0.02)
        return False

    def lu_e1_until_gold(self, cap, tag):
        """按 E1 到 E 图标变金(追加第1F解锁 E2)。只在没见到 CD 也没见到金色时才补发(防输入缓存连出 E2)。

        金色要同时满足: E 框没有 CD 数字、E1 已经进过 CD(读不到 CD 时退成"第一发 E1 之后够久")、连续 3 帧。
        """
        start = time.time()
        last_press = -9.0
        first_press = None
        n = streak = 0
        cd_at = None
        gold = False
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if cd_at is None and streak == 0 and now - last_press >= self.O_LU_E1_RESEND:
                self.send_resonance_key()
                n += 1
                last_press = now
                if first_press is None:
                    first_press = now
            self.task.next_frame()
            if self.task.in_team()[0]:
                ratio = self.box_gold_ratio('box_resonance')
                cd = self.read_cd('resonance')
                now = time.time()
                if cd > 0 and cd_at is None:
                    cd_at = now
                if cd_at is not None:
                    armed = now - cd_at >= self.LU_E2_GOLD_AFTER_CD
                else:
                    armed = first_press is not None and now - first_press >= self.LU_E2_GOLD_NO_CD_AFTER
                valid = armed and cd <= 0 and ratio >= self.LU_E_GOLD_RATIO
                if len(trace) < 60:
                    trace.append(f'{now - start:.2f}:{ratio:.2f}/cd{cd:.1f}')
                streak = streak + 1 if valid else 0
                if streak >= self.LU_E2_GOLD_CONFIRM:
                    gold = True
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(x{n},{"变金" if gold else "CAP"},{time.time() - start:.2f}s)')
        if not gold:
            self.logger.info(f'{tag}: E icon never turned gold in {cap:.1f}s — gold/cd: {" ".join(trace)}')
        return gold

    def lu_press_while_gold(self, cap, tag, primed=False):
        """E 图标是金色(E2 可放)就连按 E, 金色连续消失两帧 = E2 放出去了, 立刻停手。

        【消失一次就停】: E2 放完回路可能刚好满, 再按会当场放出强化E; E1 冷却被削到 ~1.5s, 多按还会再出 E1。
        primed: 调用方刚确认过变金 —— 第一下常常当场就把 E2 放出去, 这里一次都读不到金色,
        不当成"见过金色"的话会一直按到上限(干跑抓到的)。
        一开始就没读到金色(判据失灵)才照按到上限 —— 宁可多按几下, 也不要漏掉 E2。
        """
        start = time.time()
        n = dark = 0
        seen_gold = primed
        cast = False
        next_press = 0.0
        trace = []
        while time.time() - start < cap:
            now = time.time()
            if now >= next_press:
                self.send_resonance_key()
                n += 1
                next_press = now + self.LU_E_INTERVAL
            self.task.next_frame()
            if self.task.in_team()[0]:
                ratio = self.box_gold_ratio('box_resonance')
                cd = self.read_cd('resonance')
                if len(trace) < 60:
                    trace.append(f'{time.time() - start:.2f}:{ratio:.2f}/cd{cd:.1f}')
                if ratio >= self.LU_E_GOLD_RATIO and cd <= 0:
                    seen_gold = True
                    dark = 0
                elif seen_gold:
                    dark += 1
                    # 出现 CD 数字才算真放出去; 特效把金色遮住几帧不算。读不到数字时要暗得够久
                    if (cd > 0 and dark >= 2) or dark >= self.LU_E2_DARK_NO_CD:
                        cast = True
                        break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(x{n},{"出手" if cast else "CAP"},{time.time() - start:.2f}s)')
        if not cast:
            self.logger.info(f'{tag}: gold never went away (seen gold={seen_gold}) — {" ".join(trace)}')
        return cast

    def lu_ensure_heavy(self, tag):
        """回路重击保底: 重击指示亮着(根协议满)就按住打出去, 松手后指示还亮 = 被打断/没接受, 再来一次。

        判"打出去了"看指示由亮变暗(强化重击2 起手清空根协议)。指示一开始就没亮就什么都不做。
        """
        tries = 0
        while tries < self.LU_HEAVY_RETRY and self.lit_now(self.is_mouse_forte_full, 2):
            tries += 1
            caught = self.lu_hold_heavy(f'{tag}回路重击#{tries}')
            if caught or self.wait_until_dark(self.is_mouse_forte_full, self.LU_HEAVY_VERIFY,
                                              f'{tag}回路重击松开#{tries}'):
                return True
            self.logger.info(f'{tag}: heavy indicator still lit after hold #{tries}, pressing again')
        return tries > 0

    def lu_hold_heavy(self, tag):
        """按住回路重击, 按住期间盯重击指示。返回游戏接住没有(按住期间见到指示暗)。

        - 指示连续两帧暗 = 接住了: 从那一刻再按够 LU_HEAVY_HOLD_AFTER 秒松手;
        - 按下 LU_HEAVY_REGRIP_AT 秒还亮 = 这下没被接受: 松开 0.05s 马上重按(新的按下沿), 最多 LU_HEAVY_REGRIPS 次;
        - HUD 不在的帧不读(特效/演出里读数不准)。
        按住期间不点、不发任何会 activate 的键(会打断长按)。
        """
        start = time.time()
        press_at = start
        regrips = dark = 0
        dark_at = None
        trace = []
        self.task.mouse_down()
        self.holding_attack = True
        try:
            while time.time() - start < self.LU_HEAVY_HOLD_CAP:
                self.task.next_frame()
                now = time.time()
                if self.task.in_team()[0]:
                    try:
                        lit = bool(self.is_mouse_forte_full())
                    except Exception:
                        lit = True
                    if len(trace) < 60:
                        trace.append(f'{now - start:.2f}:{1 if lit else 0}')
                    dark = 0 if lit else dark + 1
                    if dark >= 2 and dark_at is None:
                        dark_at = now
                if dark_at is not None:
                    if now - dark_at >= self.LU_HEAVY_HOLD_AFTER:
                        break
                elif now - press_at >= self.LU_HEAVY_REGRIP_AT:
                    if regrips >= self.LU_HEAVY_REGRIPS:
                        break
                    self.task.mouse_up()
                    self.macro_wait(0.05)
                    self.task.mouse_down()
                    press_at = time.time()
                    regrips += 1
                self.macro_wait(0.02)
        finally:
            self.release_attack()
        caught = dark_at is not None
        self.macro_mark(f'{tag}({"接住" if caught else "没接住"},重按x{regrips},{time.time() - start:.2f}s)')
        if not caught or regrips:
            self.logger.info(f'{tag}: {"caught" if caught else "never caught"} after {regrips} regrips — '
                             f'{" ".join(trace)}')
        return caught

    def lit_now(self, read, frames):
        """HUD 在场时连续 frames 帧读到 read() 为真。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.task.in_team()[0]:
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

    def lu_ensure_r(self, tag):
        """收尾的 R 一定要放出来: 按不出来就攒能量再按, 直到出手或撞防卡死上限。返回放出去没有。

        每轮先读 R 亮不亮:
          - 暗(能量不够) -> 回路重击指示亮就打出去、声骸按一下(召唤类给大招能量)、连点普攻一小段;
            【暗的时候不按 R】: 按 R 加等"晚到"要 2s 不打怪, 能量永远攒不上来(干跑抓到的)。
            只每 LU_R_BLIND_EVERY 轮盲按一次, 防 R 的读数不准;
          - 亮(键被动作吞了) -> 不攒, 直接按。
        """
        # 【先读 R 再决定按不按】: R 没好时直接去攒能量, 不先干按 R —— 按 R 和等"晚到"那几秒是一下 A 都不点的
        if self.lib_ready_reads(self, 2) and self.fire_lucy_r(self.LU_R_WINDOW_FINAL, tag):
            return True
        start = time.time()
        rounds = 0
        while time.time() - start < self.LU_R_ENSURE_CAP:
            rounds += 1
            lit = self.lib_ready_reads(self, 2)
            self.macro_mark(f'{tag}R补救#{rounds}(R{"亮" if lit else "暗"})')
            if not lit:
                self.lu_ensure_heavy(f'{tag}R补救')
                self.send_echo_key()
                if self.LU_F_BEFORE_R:
                    self.spam_a_f(self.LU_R_BUILD_SLICE, f'{tag}R补救A+F')
                else:
                    self.macro_spam(self.LU_R_BUILD_SLICE, tag=f'{tag}R补救A')
                lit = self.lib_ready_reads(self, 2)
                if not lit:
                    if rounds % self.LU_R_BLIND_EVERY == 0:
                        # 防 R 的读数不准: 短按一下试试, 不等晚到, 按完接着攒
                        if self.fire_r(self, f'{tag}R补救#{rounds}盲按', self.LU_R_BLIND_WINDOW):
                            return True
                    continue
            if self.fire_lucy_r(self.LU_R_RETRY_WINDOW, f'{tag}R补救#{rounds}'):
                self.logger.info(f'{tag}: final R fired after {rounds} rescue rounds '
                                 f'({time.time() - start:.1f}s)')
                return True
        self.logger.warning(f'{tag}: final R still not fired after {self.LU_R_ENSURE_CAP:.0f}s '
                            f'of building energy ({rounds} rounds), switching anyway')
        return False

    def fire_lucy_r(self, window, tag):
        """露西发 R; 窗口里判不到演出时, 再看 LU_R_LATE_GRACE 秒内 HUD 会不会连续消失(R 起得晚)。

        判漏了的代价很大: 面板开着没人点, 宏直接去切人, 切不动超时, 整圈散掉。
        """
        if self.fire_r(self, f'{tag}R', window):
            return True
        if self.hud_gone_within(self.LU_R_LATE_GRACE, f'{tag}R晚到'):
            self.record_liberation_use()
            return True
        return False

    def box_gold_ratio(self, name):
        """技能框里金色像素的占比。拿不到返回 -1。

        按 HSV 判不按 RGB: 金色图标的亮度随背景变化很大, RGB 阈值只在背景偏亮的帧上过线。
        """
        try:
            import cv2
            box = self.task.get_box_by_name(name)
            crop = self.task.frame[box.y:box.y + box.height, box.x:box.x + box.width]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
            gold = (h >= 12) & (h <= 38) & (s > 40) & (v > 90)
            return float(gold.mean())
        except Exception:
            return -1.0

    # ---------------- 守岸人: 照搬日月守 ----------------

    def shore_open(self, sk, tag):
        """守岸人·启动段(协奏是空的): 3A -> E -> 3A -> 重击 -> 闪避 -> A 到回路满 -> 重击 -> Q。

        两次长按都锚在按下 E 的那一刻: 第一发重击是"指示一暗就松手", 长度每轮不一样,
        用相对间隔往下串误差会累加。
        """
        self.poking = False
        self.macro_spam(self.SHORE_A3_WINDOW, self.A_INTERVAL, tag=f'{tag}A1[3seg]')
        e_at = time.time()
        self.press_e(sk, tag=f'{tag}E')
        e_at = self.maybe_anchor_on_e_cd(e_at, tag=f'{tag}E落CD')
        self.spam_until(e_at + self.SHORE_E_TO_HEAVY, tag=f'{tag}A2[3seg]')
        self.hold_heavy(self.SHORE_HEAVY_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}重击1')
        self.dodge(tag=f'{tag}闪避')
        self.macro_gap(self.SHORE_DODGE_TO_A)
        # 打到回路满为止: 这几下 A 就是在攒强化重击要吃的那格回路, 攒多久每轮不一样
        self.wait_forte_full(tag=f'{tag}等回路满')
        self.hold_heavy(self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}重击2')
        self.macro_gap(self.SHORE_HEAVY_TO_QR)
        self.fire_echo(sk, tag=f'{tag}Q')

    def shore_loop(self, sk, tag):
        """守岸人·循环段(变奏入场): 边打边判, 然后 Q。

        一直 A、每 SHORE_E_RESEND 补按一次 E(CD 上是空操作); 期间:
          - 重击指示(核心满)连续两帧亮 -> 马上 强化重击 -> 闪避 -> 接着打(不等固定窗口);
          - 协奏连续两次读到 >= SHORE_LOOP_CON_QR -> 直接收手去 Q R(R 自己再给 20 协奏);
          - 打过重击 + 闪避后 AA 够 SHORE_A2_WINDOW、且整段满 SHORE_LOOP_MIN -> 正常收尾。
        QTE 第86F前不响应输入, 锁里的输入被丢掉不花钱, 所以开头照样连发盖过去。
        """
        self.poking = False
        if not sk.has_intro:
            self.logger.warning('守岸人没拿到变奏入场(上一棒协奏没满)')
            self.macro_mark(f'{tag}无变奏')
        start = time.time()
        next_a = next_e = next_con = 0.0
        na = ne = heavies = lit = con_hits = 0
        dodge_at = None
        why = 'CAP'
        con = 0.0
        while time.time() - start < self.SHORE_LOOP_CAP:
            now = time.time()
            if now >= next_con:
                next_con = now + self.SHORE_CON_POLL_EVERY
                try:
                    con = float(self.task.get_current_con())
                except Exception:
                    con = 0.0
                con_hits = con_hits + 1 if con >= self.SHORE_LOOP_CON_QR else 0
                if con_hits >= 2:
                    why = f'协奏{con:.2f}'
                    break
            if dodge_at is not None and now - dodge_at >= self.SHORE_A2_WINDOW and now - start >= self.SHORE_LOOP_MIN:
                why = '重击闪避AA'
                break
            self.task.next_frame()
            try:
                forte = bool(self.task.in_team()[0] and self.is_mouse_forte_full())
            except Exception:
                forte = False
            lit = lit + 1 if forte else 0
            if lit >= self.SHORE_FORTE_CONFIRM:
                lit = 0
                heavies += 1
                self.hold_heavy(self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}重击#{heavies}')
                self.dodge(tag=f'{tag}闪避#{heavies}')
                self.macro_gap(self.SHORE_DODGE_TO_A)
                dodge_at = time.time()
                continue
            if now >= next_e:
                sk.send_resonance_key()
                ne += 1
                next_e = now + self.SHORE_E_RESEND
            elif now >= next_a:
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}打到({why},A x{na},E x{ne},重击x{heavies},{time.time() - start:.2f}s)')
        if why == 'CAP':
            self.logger.info(f'{tag}: shorekeeper loop hit the cap (con {con:.2f}, heavies {heavies})')
        self.macro_gap(self.SHORE_A2_TO_Q)
        self.fire_echo(sk, tag=f'{tag}Q')

    def shore_ready_for_lib(self, sk, tag):
        """启动段 R 之前: 协奏不到 SHORE_LIB_MIN_CON 就补一发强化重击(第二发多半被打断了)。只补一次。"""
        con = self.read_con(self.SHORE_LIB_MIN_CON, settle=self.SHORE_LIB_CON_SETTLE, click=True)
        if con >= self.SHORE_LIB_MIN_CON:
            self.macro_mark(f'{tag}R保底(con{con:.2f},够)')
            return True
        self.logger.info(f'{tag}R保底: 放 R 之前协奏只有 {con:.2f} (< {self.SHORE_LIB_MIN_CON}), 补一发强化重击')
        self.macro_mark(f'{tag}R保底(con{con:.2f},补)')
        self.wait_forte_full(cap=self.SHORE_LIB_RESCUE_CAP, tag=f'{tag}补等回路')
        self.hold_heavy(self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}补重击')
        con2 = self.read_con(self.SHORE_LIB_MIN_CON, settle=self.SHORE_LIB_CON_SETTLE, click=True)
        self.macro_mark(f'{tag}R保底后(con{con2:.2f})')
        if con2 < self.SHORE_LIB_MIN_CON:
            self.logger.warning(f'{tag}R保底: 补完协奏还是只有 {con2:.2f}, 照样放 R '
                                f'(丽贝卡这一趟可能没变奏入场)')
        return con2 >= self.SHORE_LIB_MIN_CON

    def shore_ensure_con(self, sk, tag, cap=None):
        """协奏没满就别交棒 —— 用能给协奏的技能补: E(20) > 强化重击(6) > 普攻(1~4)。

        协奏读数没标定过满环前中间值恒为 0, 但满环那一刻读得出来, 所以退出条件照样可靠。
        """
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
                forte = bool(self.is_mouse_forte_full())
            except Exception:
                forte = False
            if forte:
                self.hold_heavy(self.SHORE_HEAVY2_HOLD, self.SHORE_HEAVY_CAP, tag=f'{tag}补协奏重击')
                continue
            self.macro_spam(self.SHORE_CON_RESCUE_SLICE, self.A_INTERVAL, tag=f'{tag}补协奏A')
        self.macro_mark(f'{tag}补协奏(cap,{rounds}轮,{time.time() - start:.2f}s)')
        self.logger.warning(f'{tag}: 补了 {time.time() - start:.1f}s 协奏还是没满, 认赔交棒(打不到怪?)')
        return False

    def shore_lib_to_rebecca(self, sk, rb, opener, tag):
        """守岸人收尾: [启动段保底] -> R -> 等演出 -> 打满协奏 -> 变奏丽贝卡。返回切过去没有。

        大招全局时停 + 隐藏 HUD 179F、第178F前不能切人, 必须等 HUD 回来才交棒。
        """
        if opener:
            self.shore_ready_for_lib(sk, tag)
        if self.fire_r(sk, f'{tag}R', self.SHORE_LIB_WINDOW):
            self.poke_until_hud_back(tag=f'{tag}R演出')
        full = self.wait_con_for_intro(sk, 'Rebecca', time_out=self.SHORE_CON_CAP)
        if not full:
            full = self.shore_ensure_con(sk, tag)
        self.warn_no_con(full, '守', '丽')
        return self.quick_switch(rb, assume_con=full, click_during=True)

    # ---- 守岸人那一段用到的原语(同样出自日月守) ----

    def spam_until(self, deadline, tag='A', interval=None):
        """连点到某个绝对时刻(墙钟)为止。已经过点就立刻返回并记一笔 late —— 看到 late 去查前一格。"""
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

    def spam_a_e(self, window, char, tag='A+E'):
        """一个窗口里 A 不停、E 定期补发, 全程不读屏(读一帧就是一顿)。"""
        start = time.time()
        na = ne = 0
        next_a = next_e = 0.0
        self.macro_mark(f'{tag}_start')
        while time.time() - start < window:
            now = time.time()
            if now >= next_a:
                self.click()
                na += 1
                next_a = now + self.A_INTERVAL
            if now >= next_e:
                char.send_resonance_key()
                ne += 1
                next_e = now + self.SHORE_E_RESEND
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}_end(A x{na},E x{ne},{time.time() - start:.2f}s)')
        return na

    def hold_heavy(self, hold, cap=None, min_hold=None, tag='重击'):
        """长按左键打重击, 回路条旁的鼠标指示【见过亮之后一暗】就松手。

        退出路径: _up(指示暗) 重击放出去了 / _up(一直暗) 没亮过, 按满 hold 兜底 /
        _up(cap) 一直亮着没暗, 这次长按没被识别。
        """
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
                try:
                    lit = bool(self.is_mouse_forte_full())
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
        held = time.time() - start
        if why == '指示暗':
            self.macro_mark(f'{tag}_up({why},{held:.2f}s)')
            return True
        self.task.next_frame()
        try:
            still = bool(self.is_mouse_forte_full())
        except Exception:
            still = None
        self.macro_mark(f'{tag}_up({why},{held:.2f}s,'
                        f'松手后回路{"还满着" if still else "已清空" if still is False else "?"})')
        return why != 'cap'

    def wait_forte_full(self, cap=None, click=True, tag='等回路满'):
        """边打边等鼠标指示亮(回路满), 连着 SHORE_FORTE_CONFIRM 帧才算。超时也返回, 宁可出普通重击。"""
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
            try:
                full = bool(self.is_mouse_forte_full())
            except Exception:
                full = False
            hits = hits + 1 if full else 0
            if hits >= self.SHORE_FORTE_CONFIRM:
                self.macro_mark(f'{tag}(满,A x{n},{time.time() - start:.2f}s)')
                return True
        self.macro_mark(f'{tag}(cap,A x{n},{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}: 等了 {cap:.1f}s 回路条也没满, 照样按重击')
        return False

    def read_cd(self, name='resonance', char=None):
        """读技能框里的 CD 数字(秒)。不在 CD / 读不到都返回 0。"""
        try:
            index = None if char is None else char.index
            return float(self.task.get_cd(name, index))
        except Exception:
            return 0.0

    def maybe_anchor_on_e_cd(self, fallback, tag='E落CD'):
        """ANCHOR_E_ON_CD 开着才去量 E 落 CD 的时刻, 否则原样返回按键时刻。"""
        if not self.ANCHOR_E_ON_CD:
            return fallback
        start = time.time()
        hits = 0
        seen_cd = 0.0
        next_click = 0.0
        while time.time() - start < self.E_CD_DETECT_CAP:
            now = time.time()
            if now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.task.next_frame()
            cd = self.read_cd('resonance')
            if cd > 0:
                hits += 1
                seen_cd = max(seen_cd, cd)
                if hits >= 2:
                    fired_at = time.time() - (hits - 1) * 0.04
                    self.macro_mark(f'{tag}(CD{seen_cd:.1f}s,+{fired_at - fallback:.2f}s)')
                    return fired_at
            else:
                hits = 0
        self.macro_mark(f'{tag}(读不到,退回按键时刻)')
        return fallback

    def read_con(self, want, settle=None, click=False, tag=None):
        """读协奏: 自己刷帧、给结算留时间, 连着几帧达到 want 就立刻返回。返回期间读到的最大值。

        不走 char.get_current_con(): 那层读到过 1 就一直缓存成 1。
        """
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

    def wait_con_for_intro(self, char, next_name, poke=True, time_out=None):
        """边打边等协奏满(连着几帧读到)再交棒。等不到就往上游找哪笔伤害打空了。"""
        time_out = self.SHORE_CON_CAP if time_out is None else time_out
        start = time.time()
        samples = []
        full = False
        full_hits = 0
        next_click = 0.0
        while time.time() - start < time_out:
            self.task.next_frame()
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
            now = time.time()
            if poke and now >= next_click:
                self.click()
                next_click = now + self.A_INTERVAL
            self.macro_wait(self.SHORE_CON_POLL)
        self.macro_mark(f'con({"full" if full else "timeout"},{time.time() - start:.2f}s)')
        self.logger.info(f'{char.name} con {" ".join(samples)} '
                         f'{"(full)" if full else f"(never filled, {next_name} gets no intro)"}')
        return full

    def seg_rb_e(self, rb, lu, tag):
        """丽贝卡(变奏入场): 连按霰弹E 到 E 进 CD -> 马上切露西。

        读到 CD 的时候枪早已切回手枪(40F), 可以直接切人。【读到 CD 立刻停手】: 霰弹E 派生 90F
        之后再按会连出手枪E(它 46F 又切回霰弹, 后面长按就不是手枪重击了)。
        两个判据任一成立就算进 CD:
          - 见过亮之后变暗(刚到场几帧技能栏是守岸人的, 所以要先见过亮);
          - E 框里 OCR 到 CD 数字(连续两帧)。入场时 E 图标可能是灰的、锁一解开第一发就出去,
            那样读不到"亮", 只有这一条能判到。
        每次都把技能栏读数、CD 数字和 E 图标变化量打进日志, 判据不灵时拿它标定。
        """
        self.poking = False
        start = time.time()
        n = 0
        seen_ready = False
        on_cd = False
        cd_hits = 0
        next_press = 0.0
        ref = None
        trace = []
        while time.time() - start < self.RB_E_CAP:
            now = time.time()
            if now >= next_press:
                rb.send_resonance_key()
                n += 1
                next_press = now + self.RB_E_INTERVAL
            self.task.next_frame()
            elapsed = time.time() - start
            if elapsed >= self.RB_E_SETTLE and self.task.in_team()[0]:
                try:
                    ready = bool(rb.resonance_available())
                except Exception:
                    ready = False
                cd = self.read_cd('resonance')
                cd_hits = cd_hits + 1 if cd > 0 else 0
                gray = self.box_gray('box_resonance')
                if ref is None:
                    ref = gray
                diff = self.gray_diff(gray, ref)
                if len(trace) < 60:
                    trace.append(f'{elapsed:.2f}:{1 if ready else 0}/cd{cd:.1f}/{diff}')
                if ready:
                    seen_ready = True
                if (seen_ready and not ready) or cd_hits >= 2:
                    on_cd = True
                    break
            self.macro_wait(0.02)
        rb.record_resonance_use()
        self.macro_mark(f'{tag}霰弹E({"CD" if on_cd else "CAP"},x{n},{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}霰弹E {"on-cooldown" if on_cd else "never read as on-cooldown"} '
                         f'after {n} presses (seen ready={seen_ready}) — ready/icon-diff: {" ".join(trace)}')
        if not on_cd:
            self.macro_wait(self.RB_E_TO_SWITCH)
        return self.quick_switch(lu, click_during=False)

    def box_gray(self, name):
        """技能框的缩小灰度图, 只用来记日志(图标换没换)。拿不到返回 None。"""
        try:
            import cv2
            box = self.task.get_box_by_name(name)
            frame = self.task.frame
            crop = frame[box.y:box.y + box.height, box.x:box.x + box.width]
            return cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (24, 24)).astype('int16')
        except Exception:
            return None

    @staticmethod
    def gray_diff(a, b):
        if a is None or b is None:
            return '-'
        return int(abs(a - b).mean())

    def seg_lu_e(self, rb, stay, opener, tag):
        """露西: [E1 -> 等变金] -> 连按 E2 到放出去 -> 站满 stay 秒 -> 按住普攻 -> 切丽贝卡。

        启动轴: 开局已经放过 E1, E2 早就解锁, 直接按 E2。
        循环轴: 上一圈 E2 用掉了, 进场时一律先按 E1 —— 不看进场那一刻 E 框是不是金色(会被特效骗)。
        按住跨过切人, 丽贝卡一上场就是重击循环。
        """
        self.poking = False
        arrive = time.time()
        self.macro_wait(self.LU_E_SETTLE)
        if opener:
            gold = self.gold_now(2)
        else:
            gold = self.lu_e1_until_gold(self.LU_E2_GOLD_CAP, f'{tag}E1')
        self.lu_press_while_gold(self.LU_E2_PRESS_CAP, f'{tag}E2', primed=gold)
        self.record_resonance_use()
        left = stay - (time.time() - arrive)
        if left > 0:
            self.macro_wait(left)
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}按住')
        return self.quick_switch(rb, click_during=False)

    def seg_rb_hold(self, rb, lu, dodge, release_to_switch, tag):
        """丽贝卡(左键已按着): 等强化重击指示亮 -> [战术闪避] -> 松手 -> 等一下 -> 切露西。

        切人时读一次丽贝卡协奏(时间轴上 >Luc^ = 读到满): 满协奏离场会让露西带着变奏进场,
        QTE 期间不响应输入, 看着就是"进场没马上 A"。
        """
        self.poking = False
        lit = self.click_until_lit(rb.is_mouse_forte_full, self.RB_HOLD_CAP, f'{tag}等狂热满',
                                   settle=self.RB_HOLD_SETTLE, confirm=self.RB_HOLD_CONFIRM, poke=False)
        if dodge:
            self.dodge_keep_hold(tag)
            self.macro_wait(self.RB_DODGE_TO_RELEASE)
        self.release_attack()
        self.macro_mark(f'{tag}松开')
        # 狂热指示暗了 = 强化重击起手了, 马上切; 读不到(没见亮 / 一直不暗)就按原来的时长兜底
        if lit and self.wait_until_dark(rb.is_mouse_forte_full, release_to_switch, f'{tag}强化重击起手'):
            self.macro_wait(self.RB_DARK_TO_SWITCH)
        else:
            self.macro_wait(max(0.0, release_to_switch))
        return self.quick_switch(lu, click_during=True, read_con=True)

    def hud_gone_within(self, cap, tag):
        """cap 秒内队伍 HUD 连续消失 LIB_GONE_CONFIRM 帧就返回 True(不按任何键)。"""
        start = time.time()
        gone = 0
        while time.time() - start < cap:
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone += 1
                if gone >= self.LIB_GONE_CONFIRM:
                    self.macro_mark(f'{tag}(HUD消失,{time.time() - start:.2f}s)')
                    return True
            else:
                gone = 0
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(HUD一直在)')
        return False

    def seg_lu_4a(self, rb, tag):
        """露西: 连点到 A4 出手 -> 切丽贝卡(切人期间继续点, A4 落在露西身上)。"""
        self.poking = False
        self.macro_spam(self.LU_4A_WINDOW, tag=f'{tag}4A')
        return self.quick_switch(rb, click_during=True)

    def seg_rb_qr(self, rb, lu, tag):
        """丽贝卡: Q -> R -> 机枪里连点 -> 大烟花 -> 满协奏变奏露西。"""
        self.poking = False
        arrive = time.time()
        self.send_echo_now(rb, tag=f'{tag}Q', window=self.RB_Q_WINDOW)
        self.macro_wait(self.RB_Q_TO_R)
        left = self.RB_R_READY_SETTLE - (time.time() - arrive)
        if left > 0:
            self.macro_wait(left)
        # 入场协奏: 低于 75% 说明前面强化重击没打中, 这圈要等大烟花打中协奏满了再切
        # (本局没见过满环前协奏中间值读 0, 会被当成低 —— 那一圈多等一会儿, 方向是安全的)
        con_in = self.read_con(self.RB_CON_LOW, settle=self.RB_CON_READ, tag=f'{tag}入场协奏')
        wait_full = con_in < self.RB_CON_LOW
        self.rb_build_r(rb, tag)
        fired = self.fire_r(rb, f'{tag}R', self.RB_R_WINDOW)
        if not fired and self.hud_gone_within(self.RB_R_LATE_GRACE, f'{tag}R晚到'):
            # 按完才起演出: 还是按"放出去了"走机枪段, 不然切人会撞在机枪里超时
            fired = True
            rb.record_liberation_use()
        if fired:
            start = self.lib_press_at
            n = nr = 0
            next_click = 0.0
            next_r = time.time() + self.RB_MG_R_EVERY
            while time.time() - start < self.RB_MG_BEFORE_SWITCH:
                now = time.time()
                if now >= next_r:
                    rb.send_liberation_key()
                    nr += 1
                    next_r = now + self.RB_MG_R_EVERY
                elif now >= next_click:
                    self.click()
                    n += 1
                    next_click = now + self.RB_MG_CLICK_INTERVAL
                self.macro_wait(0.02)
            self.macro_mark(f'{tag}机枪(A x{n},R x{nr})')
            if wait_full:
                # 入场协奏低: 等机枪 + 大烟花放完(期间队伍 HUD 不在), 再等大烟花打中协奏真满才切
                self.poke_until_hud_back(tag=f'{tag}等R放完', time_out=self.RB_R_END_CAP, tail=0.0)
                self.wait_con_for_intro(rb, 'Lucy', poke=True, time_out=self.RB_CON_WAIT_CAP)
        return self.quick_switch(lu, click_during=True, read_con=True, time_out=self.RB_SWITCH_TIME_OUT,
                                 click_interval=self.RB_MG_CLICK_INTERVAL)

    def forte_white_percent(self):
        """框架 is_forte_full 读的那一块(回路条右端的 E 按键提示)的白色占比, >0.08 算满。拿不到返回 -1。"""
        try:
            from src.char.BaseChar import forte_white_color
            box = self.task.box_of_screen_scaled(3840, 2160, 2251, 1993, 2311, 2016, name='forte_full', hcenter=True)
            return float(self.task.calculate_color_percentage(forte_white_color, box))
        except Exception:
            return -1.0

    def lu_fill_forte(self, tag):
        """露西(变奏入场)一直点 A 到回路满。跟 click_until_lit 一样的判据, 多做两件排查用的事:
        - 每一帧的白色占比都记进日志(不管判没判到);
        - 到场 LU_FILL_SNAPSHOT_AT 秒还没判满, 存一张当时的画面到 logs/(看强化E 是不是其实已经好了)。
        (用户 2026-09-14: 启动轴强化E 明明好了还要等 1~2s 才按; 实机启动轴这一步 1.5~2.7s, 循环轴 0.44s)
        """
        start = time.time()
        n = streak = 0
        next_click = 0.0
        lit = False
        snapped = False
        trace = []
        while time.time() - start < self.LU_FILL_CAP:
            self.task.next_frame()
            now = time.time()
            if now - start >= self.LU_FILL_SETTLE and self.task.in_team()[0]:
                # 判断照旧走 is_forte_full(); 白色占比只记日志 —— 算占比那一路出错时不能把判断也带坏
                try:
                    full = bool(self.is_forte_full())
                except Exception:
                    full = False
                if len(trace) < 80:
                    trace.append(f'{now - start:.2f}:{self.forte_white_percent():.2f}{"*" if full else ""}')
                streak = streak + 1 if full else 0
                if streak >= self.LU_FILL_CONFIRM:
                    lit = True
                    break
            if not snapped and now - start >= self.LU_FILL_SNAPSHOT_AT:
                snapped = True
                self.save_panel_snapshot(f'{tag}等回路满')
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}A到回路满({"亮" if lit else "CAP"},A x{n},{time.time() - start:.2f}s)')
        self.logger.info(f'{tag}A到回路满 {"lit" if lit else "CAP"} after {time.time() - start:.2f}s — '
                         f'forte white%: {" ".join(trace)}')
        return lit

    def lu_press_e3(self, tag):
        """连按强化E 到出手: 队伍 HUD 连续消失 LIB_GONE_CONFIRM 帧, 或 E 框连续两帧出 CD 数字, 立刻停手。

        不按固定时长: 按的那一刻可能正落在 A3「第46F前禁用E」里, 固定 0.6s 会整段按空;
        出手之后还接着按又会把多余的 E 带进死锁(输入缓存), 实机 23:17 就是按满 15 下。
        返回 False = 按满上限都没反应(没出去)。
        """
        start = time.time()
        n = gone = cd_hits = 0
        why = 'CAP'
        next_press = 0.0
        trace = []
        while time.time() - start < self.LU_E3_PRESS_CAP:
            now = time.time()
            if now >= next_press:
                self.send_resonance_key()
                n += 1
                next_press = now + self.E_RESEND_STEP
            self.task.next_frame()
            if not self.task.in_team()[0]:
                gone += 1
                if len(trace) < 60:
                    trace.append(f'{time.time() - start:.2f}:x')
                if gone >= self.LIB_GONE_CONFIRM:
                    why = 'HUD消失'
                    break
            else:
                gone = 0
                cd = self.read_cd('resonance')
                cd_hits = cd_hits + 1 if cd > 0 else 0
                if len(trace) < 60:
                    trace.append(f'{time.time() - start:.2f}:cd{cd:.1f}')
                if cd_hits >= 2:
                    why = '出CD'
                    break
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}(x{n},{why},{time.time() - start:.2f}s)')
        self.logger.info(f'{tag} {why} after {n} presses — {" ".join(trace)}')
        return why != 'CAP'

    def lib_ready_reads(self, char, frames):
        """HUD 在场时连续 frames 帧读到 char 的 R 可用。读不到(HUD 不在)的帧不算, 最多读 frames*3 帧。"""
        hits = 0
        for _ in range(frames * 3):
            self.task.next_frame()
            if self.task.in_team()[0]:
                try:
                    ready = bool(char.liberation_available())
                except Exception:
                    ready = False
                if not ready:
                    return False
                hits += 1
                if hits >= frames:
                    return True
            self.macro_wait(0.02)
        return False

    def rb_build_r(self, rb, tag):
        """丽贝卡放 R 之前的能量保底: R 没转好就补强化重击 / 平 A, 转好了才返回。返回转好了没有。

        每轮先读 R: 转好就走; 没转好 -> 强化重击指示还亮(狂热满, 前面那发多半被打断了)就补一发,
        否则平 A 一小段。R 的读数本身读偏了也只是多 A 一轮, 不会卡死(有上限)。
        """
        start = time.time()
        rounds = heavies = 0
        while time.time() - start < self.RB_R_BUILD_CAP:
            if self.lib_ready_reads(rb, 2):
                if rounds:
                    self.macro_mark(f'{tag}R能量补好(第{rounds}轮,补重击x{heavies},{time.time() - start:.2f}s)')
                    self.logger.info(f'{tag}: rebecca R ready after {rounds} rounds ({heavies} strong heavies, '
                                     f'{time.time() - start:.1f}s)')
                return True
            rounds += 1
            if heavies < self.RB_R_HEAVY_MAX and self.lit_now(rb.is_mouse_forte_full, 2):
                heavies += 1
                self.rb_strong_heavy(rb, f'{tag}补强化重击#{heavies}')
            else:
                self.macro_spam(self.RB_R_BUILD_SLICE, tag=f'{tag}补能量A#{rounds}')
        self.macro_mark(f'{tag}R能量没补满(CAP,{rounds}轮)')
        self.logger.warning(f'{tag}: rebecca R still not ready after {self.RB_R_BUILD_CAP:.0f}s, pressing R anyway')
        return False

    def rb_strong_heavy(self, rb, tag):
        """丽贝卡补一发强化重击: 按住过手枪重击-2 发生帧再松手(狂热满 = 松手出强化重击), 然后停手等伤害打出来。"""
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_wait(self.RB_HEAVY_PRESS)
        self.release_attack()
        self.macro_mark(f'{tag}松手')
        self.macro_wait(self.RB_HEAVY_DMG)

    def lu_enhanced_e(self, tag):
        """露西(变奏入场): A 到回路满 -> 强化E -> 等死锁演完。

        回路条满 -> 按 E 一小段 -> 等回路条变空(死锁第1F清空) -> 再等死锁剩下那截。
        【按到出去为止】: 等不到清空 = 按的时候被打断了, 回路还满着, 重按, 最多 LU_E3_TRIES 次。
        (彩色光环图标不是每次都有 —— 视频启动轴那次按之前就没有 —— 所以不拿图标判。)
        回路条一直读不到满就照样按一次 E, 然后按固定时长等。
        """
        lit = self.lu_fill_forte(tag)
        if not lit:
            e_at = time.time()
            self.spam_key(self.send_resonance_key, self.LU_E3_PRESS, interval=self.E_RESEND_STEP,
                          tag=f'{tag}强化E(没见满)', poke=False)
            self.record_resonance_use()
            left = self.LU_E3_BLIND - (time.time() - e_at)
            if left > 0:
                self.macro_wait(left)
            return False
        for attempt in range(1, self.LU_E3_TRIES + 1):
            out = self.lu_press_e3(f'{tag}强化E#{attempt}')
            self.record_resonance_use()
            if not out and self.lit_now(self.is_forte_full, 2):
                # 按满上限 HUD 都没消失、回路还满着 = 没出去, 马上再按, 不先干等回路清空
                self.logger.info(f'{tag}: enhanced E press #{attempt} got no reaction, pressing again')
                continue
            if self.wait_until_dark(self.is_forte_full, self.LU_E3_CLEAR_CAP, f'{tag}回路清空#{attempt}'):
                self.macro_wait(self.LU_E3_TAIL)
                return True
            self.logger.info(f'{tag}: transfer bar still full after enhanced E press #{attempt} '
                             f'(interrupted?), pressing again')
        self.logger.warning(f'{tag}: enhanced E never went out after {self.LU_E3_TRIES} tries, carrying on')
        return False

    def seg_lu_finale(self, sk, tag):
        """露西(变奏入场) 收尾: 强化E -> 3A -> Q -> 回路重击 -> R 面板 -> 处决 -> 变奏守岸人。"""
        self.poking = False
        self.lu_enhanced_e(tag)
        # 3A 期间重击指示提前亮了就不点完, 直接 Q 接回路重击
        self.click_until_lit(self.is_mouse_forte_full, self.LU_3A_WINDOW, f'{tag}3A')
        self.spam_key(self.send_echo_key, self.LU_Q_WINDOW, interval=self.LU_Q_INTERVAL, tag=f'{tag}Q',
                      poke=True)
        self.record_echo_use()
        if self.click_until_lit(self.is_mouse_forte_full, self.LU_HEAVY_FILL_CAP, f'{tag}A到重击亮'):
            self.lu_ensure_heavy(tag)
        else:
            # 指示一直没读到亮: 照旧按一发(可能是判据读不到), 不做重试
            self.task.mouse_down()
            self.holding_attack = True
            self.macro_mark(f'{tag}回路重击按住(没见亮)')
            self.macro_wait(self.LU_HEAVY_HOLD)
            self.release_attack()
        if self.LU_F_BEFORE_R:
            # 重击整串打完之前只点 A; 打完才开始按 F(处决会打断回路重击)
            self.macro_spam(self.LU_HEAVY_NO_F, interval=self.LU_HEAVY_MASH_INTERVAL, tag=f'{tag}等重击打完A')
            self.spam_a_f(max(0.0, self.LU_HEAVY_A_ONLY - self.LU_HEAVY_NO_F), f'{tag}等伤害A+F')
        else:
            self.macro_spam(self.LU_HEAVY_A_ONLY, interval=self.LU_HEAVY_MASH_INTERVAL, tag=f'{tag}等伤害A')
        # 等伤害那段里被打断的话根协议还是满的(或者又被 A 攒满了): 只要指示亮就再打出去
        self.lu_ensure_heavy(f'{tag}补')
        if self.LU_F_BEFORE_R:
            self.lu_a_f_until_r(tag)
        self.lu_ensure_heavy(f'{tag}R前补')
        if self.lu_ensure_r(tag):
            self.lu_ult_panel(tag)
        if self.LU_F_BREAK:
            self.spam_f_break(self.LU_F_WINDOW, f'{tag}处决')
        return self.switch_to_sk(sk, tag)

    # ==================== 一圈 / 启动轴 / 循环轴 ====================

    def run_cycle(self, rb, sk, opener, tag):
        """守岸人(HUD 在场) -> ... -> 露西收尾 -> 守岸人变奏入场、HUD 回来。"""
        marks = len(self.macro_marks)
        if opener:
            self.shore_open(sk, tag=f'{tag}守')
        else:
            self.shore_loop(sk, tag=f'{tag}守')
        self.need(self.shore_lib_to_rebecca(sk, rb, opener, tag=f'{tag}守'), 'shorekeeper -> rebecca')
        self.need(self.seg_rb_e(rb, self, tag=f'{tag}丽'), 'rebecca E -> lucy')
        stay = self.O_LU_E2_STAY if opener else self.L_LU_E12_STAY
        self.need(self.seg_lu_e(rb, stay, opener, tag=f'{tag}露'), 'lucy E -> rebecca')
        release_to_switch = self.O_RB_RELEASE_TO_SWITCH if opener else self.L_RB_RELEASE_TO_SWITCH
        self.need(self.seg_rb_hold(rb, self, opener and self.O_RB_TACTICAL_DODGE, release_to_switch,
                                   tag=f'{tag}丽'),
                  'rebecca heavy -> lucy')
        self.need(self.seg_lu_4a(rb, tag=f'{tag}露'), 'lucy 4A -> rebecca')
        self.need(self.seg_rb_qr(rb, self, tag=f'{tag}丽'), 'rebecca QR -> lucy')
        self.need(self.seg_lu_finale(sk, tag=f'{tag}露'), 'lucy -> shorekeeper')
        self.logger.info(f'{tag} cycle timeline: {self.macro_timeline(marks)}')

    def macro_enter(self, entry_char):
        rb = self.teammate('Rebecca')
        sk = self.teammate('ShoreKeeper')
        if rb is None or sk is None:
            return None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        # 处决由宏在固定位置按, 框架切人时那一发一律关掉
        for char in (self, rb, sk):
            char.check_f_on_switch = False
        self.macro_start()
        return rb, sk, previous

    def perform_opener(self, entry_char=None):
        """启动轴: 露西 E1 R -> 切守岸人 -> 一圈。返回时守岸人刚变奏入场、HUD 在场。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        rb, sk, previous = got
        self.logger.info(f'lrs opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            if self.cur is not self:
                self.need(self.quick_switch(self), 'opener: switch to lucy')
            self.seg_lu_open(tag='op露')
            self.need(self.quick_switch(sk, click_during=True, time_out=self.LU_TO_SK_TIME_OUT),
                      'opener: lucy -> shorekeeper')
            self.run_cycle(rb, sk, opener=True, tag='op')
            self.logger.info(
                f'lrs opener total {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'lrs opener broke ({e}) — timeline: {self.macro_timeline()}')
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴一圈。场上不是守岸人(宏散过)就先把场子交给她。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        rb, sk, previous = got
        try:
            if self.cur is not sk:
                self.need(self.switch_to_sk(sk, tag='L接守'), 'loop: hand the field to shorekeeper')
            self.loop_count += 1
            n = self.loop_count
            self.run_cycle(rb, sk, opener=False, tag=f'L{n}')
            self.logger.info(
                f'lrs loop #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        except MacroBroken as e:
            self.logger.warning(f'lrs loop broke ({e}) — timeline: {self.macro_timeline()}')
        finally:
            self.macro_exit(previous)
        return True

    def perform_tail(self, entry_char=None):
        """宏散掉后露西带着变奏上场: 只打收尾那一段(强化E ... 变奏守岸人), 然后回到正常循环。"""
        got = self.macro_enter(entry_char)
        if got is None:
            return False
        rb, sk, previous = got
        try:
            self.need(self.seg_lu_finale(sk, tag='T露'), 'tail: lucy -> shorekeeper')
            self.logger.info(f'lrs tail timeline: {self.macro_timeline()}')
        except MacroBroken as e:
            self.logger.warning(f'lrs tail broke ({e}) — timeline: {self.macro_timeline()}')
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def drive(self, entry):
        """三个人的 do_perform 都走这里。entry = 框架这次调的是谁(= 场上是谁)。"""
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            return self.perform_opener(entry)
        if entry is self and getattr(self, 'has_intro', False):
            self.logger.info('lrs: lucy came in with an intro, running the tail first')
            return self.perform_tail(entry)
        self.logger.info(f'lrs: opener not pending ({blocked}), running the loop')
        return self.perform_loop(entry)

    def do_perform(self):
        if not self.is_in_lrs:
            return super().do_perform()
        self.drive(self)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """启动轴待跑时必须露西先上; 其余时候交给守岸人(她是 HIGH), 这里走原版。"""
        if self.is_in_lrs and self.opener_pending():
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
