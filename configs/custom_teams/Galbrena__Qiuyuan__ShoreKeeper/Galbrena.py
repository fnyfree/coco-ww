import time

from src.char.Galbrena import Galbrena as NativeGalbrena
from src.char.ShoreKeeper import ShoreKeeper as NativeShoreKeeper
from src.combat.MacroKit import MacroKit


class Galbrena(MacroKit, NativeGalbrena):
    """嘉贝莉娜 —— 嘉仇守的主C, 也是启动轮的开场。

    轮换环: 嘉贝莉娜 -> 守岸人 -> 仇远 -> 嘉贝莉娜。

        启动轮开场 (空手入场)   Q + 长按 3 段常态重击 -> 直接切守岸人 (不带变奏)
        爆发段 (变奏入场)       长按 2 段重击 -> 点按 2 段普攻 -> Q -> 强化E -> R
                                -> 一直点普攻打恶魔连段(两次闪避断后摇)
                                -> 长按 3 段常态重击 -> 变奏守岸人

    【为什么是这个顺序 —— 从官方帧表(角色-女 r763~r885)的资源规则推出来的】:
      * 罪火只由【常态攻击命中】获取, 满 100 才能放 E3-恶翼扬升, 而 E3 给的是
        50 秒恶魔位格 + 等量净炼火。所以入场那两段重击 + 两段普攻是拿来喂罪火的。
      * 余火(每点给恶魔A/恶魔重击 +1.5% 伤害)只在【不处于恶魔位格】时才监听全队
        声骸技能施放, 每次 +8 —— 所以 Q 必须放在 E3 【之前】; 开场那一发 Q 也是
        为了多吃一次监听(她主声骸冷却 20 秒, 一圈够放两次)。
      * 大招第 1F 给 14 秒强化状态, 恶魔位格里的恶魔A/恶魔重击在这 14 秒内有倍率
        提升 —— 爆发段的长度就是照这 14 秒定的。
      * 恶魔位格在净炼火被打完时结束, 之后打出来的就是常态重击, 收尾那三段
        长按重击就落在这里。

    轴出自 E:\\轴视频\\【轮椅】嘉贝莉娜，仇远，守岸人.mp4 的「启动轮手法」/「循环轮手法」
    两段(视频 83~175 秒 / 232~263 秒)。
    视频 175~232 秒那套「进阶爆发」(A闪A 取消普攻第2/3段) 也实现了 —— 用户给的
    Sy1is 宏表, 见 BURST_MACRO_* 和 play_burst_macro()。
    **但 2026-09-18 用户决定回到原版, 所以 USE_BURST_MACRO 默认 False**,
    现在跑的是轮椅爆发; 要切回宏就连 LIB_TO_BURST 一起改(0.10 -> 0.30), 那两个数绑在一起。
    """
    # ================================================================ 固定顺序切人 (公共段)
    # 这一段在 Galbrena.py / Qiuyuan.py / ShoreKeeper.py 三个文件里逐字一样, 改一处记得同步改三处。
    # (Qiuyuan__ShoreKeeper__Xigelika / Chisa__Suisui__YangYangSp 那两队的拷贝跟这份只差这行注释。)
    # 下面各自的常量写在这一段【之后】, 所以子类想覆盖哪个直接在下面重写就行。
    #
    # 为什么不用框架的 switch_next_char():
    #   1. 它按 get_switch_priority() 现场挑人, 顺序会漂 —— 这套轴要求死顺序;
    #   2. 它内部有三处补普攻 ("performing too fast add a normal attack"、维持锁定、
    #      切换未检测到时补点击), 打完最后一下想立刻走人时那几下 A 会把节奏拖乱。
    # 代价是框架的交接记账要自己补, 全在 handoff_to() 里。

    SWITCH_TIMEOUT: float = 2.0        # 单程切人的最长尝试时间 (秒)
    SWITCH_KEY_INTERVAL: float = 0.12  # 连按数字键的间隔 (秒)
    # 切到之后等角色站稳的时间 (秒)。很敏感 —— 实测 0.50 / 0.52 会让下一个角色的起手被吞,
    # 0.55 才稳, 别再往下压
    SWITCH_SETTLE: float = 0.55
    SETTLE_ATTACK_INTERVAL: float = 0.42
    HANDOFF_CON_WAIT: float = 0.8      # 交接时等协奏读数稳定的上限 (秒)
    HANDOFF_CON_POLL: float = 0.1
    HOLD_POLL: float = 0.1             # 长按期间轮询条件的间隔 (秒)
    # 切走前按 F 处决 —— 框架 switch_next_char() 里有 current_char.f_break(True), 直接
    # 按数字键切人会绕开它。这套轴里默认【关掉】: handoff_to() 是在 switch_to_index()
    # 之后调的, 那时候下一个角色已经站上场了, 这一下 F 会被他吃掉, 而且发生在他的 R
    # 之前 (千咲"还没按 R 就开处决"就是这么来的)。处决统一由千咲在 R 之后接
    F_BREAK_ON_HANDOFF: bool = False

    # 别人用 handoff_to() 直接切给我时打的标记, 写成类属性省掉 __init__。
    # 不能只靠 has_intro —— reset_state() 每次重读队伍都会把它清成 False, 而战斗判定
    # 一抖动就会重进战斗、重读队伍, 于是角色以为自己空手入场, 跑去走开场那套流程
    intro_pending = False
    # 打上这个标记的时刻, 用来给它定保质期。
    # 【为什么需要保质期】(2026-09-04 用户报"开关一下自动战斗流程不能重置"):
    #   手动停任务时战斗循环是被任务停止打断的, 既不是 NotInCombatException 也不是
    #   CharDeadException, 所以 AutoCombatTask 里那句 combat_end() 整个跳过 ——
    #   on_combat_end() -> reset_fixed_rotation() 没跑, 标记留在角色身上;
    #   而 reset_state() 又【故意】不清它(战斗判定一抖动就会重读队伍, 清了会误判成
    #   空手入场)。两条一叠, 关掉再打开时角色还以为自己带着变奏, 直接跳过开场轴。
    # 正常交接 1~2 秒内就被 take_intro() 消费掉, 而关掉再打开怎么也不止 TTL 这么久,
    # 所以用时间就能把两种情况分开
    intro_pending_at = 0.0
    INTRO_PENDING_TTL: float = 8.0
    # 切人【真正完成】那一刻的时间戳 —— in_team 认出换人成功的那一帧, 不是
    # settle_with_attack 站稳之后。循环轴的落地E 就按这个锚点计时, 差 0.55 秒
    # 就会掉进"E 按在下落段完全没反应"的失败区
    switch_confirmed_at = 0.0

    def note_intro_handoff(self):
        """被别人用直接切人的方式交接进场时调用 —— 记下"我是带着变奏上来的"。"""
        self.intro_pending = True
        self.intro_pending_at = time.time()

    def take_intro(self):
        """本次入场是否带着变奏。读完就清, 避免下一轮误判。

        过期的标记不认 —— 见 intro_pending_at 的注释(停任务不会走 combat_end)。
        """
        pending = self.intro_pending
        if pending:
            age = time.time() - self.intro_pending_at
            if age > self.INTRO_PENDING_TTL:
                self.logger.info(f'intro_pending 已过期 ({age:.1f}s > '
                                 f'{self.INTRO_PENDING_TTL}s), 按空手入场处理')
                pending = False
        intro = self.has_intro or pending
        self.intro_pending = False
        self.intro_pending_at = 0.0
        return intro

    def reset_fixed_rotation(self, chars=None):
        """战斗结束时调 —— 在各自的 on_combat_end() 里, 把 chars 传进来。

        【要清全队, 不能只清自己】: combat_end() 只对【当前角色】调 on_combat_end(),
        另外两个人身上的 intro_pending 不传 chars 的话就留着了。
        """
        for char in (chars if chars is not None else [self]):
            if char is None:
                continue
            char.intro_pending = False
            char.intro_pending_at = 0.0

    def direct_switch(self, slot=None, assume_intro=False):
        """按数字键直接切到指定队伍位置 (1-based), 并自己补上框架的交接记账。

        Args:
            slot: 不传就用 NEXT_SLOT。
            assume_intro: True 时不读协奏, 直接认定是满的。刚放完大招要用这个 ——
                能量环读数滞后会把 has_intro 误判成 False, 下一个角色就拿不到变奏增益。
        """
        slot = self.NEXT_SLOT if slot is None else slot
        if slot is None:
            return False
        target = self.char_at_slot(slot)
        if target is None or target is self:
            return False

        has_intro = True if assume_intro else self.wait_con_full()
        if not self.switch_to_index(target.index):
            self.logger.warning(f'direct switch to slot {slot} failed')
            return False
        self.logger.info(f'direct switched to slot {slot} ({target.char_name}, intro={has_intro})')
        self.handoff_to(target, has_intro)
        return True

    def switch_to_index(self, index):
        """按数字键切到指定队伍位置, 直到 in_team 确认切过去了。

        点击落在谁身上就由谁打出来, 所以等待期间也保持点普攻, 两边连段都不断档。
        """
        start = time.time()
        while time.time() - start < self.SWITCH_TIMEOUT:
            in_team, current_index, _ = self.task.in_team()
            if in_team and current_index == index:
                # 【先记时间戳再站稳】: settle_with_attack 要花 SWITCH_SETTLE 秒,
                # 记在它后面的话锚点整体晚 0.55 秒, 靠锚点计时的动作全跟着晚
                self.switch_confirmed_at = time.time()
                self.settle_with_attack(self.SWITCH_SETTLE)
                return True
            self.task.send_key(index + 1)
            self.click()
            self.sleep(self.SWITCH_KEY_INTERVAL, check_combat=False)
        return False

    def settle_with_attack(self, duration):
        """等 duration 秒, 期间保持点普攻, 不干等。"""
        end = time.time() + duration
        while time.time() < end:
            self.click()
            self.sleep(min(self.SETTLE_ATTACK_INTERVAL, max(0.0, end - time.time())),
                       check_combat=False)

    def handoff_to(self, target, has_intro):
        """把场上角色交接给 target —— 复刻 switch_next_char() 内部那套记账。

        直接按数字键切人绕开了框架的交接逻辑, 必须自己补上, 否则:
          - 没人的 is_current_char 是 True → get_current_char() 返回 None → 下一帧崩溃;
          - has_intro / last_outro_time 不更新, 下一个角色拿不到变奏。
        """
        if self.F_BREAK_ON_HANDOFF:
            self.f_break(check_f_on_switch=True)
        self.task.in_liberation = False
        self.switch_out(con_full=has_intro)
        target.is_current_char = True
        target.has_intro = has_intro
        if has_intro and hasattr(target, 'note_intro_handoff'):
            # has_intro 是易失的, 再给对方补一个它自己保管的持久标记
            target.note_intro_handoff()
        # 用 in_team 确认那一刻, 不是现在 —— 中间隔着 settle_with_attack 那 0.55 秒
        target.last_switch_in_time = self.switch_confirmed_at or time.time()
        self.switch_confirmed_at = 0.0
        if has_intro:
            now = time.time()
            self.task.add_freeze_duration(now, target.intro_motion_freeze_duration, -100)
            self.last_outro_time = now

    def wait_con_full(self):
        """短暂轮询协奏是否满 —— 用来决定交接时给不给对方变奏。

        不能只读一次: 打完最后一下那一刻协奏其实已经满了, 但能量环读数滞后。
        必须在【按切人键之前】读: 切走之后能量环显示的已经是新角色的了。
        """
        start = time.time()
        while time.time() - start < self.HANDOFF_CON_WAIT:
            if self.is_con_full():
                return True
            self.sleep(self.HANDOFF_CON_POLL, check_combat=False)
        self.logger.info(f'con not full before switch (con={self.get_current_con()}), '
                         f'next char gets no intro')
        return False

    def char_at_slot(self, slot):
        """按队伍位置 (1-based) 取角色, 越界返回 None。"""
        index = slot - 1
        for char in self.task.chars:
            if char is not None and char.index == index:
                return char
        return None

    def attack_chain(self, count, interval, last_interval=None):
        """点按普攻打一轮连段。

        Args:
            last_interval: 最后一段之后的间隔。后面紧接切人时传更小的值 —— 按下切人键之后
                还要等游戏识别切换完成, 那段时间最后一下的动作照样在演, 两者是重叠的。
        """
        for i in range(count):
            self.click()
            if i == count - 1 and last_interval is not None:
                self.sleep(last_interval)
            else:
                self.sleep(interval)

    def hold_until(self, cond, timeout, name):
        """轮询某个条件, 期间不动手 —— 攻击键的按下/松开由调用方管。"""
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < timeout:
            if cond():
                self.logger.info(f'{name} ready after '
                                 f'{self.time_elapsed_accounting_for_freeze(start):.1f}s')
                return True
            self.sleep(self.HOLD_POLL)
        self.logger.info(f'timed out waiting for {name} ({timeout:.1f}s)')
        return False


    # ---------------------------------------------- "没打到就不许切人" 的硬 gate
    # 这套轴是靠变奏/延奏一棒接一棒串起来的: 任何一棒少做一步, 代价都直接传给下一棒。
    # 实测 21:23 那一圈就是完整的传染链:
    #   穗穗 forte3 没读到 -> 硬走 Q/R -> "liberation not ready, leaving without field"
    #   -> 领域没放, 千咲上场 -> "chisa: liberation not ready" -> 整圈裸打。
    # 所以关键步骤不再"到点就走", 改成留场重试到真打出来为止。
    #
    # 【没有超时是有意的】: 给上限就等于"打不出来也走", 那这个 gate 等于没有。
    # 唯一的出口是 check_combat() —— 脱战/任务被停时它抛异常, 整条轴一起退出。
    # 怪都没了的话, "这一步没打到"本来也就不是问题了。
    GATE_POLL: float = 0.12       # gate 轮询间隔 (秒)
    GATE_LOG_EVERY: float = 3.0   # 卡住时每隔这么久打一条日志, 好在日志里一眼看出卡在哪

    def require(self, name, cond, action=None, timeout=0):
        """留场直到 cond() 成立 —— 这一步没做到就不许往下走。

        Args:
            name: 打日志用的步骤名, 卡住时就是靠它定位。
            cond: 判据, 返回真就放行。
            action: 每轮做的事, 一般传 self.click。不传就是干等 (长按期间要用这个,
                手上已经按着了, 再 click 会打断长按)。
            timeout: >0 时的上限 (秒), 到点放行并返回 False。默认 0 = 不设上限。
                【什么时候该给上限】: 这一步做不到时"照走"能自愈的, 就必须给 ——
                比如协奏没满只是让下一棒退回启动轴(那条轴本来就为空手入场准备),
                而卡死在这里不能自愈。实测 01:18 那轮 fill_con 无上限直接把任务挂住了。

        Returns:
            bool: 条件成立返回 True; 到 timeout 放弃返回 False。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if cond():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'gate [{name}] ok after {waited:.1f}s')
                return True
            self.check_combat()
            if action is not None:
                action()
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                self.logger.warning(f'gate [{name}] gave up after {elapsed:.1f}s, moving on')
                return False
            if elapsed - last_log >= self.GATE_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'gate [{name}] not satisfied after {elapsed:.1f}s, '
                                    f'staying on field')
            self.sleep(self.GATE_POLL)

    def require_cast(self, name, send, landed, pre=None, timeout=0):
        """反复按某个键直到它真的放出去了。

        跟 require() 的区别: 这里每轮都【重新发键】。被打断时按键会被游戏吃掉,
        补发是唯一的解法。
        Args:
            send: 发键的函数。
            landed: 判据 —— "已经放出去了"返回真。
            pre: 发键之前必须先成立的条件 (比如"图标亮了"), 不成立就先不发, 省得
                在冷却里空按。不传就是每轮都发。
            timeout: >0 时的上限 (秒), 到点放弃并返回 False。默认 0 = 不设上限。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if landed():
                waited = self.time_elapsed_accounting_for_freeze(start)
                self.logger.info(f'gate [{name}] cast confirmed after {waited:.1f}s')
                return True
            self.check_combat()
            if pre is None or pre():
                send()
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if timeout > 0 and elapsed >= timeout:
                self.logger.warning(f'gate [{name}] gave up after {elapsed:.1f}s, moving on')
                return False
            if elapsed - last_log >= self.GATE_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'gate [{name}] not cast after {elapsed:.1f}s, retrying')
            self.sleep(self.GATE_POLL)

    # ============================================================ 公共段结束

    # ================================================================ 嘉仇守: 嘉贝莉娜这一棒

    # ---- 时间常量。除注明外都是官方帧表换算 (60fps, 帧/60 = 秒), 单位秒 ----
    # 变奏入场: QTE 第 44F 前不响应输入、第 54F 前不能切人。这一段【不点普攻】——
    # 入场后的第一个动作是长按重击, 提前点一下会先出 A1, 重击就接在 A1 后面了
    INTRO_ANIM = 0.75
    # ---- 重击链的时间轴 (按住不放时【按派生帧推进】, 不是按动作结束帧) ----
    #   重击1 起手 0F, 派生 29F -> 重击2 起手 29F, 派生 16F -> 重击3 起手 45F,
    #   重击3-子弹 发生 30F(绝对 75F) 之后就是脱手飞道, 松手也会自己打完;
    #   重击3 派生 70F -> 【第二轮的重击1 从绝对 115F ≈ 1.92 秒起手】。
    # 2026-09-04 实机定标: HEAVY3_HOLD 原来填 4.00(照"动作结束帧相加"算的 3.95),
    # 结果整整打出两轮才切人 —— 240F 正好是两轮 115F 再多 10F, 反过来证明推进用的是派生帧。
    # 长按出 2 段重击: 重击2 的两段伤害发生在绝对 31F / 39F, 重击3 起手在 45F,
    # 所以松手压在 54F 上 —— 两段都出完了, 第三段刚起手 9F(子弹要 30F 才发生)被干净取消
    HEAVY2_HOLD = 0.90
    HEAVY_TO_A = 0.15      # 松手 -> 改点按接普攻第 3/4 段
    A_INTERVAL = 0.35      # A3 派生 48F / A4 派生 54F, 点按不必等满派生帧, 输入缓存会接上
    A_COUNT = 2
    A_TO_ECHO = 0.10
    # Q -> 强化E。Q 是脱手技能, 不用等它演完; 但要留一帧让余火到账再进恶魔位格
    # Q 是脱手技能, 不用等它演完就能接 E3 (2026-09-04: 0.25 -> 0.05)
    ECHO_TO_E = 0.05
    # 强化E(E3-恶翼扬升) 派生 29F、动作结束 61F ≈ 1.02 秒。R 要等它把恶魔位格开出来
    E_TO_LIB = 0.55
    # 大招演出结束 -> 爆发段起手的缓冲。
    # 【这个数跟 USE_BURST_MACRO 绑在一起, 切换时两个一起改】:
    #   轮椅爆发(现在这套) = 0.10 —— BURST_CLICKS_1/2 和 DODGE_OFFSET 全是按 0.10
    #     标定出来的, 动了它两脚闪避一起跟着偏;
    #   爆发宏           = 0.30 —— 宏作者按第一下时角色已经能动了, 而
    #     click_liberation() 是"队伍 HUD 一回来就返回"(隐藏HUD 201F), 大招动作结束
    #     在 206F, 后面还有一小截收招; 不让过去的话宏的前两下被吃, 19 步整体错位。
    LIB_TO_BURST = 0.10
    # ---- 强化E + R 之后的爆发宏: Sy1is 的「3裂隙 3踢 1翅」----
    # 来源: 用户给的手机宏工具导出表(四张滚动截图, 共 19 对 38 行)。三列是
    # 按键 / 动作(矮图标=按下, 高图标=松开) / 延时ms; 【延时是这一行之后等多久】,
    # 按下行的延时 = 按住时长, 末行 9999 = 宏结束接下一步。表的结构很规整:
    #     开头     左300 左300
    #     循环 ×3  右250 左200 右250 左750 左950
    #     收尾     右250 左9999
    # 每一行按下都是 78ms, 整段表定 9.53 秒。
    # 【这是视频里的"进阶爆发"】(A闪A 取消普攻第2/3段那套), 不是原来那条轮椅爆发 ——
    # 把 USE_BURST_MACRO 关掉就退回 burst_spam() 的「21下-闪-19下-闪-5下」
    # 【默认关着】(用户 2026-09-18 决定回到原版): 宏整套代码和表都留着,
    # 改成 True 就切到「3 裂隙 3 踢 1 翅」那套。
    # 【切换时必须连 LIB_TO_BURST 一起改】—— 见那个常量的注释
    USE_BURST_MACRO = False
    BURST_MACRO_HOLD = 0.078
    BURST_MACRO_OPEN = (('L', 0.30), ('L', 0.30))
    BURST_MACRO_CYCLE = (('R', 0.25), ('L', 0.20), ('R', 0.25), ('L', 0.75), ('L', 0.95))
    BURST_MACRO_CYCLES = 3
    BURST_MACRO_TAIL = (('R', 0.25), ('L', 0.0))
    MACRO_SLICE = 0.05         # 精确等待的切片长度, 见 macro_wait()

    # ---- 爆发段 ----
    # 【闪避是省时间的手段, 不是锦上添花】(2026-09-04 用户实机结论):
    # 恶魔A4 的后摇被闪避断掉, 下一轮才能提早开始; 不闪的话大招那 14 秒强化状态里
    # 根本轮不到第二脚。所以这一段是 mash 普攻 + 两次闪避, 按【点几下】分段:
    #   25 下 -> 闪避 -> 35 下 -> 闪避 -> 5 下 -> 停手等火翼 -> 长按 3 段常态重击。
    # 下数是用户按实机数出来的。第二段比第一段多 10 下不是笔误: 第一段的起点是大招
    # 收招, 第二段的起点是闪避, 而且上一脚真打中了(命中顿帧 + 怪被削韧打歪要重新贴身)。
    #
    # 【别再试"一段点一下"】: 按派生帧一段点一下试过, 时刻是准了, 但点早一下就被游戏
    # 丢掉、代码又不重试, 整条链越拖越后; 加了余量之后又变成十几秒只点十下, 输出太慢。
    # 一下 = BURST_A_INTERVAL(0.16 秒), 所以下数就是这一脚的时间刻度。
    # 调参轨迹(2026-09-04 实机): 25(4.10s) -> 23 -> 22 -> 21 下(3.46s), 一路嫌慢
    BURST_CLICKS_1 = 21        # 起手 -> 第 1 脚闪避
    # 【第二段一口气砍了 16 下(2.56 秒)】: 35 是在强化E 还放成普通E 的那几轮定的 ——
    # 那时候恶魔位格根本没开出来, 打的是常态连段, 自然慢。E3 修好之后两段就差不多长了,
    # 这也反过来说明现在的连段才是对的
    BURST_CLICKS_2 = 19        # 第 1 脚 -> 第 2 脚 (再 +0.50s 见 DODGE_OFFSET)
    BURST_CLICKS_TAIL = 5      # 第 2 脚 -> 停手
    BURST_A_INTERVAL = 0.16
    DODGE_CANCEL = True        # 关掉就整段不闪, 但那样第二脚打不出来
    # 每一脚在"这一段最后一下之后一拍"的基础上再往后压多少秒 —— 【秒级微调】。
    # 下数的粒度是 BURST_A_INTERVAL(0.16 秒), 想动 0.1 秒这种量就不够用了, 所以粗调
    # 用下数、细调用这个。【等的这段照样按节奏点普攻】, 不是干站着
    # 调参轨迹(2026-09-04 实机): (0.10, 0.50) -> 两脚各快 0.1 -> (0.00, 0.40)
    #   -> 第一脚又嫌快 0.1 -> (0.10, 0.40)。第一脚在 0.00~0.10 之间来回一次,
    #   说明已经收敛在 0.10 附近了
    DODGE_OFFSET = (0.10, 0.40)
    # 闪避 -> 下一下普攻的间隔。【不能贴着发】: 右键之后几毫秒就发左键的话, 那一下
    # 正落在闪避的第 0 帧上, 会把闪避顶掉。人手说的"闪完马上接A"是 0.1 秒量级
    DODGE_TO_A = 0.12
    # 兜底上限, 不是正常出口 —— 正常出口是三段下数打完。
    # 【改上面的下数时必须跟着改这个】: 65 下按 160ms 是 10.4 秒, 但实际节奏会被截图
    # 开销拖到 200~265ms, 那就要 13~17 秒; 上限给小了会把尾巴【静默】砍掉, 踩过一次
    BURST_WINDOW = 24.0

    COMBAT_CHECK_EVERY = 0.5


    # 普攻打完 -> 按住左键之间的停手空档。【2026-09-04 用户实机后要求去掉】——
    # 试过 3.0 秒(想让飞天火翼演完再按住重击), 实机不要这一段。
    # 留着这个常量只是为了随时能再打开, 0 = 打完最后一下就直接按住
    BURST_TO_HEAVY = 0.0
    # 收尾长按 3 段常态重击: 松手压在 102F ≈ 1.70 秒 —— 重击3 已经起手 57F、
    # 子弹早在 75F 就脱手了, 而第二轮的重击1 要到 115F 才起手, 留 13F 余量。
    # 【这个值只能在 1.25(重击3 子弹脱手) 和 1.92(第二轮起手) 之间调, 超过 1.92 就多一轮】
    HEAVY3_HOLD = 1.70
    # 等协奏时补普攻的节奏。这一段不是连段调度, 打不打得出连段无所谓,
    # 只要别站着就行 —— 所以这里可以随便点
    FILL_CON_INTERVAL = 0.20
    # ---- 判据的上限 ----
    # 等罪火满(才能出 E3)。等不到也照按 —— 按出 E1 只是亏一点, 卡死在这里更糟
    # 等【强化E 图标亮起来】的窗口。用户 2026-09-04 实机: 强化E 的图标是高亮的,
    # 普通 E 不亮, 而且【高亮窗口很短】。所以这里不是"等够多久再按", 而是一边补普攻
    # 攒罪火、一边高频盯着那个图标, 亮了当帧就发键。窗口给宽一点没坏处 —— 亮了就走
    # 【这是硬闸: 没打出强化E 就不许往下走】(2026-09-04 用户定的保底)。
    # 原来 2.5 秒读不到就"照按", 那一下按出的是普通E1 —— 不只这一下亏, E1 一样把 E
    # 打进冷却, 这一轮 E3 再也放不出来, 恶魔位格开不出来, 整段爆发跟着废。
    # 所以改成一边点普攻攒罪火、一边盯着, 直到它亮。给上限只是防判据在别人机器上
    # 不灵时永久卡死 —— 真到点了【也不按】, 见 FORTE_PRESS_ON_TIMEOUT
    FORTE_WAIT = 8.0
    # 到点还没亮的时候按不按。默认 False: 按出 E1 会把 E 打进冷却, 比不按更糟 ——
    # 不按的话这一棒退化成常态连段, 协奏照样攒得满, 下一圈自己会补回来
    FORTE_PRESS_ON_TIMEOUT = False
    # 轮询间隔。【别把补普攻塞进轮询循环里】: 原来是 0.12 秒一轮、每轮夹一次 click(),
    # 而 click() 自己要花几十毫秒, 实际间隔远不止 0.12 —— 窗口短的话这就够错过了。
    # 现在轮询和补普攻各走各的节奏
    FORTE_POLL = 0.04
    FORTE_FILL_INTERVAL = 0.20   # 等的时候多久补一下普攻(补普攻是为了把罪火打满)
    FORTE_LOG_EVERY = 3.0        # 卡在这道闸上时每隔这么久报一次读数

    LIB_WINDOW = 3.0
    LIB_RETRY = 0.12
    ECHO_TRIES = 3
    ECHO_RETRY = 0.15
    # 协奏没满不许走: 她带不带变奏走人决定守岸人跑哪条轴(空手入场要多打一组)
    CON_GATE_TIMEOUT = 10.0
    CON_LOG_EVERY = 3.0
    TEAM_HUD_WAIT = 2.5
    LEAVE_TRIES = 2
    SWITCH_ANCHOR_MAX = 1.5

    @property
    def NEXT_SLOT(self):
        """打完切守岸人。按【类】找人而不是写死队伍位置。"""
        for char in self.task.chars:
            if isinstance(char, NativeShoreKeeper):
                return char.index + 1
        return None

    def reset_state(self):
        super().reset_state()
        # 【必须写在 reset_state 里, 光写 __init__ 保不住】: CharFactory 换类时是先建新
        # 实例再 replacement.__dict__.update(内置实例.__dict__), 凡是"内置也有、我改了值"
        # 的字段都会被静默打回内置值。她不是治疗, 内置值是 True, 不重申的话框架会在
        # 每次切人时替她按一下 F —— 处决的阻塞会把长按重击整段打断
        self.check_f_on_switch = False

    def on_combat_end(self, chars):
        self.reset_fixed_rotation(chars)
        super().on_combat_end(chars)

    # ------------------------------------------------------------------ 轴

    def do_perform(self):
        intro = self.take_intro()
        self.check_f_on_switch = False
        anchor = self.switch_in_anchor()
        if not intro:
            # 空手入场 = 开局那一下(或者轮换被打断掉回她这里)。没有变奏就没有起手的
            # 资源, 只能丢个 Q 垫点罪火就把场子交给守岸人, 让轮换自己转回来
            self.logger.info('galbrena: 空手入场, 走开场分支')
            return self.opener()
        self.sleep(max(0.0, anchor + self.INTRO_ANIM - time.time()), check_combat=False)
        self.logger.info('galbrena: 爆发轴 start')
        return self.burst_axis()

    # ------------------------------------------------------------------ 启动轮开场

    def opener(self):
        """开场: Q + 长按 3 段常态重击 -> 直接切守岸人。

        Q 放在这里不是为了伤害 —— 是为了在【进恶魔位格之前】多吃一次
        "全队不同名声骸技能"的监听(+8 余火), 她主声骸冷却 20 秒, 一圈够放两次。
        三段重击是拿来垫罪火的, 常态攻击命中是她唯一的罪火来源。
        """
        self.fire_echo()
        self.hold_heavy(self.HEAVY3_HOLD, '开场 3 段常态重击')
        self.leave(assume_intro=False, wait_con=False)

    # ------------------------------------------------------------------ 爆发轴

    def burst_axis(self):
        # ---- 长按 2 段重击 -> 点按 2 段普攻 (喂罪火) ----
        self.hold_heavy(self.HEAVY2_HOLD, '入场 2 段重击')
        self.sleep(self.HEAVY_TO_A)
        self.attack_chain(self.A_COUNT, self.A_INTERVAL, last_interval=self.A_TO_ECHO)

        # ---- Q: 必须在 E3 之前 ----
        self.fire_echo()
        self.sleep(self.ECHO_TO_E)

        # ---- 强化E(E3-恶翼扬升) -> R ----
        self.cast_forte_resonance()
        self.sleep(self.E_TO_LIB)
        self.fire_liberation()
        self.sleep(self.LIB_TO_BURST)

        # ---- 爆发 ----
        if self.USE_BURST_MACRO:
            self.play_burst_macro()
        else:
            self.burst_spam()

        # ---- 收尾: 等火翼演完 -> 长按 3 段常态重击 -> 变奏守岸人 ----
        # 【这一段停手】: 见 BURST_TO_HEAVY, 补普攻会让连段接着循环, 火翼永远打不完
        if self.BURST_TO_HEAVY > 0:
            self.logger.info(f'galbrena: 停手 {self.BURST_TO_HEAVY:.1f}s 等火翼演完')
            self.sleep(self.BURST_TO_HEAVY)
        self.hold_heavy(self.HEAVY3_HOLD, '收尾 3 段常态重击')
        self.leave()

    def play_burst_macro(self):
        """回放爆发宏 —— 强化E + R 之后的「3裂隙 3踢 1翅」, 表见 BURST_MACRO_* 的注释。

        【按绝对时刻排程, 不是"按完再等 gap"】: 每一步按下/松开本身要花几毫秒到几十
        毫秒, 逐步累加的话 19 步下来能差出小半秒, 后面几个裂隙就全错位了。
        这里每一步的目标时刻先算死, 实际花掉的时间从下一段等待里扣。

        【宏里不读屏】: 中途插截图会把节奏打乱。这一段只有 9.5 秒, 脱战不在这里判 ——
        宏跑完外面照常有协奏闸兜着。
        """
        seq = (list(self.BURST_MACRO_OPEN)
               + list(self.BURST_MACRO_CYCLE) * self.BURST_MACRO_CYCLES
               + list(self.BURST_MACRO_TAIL))
        t0 = time.time()
        at = 0.0
        for key, gap in seq:
            btn = 'left' if key == 'L' else 'right'
            self.macro_wait(t0 + at - time.time())
            self.task.mouse_down(key=btn)
            self.macro_wait(t0 + at + self.BURST_MACRO_HOLD - time.time())
            self.task.mouse_up(key=btn)
            at += self.BURST_MACRO_HOLD + gap
        spent = time.time() - t0
        self.logger.info(f'galbrena: 爆发宏回放完毕 ({len(seq)} 步, 表定 {at:.2f}s, '
                         f'实际 {spent:.2f}s, 漂移 {spent - at:+.2f}s)')

    def burst_spam(self):
        """爆发段: mash 普攻打恶魔连段, 按【点几下】分段, 每段末尾闪避断 A4 后摇。

        25 下 -> 闪避 -> 35 下 -> 闪避 -> 5 下 -> 返回
        (外面再停手等火翼演完, 然后长按 3 段常态重击)。

        闪避是省时间的: 断掉恶魔A4 的后摇, 下一轮才能提早开始 —— 不闪的话大招那
        14 秒强化状态里根本轮不到第二脚。
        """
        start = time.time()
        next_click = start
        if self.DODGE_CANCEL:
            plan = [self.BURST_CLICKS_1, self.BURST_CLICKS_2]
            tail = self.BURST_CLICKS_TAIL
        else:
            # 不闪的话整段一口气点完, 不要在中间断开
            plan = []
            tail = self.BURST_CLICKS_1 + self.BURST_CLICKS_2 + self.BURST_CLICKS_TAIL
        seg = 0
        in_seg = 0
        clicks = 0
        dodged = 0
        seg_start = 0.0
        dodge_due = None
        last_check = -self.COMBAT_CHECK_EVERY
        capped = False
        while True:
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.BURST_WINDOW:
                capped = True
                break
            # 战斗判定限流, 见 COMBAT_CHECK_EVERY
            if elapsed - last_check >= self.COMBAT_CHECK_EVERY:
                last_check = elapsed
                self.check_combat()
            need = plan[seg] if seg < len(plan) else tail
            if in_seg >= need and dodge_due is None:
                # 【这里要把时刻冻住】: 等 offset 的这段还在按节奏补普攻, next_click
                # 会一直往后跑, 直接拿它当判据的话闪避永远等不到
                off = self.DODGE_OFFSET[seg] if seg < len(self.DODGE_OFFSET) else 0.0
                dodge_due = next_click + off
            if in_seg >= need and dodge_due is not None and time.time() >= dodge_due:
                if seg >= len(plan):
                    break                      # 尾段打完
                span = elapsed - seg_start
                # 【定标用】: 光看"早了/晚了"没法改数, 要的是这一段真实点了几下、
                # 平均多少毫秒一下 —— 连段推进快慢全看它
                self.logger.info(
                    f'galbrena: 第{dodged + 1}脚闪避 @ {elapsed:.2f}s '
                    f'(这一段 {in_seg} 下 / {span:.2f}s, 实际节奏 '
                    f'{span / max(1, in_seg) * 1000:.0f}ms/下, 设定 '
                    f'{self.BURST_A_INTERVAL * 1000:.0f}ms)')
                self.dodge()
                dodged += 1
                # 闪完接普攻取消闪避后摇, 但【不能贴着发】—— 见 DODGE_TO_A
                if self.DODGE_TO_A > 0:
                    self.sleep(self.DODGE_TO_A, check_combat=False)
                self.click()
                clicks += 1
                seg += 1
                in_seg = 1                     # 闪后这一下算进下一段
                seg_start = self.time_elapsed_accounting_for_freeze(start)
                next_click = time.time() + self.BURST_A_INTERVAL
                dodge_due = None
                continue
            now = time.time()
            if now >= next_click:
                self.click()
                clicks += 1
                in_seg += 1
                # 落后了就以当下重新对齐, 不补点(补点会连着糊几下, 反而把连段打乱)
                next_click = max(now, next_click) + self.BURST_A_INTERVAL
            self.sleep(max(0.005, next_click - time.time()))
        total = self.time_elapsed_accounting_for_freeze(start)
        self.logger.info(
            f'galbrena: 爆发段结束 ({total:.2f}s, 普攻 {clicks} 下, 平均 '
            f'{total / max(1, clicks) * 1000:.0f}ms/下, 闪避 {dodged} 次'
            f'{", 撞到 BURST_WINDOW 上限" if capped else ""})')

    def dodge(self):
        """闪避 = 右键(全框架统一)。这条轴上用来断恶魔A4-2 的后摇, 省下一轮的时间。

        注意 Game Hotkey.json 里那个 "Dodge Key": "lshift" 【不是】战斗闪避 ——
        那是疾跑键, 只被导航代码用(贴墙跑那段是 mouse_down(key='right'))。
        """
        self.task.click(key='right')

    # ------------------------------------------------------------------ 动作

    def hold_heavy(self, duration, name):
        """按住普攻打常态重击链。

        重击是【固定长度的动画】, 游戏不给"这一段打完了"的信号, 只能计时;
        中途松手会把当前那一段截断。
        【按住期间一个键都不发】—— PostMessage 后端的 mouse_down()/send_key() 都会
        投一条 WM_ACTIVATE, 窗口收到就把已经按住的左键松开。
        """
        self.task.mouse_down()
        try:
            end = time.time() + duration
            while time.time() < end:
                self.sleep(min(self.HOLD_POLL, max(0.0, end - time.time())))
        finally:
            self.task.mouse_up()
        self.logger.info(f'galbrena: {name} 按住 {duration:.2f}s')
        self.sleep(0.05)

    def cast_forte_resonance(self):
        """强化E(E3-恶翼扬升) —— 【盯着图标亮的那一刻按, 窗口很短】。

        罪火没满按下去出的是 E1-迫近, 恶魔位格开不出来, 后面整段爆发全废;
        而且 E1 一样会把 E 打进冷却, 这一轮就再也放不出 E3 了。

        用户 2026-09-04 实机给的判据: 【强化E 的图标是高亮的, 普通 E 不亮】,
        而且高亮窗口很短。所以这里的形态是:
          * 高频轮询(FORTE_POLL), 补普攻走自己的节奏(FORTE_FILL_INTERVAL) ——
            原来 0.12 秒一轮还夹一次 click(), 实际间隔远不止 0.12, 窗口短就错过了;
          * 读到就【直接发键】, 不走 click_resonance() —— 它开头还要自己再读一次屏,
            窗口这么短经不起这一下;
          * 两条判据取或, 并把两个读数一起打进日志 —— 只看一个布尔值出问题时无从下手。
        """
        start = time.time()
        next_fill = 0.0
        last_log = 0.0
        while self.time_elapsed_accounting_for_freeze(start) < self.FORTE_WAIT:
            forte, e_forte = self.forte_signals()
            if forte or e_forte:
                waited = self.time_elapsed_accounting_for_freeze(start)
                # 【当帧发键】: 先发, 再记账, 中间不要再读屏
                self.send_resonance_key()
                self.record_resonance_use()
                self.logger.info(f'galbrena: 强化E 图标亮了, 当帧按下 '
                                 f'(等了 {waited:.2f}s, forte={forte} e_forte={e_forte})')
                return True
            if self.time_elapsed_accounting_for_freeze(start) - last_log >= self.FORTE_LOG_EVERY:
                last_log = self.time_elapsed_accounting_for_freeze(start)
                self.logger.warning(f'galbrena: 还在等强化E 亮 ({last_log:.1f}s, '
                                    f'forte={forte} e_forte={e_forte}), 继续点普攻攒罪火')
            if time.time() >= next_fill:
                # 补普攻是为了把罪火打满, 但它不能拖慢轮询, 所以走自己的节奏
                self.click()
                next_fill = time.time() + self.FORTE_FILL_INTERVAL
            self.task.next_frame()
            self.sleep(self.FORTE_POLL, check_combat=False)
        forte, e_forte = self.forte_signals()
        if not self.FORTE_PRESS_ON_TIMEOUT:
            # 【宁可不放也不按成普通E】—— 见 FORTE_PRESS_ON_TIMEOUT
            self.logger.warning(f'galbrena: 强化E {self.FORTE_WAIT}s 内一直没亮, 跳过这一下 '
                                f'(forte={forte} e_forte={e_forte}); 这一棒退化成常态连段, '
                                f'协奏照样攒, 下一圈自己补回来')
            return False
        self.logger.warning(f'galbrena: 强化E {self.FORTE_WAIT}s 内没读到亮, 照按 '
                            f'(forte={forte} e_forte={e_forte}) —— 这一下多半是普通E')
        return self.click_resonance(send_click=False, time_out=1)[0]


    def forte_signals(self):
        """强化E 就绪的两条判据, 分开返回好在日志里单独看每一条。"""
        return bool(self.is_forte_full()), bool(self.is_e_forte_full())

    def fire_liberation(self):
        """R 大招 —— 【在窗口里等图标亮了再按, 不是问一次就走】。

        click_liberation() 只在"图标当下就亮着"时才真发键, 读到不亮就在
        wait_if_cd_ready 里补两下返回 False, 整个过程只有 0.13~0.47 秒 ——
        而这时候 E3 的演出刚过去, 能量环那几帧最不可信。
        顺手清 task.in_liberation: 它被上一个角色留成 True 的话, click_liberation()
        开头那个 `if not self.task.in_liberation:` 会把发键整段跳过。
        click_f=False: 大招演出期间框架默认每 0.1 秒发一次 F, 会把攒着的处决提前吃掉。
        """
        self.task.in_liberation = False
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            self.task.next_frame()
            if self.liberation_available():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if self.click_liberation(wait_if_cd_ready=0.3, click_f=False):
                    self.logger.info(f'galbrena: liberation fired after {waited:.2f}s')
                    return True
            self.check_combat()
            self.click()
            self.sleep(self.LIB_RETRY)
        self.logger.warning(f'galbrena: 大招图标 {self.LIB_WINDOW}s 内没亮, 硬按一次')
        return self.click_liberation(wait_if_cd_ready=0.5, click_f=False)

    def fire_echo(self):
        """Q 声骸 —— 【连发, 不是只发一次】。

        演出期间的按键被游戏丢弃、不排队, 只发一次很容易整段被吃掉;
        Q 是脱手技能, 多按几下不会打断当前动作, 连发是安全的。
        先发键, 读屏只用来决定要不要重发 —— 读到"在冷却"就不发的话, 读错一次
        这一轮就没有声骸, 而按在冷却上只是空操作。
        """
        was_available = self.echo_available()
        if not was_available:
            self.logger.info('galbrena: echo reads on-cd, sending once anyway')
        for i in range(self.ECHO_TRIES):
            self.send_echo_key()
            self.record_echo_use()
            self.sleep(self.ECHO_RETRY, check_combat=False)
            if not was_available:
                return False
            if not self.echo_available():
                if i:
                    self.logger.info(f'galbrena: echo landed on try {i + 1}')
                return True
        self.logger.warning(f'galbrena: echo still reads available after '
                            f'{self.ECHO_TRIES} tries')
        return False

    # ------------------------------------------------------------------ 离场

    def leave(self, assume_intro=None, wait_con=True):
        """协奏满了再走, 然后按数字键直接切守岸人。

        【不走 direct_switch()】: 它开头还会自己 wait_con_full() 再轮询 0.8 秒协奏,
        而协奏是什么状态 fill_con() 刚刚才读完 —— 那 0.8 秒是纯白等。
        开场那一下更明显: 那时协奏必然是 0, 等满也不会满。

        Args:
            wait_con: 开场那一下传 False, 跳过等协奏。
        """
        con_ok = self.fill_con() if wait_con else False
        if assume_intro is not None:
            con_ok = assume_intro
        target = self.char_at_slot(self.NEXT_SLOT) if self.NEXT_SLOT else None
        if target is not None and target is not self:
            for attempt in range(1, self.LEAVE_TRIES + 1):
                # 大招/重击的演出期间队伍 HUD 整块不渲染, in_team() 认不出人,
                # 直接按数字键会白按满 SWITCH_TIMEOUT 然后判失败
                self.wait_team_hud()
                if self.switch_to_index(target.index):
                    self.handoff_to(target, con_ok)
                    self.logger.info(f'galbrena: 交棒守岸人 (intro={con_ok})')
                    return
                self.logger.warning(f'galbrena: direct switch failed '
                                    f'(第 {attempt}/{self.LEAVE_TRIES} 次), retrying')
        self.logger.warning('galbrena: falling back to framework switch, rotation may drift')
        self.switch_next_char()

    def fill_con(self):
        """等协奏满 —— 期间继续点普攻, 不干等。

        给上限是有意的: 协奏满不满取决于打没打到怪, 怪被清了就永远等不到,
        而"没带变奏"下一棒(守岸人)本来就有空手入场的分支, 能自愈。
        """
        start = time.time()
        last_log = 0.0
        while True:
            if self.is_con_full():
                waited = self.time_elapsed_accounting_for_freeze(start)
                if waited > 0.3:
                    self.logger.info(f'galbrena: 协奏满 after {waited:.1f}s')
                return True
            elapsed = self.time_elapsed_accounting_for_freeze(start)
            if elapsed >= self.CON_GATE_TIMEOUT:
                self.logger.warning(f'galbrena: 协奏没满就到点了 '
                                    f'(con={self.get_current_con():.2f}), 走人')
                return False
            if elapsed - last_log >= self.CON_LOG_EVERY:
                last_log = elapsed
                self.logger.warning(f'galbrena: 还在等协奏 ({elapsed:.1f}s, '
                                    f'con={self.get_current_con():.2f})')
            self.check_combat()
            self.click()
            self.sleep(self.FILL_CON_INTERVAL)

    def wait_team_hud(self, timeout=None):
        """等右侧队伍头像的编号渲染回来 —— 演出期间它整块是不画的。"""
        timeout = self.TEAM_HUD_WAIT if timeout is None else timeout
        start = time.time()
        while time.time() - start < timeout:
            if self.task.in_team()[0]:
                waited = time.time() - start
                if waited > 0.2:
                    self.logger.info(f'galbrena: team hud back after {waited:.2f}s')
                return True
            self.task.next_frame()
            self.sleep(0.08, check_combat=False)
        self.logger.warning(f'galbrena: team hud still unreadable after {timeout:.1f}s')
        return False

    # ------------------------------------------------------------------ 小工具

    def switch_in_anchor(self):
        """本轮的计时基准: 头像认出「切人完成」的那一刻, 不是 do_perform 被调那一刻。"""
        now = time.time()
        anchored = self.last_switch_in_time
        gap = now - anchored if anchored > 0 else -1
        if 0 <= gap <= self.SWITCH_ANCHOR_MAX:
            return anchored
        return now
