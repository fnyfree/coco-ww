"""嘉露莫轴的干跑: 假 task + 假游戏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是等到了判据, 还是撞上限出来的
  - 演出/锁输入按帧表(嘉R 3.35 / 露R 藏HUD 2.2、201F 不响应 / 莫R 4.8; 变奏 嘉 44F / 露 60F、强化 141F / 莫 80F)
  - 嘉 E3 之前罪火够不够 100(启动轴 两次重击+变奏+1A, 循环 上一圈收尾重击+中间那下+变奏+1A)
用法: 放在 <working>/configs/custom_teams/Galbrena__Lupa__Mornye/ 里(跟三个 .py 一起), 用 ok-ww 自带 python 跑:
      <ok-ww 安装目录>/data/apps/ok-ww/python/python.exe dryrun.py.tool
      working 目录从本文件位置往上自动找; 放在别处跑就设环境变量 OKWW_WORKING=<...>/data/apps/ok-ww/working
环境变量:
  DRY_LOOPS=2            循环几圈
  DRY_BREAK=0            嘉贝变奏入场时怪身上有处决提示
  DRY_FAIL=a,b,...       故意失败, 测保底分支(带 * 的整圈都失败, 其余每圈触发一次):
                         sw         切露帕第一下被拒 2.8s(被击飞/硬直)
                         massblind* 莫宁静质量能一直读不到(撞 MOR_GND_CAP 照按住)
                         rise       莫宁第一次按住被打断(没出位势转换, 撞 MOR_RISE_CAP)
                         morr*      莫宁 R 没能量(NOFIRE, 照放 E)
                         forteblind* 莫宁相对动能一直读不到(出炮撞 V6_CANNON_CAP)
                         morcon*    莫宁出炮后协奏涨得慢(撞 V6_CON_CAP, 露帕不是变奏进来)
                         echocd*    莫宁 Q 在 CD 上(没见过亮)
                         lur*       露帕 R 没能量、普攻也攒不起来(攒到 LU_R_FILL_CAP, 没破敌/没沸腾, 点 1.5s 就切)
                         lurlow     露帕变奏进来 R 能量差一截(落地点 8 下普攻才满, 要攒满再放)
                         nowolf*    露帕空中第 3 段没出焰袭(只有 1 个狼魂, 图标不亮)
                         wolfblind* 两个狼魂的图标一直读不到
                         eeeat      露帕强化E 第一下被吞(图标还亮, 要接着按)
                         lucon*     露帕强化E 回的协奏差一截(要补普攻)
                         e3dark*    嘉贝罪火不涨(强化E 一直不亮, 撞 FORTE_WAIT 不按)
                         e3eat      嘉贝强化E 第一下被吞(外圈还亮着, 要再按)
                         e3ring*    嘉贝 E 图标外圈读不到(只靠回路条, 按一次不确认)
                         gar*       嘉贝 R 没能量
                         gacon*     嘉贝这一圈不回协奏(莫宁不是变奏进来, 下一圈走地面路线)
                         hudlost    循环 #1 嘉贝 R 之后关卡结束(HUD 再也不回来)
                         brkhide    嘉贝变奏入场后 1.36s 内中间的处决提示读不到(10-16 实机, 外圈一直满着)
                         brkeat     第一下处决 F 被吞(提示还在, 要再按)
                         rswallow   每个人这一圈第一次按 R 起 4s 内的 R 全被吞(被打断, 图标还亮, 要补一轮)
                         qeat       每个人这一圈第一次按 Q 起 1.5s 内的 Q 全被吞
                         moreat     莫宁这一圈第一次按 E 起 1.6s 内的 E 全被吞
                         holdcut    莫宁 E 后那次按住被打断(按着也不出观测普攻, 要松开重按)
                         podcut     露帕破敌的普攻到 R 后 4.6s 才吃得进(被打断)
                         lubarblind* 露帕狼焰条读不到(破敌退回计时)
                         nopaw*     露帕焰袭后被打断, 短按锐爪的窗口没了(要长按重击补狼魂)
                         lukb       露帕焰袭落地后被击倒 5s(露1 补不到, 留给露2 补)
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
    sys.exit('找不到 ok-ww 的 working 目录: 把本文件放进 <working>/configs/custom_teams/Galbrena__Lupa__Mornye/ 再跑, '
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
GA, LU, MO = 0, 1, 2           # 视频的编队: 1 嘉 / 2 露 / 3 莫
NAMES = ['嘉', '露', '莫']


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
        pass


class Game:
    def __init__(self):
        self.on_field = GA
        self.pending = None
        self.hud_hidden = []
        self.flickers = []
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.con = {GA: 0.0, LU: 0.0, MO: 0.0}
        self.e_cd = {GA: 0.0, LU: 0.0, MO: 0.0}
        self.r_cd = {GA: 0.0, LU: 0.0, MO: 0.0}
        self.q_cd = {GA: 0.0, LU: 0.0, MO: 0.0}
        self.avail_at = {GA: 0.0, LU: 0.0, MO: 0.0}   # 切走后多久才能再切回来
        self.last_step = {GA: 0.0, LU: 0.0, MO: 0.0}
        self.holding = False
        self.hold_at = 0.0
        self.hold_done = False
        self.intro_until = 0.0              # 变奏入场不响应输入(声骸除外)到这一刻
        self.cycle = 0
        self.fail_used = set()
        self.hud_dead = False
        self.arrived_at = 0.0
        self.due = []
        # 嘉
        self.zuihuo = 0
        self.demon = False
        self.break_ready = BREAK            # boss 头像外圈满着(10-16 实机: 一直满到有人处决)
        self.ring_until = 0.0               # 处决打完外圈才清空
        self.blocks = {}                    # (键, 谁) -> 被打断到这一刻
        self.hold_dead = False
        # 露
        self.lu_r_at = None
        self.podi_done = False
        self.boil_until = 0.0
        self.air = False
        self.air_step = 0
        self.air_last = 0.0
        self.yanxi = False
        self.ruizhua = False
        self.wolves = 0
        self.flame = 0                      # 狼焰
        self.lu_down_until = 0.0            # 露帕被击倒(只挡她自己的输入)
        self.lu_r_short = 0                 # 露帕 R 还差几下落地普攻才满(lurlow)
        # 莫
        self.mode = 'ground'
        self.mass = 0
        self.gnd_step = 0
        self.gnd_last = 0.0
        self.gnd_buffered = False
        self.forte = 0.0
        self.obs_last = 0.0
        self.cannons = 0

    def fail_once(self, name):
        if name not in FAILS or (name, self.cycle) in self.fail_used:
            return False
        self.fail_used.add((name, self.cycle))
        rec(f'!!故意失败:{name}')
        return True

    def fail_cycle(self, name):
        """整圈失败: 这一圈每次问都是 True, 只记一次。"""
        if name not in FAILS:
            return False
        if (name, self.cycle) not in self.fail_used:
            self.fail_used.add((name, self.cycle))
            rec(f'!!故意失败:{name}(整圈)')
        return True

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
        if i == GA and v > 0 and self.fail_cycle('gacon'):
            return
        self.con[i] = min(1.0, self.con[i] + v)

    def swallowed(self, knob, key, who, dur):
        """被打断: 这一圈这个人第一次按 key 起 dur 秒内的 key 全丢。"""
        now = time.time()
        if knob not in FAILS:
            return False
        tag = (knob, who, self.cycle)
        if tag not in self.fail_used:
            self.fail_used.add(tag)
            self.blocks[(key, who)] = now + dur
            rec(f'!!故意失败:{knob}({NAMES[who]} {key} 被吞 {dur:.1f}s)')
        return now < self.blocks.get((key, who), 0.0)

    def ring(self):
        return BREAK and self.hud_visible() and (self.break_ready or time.time() < self.ring_until)

    def prompt(self):
        if not (BREAK and self.hud_visible() and self.break_ready):
            return False
        if 'brkhide' in FAILS and self.on_field == GA and time.time() < self.arrived_at + 1.36:
            return False
        return True

    def later(self, dt, fn):
        self.due.append((time.time() + dt, fn))

    def step_ok(self, who, gap):
        now = time.time()
        if now - self.last_step[who] < gap:
            return False
        self.last_step[who] = now
        return True

    # ---------- 时间推进 ----------
    def tick(self):
        now = time.time()
        if self.pending and now >= self.pending[1]:
            self.arrive(self.pending[0], now)
        for item in list(self.due):
            if now >= item[0]:
                self.due.remove(item)
                item[1]()
        if self.on_field == MO and self.mode == 'ground' and self.gnd_buffered and not self.busy():
            self.mor_ground_step(now, buffered=True)
        if self.holding:
            self.hold_tick(now)

    def arrive(self, dst, now):
        src = self.on_field
        self.pending = None
        intro = self.con[src] >= 1.0
        if intro:
            self.con[src] = 0.0
        self.on_field = dst
        self.arrived_at = now
        self.avail_at[src] = now + 1.0
        self.holding = False
        # 走的人
        if src == MO:
            self.mode = 'ground'            # 场下落地
        if src == LU:
            self.air = False
        if src == GA:
            self.demon = False              # 恶魔位格跟着这一棒走
        strong = dst == LU and intro and self.cycle > 0     # 循环里莫 -> 露 是强化变奏(追猎 3 层)
        rec(f'到场{NAMES[dst]}{"(强化变奏)" if strong else "(变奏)" if intro else ""}')
        if intro:
            self.intro_until = now + {GA: 44 / 60, LU: 141 / 60 if strong else 60 / 60, MO: 80 / 60}[dst]
        if dst == GA:
            if intro:
                self.lock(44 / 60)
                self.zuihuo = min(100, self.zuihuo + (0 if self.fail_cycle('e3dark') else 10))
                self.add_con(GA, 0.05)
                self.break_ready = BREAK or self.break_ready
        elif dst == LU:
            if intro:
                self.lock(141 / 60 if strong else 60 / 60)
                self.no_switch_until = max(self.no_switch_until, now + (141 / 60 if strong else 0.5))
                self.add_con(LU, 0.10)
        elif dst == MO:
            self.gnd_step = 0
            self.gnd_buffered = False
            if intro:
                self.lock(80 / 60)
                self.add_con(MO, 0.10)

                def to_air():
                    if self.on_field == MO:
                        self.mode = 'air'
                        self.obs_last = time.time()
                        rec('莫变奏进观测模式')
                self.later(69 / 60, to_air)
            else:
                self.mode = 'ground'

    def hold_tick(self, now):
        who = self.on_field
        held = now - self.hold_at
        if held < 0:
            return
        if who == GA and not self.hold_done and held >= 0.75:
            # 重击3 起手(45F)之后松手/切走也会自己打完, 子弹第 75F 脱手
            self.hold_done = True
            at = self.hold_at + 1.25

            def bullet():
                self.zuihuo = min(100, self.zuihuo + (0 if self.fail_cycle('e3dark') else 43))
                self.add_con(GA, 0.20)
                rec(f'嘉重击3段子弹(罪火{self.zuihuo})')
            self.due.append((at, bullet))
        elif who == MO and self.mode == 'ground' and not self.hold_done and held >= 0.30 and not self.busy():
            self.hold_done = True
            if self.mass >= 100 and not self.fail_once('rise'):
                self.mass = 0
                self.lock(0.9)
                self.add_con(MO, 0.05)
                rec('莫位势转换(升空)')

                def to_air():
                    if self.on_field == MO:
                        self.mode = 'air'
                        self.obs_last = time.time()
                self.later(0.8, to_air)
            elif self.mass < 100:
                self.mass = min(100, self.mass + 20)
                rec(f'莫普通重击(静质量能{self.mass})')
        elif who == LU and not self.air and not self.hold_done and held >= 0.40 and not self.busy()                 and now >= self.lu_down_until:
            self.hold_done = True
            self.lock(1.0)
            if self.flame >= 50:
                self.flame -= 50
                kind = '锐爪' if self.wolves >= 1 else '狼咬'

                def got():
                    self.wolves = min(2, self.wolves + 1)
                    rec(f'露{kind}(长按, 狼魂{self.wolves})')
                self.later((10 if kind == '锐爪' else 17) / 60, got)
            else:
                rec(f'露普通重击(狼焰{self.flame})')
        elif who == MO and self.mode == 'air' and not self.busy() and not self.hold_dead:
            self.mor_obs_a(now)

    def mor_obs_a(self, now):
        """观测模式 A1~A3 循环(长按或点), 相对动能满了自动出重击·反演。"""
        if now - self.obs_last < 0.40:
            return
        self.obs_last = now
        self.forte = min(1.0, self.forte + 0.10)
        self.add_con(MO, 0.01 if (self.cannons and self.fail_cycle('morcon')) else 0.06 if self.cannons else 0.03)
        if self.forte >= 1.0:
            self.forte = 0.0
            self.cannons += 1
            self.lock(0.4)
            rec('莫重击·反演')
            self.later(0.3, lambda: self.add_con(MO, 0.25))

    def mor_ground_step(self, now, buffered=False):
        """地面 A1/A2/A3 第 1F 给 20/43/37 静质量能; A1 派生 21F、A2 派生 45F, 之前按的进输入缓存。"""
        if self.gnd_step > 0 and now - self.gnd_last > 1.5:
            self.gnd_step = 0
        gaps = {1: 21 / 60, 2: 45 / 60}
        if self.gnd_step in gaps and now < self.gnd_last + gaps[self.gnd_step]:
            self.gnd_buffered = True
            return
        if self.gnd_step >= 3:
            self.gnd_buffered = False
            return
        self.gnd_buffered = False
        gain = (20, 43, 37)[self.gnd_step]
        self.gnd_step += 1
        self.gnd_last = now
        self.mass = min(100, self.mass + gain)
        self.add_con(MO, 0.03)
        rec(f'莫地面A{self.gnd_step}{"(缓存)" if buffered else ""}(静质量能{self.mass})')

    # ---------- 输入 ----------
    def press(self, key, down_time):
        now = time.time()
        self.tick()
        if key == 'f':
            if not self.prompt():
                return                      # 没提示按 F 是空按
            if self.busy() or self.fail_once('brkeat'):
                return
            self.break_ready = False
            self.ring_until = now + 1.4
            self.lock(1.5)
            rec(f'{NAMES[self.on_field]}处决')
            return
        if key in ('1', '2', '3'):
            dst = int(key) - 1
            if dst == self.on_field or self.pending:
                return
            if now < self.no_switch_until or not self.hud_visible() or now < self.avail_at[dst]:
                return
            if dst == LU and getattr(self, 'sw_block_until', 0) > now:
                return
            if dst == LU and self.fail_once('sw'):
                self.sw_block_until = now + 2.8
                return
            self.pending = (dst, now + 0.08)
            return
        if key in ('space', 'w', 'lshift'):
            return
        if not self.hud_visible():
            return
        if self.busy() and not (key == 'q' and now < self.intro_until):
            return                          # 10-16 录屏: 嘉贝到场 0.1s 按的 Q 就进了冷却, 入场动作不挡声骸
        who = self.on_field
        if key == 'e':
            if who == GA:
                if self.zuihuo >= 100 and not self.demon:
                    if self.fail_once('e3eat'):
                        return
                    self.demon = True
                    self.zuihuo = 0
                    self.lock(29 / 60)
                    self.add_con(GA, 0.08)
                    rec('嘉强化E(恶翼扬升)')
                elif now >= self.e_cd[GA]:
                    self.e_cd[GA] = now + 10.0
                    rec('!!嘉E2-掠袭' if self.demon else f'!!嘉普通E(罪火{self.zuihuo}, E 进冷却)')
            elif who == LU:
                if self.wolves >= 2:
                    if self.fail_once('eeeat'):
                        return
                    self.wolves = 0
                    self.lock(120 / 60)
                    rec('露狼舞的决意·极' if now < self.boil_until else '露狼舞的决意(沸腾已过)')
                    self.later(0.6, lambda: self.add_con(LU, 0.25 if self.fail_cycle('lucon') else 0.50))
                elif now >= self.e_cd[LU]:
                    self.e_cd[LU] = now + 12.0
                    rec(f'!!露普通E(围猎, 狼魂{self.wolves})')
            elif who == MO and self.swallowed('moreat', 'e', MO, 1.6):
                return
            elif who == MO and now >= self.e_cd[MO]:
                self.e_cd[MO] = now + 16.0
                self.lock(0.3)
                self.add_con(MO, 0.10)
                if self.mode == 'air':
                    rec('莫空中E')

                    def cannon_pod():
                        self.forte = min(1.0, self.forte + 0.59)
                        rec(f'莫浮游炮到账(相对动能{self.forte:.2f})')
                    self.later(0.5, cannon_pod)
                else:
                    rec('!!莫地面E')
            return
        if key == 'q':
            if who == MO and 'echocd' in FAILS:
                return
            if now >= self.q_cd[who] and self.swallowed('qeat', 'q', who, 1.5):
                return
            if now >= self.q_cd[who]:
                self.q_cd[who] = now + 15.0
                self.lock(0.2)
                self.add_con(who, 0.05)
                rec(f'{NAMES[who]}Q{"(空中)" if who == LU and self.air else ""}')
            return
        if key == 'r':
            if now < self.r_cd[who]:
                return
            if self.swallowed('rswallow', 'r', who, 4.0):
                return
            if who == GA:
                if self.fail_cycle('gar'):
                    return
                self.r_cd[GA] = now + 25
                self.hide(201 / 60)
                self.add_con(GA, 0.10)
                rec(f'嘉R({"恶魔位格" if self.demon else "!!没有恶魔位格"})')
                if 'hudlost' in FAILS and self.cycle == 1:
                    self.hud_dead = True
                    rec('!!关卡结束(HUD 没了)')
            elif who == LU:
                if self.fail_cycle('lur'):
                    return
                if 'lurlow' in FAILS and ('lurlow', self.cycle) not in self.fail_used:
                    self.fail_used.add(('lurlow', self.cycle))
                    self.lu_r_short = 8
                    rec('!!故意失败:lurlow(露 R 能量差 8 下普攻)')
                if self.lu_r_short > 0:
                    return
                self.r_cd[LU] = now + 25
                self.hide(132 / 60)
                self.lock(201 / 60 + 0.05)
                self.lu_r_at = now
                self.flame = 100
                self.podi_done = False
                self.yanxi = self.ruizhua = False
                self.wolves = 0
                self.air = False
                self.air_step = 0
                self.add_con(LU, 0.15)
                rec('露R')
            elif who == MO:
                if self.fail_cycle('morr'):
                    return
                self.r_cd[MO] = now + 25
                self.hide(288 / 60, flick=(2.0,))
                self.add_con(MO, 0.15)
                rec(f'莫R({"强谐振场" if self.mode == "air" else "!!地面"})')

    def tap(self):
        self.tick()
        if not self.hud_visible():
            return
        who = self.on_field
        now = time.time()
        if who == MO and self.mode == 'ground':
            if not self.busy():
                self.mor_ground_step(now)
            return
        if self.busy() or (who == LU and now < self.lu_down_until):
            return
        if who == GA:
            if not self.step_ok(GA, 0.12):
                return
            if self.demon:
                self.add_con(GA, 0.008)
            else:
                self.zuihuo = min(100, self.zuihuo + (0 if self.fail_cycle('e3dark') else 4))
                self.add_con(GA, 0.02)
        elif who == LU:
            if self.lu_r_at is not None and not self.podi_done and self.lu_r_at + 2.0 <= now <= self.lu_r_at + 6.0:
                if 'podcut' in FAILS and now < self.lu_r_at + 4.6:
                    if ('podcut', self.cycle) not in self.fail_used:
                        self.fail_used.add(('podcut', self.cycle))
                        rec('!!故意失败:podcut(破敌的普攻被打断)')
                    return
                self.podi_done = True
                self.air = True
                self.air_step = 0
                self.air_last = now
                self.boil_until = now + 12.0
                self.flame = 0
                self.lock(0.5)
                self.add_con(LU, 0.08)
                rec('露破敌(升空, 赛点沸腾)')
                return
            if self.air:
                if now - self.air_last < 0.55:
                    return
                self.air_step += 1
                self.air_last = now
                self.add_con(LU, 0.03)
                if self.air_step < 3:
                    self.flame = min(100, self.flame + (30 if now < self.boil_until else 6))
                    rec(f'露空中A{self.air_step}')
                    return
                if self.fail_cycle('nowolf') or self.flame < 50:
                    rec(f'!!露空中第3段没出焰袭(狼焰{self.flame})')
                else:
                    self.flame -= 50
                    self.wolves += 1
                    self.yanxi = True
                    self.add_con(LU, 0.07)
                    rec(f'露焰袭(狼魂{self.wolves})')
                self.lock(1.4)

                def land():
                    self.air = False
                    rec('露落地')
                    if 'lukb' in FAILS and ('lukb', self.cycle) not in self.fail_used:
                        self.fail_used.add(('lukb', self.cycle))
                        self.yanxi = False
                        self.lu_down_until = time.time() + 5.0
                        rec('!!故意失败:lukb(露被击倒 5s, 锐爪窗口没了)')
                self.later(1.4, land)
                return
            if self.yanxi and not self.ruizhua and now < self.boil_until and not self.fail_cycle('nopaw')                     and self.flame >= 50:
                self.flame -= 50
                self.ruizhua = True
                self.lock(0.3)
                self.add_con(LU, 0.05)

                def claw():
                    self.wolves += 1
                    rec(f'露锐爪(狼魂{self.wolves})')
                self.later(10 / 60, claw)
                return
            if self.step_ok(LU, 0.30):
                self.add_con(LU, 0.04)
                if self.lu_r_short > 0:
                    self.lu_r_short -= 1
                    if self.lu_r_short == 0:
                        rec('露R能量满了')
                self.flame = min(100, self.flame + (30 if now < self.boil_until else 6))
        elif who == MO:
            self.mor_obs_a(now)


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

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        key = str(key)
        end = time.time() + max(down_time, 0.003)
        G.press(key, down_time)
        left = end - time.time()
        if left > 0:
            time.sleep(left)

    def send_key_down(self, key, after_sleep=0):
        pass

    def send_key_up(self, key, after_sleep=0):
        pass

    def jump(self, after_sleep=0.01):
        pass

    def mouse_down(self, *a, **kw):
        if kw.get('key') == 'right':
            rec(f'闪避({NAMES[G.on_field]})')
            return
        G.tick()
        if not G.holding:
            G.holding = True
            G.hold_dead = G.on_field == MO and G.mode == 'air' and G.cannons == 0 and G.fail_once('holdcut')
            self._down_at = time.time()
            G.hold_at = max(time.time(), G.lock_until)     # 锁输入期间按下的, 从解锁那一刻算
            G.hold_done = False
        time.sleep(0.002)

    def mouse_up(self, *a, **kw):
        if kw.get('key') == 'right':
            return
        if G.holding and time.time() - self._down_at < 0.25:
            G.holding = False
            G.tap()
        G.tick()
        G.holding = False

    def next_frame(self):
        time.sleep(0.03)
        G.tick()

    def middle_click(self, *a, **kw):
        rec('中键锁目标')

    def in_team(self):
        return (G.hud_visible(), G.on_field, 3) if G.hud_visible() else (False, -1, 0)

    def get_current_con(self):
        G.tick()
        return G.con[G.on_field]

    def get_cd(self, name, index=None):
        return max(0.0, G.e_cd[G.on_field] - time.time()) if name == 'resonance' else 0.0

    def find_e_forte(self):
        return ga_e3_lit()

    def find_one(self, name, threshold=0.0, **kw):
        name = str(name)
        if name == 'f_break_full':
            return G.ring() or None
        if name == 'f_break':
            return G.prompt() or None
        if not G.hud_visible():
            return None
        if name == 'lupa_wolf_icon2':
            return (G.on_field == LU and G.wolves >= 2 and 'wolfblind' not in FAILS) or None
        return None

    def add_freeze_duration(self, *a, **kw):
        pass

    def box_of_screen(self, *a, **kw):
        return None

    def is_pick_f(self):
        return False

    def available(self, name, check_color=True, check_cd=True):
        who = G.on_field
        now = time.time()
        if name == 'liberation':
            if {MO: 'morr', LU: 'lur', GA: 'gar'}[who] in FAILS or (who == LU and G.lu_r_short > 0):
                return False                # 没能量: 图标是暗的
            return now >= G.r_cd[who]
        if name == 'resonance':
            return G.e_cd[who] <= now
        if name == 'echo':
            if who == MO and 'echocd' in FAILS:
                return False
            return G.q_cd[who] <= now
        return False

    def has_cd(self, name, index=None):
        return False

    def wait_until(self, *a, **kw):
        return True

    def get_resonance_key(self):
        return 'e'

    def get_echo_key(self):
        return 'q'

    def get_liberation_key(self):
        return 'r'

    def find_mouse_forte(self):
        return False


def ga_e3_lit():
    """E 图标外圈: 罪火满、还没放恶翼扬升。"""
    return G.on_field == GA and G.hud_visible() and G.zuihuo >= 100 and not G.demon and 'e3ring' not in FAILS


def ga_bar_full():
    """回路条: 罪火满, 或者恶魔位格里的净炼火(等量, 也是满的)。"""
    return G.on_field == GA and G.hud_visible() and (G.demon or G.zuihuo >= 100)


task = FakeTask()

from src.char.BaseChar import CharType  # noqa: E402
from src.char import CustomCharLoader as L  # noqa: E402
from src.char.Galbrena import Galbrena as NGalbrena  # noqa: E402
from src.char.Lupa import Lupa as NLupa  # noqa: E402
from src.char.Mornye import Mornye as NMornye  # noqa: E402

team = ('Galbrena', 'Lupa', 'Mornye')
CGa = L.load_team_char_class(NGalbrena, team)
CLu = L.load_team_char_class(NLupa, team)
CMo = L.load_team_char_class(NMornye, team)
assert CGa is not NGalbrena and CLu is not NLupa and CMo is not NMornye, 'custom team code not loaded'


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

    c.is_current_char = index == GA
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
    if cls is CGa:
        c.__dict__.update(dict(opener_armed=True, last_opener=-1, macro_t0=0.0, macro_marks=[], cur=None,
                               holding_attack=False, si_holding=False, switch_press_at=0.0, lib_press_at=0.0,
                               loop_count=0, mor_intro_at=-99.0, _con_soft_n=0, _con_soft_frame=None,
                               hud_seen_at=0.0, hud_watch=False, drive_log_at=0.0, timeline_logged=True,
                               break_seen_at=-99.0, lu_icon_ever=False, lu_fix_fails=0))
    return c


ga = build(CGa, GA, 'char_galbrena', CharType.MAIN_DPS)
lu = build(CLu, LU, 'char_lupa', CharType.SUB_DPS)
mo = build(CMo, MO, 'char_mornye', CharType.HEALER)
task.chars = [ga, lu, mo]
for c in task.chars:
    c.add_freeze_duration = lambda *a, **kw: None
    c.time_elapsed_accounting_for_freeze = lambda start, *a, **kw: time.time() - start
    c.switch_out = types.MethodType(lambda self, con_full=False: setattr(self, 'is_current_char', False), c)
ga.game_foreground = lambda: False
ga.is_forte_full = ga_bar_full
assert mo.is_healer is False, '本队里莫宁不该算奶妈'


def mass_fill():
    if G.on_field != MO or not G.hud_visible() or G.mode != 'ground' or 'massblind' in FAILS:
        return 0.0
    return int(G.mass * 34 / 100) / 34


def forte_fill():
    if G.on_field != MO or not G.hud_visible() or G.mode != 'air' or 'forteblind' in FAILS:
        return 0.0
    return int(min(1.0, G.forte) * 34) / 34


def e_air():
    return G.on_field == MO and G.hud_visible() and G.mode == 'air'


ga.mor_mass_fill = mass_fill
ga.mor_forte_fill = forte_fill
ga.mor_e_air = e_air
ga.break_prompt = G.prompt


def lu_bar_full():
    """露帕狼焰条: R 之后满着, 破敌清空。"""
    return G.on_field == LU and G.hud_visible() and G.flame >= 100 and 'lubarblind' not in FAILS


lu.is_forte_full = lu_bar_full

ok = True
try:
    G.cycle = 0
    rec('==== 启动轴 ====')
    ga.drive(ga)
    for n in range(DRY_LOOPS):
        G.cycle = n + 1
        rec(f'==== 循环 #{n + 1} ====')
        cur = next((c for c in task.chars if c.index == G.on_field), ga)
        ga.drive(cur)
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
print('OK' if ok else 'FAILED')
