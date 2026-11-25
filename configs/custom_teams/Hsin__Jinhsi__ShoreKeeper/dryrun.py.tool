"""心汐守轴的干跑: 假 task + 假游戏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是等到了判据, 还是撞上限出来的
  - 演出时长按两份录像量的(守R 3.0 / 转相 4.3 / 今汐R 3.45 / 惊龙破空 3.0 / 临寰宇 2.0 / 万阙垂天 4.3 / 守强化变奏 3.2)
用法: <ok-ww 自带 python> dryrun.py.tool
环境变量:
  DRY_LOOPS=2            循环几圈
  DRY_FULL=1             心变奏入场应世心直接满(用户实机 2026-10-05 就是这样; 0 = 作者视频那样只给 60)
  DRY_DIANCI=1           心在共鸣模态·电磁: 转相不给同奏, 今汐切进来没有变奏
  DRY_SK_CORE=5          守岸人回路几格满(普攻 +1, E +2)
  DRY_BREAK=1            怪的谐度会满(处决可用, 处决后 20s 才再满); 0 = 一直不能处决
  DRY_FAIL=a,b,...       故意失败(每圈各触发一次), 测保底分支:
                         ying    心第一段两下 A 都没打中(应世心停在 60, 要补 A)
                         heavy   心第一次按住只出普通重击(没出步红尘, 要重按)
                         lin     心第二段第一次按住被打断(临寰宇没出, 要补 A 重按)
                         sw      切今汐第一下被拒 2.8s(被击飞/硬直)
                         jxr     今汐第一段 R 能量没满(挪到第二段补)
                         bige    今汐惊龙破空第一下没出(韶光差一点, 要边 A 边按 E)
                         gauge   应世心条读不出来(走读图标的兜底)
                         hudlost 循环 #1 心第二段之后关卡结束(HUD 再也不回来)
                         hop     今汐第二段切守岸人插段被拒 3s(插段跳过)
                         r1      心第一段转相解锁后 R 被吞 2.5s(第一次 1.5s 没按出来, 走转相补救)
                         burn    心第一段按住三次都只出普通重击(步红尘没出, 转相没解锁, 走转相补救)
                         jxcd    今汐第二段切进来 E 冷却 1.8s, 这段图标认成惊龙破空(实机 10-07)
                         r2e     心第二段临寰宇之后大招能量差一截(要再普攻 6 下 R 位才变万阙垂天, 实机 10-07)
  DRY_SK_CON=0.2         循环守岸人强化变奏进场时协奏起点(0~1); 0.8 以上测"协奏够了直接QR"
"""
import os
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
FULL = os.environ.get('DRY_FULL', '1') == '1'
SK_CORE_FULL = int(os.environ.get('DRY_SK_CORE', '5'))
BREAK = os.environ.get('DRY_BREAK', '1') == '1'
SK_CON0 = float(os.environ['DRY_SK_CON']) if os.environ.get('DRY_SK_CON') else None
DIANCI = os.environ.get('DRY_DIANCI', '0') == '1'
LOG = []
T0 = [time.time()]
SK, JX, XN = 0, 1, 2          # 用户的编队: 1 守 / 2 今汐 / 3 心
NAMES = ['守', '汐', '心']


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
    """够干跑用的假游戏: 只建模这条轴用到的资源和演出。"""

    def __init__(self):
        self.on_field = SK
        self.pending = None
        self.hud_hidden = []          # [(开始, 结束)] 演出藏 HUD 的时段
        self.flickers = []            # [(开始, 结束)] 演出中途 HUD 闪回来的时段
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.break_cd = 0.0            # 处决之后这么久谐度才再满
        self.con = {SK: 0.0, JX: 0.0, XN: 0.0}
        self.holding = False
        self.hold_at = 0.0
        self.e_cd = {SK: 0.0, JX: 0.0, XN: 0.0}
        self.lib_energy = {SK: True, JX: True, XN: True}
        self.sk_forte = False
        self.sk_core = 0               # 守岸人回路: 普攻 +1, E +2, 满 SK_CORE_FULL 格
        self.sk_r_cd = 0.0
        self.jx_r_cd = 0.0
        # 今汐: 变奏/普通第4段 -> 神霓飞芒 5s -> 按了进乘岁凌霄 10s -> 凌霄第4段 -> 游龙廻光 5s -> 惊龙破空
        self.jx_shenni_until = 0.0
        self.jx_shenni_cd = 0.0
        self.jx_lx_until = 0.0
        self.jx_lx_combo = 0
        self.jx_youlong_until = 0.0
        self.jx_zhutian_cd = 0.0
        self.jx_norm_combo = 0
        self.jx_norm_last = 0.0
        self.jx_tongzou_until = 0.0
        self.jx_tongzou_last = -99.0
        # 心
        self.xn_mode = 'ying'         # ying / zhao
        self.ying = 0
        self.ying_hits = []           # (生效时刻, 值)
        self.burned_at = None
        self.r1_unlock = None
        self.r1_unlocked = False
        self.tongzou_until = 0.0
        self.zhao_xin = 0
        self.tongyu = False
        self.lin_unlocked = False
        self.hud_dead = False
        self.cycle = 0
        self.r2_need_taps = 0
        self.jx_ecd_until = 0.0
        self.cycle_jx_entries = {}
        self.burn_fails = {}
        self.r1_block_until = 0.0
        self.fail_used = set()
        self.a_count = 0

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

    def tick(self):
        now = time.time()
        if self.pending and now >= self.pending[1]:
            dst = self.pending[0]
            src = self.on_field
            self.pending = None
            intro = (self.con[src] >= 1.0 or (src == XN and now < self.tongzou_until)
                     or (src == JX and now < self.jx_tongzou_until))
            if src == JX:
                self.jx_tongzou_until = 0.0
            if self.con[src] >= 1.0:
                self.con[src] = 0.0
            if src == XN:
                self.tongzou_until = 0.0
            self.on_field = dst
            rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
            if dst == XN and intro:
                if self.xn_mode == 'ying':
                    self.ying = 100 if FULL else 60
                    self.lock_until = now + 0.55
                else:
                    self.tongyu = True
                    self.zhao_xin = 300
                    self.lock_until = now + 1.8
            elif dst == JX and intro:
                self.lock_until = now + 1.0
                if now >= self.jx_lx_until:
                    self.jx_shenni_until = now + 5.0
                if 'jxcd' in FAILS and self.cycle_jx_entries.get(self.cycle, 0) >= 1:
                    self.jx_ecd_until = now + 1.8
                    rec('!!今汐E冷却1.8s(第二段)')
                self.cycle_jx_entries[self.cycle] = self.cycle_jx_entries.get(self.cycle, 0) + 1
            elif dst == SK and intro:
                self.hide(3.2)
                if SK_CON0 is not None:
                    self.con[SK] = SK_CON0
            elif dst == XN:
                self.lock_until = now + 0.3
        for at, v in list(self.ying_hits):
            if now >= at:
                self.ying_hits.remove((at, v))
                if self.on_field == XN and self.xn_mode == 'ying' and self.burned_at is None:
                    self.ying = min(100, self.ying + v)
        # 心按住
        if self.holding and self.on_field == XN and not self.busy():
            held = now - self.hold_at
            if held >= 0.3 and self.heavy_fail is None:
                self.heavy_fail = self.xn_mode == 'ying' and self.fail_once('heavy')
                if not self.heavy_fail and self.xn_mode == 'ying' and 'burn' in FAILS \
                        and self.burn_fails.get(self.cycle, 0) < 3:
                    self.burn_fails[self.cycle] = self.burn_fails.get(self.cycle, 0) + 1
                    self.heavy_fail = True
                    rec('!!故意失败:burn')
            if held >= 0.3 and not self.lin_checked:
                self.lin_checked = True
                if self.xn_mode == 'zhao' and self.fail_once('lin'):
                    self.lin_block_until = now + 99
            if self.xn_mode == 'ying' and self.burned_at is None and held >= 0.3:
                need = 85 if not getattr(self, 'heavy_fail', False) else 999
                if self.ying >= need:
                    self.ying = 0
                    self.burned_at = now
                    self.r1_unlock = now + 2.06
                    self.lock_until = now + 2.2
                    rec('心步红尘出手')
                elif held >= 0.3 and not getattr(self, 'normal_heavy_done', False):
                    self.normal_heavy_done = True
                    self.ying = min(100, self.ying + 8)
                    rec(f'心普通重击(应世心{self.ying})')
            # 同奏: 4A 收招的余击在按住期间接着吃照世心(实机按住 ~1.6s 出手); 打空时按着的这一下马上出临寰宇。
            # lin 故障 = 这一下按住被打断(怎么都不出, 松开重按才恢复)
            if self.xn_mode == 'zhao' and self.tongyu and getattr(self, 'lin_block_until', 0) <= now                     and now - getattr(self, 'hold_eat_at', 0) >= 0.4 and self.zhao_xin > 0 and held < 1.6:
                self.hold_eat_at = now
                self.zhao_xin = max(0, self.zhao_xin - 30)
            if self.xn_mode == 'zhao' and self.tongyu and held >= 0.4 and self.zhao_xin == 0:
                if getattr(self, 'lin_block_until', 0) > now:
                    pass
                else:
                    if True:
                        self.tongyu = False
                        self.lin_unlocked = True
                        self.r2_need_taps = 6 if self.fail_once('r2e') else 0
                        self.hide(2.0)
                        self.add_con(XN, 0.3)
                        rec('心临寰宇出手')
        if self.holding and self.on_field == SK and now - self.hold_at >= 0.3 and self.sk_forte:
            self.sk_forte = False
            self.sk_core = 0
            self.add_con(SK, 0.06)
            rec('守强化重击')
        if self.r1_unlock and now >= self.r1_unlock and not self.r1_unlocked:
            self.r1_unlocked = True
            rec('心转相解锁')

    def add_con(self, i, v):
        self.con[i] = min(1.0, self.con[i] + v)

    # ---------- 输入 ----------
    def press(self, key, down_time):
        now = time.time()
        self.tick()
        if key == 'f':
            if self.break_ready():
                self.break_cd = now + 20.0
                self.no_switch_until = now + 1.57
                self.lock_until = now + 1.57
                rec('今汐处决' if self.on_field == JX else f'处决({self.on_field})')
            return
        if key in ('1', '2', '3'):
            dst = int(key) - 1
            if dst == self.on_field or self.pending:
                return
            if now < self.no_switch_until or not self.hud_visible():
                return
            if dst == JX and getattr(self, 'sw_block_until', 0) > now:
                return
            if dst == JX and self.fail_once('sw'):
                self.sw_block_until = now + 2.8
                return
            if dst == SK and self.on_field == JX and getattr(self, 'hop_block_until', 0) > now:
                return
            if dst == SK and self.on_field == JX and self.fail_once('hop'):
                self.hop_block_until = now + 3.0
                return
            self.pending = (dst, now + 0.08)
            return
        if key == 'w' or key == 'lshift':
            return
        if not self.hud_visible() or self.busy():
            return
        who = self.on_field
        if key == 'e':
            if who == JX and now < self.jx_ecd_until:
                return
            if who == JX:
                if now < self.jx_youlong_until:
                    self.jx_youlong_until = 0.0
                    self.hide(3.0)
                    self.add_con(JX, 0.3)
                    if now - self.jx_tongzou_last >= 25.0:
                        self.jx_tongzou_last = now
                        self.jx_tongzou_until = now + 3.0 + 5.0
                        rec('今汐惊龙破空(同奏)')
                    else:
                        rec('今汐惊龙破空(没同奏)')
                elif now < self.jx_lx_until:
                    if now >= self.jx_zhutian_cd:
                        self.jx_zhutian_cd = now + 10
                        self.lock_until = now + 0.8
                        rec('今汐逐天取月')
                elif now < self.jx_shenni_until and now >= self.jx_shenni_cd:
                    self.jx_shenni_until = 0.0
                    self.jx_shenni_cd = now + 12
                    self.jx_lx_until = now + 10.0
                    self.jx_lx_combo = 0
                    self.lock_until = now + 0.5
                    self.add_con(JX, 0.04)
                    rec('今汐神霓飞芒(进凌霄)')
                elif now >= self.e_cd[JX]:
                    self.e_cd[JX] = now + 3.0
                    self.lock_until = now + 0.4
                    rec('今汐流光夕影(普通E)')
                return
            if self.e_cd[who] > now:
                return
            self.e_cd[who] = now + (16 if who == SK else 10)
            if who == SK:
                self.sk_core += 2 * SK_CORE_FULL // 5      # E 给 2 格
                if self.sk_core >= SK_CORE_FULL and not self.sk_forte:
                    rec('守回路满(E)')
                self.sk_forte = self.sk_core >= SK_CORE_FULL
            self.add_con(who, 0.2)
            if who == XN and self.xn_mode == 'zhao' and not self.tongyu:
                self.tongyu = True
                self.zhao_xin = 300
            rec(f'{NAMES[who]}E')
        elif key == 'q':
            rec(f'{NAMES[who]}Q')
            self.add_con(who, 0.1)
        elif key == 'r':
            if who == SK and self.lib_energy[SK] and now >= self.sk_r_cd:
                self.sk_r_cd = now + 25
                self.hide(3.0, flick=(1.9,))
                self.add_con(SK, 1.0)
                rec('守R')
            elif who == JX and now >= self.jx_r_cd and self.jx_r_ready():
                self.jx_r_cd = now + 25
                self.hide(3.45)
                self.add_con(JX, 0.3)
                rec('今汐R')
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
                    self.tongzou_until = 0.0 if DIANCI else now + 5.0
                    self.hide(4.3, flick=(0.55, 3.6))
                    rec('心转相')
                elif self.xn_mode == 'zhao' and self.lin_unlocked and self.r2_need_taps > 0:
                    pass                     # 能量不够: 按 R 什么都不出(实机按的是借相, 这里不模拟)
                elif self.xn_mode == 'zhao' and self.lin_unlocked:
                    self.lin_unlocked = False
                    self.xn_mode = 'ying'
                    self.ying = 0
                    self.hide(4.3)
                    self.add_con(XN, 0.4)
                    rec('心万阙垂天')
                    if 'hudlost' in FAILS and self.cycle == 1:
                        self.hud_dead = True
                        rec('!!关卡结束(HUD 没了)')
                elif self.xn_mode == 'zhao':
                    rec('!!心借相(按早了)')

    def jx_r_ready(self):
        if 'jxr' in FAILS and getattr(self, 'jxr_cycle', None) is None and self.fail_once('jxr'):
            self.jxr_cycle = self.cycle
            self.jxr_until = time.time() + 12
        return time.time() >= getattr(self, 'jxr_until', 0)

    def tap(self):
        self.tick()
        if not self.hud_visible() or self.busy():
            return
        who = self.on_field
        self.a_count += 1
        self.add_con(who, 0.06)
        if who == XN and self.xn_mode == 'ying':
            if self.fail_once('ying') or getattr(self, 'ying_miss', 0) > 0:
                self.ying_miss = getattr(self, 'ying_miss', 2) - 1
                return
            self.ying_hits.append((time.time() + 0.3, 13))
        elif who == XN and self.xn_mode == 'zhao' and self.lin_unlocked and self.r2_need_taps > 0:
            self.r2_need_taps -= 1
            if self.r2_need_taps == 0:
                rec('心大招能量满(万阙垂天)')
        elif who == XN and self.xn_mode == 'zhao' and self.tongyu:
            self.zhao_xin = max(0, self.zhao_xin - 55)
        elif who == JX:
            now = time.time()
            if now < self.jx_lx_until:
                self.jx_lx_combo += 1
                self.lock_until = now + 0.35
                if self.jx_lx_combo >= 4:
                    self.jx_lx_until = 0.0
                    self.jx_youlong_until = now + 5.0
                    rec('今汐凌霄第4段(游龙廻光)')
            else:
                if now - self.jx_norm_last > 1.0:
                    self.jx_norm_combo = 0
                self.jx_norm_last = now
                self.jx_norm_combo += 1
                self.lock_until = now + 0.35
                if self.jx_norm_combo >= 4:
                    self.jx_norm_combo = 0
                    self.jx_shenni_until = now + 5.0
                    rec('今汐普通第4段(神霓飞芒窗口)')
        elif who == SK:
            self.sk_core += 1
            if self.sk_core >= SK_CORE_FULL and not self.sk_forte:
                self.sk_forte = True
                rec('守回路满')


def _break_ready(self):
    return BREAK and time.time() >= self.break_cd


Game.break_ready = _break_ready
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

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        key = str(key)
        end = time.time() + max(down_time, 0.003)
        G.press(key, down_time)
        if key in ('e', 'q', 'r') and down_time >= 0.2:
            while time.time() < end:
                time.sleep(0.02)
                G.tick()
                if not G.busy() and G.hud_visible():
                    before = len(LOG)
                    G.press(key, down_time)
                    if len(LOG) > before:
                        break
        left = end - time.time()
        if left > 0:
            time.sleep(left)

    def send_key_down(self, key, after_sleep=0):
        pass

    def send_key_up(self, key, after_sleep=0):
        pass

    def mouse_down(self, *a, **kw):
        if kw.get('key') == 'right':
            rec(f'闪避({G.on_field})')
            return
        G.tick()
        if not G.holding:
            G.holding = True
            G.hold_at = time.time()
            G.normal_heavy_done = False
            G.heavy_fail = None
            G.lin_checked = False
        time.sleep(0.002)

    def mouse_up(self, *a, **kw):
        if kw.get('key') == 'right':
            return
        if G.holding and time.time() - G.hold_at < 0.25:
            G.tap()
        if G.on_field == SK and time.time() - G.hold_at >= 0.3:
            G.sk_forte = False
            G.sk_core = 0
        G.holding = False
        G.lin_block_until = 0

    def next_frame(self):
        time.sleep(0.03)
        G.tick()

    def in_team(self):
        return (G.hud_visible(), G.on_field, 3) if G.hud_visible() else (False, -1, 0)

    def get_current_con(self):
        return G.con[G.on_field]

    def get_cd(self, name, index=None):
        if name == 'resonance' and G.on_field == JX:
            now = time.time()
            if now < G.jx_ecd_until:
                return G.jx_ecd_until - now
            if now < G.jx_lx_until:
                return max(0.0, G.jx_zhutian_cd - now)
            if now < G.jx_shenni_until or now < G.jx_youlong_until:
                return 0.0
            return max(0.0, G.e_cd[JX] - now)
        return max(0.0, G.e_cd[G.on_field] - time.time()) if name == 'resonance' else 0.0

    def find_one(self, name, threshold=0.0, **kw):
        if name in ('f_break_full', 'f_break'):
            return (G.hud_visible() and G.break_ready()) or None
        if G.on_field != XN or not G.hud_visible():
            return None
        if name == 'hsin_h1':
            return (G.xn_mode == 'ying' and G.ying >= 100 and G.burned_at is None) or None
        if name == 'hsin_lib1':
            return G.xn_mode == 'ying' or None
        if name == 'hsin_lib2':
            return (G.xn_mode == 'zhao' and G.lin_unlocked and G.r2_need_taps <= 0) or None
        return None

    def add_freeze_duration(self, *a, **kw):
        pass

    def box_of_screen(self, *a, **kw):
        return None

    def is_pick_f(self):
        return False

    def available(self, name, check_color=True, check_cd=True):
        who = G.on_field
        if name == 'liberation':
            if who == SK:
                return G.lib_energy[SK] and time.time() >= G.sk_r_cd
            if who == JX:
                return time.time() >= G.jx_r_cd and G.jx_r_ready()
            if who == XN:
                return (G.xn_mode == 'ying' and G.r1_unlocked) or G.xn_mode == 'zhao'
        if name == 'resonance':
            return G.e_cd[who] <= time.time()
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
        return G.on_field == SK and G.sk_forte and G.hud_visible()


task = FakeTask()

from src.char.BaseChar import CharType  # noqa: E402
from src.char import CustomCharLoader as L  # noqa: E402
from src.char.Hsin import Hsin as NHsin  # noqa: E402
from src.char.Jinhsi import Jinhsi as NJinhsi  # noqa: E402
from src.char.ShoreKeeper import ShoreKeeper as NShore  # noqa: E402

team = ('Hsin', 'Jinhsi', 'ShoreKeeper')
CXn = L.load_team_char_class(NHsin, team)
CJx = L.load_team_char_class(NJinhsi, team)
CSk = L.load_team_char_class(NShore, team)


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
    c.is_current_char = index == SK
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
                               timeline_logged=True, sk_intro_done_at=0.0, jx_r_pending=False))
    return c


sk = build(CSk, SK, 'char_shorekeeper', CharType.HEALER)
jx = build(CJx, JX, 'char_jinhsi', CharType.MAIN_DPS)
xn = build(CXn, XN, 'char_hsin', CharType.MAIN_DPS)
task.chars = [sk, jx, xn]
for c in task.chars:
    c.add_freeze_duration = lambda *a, **kw: None
    c.time_elapsed_accounting_for_freeze = lambda start, *a, **kw: time.time() - start
    c.switch_out = types.MethodType(lambda self, con_full=False: setattr(self, 'is_current_char', False), c)
xn.game_foreground = lambda: False


def gauge():
    if G.on_field != XN or G.xn_mode != 'ying' or 'gauge' in FAILS:
        return 0.0, 0.0
    v = G.ying
    if v >= 100:
        return 0.42, 0.42          # 满了条是粉红高柱, 紫色判据读成 0.4 上下(实机)
    return min(1.0, v / 50) * 0.88, max(0.0, (v - 50) / 50) * 0.88


xn.xn_gauge = gauge


def jx_icon(_jx):
    if G.on_field != JX or not G.hud_visible():
        return None
    xn.hud_seen_at = time.time()      # 真的 jx_e_icon 认到今汐在场会记下 HUD 时刻(看门狗)
    now = time.time()
    if now < G.jx_ecd_until:
        return 'jinglong'              # 冷却数字盖在图标上, 实机认成惊龙破空
    if now < G.jx_lx_until:
        return 'zhutian'
    if now < G.jx_youlong_until:
        return 'jinglong'
    if now < G.jx_shenni_until:
        return 'shenni'
    return 'liuguang'


xn.jx_e_icon = jx_icon


def slot_state():
    if G.on_field != XN or not G.hud_visible():
        return None
    xn.hud_seen_at = time.time()
    if G.xn_mode == 'zhao' and G.tongyu:
        return 'ready' if G.zhao_xin == 0 else 'statue'
    return '?'


xn.xn_slot_state = slot_state


def sk_segs():
    if G.on_field != SK or not G.hud_visible() or G.sk_forte:
        return 0
    return min(5, G.sk_core * 5 // SK_CORE_FULL)


xn.sk_forte_segs = sk_segs

ok = True
try:
    G.cycle = 0
    sk.do_perform()
    for n in range(1, DRY_LOOPS + 1):
        G.cycle = n
        who = [c for c in task.chars if c.is_current_char]
        (who[0] if who else sk).do_perform()
except Exception:
    import traceback
    traceback.print_exc()
    ok = False

for t, w in LOG:
    if w.startswith('[info]') and 'timeline' in w:
        print(f'{t:7.2f}  [info] timeline:')
        for part in w.split(': ', 1)[1].split(' '):
            print(f'           {part}')
    else:
        print(f'{t:7.2f}  {w}')
print('OK' if ok else 'CRASHED', f'total {time.time() - T0[0]:.1f}s')
