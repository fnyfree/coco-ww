"""龙凤维轴的干跑: 假 task + 假读屏, 真跑一遍启动宏和几步循环。

验证的是【调用链和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 两条宏回放出来的总时长跟宏表对不对得上(启动宏 22.30s / 循环宏 19.60s)
  - 手动段那两格的判据会不会卡满 cap(今汐三个 E 的状态机、长离的离火)
假游戏模型是从旧轴那份改的, 【它不是实机】: 今汐攒回路在这里是"普通E 之后再点 3 下 A"(凑的),
而且【没有预输入】—— 锁期间按的技能键直接丢, 所以今汐那三个 E 在这儿经常出不全, 那不是代码的 bug。
用法: python dryrun.py <队伍目录>
"""
import threading
import sys, time, importlib.util
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
        self.on_field = 0
        self._pending = None
        self._anim_until = 0
        self._buffered = None
        self._f_last_press = -1
        # 游戏状态
        self.echo_cd = {}
        self.e_cd = {}
        self.jx_mode = 'normal'       # normal / dash / (burst_ready 另记)
        self.burst_ready = False
        self.stacks = 1               # 长离离火
        self.holding = False
        self.hold_at = 0
        self.lib_used = {}
        self.lib_cd = {0: 20, 1: 20, 2: 25}
        self.con = {0: 0.0, 1: 0.0, 2: 0.0}

    def add_con(self, idx, v):
        self.con[idx] = min(1.0, self.con[idx] + v)

    def tick(self):
        """把待生效的换人推进一步。

        【这条轴的宏回放一次读屏都没有】, 所以不能像旧轴那样只在 in_team() 里推 ——
        那样整条宏跑完 on_field 都不会动, 后面每一个技能键都记在开局那个人头上。
        """
        now = time.time()
        if self._pending and now >= self._pending[1]:
            if self.con[self.on_field] >= 1.0:
                self.con[self.on_field] = 0.0          # 满协奏离场 = 延奏, 清零
            at = self._pending[1]
            self.on_field = self._pending[0]
            self._pending = None
            self.arrived_at = at
            # 今汐变奏入场(惊龙破空/开大之后 E 回到普通态): QTE 第60F前不响应输入
            self.lock_until = at + (1.0 if self.on_field == 1 and self.jx_mode == 'normal' else 0.0)
            # (旧轴那条"突进之后下一次回来就满了"删了, 见文件头)

    def in_team(self):
        self.tick()
        if time.time() < self._anim_until:
            return False, -1, 0
        return True, self.on_field, 3

    lock_until = 0.0
    dash_extra = 0

    def next_frame(self):
        time.sleep(0.016)

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        self.tick()
        k = str(key)
        now = time.time()
        rec(f'key:{k}@{self.on_field}')
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            if idx != self.on_field and self._pending is None and now >= self._anim_until:
                self._pending = (idx, now + 0.12)
        elif now < self._anim_until or now < self.lock_until:
            # 【这里把锁期间的技能键直接丢掉, 真机不是这样】: 鸣潮有预输入, 锁里按的键会在
            # 能出招的第一帧触发。试过在这里做 buffer, 但触发时刻要精确到几十毫秒才不会
            # 把后面的切人一起推歪 —— 这个假游戏没那个精度。
            # 【所以干跑里今汐三个 E 出不全是正常的, 不是代码的 bug】: 启动宏第14格的强化E1
            # 在变奏入场后 0.878s 就按, 比 QTE 锁(第60F = 1.0s)还早, 在这里必然被丢。
            pass
        elif k == 'r':
            f = self.on_field
            if now - self.lib_used.get(f, -99) > self.lib_cd[f]:
                self.lib_used[f] = now
                self._anim_until = now + {0: 2.8, 1: 3.45, 2: 2.27}[f]
                rec(f'R出手@{"长离今汐维里奈"[f * 2:f * 2 + 2]}')
                if f == 0:
                    self.stacks = 4
                    self.hold_at = self._anim_until
                    self.con[0] = 1.0
                if f == 1:
                    self.add_con(1, 0.2)
                if f == 2:
                    self.con[2] = 1.0
        elif k == 'e':
            f = self.on_field
            if f == 1:
                if self.burst_ready:
                    self.burst_ready = False
                    self._anim_until = now + 3.0
                    self.stacks = min(4, self.stacks + 2)
                    self.jx_mode = 'normal'
                    self.jx_forte = 0
                    self.con[1] = 1.0
                    rec('JX龙喷出手')
                elif self.jx_mode == 'normal':
                    self.jx_mode = 'dash'
                    self.lock_until = now + 0.88
                    rec('JX强化E1出手')
                elif self.jx_mode == 'dash' and now > self.e_cd.get(1, 0):
                    self.e_cd[1] = now + 10
                    self.jx_mode = 'spent'
                    self.lock_until = now + 0.6
                    rec('JX普通E出手')
            elif f == 2:
                if now > self.e_cd.get(2, 0):
                    self.e_cd[2] = now + 10
                    self.add_con(2, 0.3)
                    rec('VR E出手')
        elif k == 'q':
            f = self.on_field
            if now > self.echo_cd.get(f, 0):
                self.echo_cd[f] = now + 20
                rec(f'Q出手@{"长离今汐维里奈"[f * 2:f * 2 + 2]}')
        if down_time:
            time.sleep(down_time)
        if after_sleep:
            time.sleep(after_sleep)

    def click(self, *a, **kw):
        self.tick()
        rec('A')
        down_time = kw.get('down_time') or 0
        if down_time:
            time.sleep(down_time)
        self._tap_effect()

    def _tap_effect(self):
        """一下普攻【真打出去了】会发生什么。老的 click() 和这条轴的 tap()
        (mouse_down -> 等 -> mouse_up)都落到这里。"""
        if time.time() < self.lock_until or time.time() < self._anim_until:
            return
        if self.on_field == 0 and not self.holding:
            self._clicks = getattr(self, '_clicks', 0) + 1
            if self._clicks % 6 == 0 and self.stacks < 4:
                self.stacks += 1
        if self.on_field == 2:
            self.add_con(2, 0.08)
        if self.on_field == 1:
            self.add_con(1, 0.05)
            # 这条轴的今汐是【在场一直点 A 把回路点满】的(旧轴是切走再回来自动满),
            # 没有这一条 seg_jx_e2 会一路按满 cap。【3 这个数是凑的】: 凑到两条宏里
            # 普通E 后面那几下 A 正好解锁龙喷 —— 真机是回路条说了算, 别拿它当标定
            if self.jx_mode == 'spent':
                self.jx_forte = getattr(self, 'jx_forte', 0) + 1
                if self.jx_forte >= 3:
                    self.burst_ready = True

    def mouse_down(self, *a, **kw):
        rec('hold_down')
        self.holding = True
        self.hold_at = max(time.time(), self._anim_until)   # 演出里按下的, 演完才开始算按住

    def mouse_up(self, *a, **kw):
        rec('hold_up')
        held = time.time() - self.hold_at
        self.holding = False
        if self.on_field == 0 and self.stacks >= 4 and held > 0.2:
            self.fenshen()
        elif held <= 0.25:
            # 【短按 = 这条轴的 tap()】: 普攻现在走 mouse_down -> 等 -> mouse_up 而不是
            # click(), 不认这个的话离火和今汐的回路一下都攒不起来
            self._tap_effect()

    def fenshen(self):
        self.stacks = 0
        self.add_con(0, 0.4)
        rec('长离焚身以火出手')

    def find_mouse_forte(self):
        if self.holding and self.on_field == 0 and time.time() - self.hold_at > 0.35 and self.stacks >= 4:
            self.fenshen()
        return self.on_field == 0 and self.stacks >= 4

    def jump(self, after_sleep=0.01):
        rec('jump')

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
CL = load('Changli')(task, 0, char_name='chang_changli', char_type=CharType.MAIN_DPS)
JX = load('Jinhsi')(task, 1, char_name='char_jinhsi', char_type=CharType.MAIN_DPS)
VR = load('Verina')(task, 2, char_name='char_verina', char_type=CharType.HEALER)
task.chars = [CL, JX, VR]
for c in (CL, JX, VR):
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
    i = c.index
    c.get_current_con = lambda i=i: task.con[i]
    c.is_con_full = lambda i=i: task.con[i] >= 1.0
    c.liberation_available = lambda i=i: time.time() - task.lib_used.get(i, -99) > task.lib_cd[i]
CL.resonance_available = lambda: True
# 切人后技能栏滞后 0.25s 还显示上一个人的状态(实机 2026-09-11 看到的): 那段一律读成"在 CD 上"
def bar_lag():
    return time.time() - getattr(task, 'arrived_at', 0) < 0.25


JX.resonance_available = lambda: not bar_lag() and (time.time() > task.e_cd.get(1, 0) or task.burst_ready)
VR.resonance_available = lambda: not bar_lag() and time.time() > task.e_cd.get(2, 0)
CL.echo_available = lambda: not bar_lag() and time.time() > task.echo_cd.get(0, 0)
JX.echo_available = lambda: not bar_lag() and time.time() > task.echo_cd.get(1, 0)
VR.echo_available = lambda: not bar_lag() and time.time() > task.echo_cd.get(2, 0)
CL.judge_forte = lambda: task.stacks
CL.is_current_char = True


def run(label, fn):
    LOG.clear()
    T0[0] = time.time()
    t = time.time()
    try:
        fn(CL)
    except Exception:
        import traceback
        traceback.print_exc()
    wall = time.time() - t
    # 这条轴每一轮该出的手: 维E/维R/今汐三个E/今汐R(只有启动宏有)/长离两个E/长离R+两发焚身以火
    # 【今汐那三个 E 在干跑里经常出不全】: 假游戏把锁期间的键直接丢了, 真机有预输入。
    # 要盯的是长离那两发焚身以火和维里奈的 E/R —— 那几格不在任何锁里
    # 启动宏里长离不放焚身以火(那是手动A 的事), 这一条只在循环轮上查
    want = ('VR E出手', 'R出手@维里') + (('长离焚身以火出手',) if label.startswith('循环') else ())
    got = ' '.join(w for _, w in LOG)
    for w in want:
        if w not in got:
            print(f'  !!! {w} 一次都没出来')
    miss = [w for w in ('JX强化E1出手', 'JX普通E出手', 'JX龙喷出手') if w not in got]
    if miss:
        print('  (今汐 ' + '/'.join(m[2:-2] for m in miss) + ' 在假游戏里被锁吞了, 实机要看)')
    casts = [f'{ts:.2f}{w}' for ts, w in LOG if '出手' in w]
    print('  出手: ' + ' '.join(casts))
    sw = [w for _, w in LOG if w.startswith('key:') and w.split(':')[1][0] in '123']
    print(f'\n=== {label}: 墙钟 {wall:.2f}s, 切人键 {len(sw)}, 普攻 {sum(1 for _, w in LOG if w == "A")}, '
          f'长按 {sum(1 for _, w in LOG if w == "hold_down")}, 离火={task.stacks}, 场上={task.on_field}')


run('启动轴', CL.perform_opener)
print(CL.macro_timeline())
for i in range(4):
    run(f'循环 #{i + 1}', CL.perform_loop)
    print(CL.macro_timeline())
