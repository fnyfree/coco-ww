import ctypes
import threading
import time

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


class Changli(MacroKit, NativeChangli):
    """长离。龙凤维(今汐/长离/维里奈)里她兼任【整条轴的持宏者】。

    ================= 轴的出处 =================
    B 站「龙凤维轻松5000, 附合轴的启动与循环宏, 01今汐00长离20维里奈」(Sy1is),
    本地 E:/轴视频/。1920x1080 / 30fps / 347.7s。
    0~175s 是【一倍速实录, 能当墙钟】; 216~280s 逐页展示【启动宏】(7 页),
    281~347s 展示【循环宏】(7 页)。作者练度 今汐 0命1武 / 长离 0命0武 / 维里奈 2命0武, 实录 5500。

    ================= 一大轮的形状 =================
        启动宏(22.3s) -> 手动A(长离 11.4s) -> 手动B(今汐 12.6s) -> 循环宏(19.6s)
                      -> 手动A -> 手动B -> 循环宏 -> ...
    【两条宏都在同一个位置结束】: 今汐龙喷完变奏长离入场、长离已经点了 5 下 A ——
    所以手动段只写一份, 启动宏跑完和每条循环宏跑完接的是同一段。
    手动段视频里没有宏, 按字幕做(z = 重击):
        A: "长离入场后一直按平a打出4层离火" -> "接zRz变奏今汐入场"
        B: "今汐变奏入场后一直打, 打出一喷就行" -> "喷完切长离出来按循环宏就行了"
    "有理解的朋友可以中途切个长离打个e再切回去" 是【可选的】, 默认没做, 开关 B_MID_CL_E。
    实录里对表: 长离大招(手动A 的 R)落在 30.8s / 119s, 正好差两大轮 44s。

    ================= 两条宏怎么读出来的 =================
    宏面板三列(按键 / 动作 / 延时ms)。动作那一列【矮的图标 = 按下, 高的 = 抬起】, 所以
    【按下那行的延时 = 按住多久, 抬起那行的延时 = 松开到下一次按下的间隔】; 末行 9999 = 结束。
    启动宏 37 对键、循环宏 39 对; 每页 12 行, 每页都正好是 6 对完整的按下/抬起。
    【唯一读不死的一处】: 循环宏第 24/25 两下 A(今汐普通E 之后)骑在翻页边界上 ——
    第 4 页末尾和第 5 页开头各有一对 左键(78/250)。同一份截图流在第 5/6 页边界上也重复了
    一对 左键(100/350), 而那三下 A 对照启动宏能确定是【三下不是两下】, 说明这套截图【页间不重叠】,
    所以这里按【两下】写。真要是多读了一下, 后果只是今汐多攒一点回路, 不塌。

    ================= 宏里的 1/2/3 =================
    是【作者的队伍槽位】(从实录右上角头像读的: 1 长离 / 2 今汐 / 3 维里奈)。
    这里【按角色翻译回槽位】(char.index + 1), 用户自己的队伍顺序不一样也能跑。

    ================= 这一版是"照宏打", 判断只有手动段有 =================
    两条宏【逐条按时间回放, 中间一次读屏都没有】—— 跟人按宏一模一样, 包括
    "切人键被游戏拒绝就错过去了" 这个风险。保底(切人确认、技能出手确认、协奏判据)是下一步要加的。
    手动段用的是读屏判据: 离火满看 is_mouse_forte_full, 龙喷看 HUD 消失, 交接等协奏真满。

    ================= 改这个文件之前先读的几条 =================
    - 引擎那一半(macro_* / quick_switch / fire_lib / press_key_until_cd ...)是从
      Brant__Changli__Lupa/Changli.py 逐字抽出来的, 那边注释里的角色和实机数据【是别的轴的标定记录】。
    - 【自定义 __init__ 里跟内置版取值不同的字段会被打回内置值】(CharFactory 换类时
      replacement.__dict__.update(char.__dict__)), 要保住就在 reset_state() 里重申。
    - 宏返回的那一刻长离刚变奏入场、HUD 在场 —— 【别把返回点挪进任何演出里】, 演出期间
      读不到目标, 框架会判脱战、重进战斗、重跑启动轴。
    """

    # ==================== 全局节奏 ====================
    A_INTERVAL = 0.12            # 0.12 是实测的地板, 更密会让空中角色打出下落攻击
    KEY_INTERVAL = 0.10
    # 【普攻必须真的按住几十毫秒才打得出来】: task.click() 那条路按不住 ——
    # BaseWWTask.click 会把不带坐标的调用的 down_time 【无条件覆盖成 0.01】, 底层
    # genshin.do_click 又是"按下-立刻抬起-再睡 down_time"。所以这个文件里所有的"点一下"
    # 都走 tap(), 那是 mouse_down -> 等 -> mouse_up。
    # 实机 2026-09-18 第一次跑栽在这: 启动宏 22 下 A 全成了瞬点, 整条宏比宏表【快 1.34 秒】,
    # 今汐那三下 AAA 的间隔从 450ms 缩到 350ms —— 比凌霄 A1 的派生帧(25F = 0.42s)还短,
    # 后两下被吞, 回路攒不够, 第31格的龙喷按不出来(用户: "今汐AAA龙喷最后没喷出来卡住了")
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

    # ==================== 启动轴 ====================
    # 长离 E -> 0.1 秒切维里奈。长离 E 是地转空(E-1 第12F离地), 切慢了维里奈会出现在半空。
    # 从第一发 E 起算
    O_CL_E_WINDOW = 0.10
    O_CL_E_INTERVAL = 0.05
    O_CL_E_TO_SWITCH = 0.10

    # ==================== 维里奈 ====================
    # 【照搬赞菲维那边实机调好的维里奈】(用户 2026-09-11 要求): 进场直接 E -> Q -> R -> 二段跳
    # -> 空中 A -> 协奏满切人。不打开场 AAA —— 那 1 秒普攻只是把 E 往后推。
    # 数值出自 Phoebe__Verina__Zani/Zani.py 的 V_* 那组, 那边的处决窗口这里没有(处决手动)
    VR_E_CAP = 1.50              # E 按到进 CD 的上限。入场会吞前几发; 单层充能, "按到灰"可靠
    VR_E_TO_Q = 0.05
    VR_Q_WINDOW = 0.20
    VR_Q_TO_R = 0.10
    VR_R_WINDOW = 1.20
    VR_JUMP_GAP = 0.30           # 两下跳之间(二段跳, 把人抬到能连打空中 A 的高度)
    VR_JUMP_SETTLE = 0.16        # 第二跳到第一下空中 A
    VR_AIR_A = 0.95              # 空中 A + 落地 A
    # 协奏真满才切的上限。满了立刻走, 这只是兜底 —— 等不满往上游查哪一下打空了
    VR_CON_CAP = 6.00
    # 放完 E 之后读"R 转好没有"读几帧。有一帧亮就算转好 —— 误判成亮只多按一轮 R(按不出来会
    # 改走长离 R 那支), 误判成没亮会把维里奈的大招整个跳过
    VR_R_READS = 3

    # ==================== 今汐 ====================
    # ---- 入场: Q -> 强化E1 -> 马上切长离 ----
    # Q(角)按到声骸进 CD 的上限。帧表 QTE 第60F前不响应输入, 所以变奏入场时要盖过 1.0s;
    # 已经在 CD 上的话第一次读屏就判到, 立刻跳过。
    # 【入场这个 Q 不能省】: 实机 2026-09-11 没放它, 惊龙破空演完协奏只有 0.47(按帧表把她自己那一轮的
    # 协奏加起来正好 ~47), 长离没吃到变奏, 后面离火也补不满 —— 角召出来的持续伤害是补协奏的那一截
    JX_INTRO_Q_CAP = 1.60
    # 切进来之后多久都没见过 Q 亮, 就当它本来在 CD 上。技能栏在切人后要过几帧才换成今汐的
    # (见 press_echo_until_cd); 变奏入场有 QTE 锁, 在这里多等不花时间
    JX_Q_READY_GRACE = 0.50
    # Q 放出去之后只按 E、不按切人的时长。【必须盖过角的出手动作】: 视频 Q 到 E 隔 ~0.25s,
    # 旧值 0.12 时切人会抢在 E 前面; 强化E1 丢了就进不了凌霄, 后面整条链都错
    JX_E_AFTER_Q = 0.30
    # Q 在 CD 上(跳过)时的同一个量, 不开大的变奏入场还要再受 JX_INTRO_SWITCH_AT 约束
    JX_E_ONLY = 0.12
    # 【从到场起算】最早什么时候开始边按 E 边按切人。帧表 QTE 第78F前不能切人(1.3s), 到场判定本身比
    # 切人晚 ~0.05-0.1s, 所以 1.05 开始按不会比游戏放人更早 —— 游戏一放人就走(旧版固定按满 1.45s)。
    # 没带变奏入场(没有 QTE 锁)时不受这一条限制
    JX_INTRO_SWITCH_AT = 1.05
    JX_E_INTERVAL = 0.06
    # 变奏入场直接开大: 连按 R 盖过 QTE 的 1.0s 不响应输入
    JX_R_WINDOW = 2.50
    # 凌霄 AA -> 普通E: A1 派生 25F(0.42s), 第二下在 0.42~0.5 出手, 之后交给 E 去打断
    JX_AA = 0.55
    JX_NE_CAP = 1.20             # 普通E 按到进 CD 的上限
    # 强化E2(龙喷): 从到场起边 A 边按 E, 按到 HUD 消失的上限。回路没攒够时 E 位置还是普通E 的 CD,
    # 按了是空操作, 点出去的 A 就是在攒回路(视频里切回来点两下 A 龙喷的图标才亮)
    JX_E2_CAP = 3.00
    JX_Q_BEFORE_E2 = True        # 龙喷前放一下 Q(角), 在 CD 上是空操作(入场那次放过的话这里一般在 CD)
    JX_Q_WINDOW = 0.10
    # 龙喷演完到变奏长离: 等协奏真满的上限。E4 第174F前不能切人、隐藏 HUD 180F,
    # HUD 回来就已经能切了。等的时候不点 —— 这时她还在半空, 点击会打出下落
    JX_CON_CAP = 1.20
    E2_ANIM_TIME_OUT = 5.0

    # ==================== 长离 ====================
    # 非变奏切进来: 进场自动一下心眼·冲(变凤凰飞一下, 派生 33F = 0.55s) -> E -> 马上切今汐。
    # 先只按 E 这么久, 之后边按 E 边按切人 —— 今汐刚切走, 她的换人 CD(~1s)到之前游戏不放人,
    # E 就在这段里被接受; CD 一到就走。实机旧版固定按满 0.95s, 长离在场 1.01s, 已经贴着 CD,
    # 所以这一格快不了多少, 改的是"E 一出去就在按切人", 不再干等窗口。
    # 长离 E 两层充能, 再施放锁定 ~1.05s(E-下落 63F), 切人前不会连出第二个
    CL_E_ONLY = 0.45
    # 心眼·征: 进场先连点这么久再按 Q。【不能只点一两下】: 实机 2026-09-11 两发点在进场 0.12s 内,
    # 全被切入吞了, Q 抢在前面出去, 征没打出来。连点里第一下被接受的就是征, 后面几下落在征的动作里
    # 被丢(征-4 派生 46F = 0.77s 前不接下一段), 不会多出一段。反正今汐的换人 CD(~1s)没到也切不走
    CL_ZHENG_A = 0.45
    # 长离的 Q(火套摩托)。新摩托按一下就能切人: 按到声骸进 CD 为止(已经在 CD 上就是空操作)。
    # 【旧摩托要砸三下】: CL_OLD_MOTO = True 时改成在 CL_OLD_MOTO_WINDOW 里铺开按三次
    CL_ECHO_CAP = 1.00
    CL_OLD_MOTO = False
    CL_OLD_MOTO_PRESSES = 3
    CL_OLD_MOTO_WINDOW = 1.80
    # 变奏入场: 一直 A 到重击指示亮, 亮了马上长按。这是上限 —— 视频里冲 + 下落 + 落地就要 ~2s,
    # 差一格离火时还要 A3 A4 进心眼再打一下征
    CL_FILL_CAP = 5.00
    # 到场后这么久之内的指示读数不算: 技能栏/回路条换成长离要过几帧, 那时读到的是今汐的
    CL_FILL_SETTLE = 0.40
    # 指示连续亮这么多帧才长按(防特效闪一下)
    CL_FILL_CONFIRM = 2
    # 焚身以火放出去(指示暗)之后读长离协奏满没满的时长。满了变奏今汐, 没满马上切维里奈。
    # 焚身以火的协奏(-1 第9F)要命中才涨, 所以读一小段; 读到满立刻走
    CL_CON_READ = 0.30
    # 维里奈 R 没转好那支: 长离 R。维里奈满协奏离场时长离会带变奏进来(QTE 0.77s 不响应输入), 窗口要盖过它
    CL_R_WINDOW = 2.00
    # R 后那发焚身以火清空之后到变奏今汐
    CL_R_HEAVY_TO_JX = 0.25
    CL_CON_CAP = 1.00

    # ==================== 这条轴新加的 ====================
    # 整条宏的时间缩放。1.0 = 跟宏表一模一样; 机器慢/延迟大的时候可以往上调一点点试
    MACRO_SPEED = 1.00

    # ---- 手动A: 长离 A 到 4 层离火 -> z -> R -> z -> 变奏今汐 ----
    # 宏结束时长离已经点了 5 下 A, 实录里还要再 A 5.2 秒才满 4 层; 这是上限, 亮了就走
    A_FILL_CAP = 8.00
    # 第二发焚身以火(大招后那下)清空之后, 等协奏真满的上限
    A_CON_CAP = 2.50

    # ---- 手动B: 今汐变奏入场 -> 一直打 -> 一喷 -> 变奏长离 ----
    # 变奏入场的 QTE 锁(帧表第60F ~1.0s)里按什么都不算数。等到 0.85 就开始按 E, 靠
    # B_E1_WINDOW 那几发里的后几发落在锁刚开的那一帧 —— 不是等满 1.0 再按, 那样要白等一发
    B_INTRO_LOCK = 0.85
    # 强化E1 = 入场后第一个 E, 【不进 CD、也没有可读信号】(图标差分不行: 入场特效扫过技能栏
    # 时的差分比真出手还大), 只能连发一小段。窗口整个落在它的派生帧(53F = 0.88s)之前,
    # 所以多发的那几下只会被丢掉, 不会连出普通E
    B_E1_WINDOW = 0.40
    # 强化E1 -> 普通E 之间的凌霄连段。【要盖过强化E1 的派生锁】: E1 出手后 53F(0.88s) 之内
    # 接不了别的动作, 这段里点出去的 A 全被丢掉 —— 连点到 1.45 才有几下真打出去, 而且正好
    # 是在攒龙喷要的回路(字幕那句"一直打"就是这一段)
    B_AA = 1.45
    # 龙喷演完等协奏真满的上限。【干等是等不来的】: 实机 2026-09-18 读数卡在 0.97 一动不动
    # (伤害早结算完了), 1.5 秒里 24 次读数全是 0.97 -> read_con 判不满 -> 长离【普通入场、
    # 没有变奏】, 站那儿愣到 LOOP_LEAD_IN 结束才动(用户: "切了长离愣住然后切了维里奈")。
    # 所以这一格【要边点普攻边等】, 差的那几个点靠 A 补上来
    B_CON_CAP = 2.50
    # "有理解的朋友可以中途切个长离打个e再切回去"(实录 42.8~44.1s, 长离在场 1.3 秒)。
    # 用户 2026-09-18 要求打开。这一趟顺带给今汐多攒一点回路 —— 她在场下也吃队友的协同
    B_MID_CL_E = True
    # 长离【非变奏】入场会自动先来一下心眼·冲(派生 33F = 0.55s), 连发 E 要盖过它
    B_MID_E_WINDOW = 0.45
    # 从第一发 E 起算多久开始按切回今汐的键。【按早了不亏】: 今汐刚走, 她的换人 CD(~1s)
    # 没到游戏不放人, quick_switch 会一直按到放人为止 —— 总共就是实录里那 1.3 秒
    B_MID_E_TO_SWITCH = 0.55
    # 【要等今汐协奏真满了再切】(用户 2026-09-18 指出我这里做错了): 满着切过去长离才是
    # 【变奏入场】, 这个 E 才有意义; 不满就是普通入场, 白跑一趟还搭上两次切人的风险。
    # 等的时候照样点普攻 —— A 本来就在攒回路和协奏, 一点不浪费; 等满这个上限还没满就跳过
    B_MID_CON_CAP = 3.00
    # 【今汐大招放在手动段B 里, 不在宏里】: 循环宏第 3 页那一格是"左键 78 / 左键 50", 不是 R
    # (放大对过原图)。但实录里她在循环轮照样开大 —— 扫画面找"引常无恒"那个【换成天空的场景】,
    # 命中 11.1s(启动宏第18格) / 85.8s / 156.0s, 后两次都落在手动段B。
    # 【能放才放】: 大招是能量门控不是 CD 门控, 能量不满就跳过(实录第一轮手动B 就没开成)
    B_JX_R = True
    B_JX_R_READS = 3             # 连读这么多帧确认大招图标亮着(单帧会被特效骗)
    # ---- 处决(F) ----
    # 用户 2026-09-18: "可以在今汐A的过程中加上处决 多点伤害"。处决伤害算在【当时在场的角色】
    # 头上, 今汐在场时按最划算。
    # 【不能到处按】: 处决动画把队伍 HUD 整个隐藏, 而这条轴里"HUD 消失"是龙喷/大招出手的判据
    # (press_until_hud_gone / jx_normal_e / watch_hud_gone) —— 在那些格子里按会被【误判成技能出手】。
    # 所以只挂在两个不读 HUD 的落点上: 凌霄 AA 打完、今汐大招演完
    F_BREAK = True
    F_BREAK_ANIM = 5.00          # 按完等 HUD 回来的上限(处决带全局时停, 时长因角色而异)
    # 这条轴的今汐几乎全程在场攒回路, 旧轴那个 3.0 秒的上限不够: 实录里普通E 到龙喷有 ~7 秒
    JX_E2_CAP = 8.00

    # ==================== 保底(宏里那三格) ====================
    # 总开关。关掉就是 2026-09-18 之前那版"照宏打、一次读屏都没有"
    SAFETY = True
    # 【强化E1 连发】: 宏表是"变奏切今汐之后 0.878s 按一下 E", 比今汐变奏入场的 QTE 锁
    # (帧表第60F = 1.0s)还早 —— 全指望游戏的预输入缓冲接住。改成在同样长的一格里连发,
    # 总有一发落在锁刚开的那一帧。【多发的不会连出普通E】: 强化E1 派生 53F(0.88s),
    # 这一格只有 0.444s, 后面几发全在派生帧之前, 被丢掉
    E_SPAM_INTERVAL = 0.06
    # 【龙喷补救】: 按表那一下没把 HUD 打消失(= 龙喷没出手)时, 边 A 边按 E 最多再等这么久。
    # A 就是在攒回路 —— 回路差一点的话这几秒正好补上。实机 2026-09-18 循环宏第33格
    # 一直没喷出来, 用户报"循环那里龙喷也没出来"
    E2_EXTRA_CAP = 4.00
    # 【大招补救】: 按表那一下没把 HUD 打消失(= 没出手)时, 再按这么久。
    # 维里奈那一格实机 2026-09-18 被吞过: 长离协奏满着切过去 -> 维里奈【变奏入场】带 QTE 锁,
    # 宏表里她的 AAA 和 E 全落在锁里(用户: "变奏入场攻击的时候EQR都没办法按, 最后只按了QR")。
    # 视频里作者那一格长离协奏没满、是非变奏入场, 所以 AAA 打得出来 —— 这条轴比视频多的那两段
    # 手动(长离在场下吃了十几秒协同)是协奏多出来的来源
    R_EXTRA_CAP = 2.00
    # 【切人确认】: 数字键发出去之后在【这一格自己的 gap 里】盯到场 —— 正常 0.1 秒就到,
    # 剩下的 gap 照等, 时序一点不变; 被游戏拒绝(上一个动作/入场动画没演完)就一直补按,
    # 最多再等 SWITCH_EXTRA(游戏的换人 CD 约 1 秒)。
    # 【这是整条轴最该有的一道保底】: 切人一格错, 后面每一格都发在错的人身上
    SWITCH_EXTRA = 1.50
    SWITCH_REPRESS = 0.05        # 没到场时补按数字键的间隔
    SWITCH_CONFIRM = 2           # 连续读到目标 index 这么多帧才算到场(特效会盖住槽位)
    # 【切人没确认就别假装切过去了】: 旧写法超时之后照样 handoff, 等于骗自己 ——
    # 后面每一格都发在错的人身上, 整轮宏废掉。改成读一次场上到底是谁, 把账对齐,
    # 累计这么多次就【放弃这一轮宏】交回框架 —— 乱着打完一整轮不如早点重来。
    # 实机 2026-09-18 19:10 被怪打断时连着两次 never confirmed(1.77s / 2.02s)
    MACRO_ABORT_DESYNC = 2
    # 【按到进 CD】那类格子的上限。给今汐的普通E 也够: 就算入场那个强化E1 丢了,
    # 第一发按出来的是强化E1(不进 CD), 派生 53F(0.88s)之后第二发才是普通E ——
    # 一格里【自动把断掉的链条接回来】
    E_CD_CAP = 1.60

    # 长离变奏入场(手动B 末尾)到循环宏第一个 E。【实录量出来是 1.3 秒】: 47.4s 今汐龙喷演完、
    # 48.1~48.6s 长离变奏技(烈阳三彩)的攻击动画、48.9s 才是长离 E(字幕同一刻换成"循环宏是长离e起手")。
    # 【旧值 0.90 是实机 2026-09-18 那串问题的根子】: E 按在变奏技动画里被吞, 紧接着 0.1 秒后的
    # 切维里奈【也在动画里被游戏拒绝】—— 长离还在场, 宏照走, 那三下 A 打在长离身上, 等游戏放人时
    # 宏已经到 Q/R 那一格了(用户: "切长离后没A出来, 切到维里奈A了 ... 最后只按了QR")
    LOOP_LEAD_IN = 1.30
    # 长离【没带变奏】进来时用这个: 普通入场没有烈阳三彩那段动画, 不用等, 站着白等 1.3 秒
    # 就是白丢 1.3 秒输出
    LOOP_LEAD_IN_NO_INTRO = 0.30

    # ==================== 两条宏(逐条从视频里的宏面板抄下来的) ====================
    # 每行 = (动作, 按住ms, 松开后等ms, 注释)。动作:
    #   'A'     左键(普攻)
    #   'E'/'R'/'Q'  共鸣技能 / 共鸣解放 / 声骸技能, 【发给当时在场的那个人】
    #   'E#'    按到这个技能进 CD 为止(维里奈的 E, 变奏入场会吞前几发)
    #   'R!'    大招:   按一下之后用这一格的时间盯 HUD, 没打消失就补按
    #   'E+'    强化E1: 在这一格里连发 E(盖过变奏入场的 QTE 锁)
    #   'E!'    龙喷:   按一下之后用这一格的时间盯 HUD, 没打消失就边A边按E 补救
    #   'SPACE' 跳(走框架配置的跳跃键)
    #   '>CL'/'>JX'/'>VR'  切人; 结尾带 '^' = 这一下是变奏入场(协奏满), 【只影响框架记账】
    # 末行的 9999 在表里写成 0: 那是宏自己的结束标志, 不是真要等 10 秒
    OPENER_MACRO = (
        ('E',     78,   25, '长离E'),
        ('>VR',   78,  400, '切维里奈'),
        ('A',     94,  200, '维A1'),
        ('A',     94,  200, '维A2'),
        ('A',     78,  400, '维A3'),
        ('E#',   100,  150, '维E(按到进CD, 变奏入场会吞前几发)'),
        ('Q',     78,   78, '维声骸'),
        ('R!',    94, 2300, '维大招(这 2.3s 用来盯 HUD, 没出手就补按)'),
        ('SPACE', 78,  100, '跳'),
        ('A',     78,  250, '空中A'),
        ('A',     78,  250, ''),
        ('A',     78,  350, ''),
        ('>JX^',  78,  800, '变奏今汐'),
        ('E+',    94,  350, '今汐 强化E1(连发盖 QTE 锁, 见 E_SPAM_INTERVAL)'),
        ('A',     78,  200, ''),
        ('A',     78,  250, ''),
        ('Q',     63,   78, '今汐声骸'),
        ('R!',    78, 3500, '今汐大招(演出 3.5s; 循环宏这一格是一下 A)'),
        ('>CL',   78,  150, '切长离'),
        ('A',     78,  700, '长离A'),
        ('E',     78,   50, '长离E'),
        ('>JX',   78,  250, '切今汐'),
        ('E#',   125,  650, '今汐 普通E(按到进CD; 入场那个强化E1 丢了的话这里自动补回来)'),
        ('>CL',   78,  150, '切长离'),
        ('A',     78,  750, '长离A'),
        ('E',     78,   50, '长离E'),
        ('>JX',   78,  350, '切今汐'),
        ('A',    100,  350, ''),
        ('A',    100,  350, ''),
        ('A',    100,  250, ''),
        ('E!',    78, 3000, '今汐 强化E2 龙喷(这 3 秒用来盯 HUD, 没出手就补救)'),
        ('>CL^',  78,  500, '变奏长离'),
        ('A',     78,  350, '长离A'),
        ('A',     94,  300, ''),
        ('A',     94,  500, ''),
        ('A',     94,  600, ''),
        ('A',     78,    0, '宏结束(表里 9999)'),
    )

    # 跟启动宏的差: 今汐不开大(第18格 R -> A), 普通E 之后多两下 A(第24/25格, 补的就是
    # 大招没给的那点回路), 再加三处 ±几十毫秒的微调。其余 34 格逐字相同
    LOOP_MACRO = (
        ('E',     78,   25, '长离E'),
        ('>VR',   78,  400, '切维里奈'),
        ('A',     94,  200, '维A1'),
        ('A',     94,  200, '维A2'),
        ('A',     78,  400, '维A3'),
        ('E#',   100,  150, '维E(按到进CD, 变奏入场会吞前几发)'),
        ('Q',     78,   78, '维声骸'),
        ('R!',    94, 2300, '维大招(这 2.3s 用来盯 HUD, 没出手就补按)'),
        ('SPACE', 78,  100, '跳'),
        ('A',     78,  250, '空中A'),
        ('A',     78,  250, ''),
        ('A',     78,  350, ''),
        ('>JX^',  78,  800, '变奏今汐'),
        ('E+',    94,  350, '今汐 强化E1(连发盖 QTE 锁, 见 E_SPAM_INTERVAL)'),
        ('A',     94,  250, ''),
        ('A',     62,  250, ''),
        ('Q',     63,  120, '今汐声骸'),
        ('A',     78,   50, '今汐A(启动宏这一格是大招)'),
        ('>CL',   78,  150, '切长离'),
        ('A',     78,  700, '长离A'),
        ('E',     78,   50, '长离E'),
        ('>JX',   78,  250, '切今汐'),
        ('E#',   125,  650, '今汐 普通E(按到进CD; 入场那个强化E1 丢了的话这里自动补回来)'),
        ('A',     78,  250, '循环宏独有(翻页边界上读出来的两下, 见类注释)'),
        ('A',     78,  250, ''),
        ('>CL',   78,  150, '切长离'),
        ('A',     78,  750, '长离A'),
        ('E',     78,   50, '长离E'),
        ('>JX',   78,  350, '切今汐'),
        ('A',    100,  350, ''),
        ('A',    100,  350, ''),
        ('A',    100,  250, ''),
        ('E!',    78, 3000, '今汐 强化E2 龙喷(这 3 秒用来盯 HUD, 没出手就补救)'),
        ('>CL^',  78,  500, '变奏长离'),
        ('A',     78,  350, '长离A'),
        ('A',     94,  300, ''),
        ('A',     94,  500, ''),
        ('A',     94,  600, ''),
        ('A',     78,    0, '宏结束(表里 9999)'),
    )


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
        self.macro_desync = 0

    def reset_state(self):
        super().reset_state()
        self.opener_armed = True
        start_f_watch(self.task, self.logger)

    # ==================== 队伍判定 ====================

    @property
    def is_in_longfeng(self) -> bool:
        """本队 = 今汐 + 长离 + 维里奈。【按类名判】, 两个角色都有两套立绘两个 char_name。"""
        team_members = 0
        for char in self.task.chars:
            if char and char.name in {'Jinhsi', 'Verina'}:
                team_members += 1
        return team_members == 2

    def opener_blocked_by(self):
        """启动轴不跑的原因; 返回 None 表示该跑。

        【这里不查任何"某个技能可用"】: 场下角色的可用性读的是上次在场时 OCR 到的 CD 残值,
        拿它当门会开场愣一下。防重入靠 opener_armed + OPENER_MIN_GAP。
        """
        if not self.is_in_longfeng:
            return 'team is not jinhsi/changli/verina'
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

    def click(self, *args, **kwargs):
        """本类里所有的 self.click() 都改成按住 A_TAP —— 引擎里那些"顺手点"也一样。

        BaseChar.click 强制 down_time=0.01, 在这条轴上等于没点。参数照收不用:
        这条轴的点击【一律点屏幕中心】, 没有带坐标的调用。
        """
        self.tap()

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

    def jx_normal_e(self, jx, cap, tag):
        """今汐凌霄里的普通E: 按到进 CD。返回 'ok' / 'e2'(按出来的是强化E2 龙喷) / 'none'。

        【普通E 之后别多按】: 回路攒够的话 E 位置马上换成强化E2, 多一发就把龙喷提前放了。
        所以一读到 E 进 CD 就停; 真撞上 HUD 消失说明已经放成龙喷, 交给调用方改走后半段。
        """
        start = time.time()
        ne = live = gone = 0
        while time.time() - start < cap:
            self.task.next_frame()
            hud = bool(self.task.in_team()[0])
            if not hud:
                if live > 0:
                    gone += 1
                    if gone >= self.LIB_GONE_CONFIRM:
                        self.macro_mark(f'{tag}_成了强化E2(E x{ne},{time.time() - start:.2f}s)')
                        return 'e2'
                self.macro_wait(0.02)
                continue
            gone = 0
            if live > 0 and not jx.resonance_available():
                self.macro_mark(f'{tag}_ok(E x{ne},{time.time() - start:.2f}s)')
                jx.record_resonance_use()
                return 'ok'
            jx.send_resonance_key()
            ne += 1
            live += 1
            self.macro_wait(self.E_RESEND_STEP)
        # 按满上限都没读到进 CD: 多半是强化E1 丢了, 这一格按出来的其实是强化E1(不进 CD)
        self.macro_mark(f'{tag}_CD?(E x{ne},{cap:.1f}s)')
        self.logger.info(f'{tag}: normal E never read as on-cooldown after {ne} presses — '
                         f'入场的强化E1 可能没放出来')
        return 'none'

    def cl_fill_until_ready(self, cap, tag):
        """长离: 一直 A 到回路条右边的重击指示亮(= 离火满)。返回亮了没有。

        【到场头 CL_FILL_SETTLE 秒的读数不算】: 回路条/指示换成长离的要过几帧, 那时读到的是今汐的。
        【连续亮 CL_FILL_CONFIRM 帧才算】: 特效扫过去会闪一下。
        进来时已经亮了(提前满)就不点, 直接返回, 调用方马上长按。
        """
        start = time.time()
        n = streak = 0
        next_click = 0.0
        lit = False
        while time.time() - start < cap:
            self.task.next_frame()
            now = time.time()
            if now - start >= self.CL_FILL_SETTLE and self.task.in_team()[0]:
                try:
                    full = bool(self.is_mouse_forte_full())
                except Exception:
                    full = False
                streak = streak + 1 if full else 0
                if streak >= self.CL_FILL_CONFIRM:
                    lit = True
                    break
            if now >= next_click:
                self.click()
                n += 1
                next_click = now + self.A_INTERVAL
            self.macro_wait(0.02)
        self.macro_mark(f'{tag}({"亮" if lit else "CAP"},A x{n},{time.time() - start:.2f}s)')
        if not lit:
            self.logger.info(f'{tag}: heavy-ready indicator never lit in {cap:.1f}s ({n} clicks)')
        return lit

    def hold_heavy(self, tag):
        """按住左键到重击就绪图标熄灭(焚身以火清空离火)。按住期间一下都不点。"""
        self.task.mouse_down()
        self.holding_attack = True
        self.macro_mark(f'{tag}按住')
        try:
            return self.hold_until_forte_cleared(tag)
        finally:
            self.release_attack()
            self.macro_mark(f'{tag}松开')

    def changli_lib_then_heavy(self, tag):
        """长离 R -> 预输入重击(一路按过演出) -> 读到重击就绪图标熄灭才松手。返回清掉没有。

        大招第157F离火+4, 所以演完手上就是满离火, 紧接着的重击就是焚身以火。
        【R 一出手就按住】: 大招第191F前不响应输入(演出 168F 就结束了), HUD 回来之后按
        不等于能出招; 按住的状态会在能出招的第一帧被读到。
        """
        fired = self.fire_r(self, f'{tag}R', self.CL_R_WINDOW)
        if not fired:
            self.logger.info('changli R did not start an animation, carrying on')
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

    # ==================== 各格 ====================

    def seg_jx_e2(self, jx, tag):
        """今汐: (Q) -> 边 A 边按 E 到强化E2(龙喷)出手 -> 等演完。返回按出来没有。调用方负责变奏切走。

        回路没攒够时 E 位置还是普通E 的 CD, 按了是空操作; 点出去的 A 就是在攒回路, 攒够那一刻
        E 位置换成龙喷, 下一发 E 就出去 —— 不用猜要点几下。
        """
        self.poking = False
        if self.JX_Q_BEFORE_E2:
            self.send_echo_now(jx, tag=f'{tag}Q', window=self.JX_Q_WINDOW)
        ok = self.press_until_hud_gone(jx, self.JX_E2_CAP, tag=f'{tag}强化E2')
        if ok:
            jx.record_resonance_use()
            self.wait_hud_back(tag=f'{tag}龙喷anim', time_out=self.E2_ANIM_TIME_OUT)
        return ok

    def macro_enter(self, entry_char):
        jx = self.teammate('Jinhsi')
        vr = self.teammate('Verina')
        if jx is None or vr is None:
            return None, None, None
        previous = getattr(self.task, 'skip_combat_check', False)
        self.task.skip_combat_check = True
        self.mark_busy(True)
        self.cur = entry_char or self.on_field_char() or self
        self.poking = False
        # 处决全程交给玩家手动按, 框架切人时那一发一律关掉(内置非治疗角色默认会按)
        self.check_f_on_switch = False
        jx.check_f_on_switch = False
        vr.check_f_on_switch = False
        self.macro_desync = 0
        self.macro_start()
        return jx, vr, previous


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
                hit = task.find_one('f_break',
                                    box=task.box_of_screen(0.2, 0.2, 0.75, 0.8,
                                                           hcenter=True, vcenter=True),
                                    target_height=720)
                ready = bool(hit) and not task.is_pick_f()
            except Exception as e:
                self.logger.info(f'{tag}: f_break probe failed {e}')
                return False
            if ready:
                task.can_break = True
        if not ready:
            return False
        start = time.time()
        task.f_break()
        task.can_break = False
        # f_break() 【提示一消失就返回, 不等动画演完】—— 后面那几格都要读 HUD, 得等它回来
        self.wait_hud_back(tag=f'{tag}处决anim', time_out=self.F_BREAK_ANIM)
        self.macro_mark(f'{tag}处决({time.time() - start:.2f}s)')
        return True

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
        if act == 'A':
            self.tap(hold)
            return False
        if act == 'SPACE':
            self.task.send_key(self.task.key_config.get('Jump Key'), down_time=hold)
            self.macro_mark('跳')
            return False
        char = self.cur if self.cur is not None else self
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
            self.press_res_until_cd(char, self.E_CD_CAP, tag=f'{char.name[:2]}E')
            char.record_resonance_use()
            # 【判据成立也要把这一格的时间走完】: 提前走等于把后面每一格都往前挪,
            # 宏的节奏就不是宏表那个节奏了(切人确认也是这么处理的)
            left = (hold + gap) - (time.time() - entry)
            if left > 0:
                self.macro_wait(left)
            return True
        if act == 'R!':
            # 大招: 先照表按一下, 这一格的时间(演出时长)用来盯 HUD; 没打消失就补按
            char.send_liberation_key(down_time=hold)
            char.record_liberation_use()
            self.macro_mark(f'{char.name[:2]}R')
            if not self.SAFETY:
                return False
            if self.watch_hud_gone(gap, tag=f'{char.name[:2]}R', poke=True):
                return True
            self.logger.info(f'{char.name}: 大招按了没出手(入场锁/上一个动作没演完) — 补救')
            if self.fire_r(char, f'{char.name[:2]}R补救', self.R_EXTRA_CAP):
                self.wait_hud_back(tag=f'{char.name[:2]}Ranim')
            return True
        if act == 'E!':
            # 龙喷: 先照表按一下, 这一格的时间用来盯 HUD; 没打消失就补救
            char.send_resonance_key(down_time=hold)
            char.record_resonance_use()
            self.macro_mark(f'{char.name[:2]}龙喷')
            if not self.SAFETY:
                return False
            if self.watch_hud_gone(gap, tag=f'{char.name[:2]}龙喷', poke=True):
                return True
            self.logger.info(f'{char.name}: 龙喷没出手(回路没攒够, E 位置还是普通E 的 CD) — 补救')
            if self.press_until_hud_gone(char, self.E2_EXTRA_CAP, tag=f'{char.name[:2]}龙喷补救'):
                char.record_resonance_use()
                self.wait_hud_back(tag=f'{char.name[:2]}龙喷anim', time_out=self.E2_ANIM_TIME_OUT)
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
        self.task.send_key(slot, down_time=hold)
        if not self.SAFETY:
            self.handoff(source, target, con_full)
            self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}')
            return False
        soft = entry + hold + gap
        hard = soft + self.SWITCH_EXTRA
        last = time.time()
        hits = 0
        arrived = False
        while time.time() < hard:
            self.task.next_frame()
            in_team, index, _count = self.task.in_team()
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
        if arrived:
            self.handoff(source, target, con_full)
            self.macro_mark(f'>{target.name[:3]}{"^" if con_full else ""}'
                            f'({cost:.2f}{f",晚{late:.2f}" if late > 0.05 else ""})')
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
        jx = self.teammate('Jinhsi')
        vr = self.teammate('Verina')
        if jx is None or vr is None:
            return False
        chars = {'CL': self, 'JX': jx, 'VR': vr}
        planned = sum(h + g for _, h, g, _ in table) / 1000.0 * self.MACRO_SPEED
        start = time.time()
        marks = len(self.macro_marks)
        for act, hold_ms, gap_ms, _note in table:
            hold = hold_ms / 1000.0 * self.MACRO_SPEED
            gap = gap_ms / 1000.0 * self.MACRO_SPEED
            if act.startswith('>'):
                consumed = self.macro_switch(chars.get(act[1:].rstrip('^')), hold,
                                             act.endswith('^'), gap)
            else:
                consumed = self.macro_key(act, hold, gap)
            if not consumed:
                self.macro_wait(gap)
            if self.macro_desync >= self.MACRO_ABORT_DESYNC:
                self.macro_mark(f'{tag}_放弃(失步x{self.macro_desync})')
                self.logger.warning(
                    f'{tag}: 切人累计 {self.macro_desync} 次没确认, 这一轮宏放弃 —— '
                    f'交回框架, 下一次从手动段重来 (被打断了?)')
                self.logger.info(f'{tag}(中断): {self.macro_timeline(marks)}')
                return False
        self.logger.info(f'{tag}: {len(table)} keys in {time.time() - start:.2f}s '
                         f'(table says {planned:.2f}s) — {self.macro_timeline(marks)}')
        return True

    # ==================== 手动段(宏没覆盖的两格) ====================

    def seg_changli_zrz(self, jx, tag):
        """手动A。长离(刚变奏入场, 宏里已经点过 5 下 A): 接着 A 到 4 层离火 -> 重击(焚身以火)
        -> 大招 -> 再一发重击 -> 协奏满变奏今汐。

        【离火条读不出来】, 只看回路条右边的重击就绪指示亮没亮(is_mouse_forte_full);
        焚身以火放没放也看它暗没暗。大招第157F离火+4, 所以演完手上又是满离火, 紧接着的重击
        就是第二发焚身以火 —— 这就是字幕里的 z R z。
        """
        self.poking = False
        self.cl_fill_until_ready(self.A_FILL_CAP, tag=f'{tag}攒离火')
        self.hold_heavy(f'{tag}z1')
        self.changli_lib_then_heavy(f'{tag}Rz2')
        self.pass_intro(self, jx, self.A_CON_CAP, tag=f'{tag}协奏', poke=True)

    def seg_jinhsi_spout(self, jx, tag):
        """手动B。今汐(刚变奏入场): 强化E1 -> AA -> 普通E -> 边A边按E 到龙喷 -> 变奏长离。

        返回【长离是不是带着变奏进来的】—— 调用方拿它决定要不要等烈阳三彩那段动画。

        字幕只说"一直打, 打出一喷就行"; 拆成三个 E 是因为【三个 E 的判据各不相同】:
          · 强化E1 放完图标换成普通E 的图标、【不进 CD】, 没有可读信号 -> 只能按固定一小段;
          · 普通E 放完进 10 秒 CD -> 按到 resonance_available() 变假为止;
          · 强化E2(龙喷)要攒够回路才解锁, 解锁前按 E 是空操作、点出去的 A 就是在攒
            -> 边 A 边按 E, 按到 HUD 消失(龙喷的全局时停)为止。
        中间那趟"切个长离打个e再切回去"【要等今汐协奏满了才切】—— 满着切走长离才是变奏入场。
        【今汐大招也在这一段】: 两条宏里都没有她的 R(循环宏那一格是左键), 但实录里她在循环轮
        照样开大, 都开在这一段、龙喷前面。能量不满就跳过。
        """
        self.poking = False
        self.macro_wait(self.B_INTRO_LOCK)
        self.spam_key(jx.send_resonance_key, self.B_E1_WINDOW, interval=self.JX_E_INTERVAL,
                      tag=f'{tag}强化E1', poke=False)
        jx.record_resonance_use()
        self.macro_spam(self.B_AA, tag=f'{tag}AA')
        # 处决落点①: 凌霄连段刚推完, 削韧最足的时候; 这一格纯连点, 不读 HUD, 按了不会误判
        self.try_f_break(f'{tag}AA后')
        got = self.jx_normal_e(jx, self.JX_NE_CAP, tag=f'{tag}普通E')
        if self.B_MID_CL_E and got != 'e2':
            # 【先等协奏满】: 满着切走长离才带变奏进来(见 B_MID_CON_CAP)。等的这几秒
            # 一直在点 A, 就算最后没满也是在攒回路, 白等不掉东西
            if self.wait_con_at_least(jx, self.CON_FULL_LEVEL, tag=f'{tag}中途协奏',
                                      cap=self.B_MID_CON_CAP, poke=True):
                # assume_con: 上一行刚读到满, 不用再读一次(读一次要几十毫秒到近一秒)
                self.quick_switch(self, assume_con=True)
                self.press_e_then_switch(self, jx, self.B_MID_E_WINDOW,
                                         self.B_MID_E_TO_SWITCH, tag=f'{tag}中途长离E')
            else:
                self.macro_mark(f'{tag}中途长离E(协奏没满,跳过)')
        # 今汐大招。【排在龙喷前面】: 跟启动宏里 R -> ... -> 龙喷 的顺序一致, 而且大招自己
        # 也给协奏 —— 龙喷完要拿协奏变奏长离, 这一发正好垫上
        if self.B_JX_R and got != 'e2' and self.lib_ready_now(jx, self.B_JX_R_READS):
            if self.fire_r(jx, f'{tag}今汐R', self.JX_R_WINDOW):
                self.wait_hud_back(tag=f'{tag}今汐Ranim', time_out=self.E2_ANIM_TIME_OUT)
                # 处决落点②: 大招削韧多, 演完常常正好亮; 这里离下一个 HUD 判据还有一段
                self.try_f_break(f'{tag}R后')
        if got == 'e2':
            # 普通E 那一格直接按出了龙喷(回路早就攒够了): 后半段不用再按, 等演完就走
            self.wait_hud_back(tag=f'{tag}龙喷anim', time_out=self.E2_ANIM_TIME_OUT)
        else:
            self.seg_jx_e2(jx, tag=tag)
        # poke=True: 协奏差最后几个点的时候干等没用, 点普攻才补得上来(见 B_CON_CAP)
        self.pass_intro(jx, self, self.B_CON_CAP, tag=f'{tag}协奏', poke=True)
        return bool(self.has_intro)

    # ==================== 两条轴 ====================

    def perform_opener(self, entry_char=None):
        """启动轴 = 启动宏一张表跑完。返回时长离刚变奏入场, 下一次 do_perform 从手动A 接上。"""
        jx, vr, previous = self.macro_enter(entry_char)
        if jx is None:
            return False
        self.logger.info(f'longfengwei opener entry: {self.cur.name}')
        try:
            self.loop_count = 0
            if self.cur is not self:
                self.quick_switch(self)
            self.run_macro(self.OPENER_MACRO, '启动宏')
        finally:
            self.macro_exit(previous)
        return True

    def perform_loop(self, entry_char=None):
        """循环轴的一步 = 手动A -> 手动B -> 循环宏。返回时长离刚变奏入场(跟启动轴同一个位置)。"""
        jx, vr, previous = self.macro_enter(entry_char)
        if jx is None:
            return False
        try:
            if self.cur is not self:
                self.quick_switch(self)
            self.loop_count += 1
            n = self.loop_count
            self.seg_changli_zrz(jx, tag=f'L{n}A')
            intro = self.seg_jinhsi_spout(jx, tag=f'L{n}B')
            # 【带变奏进来的话要等烈阳三彩演完】(实录 1.3 秒), 抢在里面按的 E 会被吞、
            # 紧接着的切人也会被游戏拒绝; 没带变奏就没这段动画, 不用白等
            lead = self.LOOP_LEAD_IN if intro else self.LOOP_LEAD_IN_NO_INTRO
            self.macro_mark(f'L{n}长离{"变奏" if intro else "普通"}入场(等{lead:.2f})')
            # 【手动段也要有时间轴】: 不打的话这两格出问题时日志里只有几条 warning,
            # 看不出是哪一步慢的、协奏停在多少
            self.logger.info(f'L{n} 手动段: {self.macro_timeline()}')
            self.macro_wait(lead)
            self.run_macro(self.LOOP_MACRO, f'循环宏#{n}')
            self.logger.info(
                f'loop step #{n} {self.time_elapsed_accounting_for_freeze(self.macro_t0):.2f}s '
                f'(wall {time.time() - self.macro_t0:.2f}s)')
        finally:
            self.macro_exit(previous)
        return True

    # ==================== 框架接口 ====================

    def do_perform(self):
        """本队里长离驱动两条轴。两条都在长离变奏入场时返回, 下一次框架调的还是她。"""
        if not self.is_in_longfeng:
            return super().do_perform()
        blocked = self.opener_blocked_by()
        if blocked is None:
            # 标记要在跑之前落: 中途抛异常(脱战/任务被停)也不能让启动轴重跑
            self.opener_armed = False
            self.last_opener = time.time()
            if self.perform_opener(self):
                return
            self.logger.warning('longfengwei opener aborted, falling back to the native rotation')
            return super().do_perform()
        self.logger.info(f'longfengwei: opener not pending ({blocked}), running the loop')
        if self.perform_loop(self):
            return
        return super().do_perform()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        """宏散掉之后, 框架选人时长离优先 —— 她一上场轴就接上了。

        启动轴待跑时必须是她先上(宏的第一个动作是她的 E); 其余时候给 HIGH。
        """
        if self.is_in_longfeng:
            if self.opener_pending():
                return SwitchPriority.MUST
            return SwitchPriority.HIGH
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def on_combat_end(self, chars):
        self.release_attack()
        self.poking = False
        return super().on_combat_end(chars)
