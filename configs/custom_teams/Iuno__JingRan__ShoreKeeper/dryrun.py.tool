"""景月守轴的干跑: 假 task + 假读屏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是等到了判据, 还是撞上限出来的
  - 景燃按住左键那一段: 重击/E/闪避是不是按假游戏的资源规则串起来的
用法: <ok-ww 自带 python> dryrun.py.tool <队伍目录>
环境变量:
  DRY_LOOPS=3            循环几圈
  DRY_SI=1               假装游戏在前台, 景燃的长按走 SendInput 那条路
  DRY_EXEC=1             景燃重击3 打完时有处决可打
  DRY_NO_PROMPT=1        读不到景燃的重击提示(测 blind 兜底)
  DRY_NO_STANCE=1        读不到景燃的形态徽记(只剩 E 框 CD 跳变)
  DRY_JR_NO_R=1          景燃大招能量不够
  DRY_HOLD_DROP=1        大招演出里按下的 PostMessage 左键被游戏丢掉(测重按)
  DRY_QINGYUN=0          尤诺带海之女(不切尤诺点 Q)
  DRY_CHAIN2=1           启动轴二链重击
  DRY_FAIL=a,b,...       故意失败(每圈各触发一次), 测保底分支:
                         sw      切尤诺/切景燃第一下被拒 2.8s(被击飞/硬直)
                         skheavy 守岸人第一发重击没接住(指示一直亮)
                         iue     尤诺告终喧响头 2.6s 的 E 全被吞
                         iujump  尤诺头 1.9s 的跳全被吞
                         iur     尤诺 R 亮着但头 2.2s 的 R 全被吞
                         iubow   尤诺第二发弓 E 头 1.2s 被吞
                         iuheavy 尤诺重击第一次没接住
                         jre     景燃第一个 E 头 5.5s 全被吞
                         hudlost 循环 #2 尤诺 R 之后关卡结束(HUD 再也不回来)
"""
import os
import random
import sys
import threading
import time
import importlib.util

def _okww_working():
    """框架 working 目录(有 src/char/CustomCharLoader.py 的那层): OKWW_WORKING > 本文件往上找 > 旧版 OKWW 的默认位置。
    coco-ww 是平铺结构, 往上找到的就是安装目录本身; 以前写死旧版路径, 在 coco-ww 里跑会加载到旧框架(没有 MacroKit)。"""
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


sys.path.insert(0, _okww_working())
sys.stdout.reconfigure(encoding='utf-8')
from src.char.BaseChar import CharType

TEAM = sys.argv[1]
DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '3'))
SI = os.environ.get('DRY_SI', '0') == '1'
EXEC = os.environ.get('DRY_EXEC', '1') == '1'
NO_PROMPT = os.environ.get('DRY_NO_PROMPT', '0') == '1'
NO_STANCE = os.environ.get('DRY_NO_STANCE', '0') == '1'
JR_NO_R = os.environ.get('DRY_JR_NO_R', '0') == '1'
HOLD_DROP = os.environ.get('DRY_HOLD_DROP', '0') == '1'
QINGYUN = os.environ.get('DRY_QINGYUN', '0') == '1'
CHAIN2 = os.environ.get('DRY_CHAIN2', '0') == '1'
# 能量差多少下攻击 R 才亮(实机 2026-09-28: 尤诺/景燃共鸣效率不够, R 没亮)
IU_R_SHORT = int(os.environ.get('DRY_IU_R_SHORT', '0'))
JR_R_SHORT = int(os.environ.get('DRY_JR_R_SHORT', '0'))
# 守岸人强化变奏入场藏 HUD 的时长(实机 3.2s)
SK_QTE_HIDE = float(os.environ.get('DRY_SK_QTE_HIDE', '3.2'))
# E 放出去之后提示闪一帧(实机 L1 把它判成了重击出手)
PROMPT_BLIP = os.environ.get('DRY_PROMPT_BLIP', '1') == '1'
FAILS = set(x for x in os.environ.get('DRY_FAIL', '').split(',') if x)
random.seed(7)

LOG = []
T0 = [time.time()]
NAMES = ['景', '月', '守']
JR, IU, SK = 0, 1, 2


def rec(what):
    LOG.append((time.time() - T0[0], what))


class FakeInteraction:
    def __init__(self, task):
        self.task = task

    def send_key_down(self, key, activate=True):
        self.task.key_event(str(key), activate)

    def send_key_up(self, key):
        pass


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

    def __init__(self, task):
        self.interaction = FakeInteraction(task)

    def check_enabled(self, check_pause=False):
        pass


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor(self)
        self.chars = []
        self.char_config = {}
        self.key_config = {'Echo Key': 'q', 'Liberation Key': 'r', 'Resonance Key': 'e',
                           'Jump Key': 'space', 'Dodge Key': 'lshift'}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self.can_break = False
        self._f_last_press = -1
        self.frame = None
        self.on_field = JR
        self._pending = None
        self.arrived_at = 0.0
        self.hud_hidden_until = 0.0
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.con = {JR: 0.0, IU: 0.0, SK: 0.0}
        self.lib_used = {}
        self.lib_cd = {JR: 25, IU: 25, SK: 25}
        self.echo_cd = {}
        self.e_cd = {}
        self.holding = False          # PostMessage 按住
        self.si_down = False          # SendInput 按住
        self.hold_at = 0.0
        self.hold_valid = True
        # 守岸人
        self.sk_core = 0
        self.sk_field_until = 0.0
        # 尤诺
        self.iu_e2_until = 0.0
        self.iu_moon_until = 0.0
        self.iu_bow = False
        self.iu_charges = 0
        self.iu_charge_cd_until = 0.0
        self.iu_heavy_ready = False
        self.iu_jumped = False
        # 景燃
        self.jr_qi = 300 if CHAIN2 else 0
        self.jr_stance = 'yang'
        self.jr_xt = 0
        self.jr_ecd = {'yang': 0.0, 'yin': 0.0}
        self.jr_busy_until = 0.0
        self.jr_act = None
        self.jr_act_end = None
        self.jr_air = False           # 破秽: E 之后在空中
        self.jr_chain = 0             # 下一下普攻是第几段(2..4)
        self.jr_exec_ready = False
        self.jr_heavies = 0
        self.fail_used = set()
        self.block = {}               # 名字 -> 被吞到什么时候
        self.cycle = 0
        self.hud_dead = False

    def fail_once(self, name):
        """name 在 DRY_FAIL 里而且这一圈还没用过 -> True(这一圈只触发一次)。"""
        if name.rstrip('0123456789') not in FAILS or (name, self.cycle) in self.fail_used:
            return False
        self.fail_used.add((name, self.cycle))
        rec(f'!!故意失败:{name}')
        return True

    def blocked(self, name, span):
        """第一次调用时(如果这圈要失败)开一个 span 秒的"被吞"窗口; 窗口里返回 True。"""
        now = time.time()
        if name not in self.block and self.fail_once(name):
            self.block[name] = now + span
        return now < self.block.get(name, 0)

    # ---------------- 时间推进 ----------------
    def add_con(self, i, v):
        self.con[i] = min(1.0, self.con[i] + v)

    def locked(self):
        now = time.time()
        return now < self.lock_until or now < self.hud_hidden_until

    def attack_held(self):
        return (self.holding or self.si_down) and self.hold_valid

    def tick(self):
        now = time.time()
        if self._pending and now >= self._pending[1]:
            src, dst = self.on_field, self._pending[0]
            self._pending = None
            intro = self.con[src] >= 1.0
            if intro:
                self.con[src] = 0.0
            if src == JR:
                self.jr_act = None
            self.on_field = dst
            self.arrived_at = now
            rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
            if intro:
                if dst == SK:
                    self.lock_until = now + 1.43
                    if now < self.sk_field_until:
                        self.hud_hidden_until = now + SK_QTE_HIDE
                        self.lock_until = now + SK_QTE_HIDE
                        self.sk_qte_back = now + SK_QTE_HIDE
                elif dst == IU:
                    self.iu_r_need = IU_R_SHORT
                    self.lock_until = now + 1.30
                    if now >= self.iu_moon_until:
                        self.iu_e2_until = now + 5.0
                else:
                    self.jr_r_need = JR_R_SHORT
                    self.lock_until = now + 1.0
                    self.jr_qi = min(300, self.jr_qi + 100)
        if self.on_field == JR:
            self.jr_tick(now)

    def jr_tick(self, now):
        # 当前动作结束
        if self.jr_act is not None and now >= self.jr_busy_until:
            act = self.jr_act
            self.jr_act = None
            if act == 'heavy':
                self.jr_stance = 'yin' if self.jr_stance == 'yang' else 'yang'
                self.jr_chain = 2
                rec(f'景重击完->{self.jr_stance}')
                if self.jr_heavies == 3 and EXEC:
                    self.jr_exec_ready = True
            elif act == 'E':
                pass
        if self.jr_act is not None or self.locked():
            return
        if not self.attack_held():
            return
        if self.jr_qi >= 300:
            self.jr_qi -= 300
            if self.jr_xt > 0:
                self.jr_qi = min(300, self.jr_qi + 200)
                self.jr_xt -= 1
            self.jr_act = 'heavy'
            self.jr_r_need = getattr(self, 'jr_r_need', 0) - 3
            self.jr_busy_until = now + 1.8
            self.jr_heavies += 1
            self.add_con(JR, 0.2)
            rec(f'景重击{self.jr_heavies}出手({self.jr_stance})')
            return
        if self.jr_air:
            self.jr_air = False
            self.jr_qi = min(300, self.jr_qi + 100)
            self.jr_act = 'drop'
            self.jr_busy_until = now + 1.0
            rec('景黄泉渡/往生送')
            return
        seg = self.jr_chain if self.jr_chain else 1
        self.jr_r_need = getattr(self, 'jr_r_need', 0) - 1
        self.jr_act = f'A{seg}'
        if seg in (3, 4):
            self.jr_qi = min(300, self.jr_qi + 50)
        self.add_con(JR, 0.05)
        self.jr_busy_until = now + {1: 0.4, 2: 0.5, 3: 0.6, 4: 0.9}[seg]
        self.jr_chain = seg + 1 if seg < 4 else 1
        rec(f'景A{seg}(炁{self.jr_qi})')

    # ---------------- 读屏 ----------------
    def in_team(self):
        self.tick()
        if self.hud_dead or time.time() < self.hud_hidden_until:
            return False, -1, 0
        return True, self.on_field, 3

    def next_frame(self):
        time.sleep(0.016)
        self.tick()

    def find_mouse_forte(self):
        self.tick()
        f = self.on_field
        if f == SK:
            if self.holding and self.sk_core >= 5 and time.time() - self.hold_at >= 0.38 \
                    and getattr(self, 'sk_block_hold', None) != self.hold_at:
                if self.fail_once('skheavy'):
                    self.sk_block_hold = self.hold_at
                    return True
                self.sk_core = 0
                self.add_con(SK, 0.15)
                rec('守强化重击出手')
            return self.sk_core >= 5
        if f == JR:
            if PROMPT_BLIP and 0 < time.time() - getattr(self, 'jr_e_at', -9) < 0.05:
                return True
            return (not NO_PROMPT) and self.jr_qi >= 300
        return False

    def find_feature(self, name, box=None, threshold=0.6):
        self.tick()
        if name == 'iuno_heavy':
            if self.on_field == IU and self.iu_heavy_ready and self.holding and time.time() - self.hold_at >= 0.3 \
                    and getattr(self, 'iu_block_hold', None) != self.hold_at:
                if self.fail_once('iuheavy'):
                    self.iu_block_hold = self.hold_at
                    return True
                self.iu_heavy_ready = False
                rec('月重击出手')
            return self.on_field == IU and self.iu_heavy_ready
        if name == 'iuno_jump':
            return self.on_field == IU and time.time() < self.iu_moon_until and not self.iu_jumped
        return None

    def get_cd(self, name, index=None):
        self.tick()
        now = time.time()
        f = self.on_field
        if name != 'resonance':
            return 0.0
        if f == JR:
            left = self.jr_ecd[self.jr_stance] - now
            return left if left > 0 else -0.03
        if f == IU:
            if self.iu_bow:
                return max(0.0, self.iu_charge_cd_until - now) if self.iu_charges < 2 else 0.0
            return max(0.0, self.e_cd.get(IU, 0) - now)
        return max(0.0, self.e_cd.get(f, 0) - now)

    def get_current_con(self):
        self.tick()
        return self.con[self.on_field]

    # ---------------- 输入 ----------------
    def key_event(self, k, activate):
        now = time.time()
        f = self.on_field
        if self.si_down is False and self.holding and activate:
            pass
        if k == 'e' and f == JR:
            if not activate:
                rec('E(na)')
            cancelable = self.jr_act is None or self.jr_act.startswith('A')
            if cancelable and not self.locked() and now >= self.jr_ecd[self.jr_stance] \
                    and self.jr_heavies >= 1 and self.blocked('jre', 5.5):
                return
            if cancelable and not self.locked() and now >= self.jr_ecd[self.jr_stance]:
                self.jr_ecd[self.jr_stance] = now + 15
                self.jr_act = 'E'
                self.jr_busy_until = now + 0.5
                self.jr_air = True
                self.jr_e_at = now
                self.add_con(JR, 0.1)
                rec(f'景E出手({self.jr_stance})')
            return
        if k == 'lshift' and f == JR:
            rec('景闪避')
            if self.jr_act == 'A4':
                self.jr_act = None
                self.jr_busy_until = now
                self.jr_chain = 1
                rec('景闪避断A4')
            return
        if k == 'f':
            rec('F')
            if f == JR and self.jr_exec_ready and (self.jr_act is None or self.jr_act.startswith('A')):
                self.jr_exec_ready = False
                self.hud_hidden_until = now + 1.6
                rec('景处决出手')
            return
        self.send_key(k, activate=activate)

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0, activate=True):
        k = str(key)
        self.tick()
        now = time.time()
        f = self.on_field
        if activate and self.holding and not self.si_down:
            # PostMessage 后端: 按住期间投 WM_ACTIVATE 会把按住打断
            self.hold_valid = False
            rec('!!按住被 activate 打断')
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            busy = f == JR and self.jr_act == 'heavy'
            if idx != f and idx in (IU, JR) and self._pending is None and self.blocked(f'sw{idx}', 2.8):
                pass
            elif idx != f and self._pending is None and now >= self.no_switch_until and not busy:
                self._pending = (idx, now + 0.10)
        elif k in ('e', 'lshift', 'f') and f == JR:
            self.key_event(k, True)
        elif self.locked():
            pass
        elif k == 'e':
            if f == SK and now > self.e_cd.get(SK, 0):
                self.e_cd[SK] = now + 16
                self.add_con(SK, 0.2)
                self.lock_until = now + 0.3
                rec('守E出手')
            elif f == IU and now < self.iu_e2_until and self.blocked('iue', 2.6):
                pass
            elif f == IU and self.iu_bow and self.iu_charges == 1 and self.blocked('iubow', 1.2):
                pass
            elif f == IU:
                if now < self.iu_e2_until:
                    self.iu_e2_until = 0.0
                    self.iu_jumped = False
                    self.e_cd[IU] = now + 8
                    self.iu_moon_until = now + 15
                    self.lock_until = now + 0.1
                    self.add_con(IU, 0.1)
                    rec('月E告终喧响出手')
                elif self.iu_bow and self.iu_charges > 0:
                    self.iu_charges -= 1
                    if self.iu_charges == 1:
                        self.iu_charge_cd_until = now + 10
                    self.lock_until = now + 1.33
                    self.add_con(IU, 0.25)
                    rec(f'月弓E出手(剩{self.iu_charges})')
                    if self.iu_charges == 0:
                        self.iu_heavy_ready = True
                        self.con[IU] = 1.0
        elif k == 'q':
            if now > self.echo_cd.get(f, 0):
                self.echo_cd[f] = now + 20
                rec(f'Q出手@{NAMES[f]}')
        elif k == 'r':
            ok = now - self.lib_used.get(f, -99) > self.lib_cd[f]
            if f == JR and (JR_NO_R or getattr(self, 'jr_r_need', 0) > 0):
                ok = False
            if f == IU and getattr(self, 'iu_r_need', 0) > 0:
                ok = False
            if ok and f == IU and self.blocked('iur', 2.2):
                ok = False
            if ok:
                self.lib_used[f] = now
                rec(f'R出手@{NAMES[f]}')
                if f == SK:
                    self.hud_hidden_until = now + 3.0
                    self.no_switch_until = now + 2.97
                    self.sk_field_until = now + 60
                    self.add_con(SK, 0.35)
                elif f == IU:
                    self.hud_hidden_until = now + 3.9
                    self.no_switch_until = now + 3.9
                    if self.cycle == 2 and self.fail_once('hudlost'):
                        self.hud_dead = True
                        rec('!!关卡结束(HUD 再也不回来)')
                    self.iu_bow = self.iu_jumped
                    if not self.iu_jumped:
                        rec('!!月没跳就放了R(没换弓)')
                    self.iu_charges = 2
                    self.iu_moon_until = now + 15
                else:
                    self.hud_hidden_until = now + 3.0
                    self.no_switch_until = now + 3.5
                    self.jr_qi = min(300, self.jr_qi + 200)
                    self.jr_xt = 3
                    self.jr_heavies = 0
                    if HOLD_DROP and self.holding and not self.si_down:
                        self.hold_valid = False
                        rec('!!演出里的 PostMessage 左键按下被丢掉')
        elif k == 'lshift':
            rec(f'闪避@{NAMES[f]}')
            self.lock_until = now + 0.35          # 闪避动作里按跳会被吞
        elif k == 'space':
            rec(f'跳@{NAMES[f]}')
            if f == IU and now < self.iu_moon_until and not self.iu_jumped and self.blocked('iujump', 1.9):
                pass
            elif f == IU and now < self.iu_moon_until and not self.iu_jumped:
                self.iu_jumped = True
                self.lock_until = now + 1.2
                rec('月跳换形态出手')
        if after_sleep:
            time.sleep(after_sleep)

    def jump(self, after_sleep=0.01):
        self.send_key('space', after_sleep=after_sleep)

    def get_resonance_key(self):
        return 'e'

    def get_echo_key(self):
        return 'q'

    def get_liberation_key(self):
        return 'r'

    def click(self, *a, **kw):
        self.tick()
        if self.locked():
            return
        f = self.on_field
        if f == SK:
            self.sk_core = min(5, self.sk_core + 1)
            self.add_con(SK, 0.04)
        elif f == IU:
            self.add_con(IU, 0.03)
            self.iu_r_need = getattr(self, 'iu_r_need', 0) - 1

    def mouse_down(self, *a, **kw):
        self.tick()
        rec(f'hold_down@{NAMES[self.on_field]}')
        self.holding = True
        self.hold_valid = not (HOLD_DROP and time.time() < self.hud_hidden_until)
        self.hold_at = time.time()

    def mouse_up(self, *a, **kw):
        self.tick()
        now = time.time()
        f = self.on_field
        held = now - self.hold_at
        if f == SK and self.sk_core >= 5 and held >= 0.2 and getattr(self, 'sk_block_hold', None) != self.hold_at:
            self.sk_core = 0
            self.add_con(SK, 0.15)
            rec('守强化重击出手')
        elif f == IU and self.iu_heavy_ready and held >= 0.2 and getattr(self, 'iu_block_hold', None) != self.hold_at:
            self.iu_heavy_ready = False
            rec('月重击出手')
        elif f in (SK, IU):
            self.click()
        if self.holding and held < 0.1 and f != JR:
            self.click()
        self.holding = False
        self.hold_valid = True

    def si(self, up):
        rec(f'SI_{"up" if up else "down"}')
        self.si_down = not up
        if not up:
            self.hold_valid = True
        return True

    def add_freeze_duration(self, *a, **kw):
        pass

    def box_of_screen_scaled(self, *a, **kw):
        raise RuntimeError('no screen')

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return time.time() - start


def load(name):
    p = rf'{TEAM}\{name}.py'
    spec = importlib.util.spec_from_file_location(f'dry_{name}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m, getattr(m, name)


task = FakeTask()
jr_mod, JRCls = load('JingRan')
_, IUCls = load('Iuno')
_, SKCls = load('ShoreKeeper')
if SI:
    jr_mod.send_input_left = task.si
JING = JRCls(task, JR, char_name='char_jingran', char_type=CharType.MAIN_DPS)
IUNO = IUCls(task, IU, char_name='char_iuno', char_type=CharType.SUB_DPS)
SHO = SKCls(task, SK, char_name='char_shorekeeper', char_type=CharType.HEALER)
task.chars = [JING, IUNO, SHO]
JING.IU_QINGYUN = QINGYUN
JING.JR_CHAIN2_OPENER = CHAIN2
if SI:
    JING.game_foreground = lambda: True


def fake_stance():
    if NO_STANCE:
        return None, 0.0, 0.0
    task.tick()
    if task.on_field != JR or (task.jr_act == 'heavy' and random.random() < 0.3):
        return None, 0.0, 0.0
    return task.jr_stance, 0.3, 0.0


JING.jr_stance = fake_stance


def bar_lag():
    return time.time() - task.arrived_at < 0.25


for c in (JING, IUNO, SHO):
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
    i = c.index
    c.liberation_available = lambda i=i: (time.time() - task.lib_used.get(i, -99) > task.lib_cd[i]
                                          and not (i == IU and getattr(task, 'iu_r_need', 0) > 0)
                                          and not (i == JR and (JR_NO_R or getattr(task, 'jr_r_need', 0) > 0)))
    c.echo_available = lambda i=i: not bar_lag() and time.time() > task.echo_cd.get(i, 0)
SHO.resonance_available = lambda: not bar_lag() and task.on_field == SK and time.time() > task.e_cd.get(SK, 0)
IUNO.resonance_available = lambda: (not bar_lag() and task.on_field == IU
                                    and (time.time() < task.iu_e2_until or time.time() > task.e_cd.get(IU, 0)))
JING.is_current_char = True


def run(label, fn, *args):
    task.cycle += 1
    task.block = {}
    LOG.clear()
    T0[0] = time.time()
    t = time.time()
    try:
        fn(*args)
    except Exception:
        import traceback
        traceback.print_exc()
    wall = time.time() - t
    keys = [f'{ts:.2f}{w}' for ts, w in LOG if any(s in w for s in ('出手', '到场', '闪避', '完->', '!!', '黄泉', 'SI_'))]
    print('  ' + ' '.join(keys))
    print(f'=== {label}: 墙钟 {wall:.2f}s, 场上={NAMES[task.on_field]}, 景燃重击 {task.jr_heavies}')
    print('  ' + JING.macro_timeline())


run('启动轴', JING.drive, JING)
for n in range(DRY_LOOPS):
    run(f'循环 #{n + 1}', JING.drive, task.chars[task.on_field])
