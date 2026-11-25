"""露丽守轴的干跑: 假 task + 假读屏, 真跑一遍启动轴和几圈循环。

验证的是【调用链、判据分支和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 每一格是不是等到了该等的判据(不是撞上限出来的)
  - 露西强化重击期间 HUD 读数闪一帧时 R 会不会被误判成放出去
用法: <ok-ww 自带 python> dryrun.py.tool <队伍目录>
"""
import threading
import sys, time, random, importlib.util
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
import os
# 丽贝卡切进来之后多久才接受 R(实机见过 >1.5s); 设成 >3.0 可以走到"窗口结束后 R 晚到"那条分支
RB_R_DELAY = float(os.environ.get('DRY_RB_R_DELAY', '1.6'))
DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '3'))
# 回路重击被怪打断的概率(打断了根协议不清空, 指示还亮着)
HEAVY_INTERRUPT = float(os.environ.get('DRY_HEAVY_INTERRUPT', '0.5'))
# 收尾时露西大招能量差多少下普攻(强化E 放出去时开始算); 0 = 能量够
LU_R_SHORT = int(os.environ.get('DRY_LU_R_SHORT', '0'))
# 回路重击打出去后出现处决机会的概率, 以及松手后多久出现(实机常落在头 2.6s 里)
EXEC_PROB = float(os.environ.get('DRY_EXEC_PROB', '1.0'))
# 按强化E 时被打断的概率(每次按下都掷一次)
E3_INTERRUPT = float(os.environ.get('DRY_E3_INTERRUPT', '0'))
# 露西进场头 0.6s E 框被特效染金(1 = 开)
GOLD_FLASH = os.environ.get('DRY_GOLD_FLASH', '1') == '1'
# 露西最后一下普攻之后多久内按强化E 无效(模拟 A3 第46F前禁用E)
A3_ELOCK = float(os.environ.get('DRY_A3_ELOCK', '0.77'))
# 丽贝卡强化重击被打断的模拟: R 能量差多少下普攻, 以及狂热是不是还满着(还能再补一发强化重击)
RB_R_SHORT = int(os.environ.get('DRY_RB_R_SHORT', '0'))
RB_FERVOR_LEFT = os.environ.get('DRY_RB_FERVOR_LEFT', '0') == '1'
# 丽贝卡 QR 入场时的协奏(强化重击出手时设成这个值; <0 = 不改, 照旧 +0.35)
RB_CON_ENTRY = float(os.environ.get('DRY_RB_CON_ENTRY', '-1'))
# 守岸人每下普攻涨多少协奏(大 = 很快到 80% 走协奏捷径; 小 = 走 重击闪避AA 正常收尾)
SK_A_CON = float(os.environ.get('DRY_SK_A_CON', '0.03'))
EXEC_DELAY = float(os.environ.get('DRY_EXEC_DELAY', '1.0'))
LOG = []
T0 = [time.time()]
NAMES = ['露', '丽', '守']
LU, RB, SK = 0, 1, 2
random.seed(7)


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

    def check_enabled(self, check_pause=False):
        pass


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor()
        self.chars = []
        self.key_config = {'Echo Key': 'q', 'Liberation Key': 'r', 'Resonance Key': 'e',
                           'Jump Key': 'space', 'Dodge Key': 'lshift'}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self.can_break = False
        self.has_lavitator = False
        self._f_last_press = -1
        self.on_field = LU
        self._pending = None
        self.arrived_at = 0.0
        self.hud_hidden_until = 0.0
        self.lock_until = 0.0
        self.no_switch_until = 0.0
        self.con = {LU: 0.3, RB: 0.3, SK: 0.0}
        self.lib_used = {}
        self.lib_cd = {LU: 20, RB: 20, SK: 25}
        self.e_cd = {}
        self.echo_cd = {}
        self.holding = False
        self.hold_at = 0.0
        self.sk_core = 0.0
        self.lu_proto = 60
        self.lu_clear_at = 0.0
        self.lu_enh = False
        self.lu_root = 0
        self.lu_stage = 'E1'
        self.lu_heavy_until = 0.0
        self.panel = None

    def add_con(self, i, v):
        self.con[i] = min(1.0, self.con[i] + v)

    def locked(self):
        now = time.time()
        return now < self.lock_until or now < self.hud_hidden_until

    def in_team(self):
        now = time.time()
        if self._pending and now >= self._pending[1]:
            src, dst = self.on_field, self._pending[0]
            self._pending = None
            intro = self.con[src] >= 1.0
            if intro:
                self.con[src] = 0.0
            self.on_field = dst
            self.arrived_at = now
            rec(f'到场{NAMES[dst]}{"(变奏)" if intro else ""}')
            if intro:
                if dst == SK:
                    self.hud_hidden_until = now + 3.2       # 强化QTE 隐藏 HUD
                    self.lock_until = now + 3.2
                elif dst == RB:
                    self.lock_until = now + 0.9
                else:
                    self.lock_until = now + 0.75
        if now < self.hud_hidden_until:
            return False, -1, 0
        # 露西回路重击的特效: HUD 读数偶尔闪一帧
        if self.on_field == LU and now < self.lu_heavy_until and random.random() < 0.15:
            return False, -1, 0
        return True, self.on_field, 3

    def next_frame(self):
        time.sleep(0.016)

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        k = str(key)
        now = time.time()
        f = self.on_field
        rec(f'key:{k}@{NAMES[f]}')
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            if (idx != f and self._pending is None and now >= self.no_switch_until
                    and self.panel is None and now >= self.lu_heavy_until - 4.0):
                self._pending = (idx, now + 0.10)
        elif self.locked():
            pass
        elif k == 'e':
            if f == SK and now > self.e_cd.get(SK, 0):
                self.e_cd[SK] = now + 8
                self.add_con(SK, 0.2)
                self.lock_until = now + 0.67
                rec('守E出手')
            elif f == RB and now > self.e_cd.get(RB, 0):
                self.e_cd[RB] = now + 4
                self.lock_until = now + 0.6
                self.add_con(RB, 0.1)
                rec('丽霰弹E出手')
            elif f == LU:
                if self.lu_proto >= 100 and not self.lu_enh and now - getattr(self, 'lu_last_a', -9) < A3_ELOCK:
                    pass                               # A3「第46F前禁用E」: 刚打完普攻那一截按 E 没反应
                elif self.lu_proto >= 100 and not self.lu_enh and random.random() < E3_INTERRUPT:
                    self.lock_until = now + 0.8        # 按的时候被怪打断: 没出去, 回路还满着
                    rec('露强化E被打断')
                elif self.lu_proto >= 100 and not self.lu_enh:
                    self.lu_enh = True
                    self.lu_r_need = LU_R_SHORT
                    self.e_cd[LU] = now + 15           # 按出去 E 框出 CD 数字
                    self.hud_hidden_until = now + 1.3  # 实机 23:17: 强化E 一出手队伍 HUD 就没了
                    self.lock_until = now + 2.8
                    self.no_switch_until = now + 2.8
                    self.lu_clear_at = now + 1.47
                    self.add_con(LU, 0.1)
                    rec('露强化E出手')
                elif self.lu_stage == 'E1' and now > self.e_cd.get(LU, 0) and not self.lu_enh:
                    self.lu_stage = 'E2'
                    self.lock_until = now + 0.77
                    self.e_cd[LU] = now + 1.5          # E1-2 第30F减少E1冷却90%: 读得到一小段 CD
                    self.lu_proto = min(100, self.lu_proto + 20)
                    rec('露E1出手')
                elif self.lu_stage == 'E2' and not self.lu_enh:
                    self.lu_stage = 'E1'
                    self.e_cd[LU] = now + 6
                    self.lock_until = now + 1.07
                    self.lu_proto = min(100, self.lu_proto + 30)
                    rec('露E2出手')
        elif k == 'q':
            if now > self.echo_cd.get(f, 0):
                self.echo_cd[f] = now + 15
                rec(f'Q出手@{NAMES[f]}')
        elif k == 'r':
            if f == LU and now < self.lu_heavy_until:
                pass
            elif f == LU and getattr(self, 'lu_r_need', 0) > 0:
                pass                                   # 能量不够, 按了没反应
            elif f == RB and getattr(self, 'rb_r_need', 0) > 0:
                pass
            elif f == RB and now - self.arrived_at < RB_R_DELAY:
                # 实机 2026-09-14: 丽贝卡切进来之后 R 起得晚, 1.5s 窗口里判不到演出
                pass
            elif now - self.lib_used.get(f, -99) > self.lib_cd[f]:
                self.lib_used[f] = now
                rec(f'R出手@{NAMES[f]}')
                if f == SK:
                    self.hud_hidden_until = now + 3.0
                    self.no_switch_until = now + 2.97
                    self.con[SK] = 1.0
                elif f == RB:
                    self.hud_hidden_until = now + 6.9       # 机枪 + 大烟花期间队伍 HUD 不在
                    self.no_switch_until = now + 6.9
                    if self.con[RB] >= 0.75:
                        self.con[RB] = 1.0                  # 正常: R 放完协奏就满了
                    else:
                        self.rb_fw_at = now + 7.6           # 前面强化重击没打中: 要等大烟花打中才满
                else:
                    self.hud_hidden_until = now + 99
                    # 收尾那次(强化状态里放的)面板收起后协奏满; 启动轴第一发不满
                    self.panel = {'open_at': now + 3.4, 'clicks': 0, 'last': 0.0, 'final': self.lu_enh,
                                  'stuck': self.lu_enh}
                    self.lu_enh = False
                    self.lu_root = 0
                    self.lu_proto = 0
        elif k == 'f':
            rec('F')
            if f == LU and now < getattr(self, 'lu_chain_end', 0):
                rec('!!F落在回路重击整串里')
            # 回路重击打出去之后出现一次处决机会: 第一下 F 就打出处决, 演出 1.6s 隐藏 HUD
            if f == LU and getattr(self, 'exec_ready', False) and now >= self.exec_ready_at:
                self.exec_ready = False
                self.hud_hidden_until = now + 1.6
                self.lock_until = now + 1.6
                rec('露处决出手')
        if after_sleep:
            time.sleep(after_sleep)

    def click(self, *a, **kw):
        now = time.time()
        if kw.get('key') == 'right':
            rec('闪避(click)')
            return
        rec('A')
        if self.panel is not None:
            p = self.panel
            if now >= p['open_at'] and not p['stuck'] and now - p['last'] >= 0.12:
                p['clicks'] += 1
                p['last'] = now
                if p['clicks'] >= 8:
                    self.panel = None
                    self.hud_hidden_until = now + 0.3
                    if p['final']:
                        self.con[LU] = 1.0
                    rec('露面板收起')
            return
        if self.locked():
            return
        f = self.on_field
        if f == SK:
            self.sk_core = min(5.0, self.sk_core + 0.45)
            self.add_con(SK, SK_A_CON)
        elif f == RB:
            self.add_con(RB, 0.02)
            if getattr(self, 'rb_r_need', 0) > 0:
                self.rb_r_need -= 1
        elif f == LU:
            if now < self.lu_heavy_until:
                return
            self.lu_last_a = now
            if getattr(self, 'lu_r_need', 0) > 0:
                self.lu_r_need -= 1
            if self.lu_enh:
                self.lu_root += 1
            else:
                self.lu_proto = min(100, self.lu_proto + 2)
            self.add_con(LU, 0.02)

    def scroll(self, *a, **kw):
        rec(f'scroll{a}')
        # 实机 2026-09-14 循环轴: 光标卡在用不了的选项上, 滚过一格之后左键才生效
        if self.panel is not None:
            self.panel['stuck'] = False

    def scroll_relative(self, *a, **kw):
        self.scroll(*a, **kw)

    def mouse_down(self, *a, **kw):
        rec('hold_down')
        self.holding = True
        self.hold_at = time.time()
        self.lu_hold_rolled = False

    def mouse_up(self, *a, **kw):
        now = time.time()
        rec('hold_up')
        f = self.on_field
        if f == RB and self.rb_full():
            rec('丽强化重击出手')
            if RB_CON_ENTRY >= 0:
                self.con[RB] = RB_CON_ENTRY
            else:
                self.add_con(RB, 0.35)
            # 模拟那发强化重击被打断: 大招能量没进账、狂热还满着
            self.rb_r_need = RB_R_SHORT
            self.rb_fervor_left = RB_FERVOR_LEFT
        elif f == RB and getattr(self, 'rb_fervor_left', False) and now - self.hold_at >= 0.53:
            rec('丽补强化重击出手')
            self.rb_fervor_left = False
            self.rb_r_need = max(0, getattr(self, 'rb_r_need', 0) - 25)
            self.lock_until = now + 1.3
        self.holding = False

    def rb_full(self):
        return (self.holding and self.on_field == RB
                and time.time() - max(self.hold_at, self.arrived_at) >= 3.0)

    def find_mouse_forte(self):
        now = time.time()
        f = self.on_field
        if f == SK:
            if self.holding and self.sk_core >= 5 and now - self.hold_at >= 0.38:
                self.sk_core = 0
                self.add_con(SK, 0.1)
                rec('守强化重击出手')
            return self.sk_core >= 5
        if f == RB:
            return self.rb_full() or (getattr(self, 'rb_fervor_left', False) and not self.holding)
        # 露西: 按住 0.3s 时游戏决定接不接这下回路重击(HEAVY_INTERRUPT 概率没接住, 松开重按会再掷一次)
        if (self.holding and self.lu_root >= 8 and now - self.hold_at >= 0.3
                and not getattr(self, 'lu_hold_rolled', False) and now >= self.lock_until):
            self.lu_hold_rolled = True
            if random.random() < HEAVY_INTERRUPT:
                rec('露回路重击没接住')
            else:
                rec('露回路重击出手')
                self.lu_root = 0
                self.lu_heavy_until = now + 5.1
                self.lu_chain_end = now + 0.7 + 1.7     # 按住剩下的 + 强化重击3 整串
                self.add_con(LU, 0.2)
                self.exec_ready = random.random() < EXEC_PROB
                self.exec_ready_at = now + EXEC_DELAY
                if getattr(self, 'lu_r_need', 0) > 0:
                    self.lu_r_need = max(0, self.lu_r_need - 15)
        return self.lu_root >= 8

    def lucy_forte(self):
        if self.lu_clear_at and time.time() >= self.lu_clear_at:
            self.lu_proto = 0
            self.lu_clear_at = 0.0
        return self.on_field == LU and self.lu_proto >= 100

    def jump(self, after_sleep=0.01):
        rec('jump')

    def get_resonance_key(self, *a, **kw):
        return 'e'

    def get_echo_key(self, *a, **kw):
        return 'q'

    def get_liberation_key(self, *a, **kw):
        return 'r'

    def get_cd(self, name, index=None):
        if name == 'resonance':
            # 露西 E2 解锁后 E 框换成金色强化E 图标, 上面没有 CD 数字
            if self.on_field == LU and self.lu_stage == 'E2' and time.time() >= self.lock_until:
                return 0.0
            return max(0.0, self.e_cd.get(self.on_field, 0) - time.time())
        return 0.0

    def get_current_con(self):
        if time.time() >= getattr(self, 'rb_fw_at', 1e18):
            self.con[RB] = 1.0
            self.rb_fw_at = 1e18
            rec('丽大烟花打中协奏满')
        return self.con[self.on_field]

    def is_con_full(self):
        return self.con[self.on_field] >= 1.0

    def add_freeze_duration(self, *a, **kw):
        pass

    def raise_not_in_combat(self, msg):
        raise RuntimeError(msg)

    def box_of_screen_scaled(self, *a, **kw):
        return None

    def find_one(self, *a, **kw):
        return None

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return time.time() - start


def load(name):
    p = rf'{TEAM}\{name}.py'
    spec = importlib.util.spec_from_file_location(f'dry_{name}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return getattr(m, name)


task = FakeTask()
LUCY = load('Lucy')(task, LU, char_name='char_lucy', char_type=CharType.MAIN_DPS)
REB = load('Rebecca')(task, RB, char_name='char_rebecca', char_type=CharType.SUB_DPS)
SHO = load('ShoreKeeper')(task, SK, char_name='char_shorekeeper', char_type=CharType.HEALER)
task.chars = [LUCY, REB, SHO]


def bar_lag():
    return time.time() - task.arrived_at < 0.25


for c in (LUCY, REB, SHO):
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
    i = c.index
    c.get_current_con = lambda i=i: task.con[i]
    c.is_con_full = lambda i=i: task.con[i] >= 1.0
    c.liberation_available = lambda i=i: time.time() - task.lib_used.get(i, -99) > task.lib_cd[i]
    c.echo_available = lambda i=i: not bar_lag() and time.time() > task.echo_cd.get(i, 0)
SHO.resonance_available = lambda: not bar_lag() and time.time() > task.e_cd.get(SK, 0)
REB.resonance_available = lambda: not bar_lag() and not task.locked() and time.time() > task.e_cd.get(RB, 0)
REB.liberation_available = lambda: (time.time() - task.lib_used.get(RB, -99) > task.lib_cd[RB]
                                    and getattr(task, 'rb_r_need', 0) <= 0)
LUCY.resonance_available = lambda: True
LUCY.is_forte_full = task.lucy_forte
# 回路重击整串没打完之前 R 读成 CD 中(实机: 打完时 R 还差 ~1s)
LUCY.liberation_available = lambda: (time.time() - task.lib_used.get(LU, -99) > task.lib_cd[LU]
                                     and time.time() >= task.lu_heavy_until
                                     and getattr(task, 'lu_r_need', 0) <= 0)
# E1 追加打完(锁结束)之后 E 框变金
LUCY.box_gold_ratio = lambda name: (0.3 if task.on_field == LU and (
    (task.lu_stage == 'E2' and time.time() >= task.lock_until)
    # 实机 2026-09-14: 露西进场时 E 框被丽贝卡的特效染成金色(没有解锁 E2)
    or (GOLD_FLASH and time.time() - task.arrived_at < 0.6)) else 0.0)
LUCY.is_current_char = True


def run(label, fn, *args):
    LOG.clear()
    T0[0] = time.time()
    t = time.time()
    try:
        fn(*args)
    except Exception:
        import traceback
        traceback.print_exc()
    wall = time.time() - t
    casts = [f'{ts:.2f}{w}' for ts, w in LOG if '出手' in w or '到场' in w or '收起' in w or '闪避' in w]
    print('  ' + ' '.join(casts))
    print(f'=== {label}: 墙钟 {wall:.2f}s, 场上={NAMES[task.on_field]}, '
          f'普攻 {sum(1 for _, w in LOG if w == "A")}, F {sum(1 for _, w in LOG if w == "F")}')
    print('  ' + LUCY.macro_timeline())


run('启动轴', LUCY.drive, LUCY)
for n in range(DRY_LOOPS):
    run(f'循环 #{n + 1}', LUCY.drive, SHO)
