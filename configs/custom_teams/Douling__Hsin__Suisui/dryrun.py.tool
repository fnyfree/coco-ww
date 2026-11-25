"""心卜穗轴的干跑: 假 task + 假游戏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是等到了判据, 还是撞上限出来的
  - 演出时长按视频/帧表(卜灵R 3.6 / 穗穗R 4.3 / 转相 4.3 / 临寰宇 2.0 / 万阙垂天 5.0)
用法: <ok-ww 自带 python> dryrun.py.tool
环境变量:
  DRY_LOOPS=2            循环几圈
  DRY_FULL=0             心变奏入场应世心直接满(1) / 只给 60(0, 视频循环轴那样)
  DRY_BREAK=0            统御众机里怪的谐度满(处决可用)
  DRY_FAIL=a,b,...       故意失败(每圈各触发一次), 测保底分支:
                         sw       切心第一下被拒 2.8s(被击飞/硬直)
                         sue1     启动轴穗穗醒春潮图标不亮(已经在濯雨时, 走照按 E)
                         blcut    卜灵第一段重击被打断(要同色重按)
                         heavy    心第一段第一次按住只出普通重击(没出步红尘)
                         r1       心转相解锁后 R 被吞 2.5s(走转相补救)
                         e1miss   心第二段照世相 E 没引爆活字心机(照世心不够 300, 走统御众机补救)
                         lin      心第二段第一次按住被打断(临寰宇没出, 要补 A 重按)
                         sucon    穗穗大招后协奏差一截(要补)
                         hudlost  循环 #1 心第二段之后关卡结束(HUD 再也不回来)
                         sueslow  启动轴穗穗前 4s 普攻打不中(水云息不涨, 醒春潮晚亮)
                         sueeat   穗穗醒春潮第一下被吞(图标还亮, 要接着 A 再按)
                         fangslow 穗穗第二格普攻一半没打中(芳菲信涨得慢, 要靠补 E + 放长的上限)
                         nowave   心万阙垂天把这一波打死, 新怪 2s 后才刷(穗穗变奏打空不回协奏, 要等新怪再切)
                         air2a    卜灵 2A 那下成了下落(给震不给艮, 10-08 L2: 艮被挤掉成 震震震震)
                         gua3     卜灵第一格 A2 的艮没打出来(只有 3 个卦象, 第一发蓝之后外圈灭 -> 直接开 R)
"""
import os
import random
import sys
import threading
import time
import types

def _okww_working():
    """框架 working 目录(有 src/char/CustomCharLoader.py 的那层): OKWW_WORKING > 本文件往上找 > 旧版 OKWW 的默认位置。
    coco-ww 是平铺结构, 往上找到的就是安装目录本身; 以前写死旧版路径, 在 coco-ww 里跑测的是旧框架和旧版队伍代码。"""
    import os
    cands = [os.environ.get('OKWW_WORKING')]
    d = os.path.dirname(os.path.abspath(__file__))
    while True:
        cands.append(d)
        up = os.path.dirname(d)
        if up == d:
            break
        d = up
    cands.append(r'F:\ok-ww\data\apps\ok-ww\working')
    for c in cands:
        if c and os.path.isfile(os.path.join(c, 'src', 'char', 'CustomCharLoader.py')):
            return c
    raise SystemExit('找不到框架的 working 目录(里面有 src/char/CustomCharLoader.py), 设环境变量 OKWW_WORKING 再跑')


_WORKING = _okww_working()
sys.path.insert(0, _WORKING)
os.chdir(_WORKING)             # 框架按相对路径 configs/custom_teams 找队伍代码
sys.stdout.reconfigure(encoding='utf-8')

DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '2'))
FAILS = set(x for x in os.environ.get('DRY_FAIL', '').split(',') if x)
FULL = os.environ.get('DRY_FULL', '0') == '1'
BREAK = os.environ.get('DRY_BREAK', '0') == '1'
random.seed(7)
LOG = []
T0 = [time.time()]
XN, BL, SU = 0, 1, 2          # 视频的编队: 1 心 / 2 卜灵 / 3 穗穗
NAMES = ['心', '卜', '穗']


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
        self.on_field = XN
        self.pending = None
        self.hud_hidden = []
        self.flickers = []
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.con = {XN: 0.2, BL: 0.0, SU: 0.0}     # 协奏按帧表的协奏回收/100 粗估
        self.e_cd = {XN: 0.0, BL: 0.0, SU: 0.0}
        self.r_cd = {XN: 0.0, BL: 0.0, SU: 0.0}
        self.holding = False
        self.hold_at = 0.0
        self.cycle = 0
        self.fail_used = set()
        self.hud_dead = False
        self.last_step = {XN: 0.0, BL: 0.0, SU: 0.0}
        self.arrived_at = 0.0
        self.jump_at = 0.0
        self.break_cd = 0.0
        # 穗穗
        self.shui = 0
        self.fang = 0
        self.qiang_until = 0.0
        # 卜灵
        self.gua = []
        self.bl_combo = 0
        self.bl_e_at = -9.0
        self.plunge_land_at = 0.0
        self.hold_segs = 0
        self.bl_got = set()
        # 心
        self.xn_mode = 'ying'
        self.ying = 100 if FULL else 60
        self.ying_hits = []
        self.burned_at = None
        self.r1_unlock = None
        self.r1_unlocked = False
        self.r1_block_until = 0.0
        self.huozi = False
        self.zhao_xin = 0
        self.zhao_due = []
        self.tongyu = False
        self.lin_unlocked = False
        self.heavy_fail = None
        self.lin_block = False
        self.normal_heavy_done = False

    def fail_once(self, name):
        if name not in FAILS or (name, self.cycle) in self.fail_used:
            return False
        self.fail_used.add((name, self.cycle))
        rec(f'!!故意失败:{name}')
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

    def busy(self):
        return time.time() < self.lock_until

    def add_con(self, i, v):
        self.con[i] = min(1.0, self.con[i] + v)

    def gua_add(self, g):
        self.gua = (self.gua + [g])[-4:]

    def tick(self):
        now = time.time()
        if self.pending and now >= self.pending[1]:
            dst = self.pending[0]
            src = self.on_field
            self.pending = None
            intro = self.con[src] >= 1.0
            if intro:
                self.con[src] = 0.0
            self.on_field = dst
            self.arrived_at = now
            rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
            if dst == SU and intro:
                self.qiang_until = now + 15.0
                self.lock_until = now + 0.6
                self.no_switch_until = now + 1.0
                if now >= getattr(self, 'no_target_until', 0.0):
                    self.add_con(SU, 0.47)             # QTE 10 + 常规 9.6 + P1 27.6
                else:
                    rec('穗变奏打空(没怪, 协奏不回)')
            elif dst == XN and intro:
                if self.xn_mode == 'ying':
                    self.ying = 100 if FULL else min(100, self.ying + 60)
                    self.lock_until = now + 0.45
                else:
                    self.lock_until = now + 0.9
            elif dst == BL and intro:
                self.lock_until = now + 1.17
        for at, v in list(self.ying_hits):
            if now >= at:
                self.ying_hits.remove((at, v))
                if self.on_field == XN and self.xn_mode == 'ying' and self.burned_at is None:
                    self.ying = min(100, self.ying + v)
        for at, v in list(self.zhao_due):
            if now >= at:
                self.zhao_due.remove((at, v))
                if self.xn_mode == 'zhao' and not self.tongyu:
                    self.zhao_xin = min(300, self.zhao_xin + v)
        if self.plunge_land_at and now >= self.plunge_land_at:
            self.plunge_land_at = 0.0
            if self.on_field == BL:
                self.gua_add('震')
                rec('卜下落落地+震')
        # 按住
        if self.holding and not self.busy():
            held = now - self.hold_at
            who = self.on_field
            if who == XN and self.xn_mode == 'ying' and self.burned_at is None and held >= 0.3:
                if self.heavy_fail is None:
                    self.heavy_fail = self.fail_once('heavy')
                if self.ying >= 85 and not self.heavy_fail:
                    self.ying = 0
                    self.burned_at = now
                    self.r1_unlock = now + 2.06
                    self.lock_until = now + 2.2
                    self.add_con(XN, 0.1)
                    rec('心步红尘出手')
                elif not self.normal_heavy_done:
                    self.normal_heavy_done = True
                    self.ying = min(100, self.ying + 8)
                    rec(f'心普通重击(应世心{self.ying})')
            if who == XN and self.xn_mode == 'zhao' and held >= 0.3:
                if not getattr(self, 'lin_checked', False):
                    self.lin_checked = True
                    self.lin_block = self.fail_once('lin')
                # 实机(10-06): 按住重击不消耗照世心, 照世心被普攻打空(重击位换人像)之后按住 ~0.45s 才出临寰宇
                if self.tongyu and not self.lin_block and self.zhao_xin == 0 and held >= 0.45:
                    if True:
                        self.tongyu = False
                        self.lin_unlocked = True
                        self.hide(2.0)
                        self.add_con(XN, 0.15)
                        rec('心临寰宇出手')
            if who == BL:
                self.bl_hold_tick()
        if self.r1_unlock and now >= self.r1_unlock and not self.r1_unlocked:
            self.r1_unlocked = True
            rec('心转相解锁')

    def bl_hold_tick(self):
        if not self.holding or self.on_field != BL:
            return
        k = self.hold_segs
        if k >= 2 or time.time() - self.hold_at < 0.67 + 1.0 * k:
            return
        if len(self.gua) < 2:
            if k == 0:
                rec('卜鬼门占卦(没卦象)')
            self.hold_segs = 2
            return
        if k == 0 and self.fail_once('blcut'):
            rec('卜重击被打断(没出)')
            self.hold_segs = 2
            return
        pair = self.gua[-2:]
        del self.gua[-2:]
        kind = '少阴' if pair[0] == pair[1] else '少阳'
        self.bl_got.add(kind)
        self.add_con(BL, 0.15)
        self.hold_segs = k + 1
        rec(f'卜重击出手({kind}{"".join(pair)})')

    def step_ok(self, who, gap):
        now = time.time()
        if now - self.last_step[who] < gap:
            return False
        self.last_step[who] = now
        return True

    # ---------- 输入 ----------
    def press(self, key, down_time):
        now = time.time()
        self.tick()
        if key == 'f':
            if BREAK and self.on_field == XN and self.tongyu and now >= self.break_cd:
                self.break_cd = now + 20.0
                self.hide(1.2)
                rec('心处决')
            return
        if key in ('1', '2', '3'):
            dst = int(key) - 1
            if dst == self.on_field or self.pending:
                return
            if now < self.no_switch_until or not self.hud_visible():
                return
            if dst == XN and getattr(self, 'sw_block_until', 0) > now:
                return
            if dst == XN and self.fail_once('sw'):
                self.sw_block_until = now + 2.8
                return
            self.pending = (dst, now + 0.08)
            return
        if key in ('space', 'w', 'lshift'):
            return
        if not self.hud_visible() or self.busy():
            return
        who = self.on_field
        if key == 'e':
            if who == SU:
                if self.shui >= 120 and now >= self.qiang_until and 'sue1' not in FAILS and self.fail_once('sueeat'):
                    return
                if self.shui >= 120 and now >= self.qiang_until and 'sue1' not in FAILS:
                    self.shui = 0
                    self.fang = 0
                    self.qiang_until = now + 15.0
                    self.lock_until = now + 0.3
                    self.add_con(SU, 0.30)
                    rec('穗醒春潮(进濯雨时)')
                elif now >= self.e_cd[SU]:
                    self.e_cd[SU] = now + 4.0
                    if now < self.qiang_until:
                        self.fang += 100
                        rec('穗E(濯雨时)')
                    else:
                        self.shui += 40
                        rec('穗E(浣尘时)')
                    self.lock_until = now + 0.3
                    self.add_con(SU, 0.06)
                return
            if who == BL:
                if now >= self.e_cd[BL]:
                    self.e_cd[BL] = now + 12.0
                    self.gua_add('震')
                    self.bl_e_at = now
                    self.bl_combo = 0
                    self.lock_until = now + 0.63
                    self.add_con(BL, 0.23)
                    rec('卜E(+震)')
                return
            if who == XN:
                if self.xn_mode == 'zhao' and not self.tongyu and self.zhao_xin >= 300:
                    self.tongyu = True
                    self.lock_until = now + 0.4
                    rec('心万阙连衡(进统御众机)')
                elif self.xn_mode == 'zhao' and now >= self.e_cd[XN]:
                    self.e_cd[XN] = now + 20.0
                    if self.huozi and not self.fail_once('e1miss'):
                        self.zhao_xin = min(300, self.zhao_xin + 150)
                        self.zhao_due.append((now + 1.5, 300))
                        rec('心照世相E(引爆活字心机)')
                    else:
                        self.zhao_xin = min(300, self.zhao_xin + 60)
                        rec('心照世相E(没活字心机)')
                    self.huozi = False
                    self.lock_until = now + 0.5
                    self.add_con(XN, 0.1)
                elif self.xn_mode == 'zhao' and self.tongyu:
                    rec('!!心统御众机里按了E')
                return
        elif key == 'q':
            rec(f'{NAMES[who]}Q')
            self.add_con(who, 0.05)
        elif key == 'r':
            if who == SU and now >= self.r_cd[SU]:
                self.r_cd[SU] = now + 25
                self.hide(4.3)
                self.add_con(SU, 0.2 if 'sucon' not in FAILS or (('sucon', self.cycle) in self.fail_used)
                             else (self.fail_once('sucon') and 0.0))
                rec(f'穗R(芳菲信{self.fang})')
            elif who == BL and now >= self.r_cd[BL]:
                self.r_cd[BL] = now + 25
                self.hide(3.6)
                self.add_con(BL, 0.2)
                rec(f'卜R({"开阵" if self.bl_got >= {"少阳", "少阴"} else "没开阵"})')
                self.bl_got = set()
            elif who == XN:
                if self.xn_mode == 'ying' and self.r1_unlocked and now < self.r1_block_until:
                    pass
                elif self.xn_mode == 'ying' and self.r1_unlocked and self.fail_once('r1'):
                    self.r1_block_until = now + 2.5
                elif self.xn_mode == 'ying' and self.r1_unlocked:
                    self.r1_unlocked = False
                    self.r1_unlock = None
                    self.burned_at = None
                    self.xn_mode = 'zhao'
                    self.zhao_xin = 0
                    self.hide(4.3, flick=(0.55, 3.6))
                    rec('心转相')
                elif self.xn_mode == 'zhao' and self.lin_unlocked:
                    self.lin_unlocked = False
                    self.xn_mode = 'ying'
                    self.ying = 0
                    self.hide(5.0)
                    self.add_con(XN, 0.15)
                    rec('心万阙垂天')
                    if self.fail_once('nowave'):
                        self.no_target_until = now + 5.0 + 2.0      # 演出 5s + 新怪 2s 后才刷
                    if 'hudlost' in FAILS and self.cycle == 1:
                        self.hud_dead = True
                        rec('!!关卡结束(HUD 没了)')
                elif self.xn_mode == 'zhao':
                    rec('!!心借相(按早了)')

    def tap(self):
        self.tick()
        if not self.hud_visible() or self.busy():
            return
        who = self.on_field
        now = time.time()
        if who == SU:
            if not self.step_ok(SU, 0.40):
                return
            self.add_con(SU, 0.05)
            if 'sueslow' in FAILS and self.cycle == 0 and now - self.arrived_at < 4.0:
                return
            if now < self.qiang_until:
                self.fang += 60 if 'fangslow' in FAILS else 142
            else:
                self.shui += 41
        elif who == BL:
            if self.jump_at and now - self.jump_at < 0.5:
                self.jump_at = 0.0
                self.plunge_land_at = now + 0.70
                self.lock_until = now + 0.77
                self.add_con(BL, 0.05)
                rec('卜跳A(下落)')
                return
            if not self.step_ok(BL, 0.40):
                return
            self.add_con(BL, 0.03)
            if now - self.bl_e_at < 1.2:
                self.bl_e_at = -9.0
                self.add_con(BL, 0.045)
                self.gua_add('震')
                rec('卜A4(由E派生, +震)')
                return
            if now - getattr(self, 'bl_last', 0.0) > 1.0:
                self.bl_combo = 0
            self.bl_last = now
            self.bl_combo += 1
            if self.bl_combo == 2 and now - self.arrived_at < 1.5 and self.fail_once('gua3'):
                pass
            elif self.bl_combo == 2 and now - self.arrived_at < 1.5 and self.fail_once('air2a'):
                self.gua_add('震')
                self.bl_combo = 0                       # 下落落地, 连段从 A1 重来
                self.last_step[BL] = now - 0.30         # 落地收招只有 8F
                rec('卜2A成了下落(+震)')
            elif self.bl_combo == 2:
                self.gua_add('艮')
            elif self.bl_combo == 4:
                self.gua_add('震')
                self.bl_combo = 0
        elif who == XN:
            if not self.step_ok(XN, 0.16):
                return
            self.add_con(XN, 0.03)
            if self.xn_mode == 'ying':
                self.ying_hits.append((now + 0.3, 13))
            elif self.tongyu:
                self.zhao_xin = max(0, self.zhao_xin - 10)        # 实机普攻打空照世心要 ~5.2s
            else:
                if not self.huozi and now - self.arrived_at < 3.0:
                    self.huozi = True
                    rec('心A1(活字心机)')
                self.zhao_due.append((now + 0.2, 6))


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
        G.tick()
        if G.hud_visible() and not G.busy():
            G.jump_at = time.time()
            rec('跳')

    def mouse_down(self, *a, **kw):
        if kw.get('key') == 'right':
            rec(f'闪避({NAMES[G.on_field]})')
            return
        G.tick()
        if not G.holding:
            G.holding = True
            G.hold_at = time.time()
            G.normal_heavy_done = False
            G.heavy_fail = None
            G.lin_checked = False
            G.lin_block = False
            G.hold_segs = 0
        time.sleep(0.002)

    def mouse_up(self, *a, **kw):
        if kw.get('key') == 'right':
            return
        if G.holding and time.time() - G.hold_at < 0.25:
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

    def find_one(self, name, threshold=0.0, **kw):
        name = str(name)
        if name in ('f_break_full', 'f_break'):
            return (BREAK and G.hud_visible() and G.on_field == XN and G.tongyu and time.time() >= G.break_cd) or None
        if not G.hud_visible():
            return None
        if G.on_field == SU:
            if name.endswith('suisui_e1'):
                return (G.shui >= 120 and time.time() >= G.qiang_until and 'sue1' not in FAILS) or None
            if name.endswith('suisui_forte3'):
                return (time.time() < G.qiang_until and G.fang >= 600) or None
            return None
        if G.on_field != XN:
            return None
        if name == 'hsin_h1':
            return (G.xn_mode == 'ying' and G.ying >= 100 and G.burned_at is None) or None
        if name == 'hsin_lib1':
            return G.xn_mode == 'ying' or None
        if name == 'hsin_lib2':
            return (G.xn_mode == 'zhao' and G.lin_unlocked) or None
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
            if who == XN:
                return (G.xn_mode == 'ying' and G.r1_unlocked) or G.xn_mode == 'zhao'
            return now >= G.r_cd[who]
        if name == 'resonance':
            return G.e_cd[who] <= now
        if name == 'echo':
            return True
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


task = FakeTask()

from src.char.BaseChar import CharType  # noqa: E402
from src.char import CustomCharLoader as L  # noqa: E402
from src.char.Hsin import Hsin as NHsin  # noqa: E402
from src.char.Douling import Douling as NDouling  # noqa: E402
from src.char.Suisui import Suisui as NSuisui  # noqa: E402

team = ('Douling', 'Hsin', 'Suisui')
CXn = L.load_team_char_class(NHsin, team)
CBl = L.load_team_char_class(NDouling, team)
CSu = L.load_team_char_class(NSuisui, team)
assert CXn is not NHsin and CBl is not NDouling and CSu is not NSuisui, 'custom team code not loaded'


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

    c.is_current_char = index == XN
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
    if cls is CXn:
        c.__dict__.update(dict(opener_armed=True, last_opener=-1, macro_t0=0.0, macro_marks=[], cur=None,
                               holding_attack=False, si_holding=False, switch_press_at=0.0, lib_press_at=0.0,
                               loop_count=0, hud_seen_at=0.0, hud_watch=False, drive_log_at=0.0,
                               timeline_logged=True))
    return c


xn = build(CXn, XN, 'char_hsin', CharType.MAIN_DPS)
bl = build(CBl, BL, 'char_douling', CharType.HEALER)
su = build(CSu, SU, 'char_suisui', CharType.HEALER)
task.chars = [xn, bl, su]
for c in task.chars:
    c.add_freeze_duration = lambda *a, **kw: None
    c.time_elapsed_accounting_for_freeze = lambda start, *a, **kw: time.time() - start
    c.switch_out = types.MethodType(lambda self, con_full=False: setattr(self, 'is_current_char', False), c)
xn.game_foreground = lambda: False


def gauge():
    if G.on_field != XN or G.xn_mode != 'ying' or not G.hud_visible():
        return 0.0, 0.0
    v = G.ying
    if v >= 100:
        return 0.42, 0.42          # 满了条是粉红高柱, 紫色判据读成 0.4 上下(实机)
    return min(1.0, v / 50) * 0.88, max(0.0, (v - 50) / 50) * 0.88


xn.xn_gauge = gauge


def tongyu():
    if G.on_field != XN or not G.hud_visible():
        return None
    xn.hud_seen_at = time.time()
    xn._ty_score = 0.95 if G.tongyu else 0.1
    return G.tongyu


xn.xn_tongyu = tongyu


def slot_state():
    if G.on_field != XN or not G.hud_visible():
        return None
    xn.hud_seen_at = time.time()
    if not G.tongyu:
        return '?'
    return 'ready' if G.zhao_xin == 0 else 'statue'


xn.xn_slot_state = slot_state
xn.target_visible = lambda: time.time() >= getattr(G, 'no_target_until', 0.0)


def statue_count():
    if G.on_field != XN or not G.hud_visible() or not G.tongyu:
        return 0
    return int((300 - G.zhao_xin) // 37.5)          # 8 颗雕像, 每颗 37.5 照世心


xn.xn_statue_count = statue_count
xn.break_prompt = lambda: BREAK and G.on_field == XN and G.tongyu and time.time() >= G.break_cd and G.hud_visible()


def ring():
    if G.on_field != BL or G.holding or len(G.gua) < 2 or not G.hud_visible():
        return None, 0.0, 0.0
    a, b = G.gua[-2:]
    return ('blue', 0.0, 0.4) if a == b else ('yellow', 0.4, 0.0)


xn.bl_ring = ring


def gua():
    if not G.hud_visible():
        return None
    return ''.join(G.gua) if G.on_field == BL else ''


xn.bl_gua = gua
if os.environ.get('DRY_BL_BUDGET'):
    xn.BL1_CON_BUDGET = float(os.environ['DRY_BL_BUDGET'])     # 放宽协奏账, 让补卦象那条路跑一遍

ok = True
try:
    G.cycle = 0
    rec('==== 启动轴 ====')
    xn.drive(xn)
    for n in range(DRY_LOOPS):
        G.cycle = n + 1
        rec(f'==== 循环 #{n + 1} ====')
        cur = next((c for c in task.chars if c.index == G.on_field), xn)
        xn.drive(cur)
        if G.hud_dead:
            break
except Exception:
    import traceback
    ok = False
    rec('!!EXCEPTION ' + traceback.format_exc())
for t, w in LOG:
    print(f'{t:7.2f}  {w}')
print('OK' if ok else 'FAILED')
