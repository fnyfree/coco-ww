"""日月守轴的干跑: 假 task + 假游戏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是等到了判据, 还是撞上限出来的
  - 演出/锁输入按帧表: 日 变奏 68F / R2 藏 HUD 131F / 俯首之刻 7s / 不朽者之肃 藏 HUD 195F、220F 不响应;
    月 变奏 78F / R 藏 HUD 234F、239F 不响应 / 变弓 103F(闪避断掉) / 弓 E 80F;
    守 变奏 86F / 强化重击 86F
  - 日 资源按帧表「特殊能量」: 战势(鸣铁 3x17.54, 斩锋回满, 后撤/升拳清空) / 权柄(变奏 20, R1 40, 斩锋 10,
    旋切 9.9, 升拳 7.6, 鸣铁 3.4) / 威慑(开局 1, 落袭 1, 守带着日的延奏 14s 内变奏出去 1)
  - 假游戏模拟不了预输入缓冲(长按在锁里按下, 从解锁那一刻算; 只有旋切 54F 里按下的长按整段不认, 松手出 A1), 也不模拟怪和伤害
  - 读屏替身: 战势条(满了粉白 + 高条一跳一跳, 会连着几帧低) / 尤诺左下那格的形态(长枪 = 环, 星球 = 弓, 至臻的完满盖住时读不出)
用法: 放在 <working>/configs/custom_teams/Augusta__Iuno__ShoreKeeper/ 里(跟三个 .py 一起), 用 ok-ww 自带 python 跑:
      <ok-ww 安装目录>/data/apps/ok-ww/python/python.exe dryrun.py.tool
      working 目录从本文件位置往上自动找; 放在别处跑就设环境变量 OKWW_WORKING=<...>/data/apps/ok-ww/working
环境变量:
  DRY_LOOPS=2            循环几圈
  DRY_BREAK=0            日升拳落地时怪身上有处决提示
  DRY_FAIL=a,b,...       故意失败, 测保底分支(带 * 的整圈都失败, 其余每圈触发一次):
                         sw         切月第一下被拒 2.8s(被击飞/硬直)
                         skanim*    守岸人变奏入场带镜头(HUD 藏 1.2s)
                         skcon*     守岸人 R 演完协奏不满(要补 E / 重击 / 普攻)
                         iulow      尤诺变奏进来 R 能量不够(补一发告终 E 才亮)
                         iulow2     尤诺变奏进来 R 能量不够, 补完告终 E 还差 12 下普攻
                         iulate     尤诺变奏进来 R 能量不够, 按下告终 E 1.2s 后才亮(还在告终动作里, 跳被丢; 18:24 实机补完 E 和 Q 还暗)
                         zsover     日撤Z切打完战势已经满了(变奏入场后的旋切把战势补满, 升拳要靠跳)
                         iurcd      尤诺变奏进来 R 冷却还剩 1.0s
                         jumpeat    尤诺第一下跳被吞(可跳图标还在, 要再跳一次)
                         iubowflip* 尤诺在弓形态放 R, 演完变回环(实机 10-18 15:12 是还是弓, 那个是默认)
                         jumpblind* 可跳图标一直读不到(只跳一次)
                         formblind* 尤诺左下那格的形态一直读不出(退回可跳图标的老判法)
                         spinlong*  日旋切的动作比 54F 长(到 1.2s), 升拳那一下长按按在里面不认(要松手再按)
                         zsblind*   日战势条一直读不到(退回准星旁指示, 升拳按满一次上限)
                         heavyblind* 至臻的完满图标一直读不到(补 E2 撞上限, 重击按满上限)
                         iucon*     尤诺重击之后协奏差一截(要边 A 边补 E)
                         aulow      日放 R1 时能量差 8 下普攻(攒能量 + 之后每圈补 Q)
                         r1eat      日这一圈第一次按 R 起 1.5s 内的 R 全被吞(R1 没放出来, 再攒/再按)
                         r2miss     日第一次长按 R2 没被认出来(还在 R1 里, 要再长按)
                         r1dead*    日 R1 能量不够放不出来, 但 2 层威慑时 R 图标照样亮(赫日威临不吃能量)
                         ws1*       守岸人没带着日的延奏变奏(只有落袭那 1 层威慑, 没有 R2)
                         echain*    E 框的斩锋冷却数字读不到(E 串撞 AUG_E_CHAIN_CAP)
                         forteblind* 准星旁的战势/回路指示一直读不到(全退回固定时长)
                         aucon*     日这一圈协奏涨得慢(升拳后要边 A 边等)
                         hudlost    循环 #1 日 R1 之后关卡结束(HUD 再也不回来)
                         qeat       每个人这一圈第一次按 Q 起 1.5s 内的 Q 全被吞
"""
import os
import random
import sys
import threading
import time
import types
from pathlib import Path


def find_working():
    """ok-ww 的 working 目录(里面有 src/char/CustomCharLoader.py): OKWW_WORKING > 本文件往上 > 当前目录往上。"""
    marker = Path('src') / 'char' / 'CustomCharLoader.py'
    cands = [Path(os.environ['OKWW_WORKING'])] if os.environ.get('OKWW_WORKING') else []
    here = Path(os.path.abspath(__file__))
    cands += [*here.parents, Path.cwd(), *Path.cwd().parents]
    for c in cands:
        if (c / marker).is_file():
            return str(c)
    sys.exit('找不到 ok-ww 的 working 目录: 把本文件放进 <working>/configs/custom_teams/Augusta__Iuno__ShoreKeeper/ 再跑, '
             '或者设环境变量 OKWW_WORKING')


sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
WORKING = find_working()
sys.path.insert(0, WORKING)
os.chdir(WORKING)              # 框架按相对路径 configs/custom_teams 找队伍代码

DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '2'))
FAILS = set(x for x in os.environ.get('DRY_FAIL', '').split(',') if x)
BREAK = os.environ.get('DRY_BREAK', '0') == '1'
random.seed(7)
LOG = []
T0 = [time.time()]
SK, IU, AU = 0, 1, 2           # 编队: 1 守 / 2 月 / 3 日(代码按角色切人, 跟编队无关)
NAMES = ['守', '月', '日']
F = 1 / 60
MT_HIT_F = (22, 28, 33)                     # 鸣铁三段出伤帧(帧表 重击-1 22F, 重击-2 28F 两下隔 5F)


def rec(what):
    LOG.append((time.time() - T0[0], what))


class FakeExecutor:
    # coco-ww 的 MacroKit.macro_wait 走 BaseChar.precise_wait, 它每片都看 exit_event(停任务)和暂停状态
    exit_event = threading.Event()
    current_task = None
    debug_mode = True
    pause_start = None

    def blocked_by_pause(self):
        return False

    trigger_tasks = []
    onetime_tasks = []
    interaction = None

    def check_enabled(self, check_pause=False):
        # 引擎的 macro_wait 每 0.1s 调一次: 按住期间也要推进假游戏(鸣铁的段数、俯首之刻)
        if G is not None:
            G.tick()


G = None


class Hit:
    def __init__(self, confidence):
        self.confidence = confidence


class Game:
    def __init__(self):
        self.on_field = AU
        self.pending = None
        self.hud_hidden = []
        self.flickers = []
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.con = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.e_cd = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.r_cd = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.q_cd = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.avail_at = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.last_step = {SK: 0.0, IU: 0.0, AU: 0.0}
        self.blocks = {}
        self.holding = False
        self.hold_at = 0.0
        self.cycle = 0
        self.fail_used = set()
        self.hud_dead = False
        self.arrived_at = 0.0
        self.due = []
        self.break_ready = False
        self.r_down_at = None
        # 日
        self.zs = 0.0                       # 战势
        self.qb = 0.0                       # 权柄
        self.ws = 1                         # 威慑(开局炽盛决意 1 层)
        self.au_short = 0                   # R 能量还差几下普攻
        self.au_stage = None                # 按住时: None / 'back' / 'mt'
        self.mt_hits = 0
        self.mt_at = 0.0
        self.back_at = 0.0
        self.back_end = 0.0                 # 后撤收招(46F): 这之前长按出不了鸣铁(18:24 录屏)
        self.mt_derive = 0.0                # 鸣铁派生(41F): 这之前点的旋切补施放排到这时才出
        self.permit = (0.0, 0.0)            # 旋切许可窗口
        self.recover_until = 0.0            # 许可被打断后 2s 内还能补一次旋切
        self.e_step = 0                     # E2/E3/E4 走到第几步
        self.air_until = 0.0
        self.r1_at = None
        self.majesty_from = None            # 俯首之刻开始(R2 镜头结束)
        self.majesty_until = 0.0
        self.lieyang = 0
        self.outro_holder = None            # 带着日延奏的人
        self.outro_until = 0.0
        self.r2_ignored = False
        self.spin_until = 0.0               # 旋切派生前: 这段里按下的长按整段不认
        self.hold_ignored = False
        # 月
        self.moon_until = 0.0
        self.form = 'ring'
        self.gz_unlock_until = 0.0          # 告终的喧响解锁
        self.bow_charges = 2
        self.bow_recharge_at = 0.0
        self.bow_e_count = 0
        self.heavy_icon_at = None
        self.jump_at = 0.0
        self.au_intro_at = -99.0
        self.iu_short = False
        self.iu_short_a = 0                 # 补完告终 E 之后 R 还差几下普攻
        self.iu_late = False                # 告终 E 的能量出伤打完才到账(iulate)
        self.gz_lock_until = 0.0            # 告终 E 的动作(98F)
        # 守
        self.sk_data = 0
        self.sk_step = 0
        self.sk_last_a = 0.0

    # ---------- 失败旋钮 ----------
    def fail_once(self, name):
        if name not in FAILS or (name, self.cycle) in self.fail_used:
            return False
        self.fail_used.add((name, self.cycle))
        rec(f'!!故意失败:{name}')
        return True

    def fail_cycle(self, name):
        if name not in FAILS:
            return False
        if (name, self.cycle) not in self.fail_used:
            self.fail_used.add((name, self.cycle))
            rec(f'!!故意失败:{name}(整圈)')
        return True

    def swallowed(self, knob, key, who, dur):
        now = time.time()
        if knob not in FAILS:
            return False
        tag = (knob, who, self.cycle)
        if tag not in self.fail_used:
            self.fail_used.add(tag)
            self.blocks[(key, who)] = now + dur
            rec(f'!!故意失败:{knob}({NAMES[who]} {key} 被吞 {dur:.1f}s)')
        return now < self.blocks.get((key, who), 0.0)

    # ---------- HUD / 锁 ----------
    def hud_visible(self, now=None):
        now = time.time() if now is None else now
        if self.hud_dead:
            return False
        for a, b in self.flickers:
            if a <= now < b:
                return True
        for a, b in self.hud_hidden:
            if a <= now < b:
                return False
        return True

    def hide(self, dur, flick=()):
        now = time.time()
        self.hud_hidden.append((now, now + dur))
        for f in flick:
            self.flickers.append((now + f, now + f + 0.06))
        self.lock_until = max(self.lock_until, now + dur)
        self.no_switch_until = max(self.no_switch_until, now + dur)

    def lock(self, dur):
        self.lock_until = max(self.lock_until, time.time() + dur)

    def busy(self):
        return time.time() < self.lock_until

    def add_con(self, i, v):
        if i == AU and v > 0 and 'aucon' in FAILS and self.fail_cycle('aucon'):
            v *= 0.5
        self.con[i] = min(1.0, self.con[i] + v)

    def later(self, dt, fn):
        self.due.append((time.time() + dt, fn))

    def step_ok(self, who, gap):
        now = time.time()
        if now - self.last_step[who] < gap:
            return False
        self.last_step[who] = now
        return True

    def in_majesty(self, now=None):
        now = time.time() if now is None else now
        return self.majesty_from is not None and self.majesty_from <= now < self.majesty_until

    def prompt(self):
        return BREAK and self.hud_visible() and self.break_ready

    # ---------- 读屏替身 ----------
    def forte_lit(self):
        """准星旁指示: 日 = 战势满(俯首之刻里 = 9 次烈阳打完); 守 = 回路满。"""
        if 'forteblind' in FAILS:
            return False
        if self.on_field == AU:
            if self.in_majesty():
                return self.lieyang >= 9
            return self.zs >= 100 and self.hud_visible()
        if self.on_field == SK:
            return self.sk_data >= 6 and self.hud_visible()
        return False

    def au_energy_ok(self):
        return self.au_short == 0

    def zs_raw(self):
        """日的战势条 -> (紫格数, 白格数, 高条占比, 最后两格紫)。满了: 粉白条, 高条每 0.12s 一跳、每三跳有一跳是低的。"""
        if 'zsblind' in FAILS or self.on_field != AU:
            return None
        if self.zs >= 100:
            return 0, 34, (0.0 if int(time.time() / 0.12) % 3 == 0 else 0.5), False
        ticks = int(self.zs * 34 / 100)
        return ticks, 0, 0.0, False

    def iu_form(self):
        """尤诺左下那格: 有月相时环 = 长枪 / 弓 = 星球; 至臻的完满图标盖住时读不出。"""
        if 'formblind' in FAILS or self.on_field != IU or not self.hud_visible() or self.iu_heavy_icon():
            return '?'
        if time.time() >= self.moon_until:
            return '?'
        return 'bow' if self.form == 'bow' else 'ring'

    def iu_jump_icon(self):
        # 内置模板 iuno_jump 长枪(环)和星球(弓)都认
        return (self.on_field == IU and self.hud_visible() and time.time() < self.moon_until
                and not self.iu_heavy_icon() and 'jumpblind' not in FAILS)

    def iu_heavy_icon(self):
        return (self.on_field == IU and self.hud_visible() and self.heavy_icon_at is not None
                and time.time() >= self.heavy_icon_at and 'heavyblind' not in FAILS)

    # ---------- 时间推进 ----------
    def tick(self):
        now = time.time()
        if self.pending and now >= self.pending[1]:
            self.arrive(self.pending[0], now)
        for item in list(self.due):
            if now >= item[0]:
                self.due.remove(item)
                item[1]()
        if self.bow_charges < 2 and now >= self.bow_recharge_at:
            self.bow_charges += 1
            self.bow_recharge_at = now + 10.0 if self.bow_charges < 2 else 0.0
        if self.majesty_from is not None and now >= self.majesty_until:
            self.final_strike(auto=True)
        if self.holding:
            self.hold_tick(now)

    def arrive(self, dst, now):
        src = self.on_field
        self.pending = None
        intro = self.con[src] >= 1.0
        if intro:
            self.con[src] = 0.0
        # 日的延奏: 下一个上场的人 14s 内变奏出去 -> 日 +1 威慑; 离场就没了
        if src == self.outro_holder:
            if intro and now < self.outro_until and not self.fail_cycle('ws1'):
                self.ws = min(2, self.ws + 1)
                rec(f'日+1威慑(延奏监听, {self.ws}层)')
            self.outro_holder = None
        if src == AU and intro:
            self.outro_holder = dst
            self.outro_until = now + 14.0
        self.on_field = dst
        self.arrived_at = now
        self.lock_until = now               # 上一个人的动作锁跟着她走
        self.avail_at[src] = now + 1.0
        self.holding = False
        self.au_stage = None
        rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
        if dst == AU:
            self.e_step = 0
            if intro:
                self.au_intro_at = now
                self.lock(68 * F)
                self.no_switch_until = max(self.no_switch_until, now + 68 * F)
                self.zs = min(650.0, self.zs + 100)     # 文本上限 100, 实际上限 650
                self.qb = min(100.0, self.qb + 20)
                self.add_con(AU, 0.05)
                self.break_ready = BREAK
                if 'aulow' in FAILS and ('aulow', self.cycle) not in self.fail_used:
                    self.fail_used.add(('aulow', self.cycle))
                    self.au_short = 8
                    rec('!!故意失败:aulow(日 R 能量差 8 下普攻)')
        elif dst == IU:
            if intro:
                self.lock(78 * F)
                self.no_switch_until = max(self.no_switch_until, now + 78 * F)
                if now >= self.moon_until:
                    self.gz_unlock_until = now + 5.0
                if self.fail_once('iulow'):
                    self.iu_short = True
                if self.fail_once('iulate'):
                    self.iu_short = True
                    self.iu_late = True
                if self.fail_once('iulow2'):
                    self.iu_short = True
                    self.iu_short_a = 12
                if self.fail_once('iurcd'):
                    self.r_cd[IU] = max(self.r_cd[IU], now + 1.0)
            self.form = 'ring' if now >= self.moon_until else self.form
            self.bow_e_count = 0
            self.heavy_icon_at = None
        elif dst == SK:
            self.sk_step = 0
            if intro:
                if self.fail_cycle('skanim'):
                    self.hide(1.2)
                self.lock(86 * F)
                self.no_switch_until = max(self.no_switch_until, now + 86 * F)

    def hold_tick(self, now):
        who = self.on_field
        held = now - self.hold_at
        if held < 0:
            return
        if who == AU:
            if self.in_majesty() or self.majesty_from is not None or self.hold_ignored:
                return
            if self.au_stage is None and held >= 0.30 and not self.busy():
                if self.zs >= 100 and now >= self.air_until:
                    self.zs -= 100
                    self.qb = min(100.0, self.qb + 1)
                    self.au_stage = 'back'
                    self.back_at = now
                    self.back_end = now + 46 * F
                    self.permit = (now + 16 * F, now + 240 * F)
                    self.lock(26 * F)
                    self.add_con(AU, 0.02)
                    rec('日后撤(战势清空, 旋切许可)')
                elif now >= self.back_end:
                    self.au_stage = 'mt'
                    self.mt_at = now
                    self.mt_derive = now + 41 * F
                    self.mt_hits = 0
                    if self.permit[0] <= now < self.permit[1]:
                        self.permit = (0.0, 0.0)
                        self.recover_until = now + 2.0
                    rec(f'日鸣铁(战势{self.zs:.0f})')
            elif self.au_stage == 'mt' and self.mt_hits < 3 and now >= self.mt_at + MT_HIT_F[self.mt_hits] * F:
                self.mt_hit()
        elif who == SK:
            if self.au_stage is None and held >= 0.30 and not self.busy():
                self.au_stage = 'sk'
                if self.sk_data >= 6:
                    self.sk_data -= 6
                    self.lock(0.50)             # 86F 收招, 但 Q/R/切人 0.3s 后就能断
                    self.add_con(SK, 0.06)
                    rec('守强化重击')
                else:
                    self.lock(1.0)
                    rec(f'!!守化蝶(回路没满, 数据{self.sk_data})')
        elif who == IU:
            if self.au_stage is None and held >= 0.20 and not self.busy():
                self.au_stage = 'iu'
                if self.heavy_icon_at is not None and now >= self.heavy_icon_at:
                    self.heavy_icon_at = None
                    self.lock(1.0)
                    self.con[IU] = 0.85 if self.fail_cycle('iucon') else 1.0
                    rec('月重击(至臻的完满)')
                else:
                    rec('!!月普通重击')

    def mt_hit(self):
        if self.on_field != AU or self.mt_hits >= 3:
            return
        self.mt_hits += 1
        self.zs = min(650.0, self.zs + 17.54)
        self.qb = min(100.0, self.qb + 1.13)
        self.add_con(AU, 0.02)
        if self.mt_hits == 3:
            rec(f'日鸣铁打完(战势{self.zs:.0f}, 权柄{self.qb:.0f})')

    def release(self, held):
        """松开左键(按住超过 0.25s 的)。"""
        now = time.time()
        if self.on_field == AU and self.majesty_from is None:
            if self.hold_ignored:
                self.hold_ignored = False
                rec(f'!!日长按{held:.2f}s 没被认(按在旋切里), 松手出 A1')
                self.au_stage = None
                self.tap()
                return
            # 9-03 实机: 指示暗(= 后撤扣条)后 0.3s 松手就带出旋切, 一暗就松只有后撤
            if self.au_stage == 'back' and now - self.back_at >= 0.28 and self.permit[0] <= now < self.permit[1]:
                self.spin('松手晚自动')
            elif self.au_stage == 'mt' and self.zs >= 100:
                self.rise()
            elif self.au_stage == 'mt':
                # 松了鸣铁照样打完
                for k in range(self.mt_hits, 3):
                    self.later(max(0.0, self.mt_at + MT_HIT_F[k] * F - now), self.mt_hit)
        self.au_stage = None

    def spin(self, how='', at=None):
        """at: 排队出的旋切按排好的那一刻算(later 的粒度有 0.1s)。"""
        if 'zsover' in FAILS and time.time() - self.au_intro_at < 4.0 and self.fail_once('zsover'):
            self.zs = max(self.zs, 100.0)
        self.permit = (0.0, 0.0)
        self.recover_until = 0.0
        self.qb = min(100.0, self.qb + 9.92)
        dur = 1.20 if 'spinlong' in FAILS and self.fail_cycle('spinlong') else 54 * F
        t0 = time.time() if at is None else at
        self.lock_until = max(self.lock_until, t0 + dur)    # 旋切 派生54F
        self.spin_until = t0 + dur
        self.add_con(AU, 0.08)
        rec(f'日旋切{"(" + how + ")" if how else ""}(权柄{self.qb:.0f})')

    def rise(self):
        self.zs = max(0.0, self.zs - 100)
        self.qb = min(100.0, self.qb + 7.64)
        self.lock(43 * F)
        self.air_until = time.time() + 1.0
        self.no_switch_until = max(self.no_switch_until, self.air_until)
        self.add_con(AU, 0.08)
        rec(f'日升拳(权柄{self.qb:.0f})')
        if self.au_short > 0:
            self.au_short = max(0, self.au_short - 2)

    def final_strike(self, auto=False):
        now = time.time()
        if self.majesty_from is not None:
            # 俯首之刻提前结束: 它藏 HUD 的那一段也到此为止
            self.hud_hidden = [(x, min(y, now)) if (x, y) == (self.majesty_from, self.majesty_until) else (x, y)
                               for x, y in self.hud_hidden]
        self.majesty_from = None
        self.lieyang = 0
        self.hud_hidden.append((time.time(), time.time() + 195 * F))
        self.lock(220 * F)
        self.no_switch_until = time.time() + 220 * F
        self.add_con(AU, 0.10)
        rec(f'日不朽者之肃{"(俯首之刻到时自动)" if auto else ""}')

    # ---------- 输入 ----------
    def press(self, key, down_time):
        now = time.time()
        self.tick()
        who = self.on_field
        if key == 'f':
            if not self.prompt() or self.busy():
                return
            self.break_ready = False
            self.lock(94 * F)
            rec(f'{NAMES[who]}处决')
            return
        if key in ('1', '2', '3'):
            dst = int(key) - 1
            if dst == self.on_field or self.pending:
                return
            if now < self.no_switch_until or not self.hud_visible() or now < self.avail_at[dst]:
                return
            if dst == IU and getattr(self, 'sw_block_until', 0) > now:
                return
            if dst == IU and self.fail_once('sw'):
                self.sw_block_until = now + 2.8
                return
            self.pending = (dst, now + 0.08)
            return
        if key == 'space':
            self.jump()
            return
        if who == AU and self.majesty_from is not None:
            return                          # 俯首之刻里只有烈阳/不朽者之肃/闪避
        if not self.hud_visible():
            return
        if key == 'r' and who == AU and self.r1_at is not None and now < self.r1_at + 2.0:
            return                          # R1 里短按 R 没用(长按走 press_long)
        # 告终 E 的动作里 Q 照样能放(18:24 实机 补E_ok 后 0.12s 就 Q_ok)
        if self.busy() and not (key == 'q' and who == IU and now < self.gz_lock_until):
            return
        if key == 'e':
            self.press_e(who, now)
        elif key == 'q':
            if now >= self.q_cd[who] and self.swallowed('qeat', 'q', who, 1.5):
                return
            if now >= self.q_cd[who]:
                self.q_cd[who] = now + 20.0
                self.lock(0.3)
                self.add_con(who, 0.05)
                if who == AU and self.au_short > 0:
                    self.au_short = 0
                rec(f'{NAMES[who]}Q')
        elif key == 'r':
            self.press_r(who, now)

    def press_e(self, who, now):
        if who == AU:
            if self.qb >= 100 or self.e_step > 0:
                self.e_step += 1
                name, dur = {1: ('迅击', 0.77), 2: ('跃空', 0.73), 3: ('落袭', 1.23)}[self.e_step]
                self.lock(dur)
                self.add_con(AU, 0.08)
                if self.e_step == 3:
                    self.qb = 0.0
                    self.ws = min(2, self.ws + 1)
                    self.e_step = 0
                    rec(f'日E4落袭(权柄清空, 威慑{self.ws}层)')
                else:
                    rec(f'日E{self.e_step + 1}{name}')
            elif now >= self.e_cd[AU]:
                self.e_cd[AU] = now + 15.0
                self.qb = min(100.0, self.qb + 10)
                self.lock(37 * F)
                self.add_con(AU, 0.10)
                rec(f'日E1斩锋(权柄{self.qb:.0f})')

                def full():
                    self.zs = min(650.0, self.zs + 101.55)
                self.later(37 * F, full)
        elif who == IU:
            if now < self.moon_until and self.form == 'bow':
                if self.bow_charges <= 0:
                    return
                self.bow_charges -= 1
                if self.bow_recharge_at == 0.0:
                    self.bow_recharge_at = now + 10.0
                self.bow_e_count += 1
                self.lock(80 * F)
                n = self.bow_e_count
                rec(f'月弓E#{n}(越限弦引)')

                def arrow():
                    self.add_con(IU, 0.45)
                    if n >= 2 and self.con[IU] >= 0.85:
                        self.con[IU] = 0.95
                        self.heavy_icon_at = time.time() + 0.15
                self.later(50 * F, arrow)
            elif now < self.gz_unlock_until and now >= self.e_cd[IU]:
                self.gz_unlock_until = 0.0
                self.e_cd[IU] = now + 8.0
                self.moon_until = now + 15.0
                self.gz_lock_until = now + 98 * F
                if self.iu_late:
                    self.iu_late = False
                    self.later(1.2, lambda: (setattr(self, 'iu_short', False), rec('月告终出伤打完(R能量够了)')))
                else:
                    self.iu_short = False
                self.iu_short_a = max(0, self.iu_short_a - 4)
                self.lock(98 * F)
                self.add_con(IU, 0.08)
                rec('月E告终的喧响(月相 15s)')
            elif now >= self.e_cd[IU]:
                self.gz_unlock_until = now + 5.0
                self.lock(0.6)
                self.add_con(IU, 0.06)
                rec('月E原初的律动(告终解锁 5s)')
        elif who == SK:
            if now >= self.e_cd[SK]:
                self.e_cd[SK] = now + 15.0
                self.lock(40 * F)
                self.sk_step = 1            # E 之后短按直接出 A2
                self.sk_data = min(9, self.sk_data + 1)
                self.add_con(SK, 0.20)
                rec('守E')

    def press_r(self, who, now):
        if now < self.r_cd[who]:
            return
        if self.swallowed('r1eat', 'r', who, 1.5) if who == AU else False:
            return
        if who == AU:
            if not self.au_energy_ok() or (self.ws >= 2 and self.fail_cycle('r1dead')):
                return
            self.r_cd[AU] = now + 25.0
            self.r1_at = now
            self.qb = min(100.0, self.qb + 40)
            self.lock(2.0)
            self.no_switch_until = max(self.no_switch_until, now + 0.30)     # 视频 R1 后 0.6s 就切走了
            self.add_con(AU, 0.10)
            rec(f'日R1誓锋不殒(权柄{self.qb:.0f}, 威慑{self.ws}层)')
            if 'hudlost' in FAILS and self.cycle == 1:
                self.hud_dead = True
                rec('!!关卡结束(HUD 没了)')
        elif who == IU:
            if self.iu_short or self.iu_short_a > 0:
                return
            self.r_cd[IU] = now + 25.0
            self.hide(234 * F, flick=(1.5,))
            self.lock(239 * F)
            self.moon_until = now + 15.0
            was_bow = self.form == 'bow'
            self.form = 'ring' if not was_bow or 'iubowflip' in FAILS else 'bow'
            self.add_con(IU, 0.10)
            rec(f'月R(月相 15s{", 在弓形态放的" if was_bow else ""}{", 演完还是弓" if self.form == "bow" else ""})')
        elif who == SK:
            self.r_cd[SK] = now + 25.0
            self.hide(2.5, flick=(0.8,))
            self.later(2.4, lambda: self.add_con(SK, 0.40 if 'skcon' in FAILS and self.fail_cycle('skcon') else 1.0))
            rec('守R')

    def press_long(self, key, down_at, dur):
        """长按 R 松开。"""
        now = time.time()
        self.tick()
        if key != 'r' or self.on_field != AU or not self.hud_visible():
            return
        if self.ws < 2 or dur < 0.5 or self.majesty_from is not None:
            rec(f'!!日长按R没用(威慑{self.ws}, {dur:.2f}s)')
            return
        # 长按 ~0.46s 才认出来(18:24 录屏), 认出来那一刻还在 R1 横劈(81F)之前 = 吞了尾刀; 派生88F 之前认出的排到 88F
        t2 = down_at + 0.46
        if self.r1_at is not None and t2 < self.r1_at + 81 * F and now < self.r1_at + 2.5:
            rec('!!日长按R太早(吞了R1尾刀)')
            return
        if self.r1_at is not None:
            t2 = max(t2, self.r1_at + 88 * F)
        if self.fail_once('r2miss'):
            return
        self.ws = 0
        self.r1_at = None
        self.hud_hidden.append((t2, t2 + 131 * F))
        self.lock_until = max(self.lock_until, t2 + 131 * F)
        self.majesty_from = t2 + 131 * F
        self.majesty_until = self.majesty_from + 7.0
        self.hud_hidden.append((self.majesty_from, self.majesty_until))
        self.no_switch_until = max(self.no_switch_until, self.majesty_until + 220 * F)
        self.add_con(AU, 0.05)
        rec('日R2赫日威临(俯首之刻 7s)')

    def jump(self):
        now = time.time()
        if self.on_field == AU:
            if not self.hud_visible() or self.majesty_from is not None:
                return
            if now < self.air_until:
                rec('!!日升拳里又跳了(打断升拳)')
                return
            if self.busy():
                return
            if self.zs >= 100:
                self.rise()
            else:
                self.air_until = now + 0.8
                rec(f'!!日跳了(战势{self.zs:.0f} 不满, 没出升拳)')
            return
        if self.on_field != IU or not self.hud_visible() or self.busy():
            return
        if self.fail_once('jumpeat'):
            return
        if now < self.moon_until:
            if self.form == 'ring':
                self.form = 'bow'
                self.jump_at = now
                self.lock(103 * F)
                rec('月跳(换弓)')
            else:
                self.form = 'ring'
                rec('!!月又跳了一下(换回环)')

    def dodge(self):
        now = time.time()
        rec(f'闪避({NAMES[self.on_field]})')
        if self.on_field == IU and self.hud_visible():
            if now - self.jump_at < 0.40:
                self.lock_until = now + 0.10    # 闪掉变弓动画
            elif self.bow_e_count and now >= self.lock_until - 30 * F:
                self.lock_until = now + 0.20    # 闪避断弓 E 后摇

    def tap(self):
        self.tick()
        who = self.on_field
        now = time.time()
        if who == AU and self.in_majesty():
            if now < self.lock_until or not self.step_ok(AU, 0.10):
                return
            self.lieyang += 1
            self.add_con(AU, 0.01)
            if self.lieyang >= 10:
                self.final_strike()
            elif self.lieyang == 9:
                self.lock(0.40)             # 第 9 下烈阳还在出手, 紧跟着的那一下点被丢(10-18 14:52 实机)
                rec('日烈阳 x9(指示亮)')
            return
        if not self.hud_visible() or self.busy():
            return
        if who == AU:
            if self.majesty_from is not None:
                return
            if self.permit[0] <= now < self.permit[1] or now < self.recover_until:
                how = '补施放' if now < self.recover_until else ''
                if now < self.mt_derive:
                    # 鸣铁还没到派生, 旋切排到派生那一刻(18:24 录屏: 鸣铁起手前点的旋切在 41F 才出)
                    self.permit = (0.0, 0.0)
                    self.recover_until = 0.0
                    self.later(self.mt_derive - now,
                               lambda d=self.mt_derive: self.on_field == AU and self.spin(how + '(鸣铁派生)', at=d))
                    return
                self.spin(how)
                return
            if not self.step_ok(AU, 0.12):
                return
            self.zs = min(650.0, self.zs + 15.23)
            self.qb = min(100.0, self.qb + 1.5)
            self.add_con(AU, 0.02)
            if self.au_short > 0:
                self.au_short -= 1
                if self.au_short == 0:
                    rec('日R能量满了')
        elif who == IU:
            if not self.step_ok(IU, 0.30):
                return
            self.add_con(IU, 0.04 if self.form == 'bow' else 0.03)
            if not self.iu_short and self.iu_short_a > 0:
                # 弓 A 回能量比地面环 A 快(帧表: 弓 A 一串 9.4/2.6s, 环 A 7/2.8s)
                self.iu_short_a = max(0, self.iu_short_a - (2 if now < self.moon_until and self.form == 'bow' else 1))
                if self.iu_short_a == 0:
                    rec('月R能量满了')
        elif who == SK:
            if now - self.sk_last_a < 0.35:
                return
            if now - self.sk_last_a > 1.2 and self.sk_step != 1:
                self.sk_step = 0
            self.sk_last_a = now
            self.sk_step = self.sk_step % 3 + 1
            self.sk_data = min(9, self.sk_data + (2 if self.sk_step == 3 else 1))
            self.add_con(SK, 0.04)


G = Game()


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor()
        self.chars = []
        self.key_config = {'Echo Key': 'q', 'Liberation Key': 'r', 'Resonance Key': 'e',
                           'Jump Key': 'space', 'Dodge Key': 'lshift'}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self._f_last_press = -1
        self.frame = None
        self.enabled = True
        self.can_break = False
        self._down_at = 0.0
        self._key_down = {}
        self.char_config = {}

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        key = str(key)
        end = time.time() + max(down_time, 0.003)
        G.press(key, down_time)
        left = end - time.time()
        if left > 0:
            time.sleep(left)

    def send_key_down(self, key, after_sleep=0):
        self._key_down[str(key)] = time.time()

    def send_key_up(self, key, after_sleep=0):
        key = str(key)
        at = self._key_down.pop(key, time.time())
        G.press_long(key, at, time.time() - at)

    def jump(self, after_sleep=0.01):
        G.press('space', 0.02)

    def mouse_down(self, *a, **kw):
        if kw.get('key') == 'right':
            G.dodge()
            return
        G.tick()
        if not G.holding:
            G.holding = True
            self._down_at = time.time()
            G.hold_at = max(time.time(), G.lock_until)     # 锁输入期间按下的, 从解锁那一刻算
            G.au_stage = None
            # 旋切派生(54F)之前按下的长按整段不认(10-18 15:12 实机)
            G.hold_ignored = G.on_field == AU and time.time() < G.spin_until and G.majesty_from is None
        time.sleep(0.002)

    def mouse_up(self, *a, **kw):
        if kw.get('key') == 'right':
            return
        held = time.time() - self._down_at
        if G.holding and held < 0.25:
            G.holding = False
            G.hold_ignored = False
            G.tap()
        elif G.holding:
            G.tick()
            G.holding = False
            G.release(held)
        G.tick()
        G.holding = False

    def next_frame(self):
        time.sleep(0.03)
        G.tick()

    def in_team(self):
        return (True, G.on_field, 3) if G.hud_visible() else (False, -1, 0)

    def get_current_con(self):
        G.tick()
        return G.con[G.on_field]

    def get_cd(self, name, index=None):
        who = G.on_field if index is None else index
        now = time.time()
        if name == 'resonance':
            if who == IU and now < G.moon_until and G.form == 'bow' and G.bow_charges < 2:
                if 'echain' in FAILS and who == AU:
                    return -1.0
                return max(0.0, G.bow_recharge_at - now) or -1.0
            if who == AU and 'echain' in FAILS:
                return -1.0
            left = G.e_cd[who] - now
        elif name == 'liberation':
            if who == AU and G.ws >= 2:
                return -1.0                         # 10-18 实机: 2 层威慑时 R 键是赫日威临可用, 不显示 R1 的冷却
            left = G.r_cd[who] - now
        elif name == 'echo':
            left = G.q_cd[who] - now
        else:
            return -1.0
        return left if left > 0 else -1.0          # 框架: 没 CD 返回负数

    def has_cd(self, name, index=None):
        return self.get_cd(name, index) > 0.2

    def available(self, name, check_color=True, check_cd=True):
        who = G.on_field
        now = time.time()
        if not G.hud_visible():
            return False
        if name == 'liberation':
            if who == IU and (G.iu_short or G.iu_short_a > 0):
                return False
            return now >= G.r_cd[who]
        if name == 'resonance':
            if who == IU and now < G.moon_until and G.form == 'bow':
                return G.bow_charges > 0
            return G.e_cd[who] <= now
        if name == 'echo':
            return G.q_cd[who] <= now
        return False

    def find_feature(self, name, box=None, threshold=0.0, **kw):
        if name == 'iuno_jump':
            return [Hit(0.9)] if G.iu_jump_icon() else []
        if name == 'iuno_heavy':
            return [Hit(0.9)] if G.iu_heavy_icon() else []
        return []

    def find_one(self, name, threshold=0.0, **kw):
        name = str(name)
        if name == 'f_break':
            return Hit(0.95) if G.prompt() else None
        if not G.hud_visible() or G.on_field != AU:
            return None
        if name == 'Augusta_lib2':
            return Hit(0.80 if G.ws >= 2 else 0.55)
        if name == 'Augusta_lib1':
            return Hit(0.55 if G.ws >= 2 else 0.80)
        return None

    def find_mouse_forte(self):
        return G.forte_lit()

    def add_freeze_duration(self, *a, **kw):
        pass

    def box_of_screen(self, *a, **kw):
        return None

    def is_pick_f(self):
        return False

    def wait_until(self, *a, **kw):
        return True

    def get_resonance_key(self):
        return 'e'

    def get_echo_key(self):
        return 'q'

    def get_liberation_key(self):
        return 'r'


task = FakeTask()

from src.char.BaseChar import CharType  # noqa: E402
from src.char import CustomCharLoader as L  # noqa: E402
from src.char.Augusta import Augusta as NAugusta  # noqa: E402
from src.char.Iuno import Iuno as NIuno  # noqa: E402
from src.char.ShoreKeeper import ShoreKeeper as NShoreKeeper  # noqa: E402

team = ('Augusta', 'Iuno', 'ShoreKeeper')
CAu = L.load_team_char_class(NAugusta, team)
CIu = L.load_team_char_class(NIuno, team)
CSk = L.load_team_char_class(NShoreKeeper, team)
assert CAu is not NAugusta and CIu is not NIuno and CSk is not NShoreKeeper, 'custom team code not loaded'


class Logger:
    def info(self, m):
        rec(f'[info] {m}')

    def warning(self, m):
        rec(f'[WARN] {m}')

    def debug(self, m):
        pass

    def error(self, m):
        rec(f'[ERR] {m}')


def build(cls, index, name, char_type):
    c = object.__new__(cls)
    c.task = task
    c.index = index
    c.char_name = name
    c.logger = Logger()
    c._char_type = char_type

    c.is_current_char = index == AU
    c.has_intro = False
    c.has_sub_dps_intro = False
    c.current_con = 0
    c.last_switch_in_time = 0
    c.last_switch_time = 0
    c.last_outro_time = 0
    c._buff_time = 0
    c.last_buff_time = 0
    c.intro_motion_freeze_duration = 0.9
    c.check_f_on_switch = False
    c.last_res = c.last_liberation = c.last_echo = 0
    c._resonance_available = c._echo_available = c._liberation_available = False
    c.freeze_durations = []
    c.ring_index = -1
    c._last_known_form = -1
    if cls is CAu:
        c.__dict__.update(dict(opener_armed=True, last_opener=-1, macro_t0=0.0, macro_marks=[], cur=None,
                               holding_attack=False, si_holding=False, switch_press_at=0.0, lib_press_at=0.0,
                               loop_count=0, sk_intro_done_at=0.0, sk_arrived_at=0.0, aug_energy_short=False,
                               _con_soft_n=0, _con_soft_frame=None, hud_seen_at=0.0, hud_watch=False,
                               drive_log_at=0.0, timeline_logged=True))
    return c


sk = build(CSk, SK, 'char_shorekeeper', CharType.HEALER)
iu = build(CIu, IU, 'char_iuno', CharType.SUB_DPS)
au = build(CAu, AU, 'char_augusta', CharType.MAIN_DPS)
task.chars = [sk, iu, au]
for c in task.chars:
    c.add_freeze_duration = lambda *a, **kw: None
    c.time_elapsed_accounting_for_freeze = lambda start, *a, **kw: time.time() - start
    c.switch_out = types.MethodType(lambda self, con_full=False: setattr(self, 'is_current_char', False), c)
au.game_foreground = lambda: False
# 日的 R 图标: 框架读 R 框亮度(能量) + 比 lib1/lib2 两张图
au.liberation_available = lambda check_color=True: (G.on_field == AU and G.hud_visible() and G.au_energy_ok()
                                                    and time.time() >= G.r_cd[AU] and G.ws < 2)
au.check_majesty = lambda: G.on_field == AU and G.hud_visible() and G.ws >= 2   # 赫日威临不吃能量
au.current_liberation = lambda: 0.3 if (au.liberation_available() or au.check_majesty()) else 0.0
au.break_prompt = G.prompt
au.aug_zs_raw = G.zs_raw
au.iu_form = G.iu_form

ok = True
try:
    G.cycle = 0
    rec('==== 启动轴 ====')
    au.drive(au)
    for n in range(DRY_LOOPS):
        G.cycle = n + 1
        rec(f'==== 循环 #{n + 1} ====')
        cur = next((c for c in task.chars if c.index == G.on_field), au)
        au.drive(cur)
        if G.hud_dead:
            break
except Exception:
    import traceback
    ok = False
    rec('!!EXCEPTION ' + traceback.format_exc())
for t, w in LOG:
    print(f'{t:7.2f}  {w}')

# 每一棒的墙钟(到场 -> 下一次到场), 跟视频对表用
arrivals = [(t, w) for t, w in LOG if w.startswith('到场')]
print('\n---- 每一棒 ----')
for (t, w), (t2, _) in zip(arrivals, arrivals[1:] + [(LOG[-1][0], '')]):
    print(f'{t:7.2f}  {w[2:]:<10} {t2 - t:5.2f}s')
bad = [w for _, w in LOG if w.startswith('!!') and not w.startswith('!!故意失败')]
print(f'\n异常动作 {len(bad)} 处' + (': ' + ' | '.join(bad[:12]) if bad else ''))
print('OK' if ok else 'FAILED')
