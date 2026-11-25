"""秧千穗轴的干跑: 假 task + 假读屏, 真跑一遍启动宏和几条循环宏。

验证的是【调用链和逐格墙钟】, 不是打得对不对:
  - 有没有 AttributeError / 参数对不上
  - 两条宏回放出来的总时长跟宏表对不对得上(启动宏 40.22s / 循环宏 38.49s,
    假游戏的大招演出比真的短, 所以墙钟会短几秒)
  - 烟岚窗口(穗穗大招后切秧秧 -> 千咲变奏切秧秧)花了多少游戏时间
  - 末尾处决那格两条路(有提示 / 没提示等后摇)都走到: 处决提示隔一条宏给一次
假游戏模型是从 Changli__Jinhsi__Verina/dryrun.py.tool 改的, 【它不是实机】:
  - 秧秧的【流响】是个很粗的模型(一下普攻 -14, 长按按秒数扣), 但"流响耗尽时按 E 才是
    剑式流转(给苍翎)"这条规则【是照实机建的】;
  - 【没有预输入】, 锁期间按的技能键直接丢, 真机会在能出招的第一帧触发;
  - 穗穗/千咲只有 CD 和协奏, 没有各自的机制。
用法: python dryrun.py.tool <队伍目录>
"""
import threading
import os
import sys, time, importlib.util
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
sys.stdout.reconfigure(encoding='utf-8')
from src.char.BaseChar import CharType

# --clean: 不造任何失败(千咲大招/强化E 都一次成功), 用来量正常路径下每一格的墙钟
CLEAN = '--clean' in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
TEAM = ARGS[0] if ARGS else os.path.dirname(os.path.abspath(__file__))   # 默认: 本文件所在的队伍目录
LOG = []
T0 = [time.time()]
WHO = ['穗穗', '千咲', '秧秧']


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
        # 内置 Chisa.get_char_type() 会读 "Chisa DPS"; 干跑里当奶妈(跟实机这一队一致)
        self.char_config = {'Chisa DPS': False}
        self.skip_combat_check = False
        self.use_liberation = True
        self.in_liberation = False
        self.can_break = False
        self.has_lavitator = False
        self.on_field = 0          # 开局场上是穗穗(1 号位)
        self._pending = None
        self._anim_until = 0
        self._f_last_press = -1
        self.echo_cd = {}
        self.e_cd = {}
        self.lib_used = {}
        # 【千咲的大招能量得打够才行】: 模拟实机"循环轮 R 放不出来"。
        # 一下普攻 +1, 要 lib_need 下才满 —— 循环宏只给她 5 下, 所以必然要靠能量闸补
        self.lib_energy = {0: 99, 1: 0, 2: 99}
        self.lib_need = 8
        self.lib_cd = {0: 20, 1: 20, 2: 20}
        self.con = {0: 0.0, 1: 0.0, 2: 0.0}
        self.holding = False
        self.hold_at = 0
        self.lock_until = 0.0
        self.arrived_at = 0.0
        # ---- 秧秧·玄翎 ----
        self.cang = 0              # 苍翎 0~2
        self.liuxiang = 100.0      # 秧秧【流响】: 普攻消耗, 【耗尽时按E 才是剑式流转】
        self.sui_forte = 0.0       # 穗穗三段回路(芳菲信): 一下普攻 +0.35, 满 3 档
        self.form = 'cang'         # 当前剑式 cang / yu
        self.linfeng_until = 0     # 聆风祀声
        self.chi_e_star_until = 0  # 千咲强化E 就绪窗口
        self.chi_r_count = 0       # 千咲放了几次大招
        # 【故意让第 N 次的强化E 放不出来】: 模拟"齿轨轮回被入场锁/硬直吞掉"。
        # 不跑这条路的话 A~ 那格的补救分支永远不会被执行 —— 2026-09-22 就是这么
        # 漏掉一个 UnboundLocalError, 实机当场停任务
        self.chi_e_fail_on = 0 if CLEAN else 2
        # 【千咲第一次上场放大招一律不给, 切走一次就恢复】: 复现"R! 那一格能量没好、
        # 过了那一格才好"的场景 —— 不造这个, A~ 里补 R 的分支永远不会被执行。
        # 原来是"前 25 次按 R 不给", 窗口里改成只连发 0.4 秒(十来次)之后, 拒绝次数会一直
        # 拖到 A~ 那格, 补 R 的分支反而走不到 —— 所以改成按"切走没有"来放行
        self.chi_r_block = not CLEAN
        self.chi_r_tried = False
        # 处决提示: 主程序按条设置, 隔一条宏给一次, 让 'F?' 的两条路都走到
        self.f_prompt = False
        self.freeze = []
        self.slow_intro = 0.0

    def add_con(self, idx, v):
        self.con[idx] = min(1.0, self.con[idx] + v)

    def tick(self):
        now = time.time()
        if self._pending and now >= self._pending[1]:
            intro = self.con[self.on_field] >= 1.0
            if intro:
                self.con[self.on_field] = 0.0          # 满协奏离场 = 延奏, 清零
            at = self._pending[1]
            if self.on_field == 1 and self.chi_r_block and self.chi_r_tried:
                self.chi_r_block = False
            self.on_field = self._pending[0]
            self._pending = None
            self.arrived_at = at
            if self.on_field == 2 and intro:
                # 变奏技能·羽挟苍空: 回 1 点苍翎; 入场动作 0.9s 不响应输入
                self.cang = min(2, self.cang + 1)
                rec(f'秧变奏入场(苍翎{self.cang},流响{self.liuxiang:.0f})')
                self.lock_until = at + 0.9
            else:
                self.lock_until = at

    def in_team(self):
        self.tick()
        if time.time() < self._anim_until:
            return False, -1, 0
        return True, self.on_field, 3

    def next_frame(self):
        time.sleep(0.016)

    def send_key(self, key, down_time=0.02, interval=-1, after_sleep=0):
        self.tick()
        k = str(key).lower()
        now = time.time()
        rec(f'key:{k}@{WHO[self.on_field]}')
        if k == 'f' and self.f_prompt and now >= self._anim_until:
            self.f_prompt = False
            rec('处决')
            self._anim_until = now + 1.2      # 处决动画
        if k in ('1', '2', '3'):
            idx = int(k) - 1
            if idx != self.on_field and self._pending is None and now >= self._anim_until:
                delay = 0.04
                # 【变奏切秧秧被游戏拖慢】: 实机 09-26 循环#1 两次 0.57/0.69s 才到场,
                # 主程序在第 1 条循环宏打开, 验证 E 会跟着到场时刻往后推
                if idx == 2 and self.con[self.on_field] >= 1.0 and self.slow_intro > 0:
                    delay = self.slow_intro
                    rec(f'变奏切秧秧被拖慢{delay:.2f}s')
                self._pending = (idx, now + delay)
        elif k in ('w', 'a', 's', 'd', 'space'):
            pass
        elif now < self._anim_until or now < self.lock_until:
            pass       # 锁期间的技能键直接丢(真机有预输入, 见文件头)
        elif k == 'r':
            f = self.on_field
            # 【能量不够时 R 是发不出去的】: fire_r 不问 liberation_available, 它只发键+看
            # HUD 有没有消失 —— 所以能量门控必须建在这儿, 否则干跑里 R 永远"出手"
            energy_ok = (self.lib_energy.get(f, 99) >= self.lib_need
                         and not (f == 1 and self.chi_r_block))
            if f == 1 and self.chi_r_block:
                self.chi_r_tried = True
            if energy_ok and now - self.lib_used.get(f, -99) > self.lib_cd[f]:
                self.lib_used[f] = now
                self._anim_until = now + {0: 2.8, 1: 2.6, 2: 2.4}[f]
                self.add_con(f, 0.25)
                if f == 0:
                    self.sui_forte = 0.0                  # 穗穗大招把回路清空
                self.lib_energy[f] = 0
                if f == 1:
                    self.chi_r_count += 1
                    if self.chi_r_count != self.chi_e_fail_on:
                        self.chi_e_star_until = now + 8.0  # 大招后残响满 -> 齿轨轮回就绪
                    else:
                        rec('千咲强化E【故意失败】这一轮')
                if f == 2:
                    self.cang = min(2, self.cang + 1)     # 裁羽寂万音: 回 1 点苍翎
                    self.liuxiang = 0.0                   # 帧数据表: 施放大招后清空流响
                rec(f'R出手@{WHO[f]}' + (f'(苍翎{self.cang})' if f == 2 else ''))
        elif k == 'e':
            f = self.on_field
            if f == 2:
                # 【剑式流转 vs 剑式切换】: 只有流响耗尽时按下去的才回 100 流响 + 1 苍翎,
                # 没耗尽的只换形态。2026-09-22 实机 8 轮里 7 轮 1苍 没出手就是栽在这
                self.form = 'yu' if self.form == 'cang' else 'cang'
                self.lock_until = now + 0.25
                if self.liuxiang <= 0:
                    self.liuxiang = 100.0
                    self.cang = min(2, self.cang + 1)
                    rec(f'秧E剑式流转->{self.form}(苍翎{self.cang})')
                else:
                    rec(f'秧E剑式切换->{self.form}(流响{self.liuxiang:.0f},不给苍翎)')
            elif now > self.e_cd.get(f, 0):
                self.e_cd[f] = now + 10
                self.add_con(f, 0.12)
                if f == 1 and now < self.chi_e_star_until:
                    # 齿轨轮回放出去了 -> 进电锯模式, 强化态被消耗, E 图标暗下去
                    self.chi_e_star_until = 0
                    rec('千咲齿轨轮回→电锯模式')
                else:
                    rec(f'E出手@{WHO[f]}')
        elif k == 'q':
            f = self.on_field
            if now > self.echo_cd.get(f, 0):
                self.echo_cd[f] = now + 20
                self.add_con(f, 0.15)
                rec(f'Q出手@{WHO[f]}')
        if down_time:
            time.sleep(down_time)
        if after_sleep:
            time.sleep(after_sleep)

    def click(self, *a, **kw):
        self.tick()
        rec('A')
        if kw.get('down_time'):
            time.sleep(kw['down_time'])
        self._tap_effect()

    def _tap_effect(self):
        now = time.time()
        if now < self.lock_until or now < self._anim_until:
            return
        f = self.on_field
        self.add_con(f, 0.02)
        self.lib_energy[f] = self.lib_energy.get(f, 0) + 1
        if f == 0:
            self.sui_forte = min(3.0, self.sui_forte + 0.35)
        if f == 2:
            # 帧数据表: 羽强化A(聆风祀声/解锁期间的普攻)不扣流响
            if now >= self.linfeng_until:
                self.liuxiang = max(0.0, self.liuxiang - 14.0)   # 一下普攻耗一点流响
            if now < self.linfeng_until:
                rec('秧湮象风华')

    def mouse_down(self, *a, **kw):
        rec('hold_down')
        self.holding = True
        self.hold_at = max(time.time(), self._anim_until, self.lock_until)

    def mouse_up(self, *a, **kw):
        rec('hold_up')
        held = time.time() - self.hold_at
        self.holding = False
        if held > 0.30 and time.time() >= self._anim_until:
            self.add_con(self.on_field, held * 0.80)
            self.lib_energy[self.on_field] = self.lib_energy.get(self.on_field, 0) + int(held * 6)
        if self.on_field == 2 and held > 0.30:
            # 长按 = 普攻连段, 一路耗流响; 【苍翎满 2 才派生重击】
            self.liuxiang = max(0.0, self.liuxiang - held * 55.0)
            if self.cang >= 2:
                self.heavy()
        elif held <= 0.25:
            self._tap_effect()

    def heavy(self):
        """重击·苍剑式 / 重击·羽剑式(接释羽风落)。都消耗全部苍翎。"""
        self.cang = 0
        self.add_con(2, 0.15)
        if self.form == 'yu':
            self.linfeng_until = time.time() + 12.0
            rec('秧重击·羽剑式→释羽风落(聆风祀声12s)')
        else:
            rec('秧重击·苍剑式')

    def find_mouse_forte(self):
        """重击就绪图标。按住够久就在这里结算重击(跟实机一样)。

        【它代表"苍翎 >= 1", 不是"满 2"】—— 2026-09-22 实机 trace 是这么显示的:
        变奏入场只给 1 点苍翎, 图标就已经亮着了(苍翎满(0.04s) 是在按 E 之前读到的)。
        所以这个判据只能告诉你【重击有没有出手】(出手 -> 苍翎清零 -> 灭),
        告诉不了你【能不能出手】。
        (更早一版以为"聆风祀声会让它一直亮", 那是看视频截图得出的 —— 画面上粉环确实
        一直亮, 但框架认的是 mouse_forte 这张图, 湮象风华换了图案就匹配不上了。)
        """
        if self.holding and self.on_field == 2 and time.time() - self.hold_at > 0.30:
            self.liuxiang = max(0.0, self.liuxiang - 2.0)
            if self.cang >= 2:
                self.heavy()
        return self.on_field == 2 and self.cang >= 1

    def find_e_forte(self):
        return False

    def jump(self, after_sleep=0.01):
        rec('jump')

    def is_pick_f(self):
        return False

    def f_break(self):
        if not self.f_prompt:
            return None
        self.f_prompt = False
        rec('处决')
        self._anim_until = time.time() + 1.2      # 处决动画: HUD 整个不在
        return True

    def get_resonance_key(self, *a, **kw):
        return 'e'

    def get_echo_key(self, *a, **kw):
        return 'q'

    def get_liberation_key(self, *a, **kw):
        return 'r'

    def get_current_con(self):
        return self.con[self.on_field]

    def is_con_full(self):
        return self.con[self.on_field] >= 1.0

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        if duration < 0:
            duration = time.time() - start
        if start > 0 and duration > freeze_time:
            self.freeze.append((start, duration, freeze_time))

    def raise_not_in_combat(self, msg):
        raise RuntimeError(msg)

    def box_of_screen(self, *a, **kw):
        return None

    def box_of_screen_scaled(self, *a, **kw):
        return None

    def find_one(self, name=None, *a, **kw):
        # 穗穗三段回路那张图。【故意让表里给的 A 攒不满】(表里到 R 之前只有几下),
        # 这样干跑能走到 wait_forte3 的等待分支, 验证它会自己补点普攻
        if name == 'suisui_forte3':
            return self.sui_forte >= 3.0
        if name == 'f_break':
            return self.f_prompt and time.time() >= self._anim_until
        return None

    def get_box_by_name(self, name):
        return name

    def calculate_color_percentage(self, color=None, box=None, *a, **kw):
        # 千咲 E 图标的白像素占比: 强化态就绪时给个亮起后的典型值(旧版实测 0.158~0.211)
        if box == 'box_resonance' and self.on_field == 1                 and time.time() < self.chi_e_star_until:
            return 0.18
        return 0.0

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        # 跟 BaseCombatTask 同一个算法: 起点之后的时停段扣掉 (时长 - freeze_time)
        to_minus = 0
        for fs, d, ft in self.freeze:
            if start < fs:
                if ft == -100:
                    if not intro_motion_freeze:
                        continue
                    ft = 0
                to_minus += d - ft
        return time.time() - start - to_minus


def load(name):
    p = rf'{TEAM}\{name}.py'
    spec = importlib.util.spec_from_file_location(f'dry_{name}', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return getattr(m, name)


task = FakeTask()
SUI = load('Suisui')(task, 0, char_name='char_suisui', char_type=CharType.MAIN_DPS)
CHI = load('Chisa')(task, 1, char_name='char_chisa', char_type=CharType.HEALER)
YY = load('YangYangSp')(task, 2, char_name='char_yangyangsp', char_type=CharType.MAIN_DPS)
task.chars = [SUI, CHI, YY]
for c in (SUI, CHI, YY):
    c.check_combat = lambda: None
    c.sleep = lambda sec, check_combat=True: time.sleep(max(0.0, sec))
    i = c.index
    c.get_current_con = lambda i=i: task.con[i]
    c.is_con_full = lambda i=i: task.con[i] >= 1.0
    def _lib_avail(i=i):
        if i == 1 and task.chi_r_block:
            return False
        return (time.time() - task.lib_used.get(i, -99) > task.lib_cd[i]
                and task.lib_energy.get(i, 99) >= task.lib_need)
    c.liberation_available = _lib_avail


# 切人后技能栏滞后 0.25s 还显示上一个人的状态: 那段一律读成"在 CD 上"
def bar_lag():
    return time.time() - task.arrived_at < 0.25


SUI.resonance_available = lambda: not bar_lag() and time.time() > task.e_cd.get(0, 0)
CHI.resonance_available = lambda: not bar_lag() and time.time() > task.e_cd.get(1, 0)
YY.resonance_available = lambda: True          # 剑式切换不进 CD
for c, i in ((SUI, 0), (CHI, 1), (YY, 2)):
    c.echo_available = lambda i=i: not bar_lag() and time.time() > task.echo_cd.get(i, 0)
YY.is_current_char = True
# 秧秧强化E 的判据接假游戏的流响(实机读 E 图标的粉环, 那个在录屏上单独验过)
def _yy_ex_read():
    ok = task.on_field == 2 and time.time() >= task.arrived_at + 0.07 and task.liuxiang <= 0
    return ok, (1.0 if ok else 0.0), 0.0, -1.0
YY.yy_ex_read = _yy_ex_read
YY.yy_ex_snapshot = lambda kind: rec(f'EY截图({kind})')
# 'AX' 读流响条: 接假游戏的流响(实机读条的办法在录屏和 300 多张截图上单独验过)
YY.liuxiang_level = lambda: task.liuxiang / 100.0
# 剑式核对: 接假游戏的剑式(实机读 E 图标图案, 那个在录屏和截图上单独验过)
YY.yy_form = lambda: (({'cang': '苍', 'yu': '羽'}[task.form]
                       if task.on_field == 2 and time.time() >= task.arrived_at + 0.07 else None), 0.5)


def run(label, fn):
    LOG.clear()
    T0[0] = time.time()
    t = time.time()
    try:
        fn(YY)
    except Exception:
        import traceback
        traceback.print_exc()
    wall = time.time() - t
    got = ' '.join(w for _, w in LOG)
    want = ['R出手@穗穗', 'R出手@千咲', '秧重击·苍剑式', 'R出手@秧秧',
            '秧重击·羽剑式→释羽风落(聆风祀声12s)']
    for w in want:
        if w not in got:
            print(f'  !!! {w} 一次都没出来')
    casts = [f'{ts:.2f}{w}' for ts, w in LOG if ('出手' in w or '重击' in w or '入场' in w
                                                 or w == '处决' or '湮象' in w)]
    print('  出手: ' + ' '.join(casts))
    sw = [w for _, w in LOG if w.startswith('key:') and w.split(':')[1][0] in '123']
    print(f'=== {label}: 墙钟 {wall:.2f}s, 切人键 {len(sw)}, 普攻 {sum(1 for _, w in LOG if w == "A")}, '
          f'长按 {sum(1 for _, w in LOG if w == "hold_down")}, 苍翎={task.cang}, 场上={WHO[task.on_field]}\n')


task.f_prompt = True
run('启动轴(宏表 40.22s)', lambda _: YY.run_opener_from(SUI))
print(YY.macro_timeline(), '\n')
for i in range(3):
    task.f_prompt = (i % 2 == 1)
    task.slow_intro = 0.6 if i == 0 else 0.0
    run(f'循环 #{i + 1}(宏表 38.49s, 处决提示{"有" if task.f_prompt else "无"})',
        lambda _: YY.perform_loop(YY))
    print(YY.macro_timeline(), '\n')
