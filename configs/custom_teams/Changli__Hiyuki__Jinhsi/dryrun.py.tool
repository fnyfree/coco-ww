"""龙凤雪轴的干跑: 假 task + 假读屏, 真跑一遍整条轴。

验证的是【调用链、按住左键有没有被激活窗口打断、逐格墙钟】, 不是打得对不对。
假游戏: 换人 CD 1.0s / 切人生效 0.08s / 各人大招演出藏 HUD / 今汐 E4 藏 HUD / 绯雪强化重击 2.5s 不能切人。
用法: python dryrun.py.tool <队伍目录>
"""
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
LOG = []
T0 = [time.time()]
NAMES = {0: '今汐', 1: '长离', 2: '绯雪'}


def rec(what):
    LOG.append((time.time() - T0[0], what))


class FakeInteraction:
    def send_key_down(self, key, activate=True):
        TASK.key_event(str(key), activate)

    def send_key_up(self, key):
        TASK.key_up(str(key))

    def post(self, msg, w=0, l=0, hwnd=None):
        import win32con
        if msg == win32con.WM_RBUTTONDOWN:
            rec('闪避(quiet)')


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
    interaction = FakeInteraction()

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
        self.on_field = 1
        self.arrived_at = time.time()
        self.pending = None
        self.left_at = {0: -99, 1: -99, 2: -99}
        self.anim_until = 0.0
        self.no_switch_until = 0.0
        self.holding = False
        self.hold_at = 0.0
        self.cl_forte = False
        self.hy_e_at = -99
        self.hy_heavy_at = -99
        self.hy_yuqiu = False
        self.r2_down = False
        self.jx_e3_cd = 0.0
        self.hy_e_cd = 0.0
        self.last_jump = -99

    # ---- 场上/HUD ----
    def in_team(self):
        now = time.time()
        if self.pending and now >= self.pending[1]:
            self.left_at[self.on_field] = now
            self.on_field = self.pending[0]
            self.pending = None
            self.arrived_at = now
            rec(f'到场 {NAMES[self.on_field]}')
        if now < self.anim_until:
            return False, -1, 0
        return True, self.on_field, 3

    def next_frame(self):
        time.sleep(0.016)

    def validate_key(self, key):
        return str(key)

    # ---- 输入 ----
    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        self.key_event(str(key), True)
        self.key_up(str(key))

    def send_key_down(self, key, after_sleep=0):
        self.key_event(str(key), True)

    def send_key_up(self, key, after_sleep=0):
        self.key_up(str(key))

    def activate(self):
        if self.holding:
            rec('!!! 按住左键期间激活了窗口, 左键被松开')
            self.holding = False

    def key_event(self, k, activate):
        now = time.time()
        if activate:
            self.activate()
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            if (idx != self.on_field and self.pending is None and now >= self.anim_until
                    and now >= self.no_switch_until and now - self.left_at[idx] >= 1.0):
                self.pending = (idx, now + 0.08)
            return
        if now < self.anim_until:
            return
        f = self.on_field
        if k == 'space':
            self.last_jump = now
            rec(f'跳@{NAMES[f]}')
        elif k == 'r':
            if f == 1:
                self.anim_until = now + 2.8
                self.cl_forte = True
                rec('长离R出手')
            elif f == 0:
                self.anim_until = now + 3.45
                rec('今汐R出手')
            elif self.hy_yuqiu:
                self.r2_down = True
                self.r2_at = now
                rec('绯雪R2按下')
            elif now - self.hy_heavy_at > 1.6 and self.hy_heavy_at > 0:
                self.anim_until = now + 4.0
                self.hy_yuqiu = True
                rec('绯雪R1出手')
        elif k == 'e':
            if f == 0:
                last = CL.macro_marks[-1] if CL.macro_marks else ''
                if last.startswith('10今汐A4') or last.startswith('28今汐变奏A4'):
                    self.anim_until = now + 3.0
                    rec('今汐E4出手')
                elif now > self.jx_e3_cd and ('E3' in last or '空中' in last or '今汐A1' in last):
                    self.jx_e3_cd = now + 10
                    rec('今汐E3出手')
            elif f == 2:
                if not self.hy_yuqiu and now > self.hy_e_cd:
                    self.hy_e_cd = now + 20
                    self.hy_e_at = now
                    rec('绯雪常世E出手')
        elif k == 'q':
            rec(f'Q@{NAMES[f]}')
        elif k == 'f':
            rec('F')

    def key_up(self, k):
        if k == 'r' and self.r2_down:
            self.r2_down = False
            self.anim_until = time.time() + 5.6
            rec(f'绯雪R2松开(按住 {time.time() - self.r2_at:.2f}s)')

    def click(self, *a, key='left', **kw):
        self.activate()
        rec('闪避' if key == 'right' else 'A')

    def mouse_down(self, *a, **kw):
        self.activate()
        self.holding = True
        self.hold_at = time.time()
        rec('按住')

    def mouse_up(self, *a, **kw):
        self.holding = False
        rec('松开')

    def find_mouse_forte(self):
        now = time.time()
        if self.on_field == 1:
            if self.cl_forte and self.holding and now - max(self.hold_at, self.anim_until) > 0.2:
                self.cl_forte = False
                rec('长离焚身以火出手')
            return self.cl_forte and now >= self.anim_until
        if self.on_field == 2 and not self.hy_yuqiu:
            lit = self.hy_e_at > 0 and now - self.hy_e_at > 1.3 and self.hy_heavy_at < 0
            if lit and self.holding and now - self.hold_at > 0.15:
                self.hy_heavy_at = now
                rec('绯雪重击出手')
                return False
            return lit
        return False

    def jump(self, after_sleep=0.01):
        self.key_event('space', True)

    def get_resonance_key(self, *a, **kw):
        return 'e'

    def get_echo_key(self, *a, **kw):
        return 'q'

    def get_liberation_key(self, *a, **kw):
        return 'r'

    def get_current_con(self):
        return 1.0

    def is_con_full(self):
        return True

    def add_freeze_duration(self, *a, **kw):
        pass

    def raise_not_in_combat(self, msg):
        raise RuntimeError(msg)

    def box_of_screen(self, *a, **kw):
        return None

    def box_of_screen_scaled(self, *a, **kw):
        return None

    def find_one(self, *a, **kw):
        return None

    def is_pick_f(self):
        return False

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return time.time() - start


def load(name):
    p = rf'{TEAM}\{name}.py'
    spec = importlib.util.spec_from_file_location(f'dry_{name}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return getattr(m, name)


TASK = FakeTask()
JX = load('Jinhsi')(TASK, 0, char_name='char_jinhsi', char_type=CharType.MAIN_DPS)
CL = load('Changli')(TASK, 1, char_name='chang_changli', char_type=CharType.MAIN_DPS)
HY = load('Hiyuki')(TASK, 2, char_name='char_hiyuki', char_type=CharType.MAIN_DPS)
TASK.chars = [JX, CL, HY]
for c in (JX, CL, HY):
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
    c.get_current_con = lambda: 1.0
    c.is_con_full = lambda: True
    c.liberation_available = lambda: True
    c.echo_available = lambda: True
CL.resonance_available = lambda: True
JX.resonance_available = lambda: time.time() > TASK.jx_e3_cd
HY.resonance_available = lambda: time.time() > TASK.hy_e_cd
# 绯雪回路圆牌(kendo_state 直接读 task.frame 像素, 假 task 没有画面): 假读屏一律"读不到",
# iai_until_drop 走 KENDO_BLIND 按时刻连点的兜底分支, 测的是调用链和墙钟, 不测居合判据本身
CL.kendo_state = lambda: (0, 0)
# perform_axis 一直打到 task.in_combat() 为假才停(每段归刃演完查一次)。假 task 原来没有这个方法, 异常被当成"还在打",
# 永远停不下来。跑完启动轴 + DRY_LOOPS 段循环(默认 3 = loop1~loop3 一整圈)就当脱战。
import os  # noqa: E402
DRY_LOOPS = int(os.environ.get('DRY_LOOPS', '3'))
TASK.in_combat = lambda: getattr(CL, 'round_no', 1) < 1 + DRY_LOOPS
CL.is_current_char = True

T0[0] = time.time()
start = time.time()
try:
    CL.perform_axis()
except Exception:
    import traceback
    traceback.print_exc()
wall = time.time() - start

print(f'墙钟 {wall:.2f}s, 场上={NAMES[TASK.on_field]}')
for ts, w in LOG:
    if w.startswith('!!!'):
        print(f'  {ts:6.2f} {w}')
print('  出手: ' + ' '.join(f'{ts:.2f}{w}' for ts, w in LOG if '出手' in w or 'R2' in w))
# 每次到场 -> 在场多久
arr = [(ts, w[3:]) for ts, w in LOG if w.startswith('到场 ')]
seq = []
for i, (ts, who) in enumerate(arr):
    end = arr[i + 1][0] if i + 1 < len(arr) else wall
    seq.append(f'{i + 2:02d}{who}{end - ts:.2f}')
print('  在场: ' + ' '.join(seq))
print(CL.macro_timeline())
