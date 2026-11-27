"""持宏引擎 MacroKit 的 HUD 看门狗 (2026-11-27)。

宏跑着的时候框架的脱战判定是关着的, 打进结算画面、关卡结束、窗口没了, 只能宏自己发现。
亲眼看着队伍 HUD 连续 16 秒不在 (队伍类可以用 HUD_LOST_ABORT 改), 就按脱战交回框架 (task.raise_not_in_combat)。
光是很久没人看 HUD 不算; 大招演出不算 (50 局实机记录里, 局中 HUD 连续不在最长 9.8 秒)。

谁看到的算数:
- coco-ww: 框架每次 in_team 都记 (BaseWWTask.note_team_hud), 队伍代码自己的等待循环也算。
  11-27 17:14 实机打进结算画面, 宏卡在切人超时里, 只算 MacroKit 自己读的话晚 2.5 秒才开始算;
  12:54 那局卡在队伍自己的等大招循环里 (8 秒一个标记), 只算 MacroKit 自己读的根本连不成一段。
- 原版 OKWW: 框架不记, 只有 MacroKit 自己读的 (hud_ok, 切人循环)。下面每个场景两种都跑一遍。

11-27 实机: 长离/露帕/布兰特打进奇点扩张结算画面, 没有看门狗, 宏又按了 86 秒才停。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_macrokit_hud_watchdog.py
"""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.combat import MacroKit as kit_module  # noqa: E402
from src.task.BaseWWTask import BaseWWTask, TEAM_HUD_GAP  # noqa: E402

SOURCE = ROOT / 'src' / 'combat' / 'MacroKit.py'
TICK = 0.5


class Stopped(Exception):
    pass


class Clock:
    now = 0.0

    def time(self):
        return self.now


class FakeExecutor:
    def __init__(self, clock):
        self.clock = clock
        self.frame_at = None    # 缓存帧是哪一刻截的; None = 刚截的

    @property
    def _last_frame_time(self):
        return self.clock.now if self.frame_at is None else self.frame_at


class FakeTask:
    """原版 OKWW 的 task: in_team 不记。"""

    def __init__(self, clock, hud):
        self.clock = clock
        self.hud = hud          # 某一刻 HUD 在不在
        self.executor = FakeExecutor(clock)
        self.stops = []

    def in_team(self):
        # 读的是缓存帧: 画面停在哪一刻, 看到的就是那一刻的 HUD
        frame_at = self.executor._last_frame_time
        visible = self.hud(frame_at)
        self.note(visible)
        return visible, (0 if visible else -1), (3 if visible else 1)

    def note(self, visible):
        pass

    def raise_not_in_combat(self, message):
        self.stops.append((self.clock.now, message))
        raise Stopped(message)


class CocoTask(FakeTask):
    """coco-ww 的 task: 每次 in_team 都用框架的 note_team_hud 记一笔。"""
    team_hud = BaseWWTask.team_hud
    note = BaseWWTask.note_team_hud


class Holder(kit_module.MacroKit):
    name = 'Changli'

    def __init__(self, task):
        self.task = task


class WatchdogCase(unittest.TestCase):
    task_cls = FakeTask

    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(kit_module, 'time', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_macro(self, hud, until, marks=lambda t: True, reads=lambda t: False, team_reads=lambda t: False,
                  frame_at=lambda t: None, holder_cls=Holder, start=True, restart=lambda t: False):
        """0 秒开宏, 每 0.5 秒一拍。这一拍里依次:
        restart(t) 为真时宏又调一次 macro_start (每圈开头); reads(t) 为真时 MacroKit 自己的等待循环读一次 HUD (hud_ok);
        team_reads(t) 为真时队伍代码自己的循环直接读一次 (task.in_team); marks(t) 为真时打一个标记。

        返回 (停下的时刻或 None, task)。
        """
        task = self.task_cls(self.clock, hud)
        holder = holder_cls(task)
        self.clock.now = 0.0
        if start:
            holder.macro_start()
        else:
            holder.macro_t0, holder.macro_marks = 0.0, []
        t = 0.0
        while t <= until:
            self.clock.now = t
            task.executor.frame_at = frame_at(t)
            if restart(t):
                holder.macro_start()
            if reads(t):
                holder.hud_ok()
            if team_reads(t):
                task.in_team()
            if marks(t):
                try:
                    holder.macro_mark('x')
                except Stopped:
                    return t, task
            t = round(t + TICK, 6)
        return None, task


class WatchdogTest(WatchdogCase):
    """原版 OKWW (框架不记 in_team)。CocoWatchdogTest 拿 coco-ww 的框架把同样的场景再跑一遍。"""
    sees_team_reads = False   # 队伍代码自己直接读的 task.in_team 看门狗看不看得到

    def test_results_screen_stops_the_macro(self):
        stopped, task = self.run_macro(lambda t: t < 30.2, until=120)
        self.assertIsNotNone(stopped, '打进结算画面要停')
        # 30 秒最后一次看到, 32 秒第一次看到不在, 连续 16 秒
        self.assertAlmostEqual(48.0, stopped)
        self.assertIn('HUD', task.stops[0][1])
        self.assertEqual(1, len(task.stops))

    def test_long_animations_do_not_stop(self):
        # 大招演出 12 秒、10 秒, 中间 HUD 回来过
        hud = lambda t: not (20 <= t < 32 or 34 <= t < 44)  # noqa: E731
        stopped, _ = self.run_macro(hud, until=120)
        self.assertIsNone(stopped)

    def test_long_animations_with_dense_reads_do_not_stop(self):
        # 等待循环一直在读也一样: 局中最长的演出 (莉露辛大招 8.2 秒 + 收尾 1.6 秒) 离 16 秒还远
        hud = lambda t: not (20 <= t < 29.8 or 40 <= t < 49.8)  # noqa: E731
        stopped, _ = self.run_macro(hud, until=120, reads=lambda t: True, team_reads=lambda t: True)
        self.assertIsNone(stopped)

    def test_nobody_looking_does_not_count(self):
        # 5~22 秒整段连点不刷新画面, 缓存帧停在 5 秒的大招演出上 (那时 HUD 不在), 其实 6 秒起 HUD 就回来了;
        # 22 秒又放一个 9 秒的大招。旧缓存帧也算数的话, 4 秒起就一直「不在」, 20 秒就会误停
        hud = lambda t: not (3 <= t < 6 or 22 <= t < 31)  # noqa: E731
        stale = lambda t: 5.0 if 5 <= t < 22 else None  # noqa: E731
        stopped, _ = self.run_macro(hud, until=80, frame_at=stale, reads=lambda t: True,
                                    team_reads=lambda t: True)
        self.assertIsNone(stopped)

    def test_long_gap_between_looks_restarts_the_count(self):
        # HUD 10 秒起就没了, 但前面只有每 7 秒一个标记、中间谁也不看, 接不上; 38 秒起每拍都有标记
        sparse = lambda t: (t >= 10 and (t - 10) % 7 == 0) or t >= 38 or t < 10  # noqa: E731
        stopped, _ = self.run_macro(lambda t: t < 10, until=120, marks=sparse)
        self.assertAlmostEqual(38.0 + 16.0, stopped)

    def test_wait_loops_keep_the_count_going(self):
        # 11-27 那局: 结算画面上每一步都在等大招演出, 等满 8 秒超时才打下一个标记,
        # 等的时候循环一直在读 HUD (看到不在), 这样也算连成一段
        every_8s = lambda t: t < 10 or (t - 10) % 8 == 0  # noqa: E731
        stopped, _ = self.run_macro(lambda t: t < 10, until=120, marks=every_8s, reads=lambda t: t >= 10)
        # 10 秒第一次看到不在, 26 秒满 16 秒, 下一个标记在 26 秒
        self.assertAlmostEqual(26.0, stopped)

    def test_new_loop_does_not_restart_the_count(self):
        # 结算画面上宏还在一圈圈往下跑, 每 10 秒开一圈 (macro_start), 不能每圈从头算
        stopped, _ = self.run_macro(lambda t: t < 30.2, until=120, restart=lambda t: t > 0 and t % 10 == 0)
        self.assertAlmostEqual(48.0, stopped)

    def test_team_can_set_its_own_limit(self):
        class Strict(Holder):
            HUD_LOST_ABORT = 8.0

        stopped, _ = self.run_macro(lambda t: t < 30.2, until=120, holder_cls=Strict)
        self.assertAlmostEqual(32.0 + 8.0, stopped)

    def test_not_watching_before_macro_start(self):
        stopped, task = self.run_macro(lambda t: t < 5, until=60, start=False)
        self.assertIsNone(stopped)
        self.assertEqual([], task.stops)

    def test_stops_once_per_macro_run(self):
        task = self.task_cls(self.clock, lambda t: t < 5)
        holder = Holder(task)
        self.clock.now = 0.0
        holder.macro_start()
        t = 0.0
        while t <= 60:
            self.clock.now = t
            try:
                holder.macro_mark('x')
            except Stopped:
                pass   # 队伍代码里的 except Exception 把它吞了
            t = round(t + TICK, 6)
        self.assertEqual(1, len(task.stops))

    def test_hud_ok_still_answers_the_same(self):
        task = self.task_cls(self.clock, lambda t: t < 5)
        holder = Holder(task)
        holder.macro_start()
        self.clock.now = 1.0
        self.assertIs(True, holder.hud_ok())
        self.assertEqual(1.0, holder.hud_seen_at)
        self.clock.now = 6.0
        self.assertIs(False, holder.hud_ok())
        self.assertEqual(6.0, holder.hud_missing_at)

    def test_team_own_wait_loop_reads(self):
        # 12:54 那局的形状: 结算画面上卡在队伍自己的等大招循环里, 8 秒才打一个标记, 循环里直接读 task.in_team
        every_8s = lambda t: t < 10 or (t - 10) % 8 == 0  # noqa: E731
        stopped, _ = self.run_macro(lambda t: t < 10, until=120, marks=every_8s, team_reads=lambda t: t >= 10)
        if self.sees_team_reads:
            # coco-ww: 队伍自己循环里读的也算, 10 秒起一直看着不在, 26 秒那个标记停
            self.assertAlmostEqual(26.0, stopped)
        else:
            # 原版 OKWW: 框架不记, 在 MacroKit 看来标记之间 8 秒没人看, 连不成一段, 不停 —— 宁可晚停, 不能误停
            self.assertIsNone(stopped)


class CocoWatchdogTest(WatchdogTest):
    """coco-ww: 框架每次 in_team 都记 (BaseWWTask.note_team_hud), 谁读的都算。"""
    task_cls = CocoTask
    sees_team_reads = True

    def test_absent_before_macro_start_counts_from_start(self):
        # HUD 在开宏之前就没了 (开宏那一刻算见过), 从开宏算满 16 秒
        task = CocoTask(self.clock, lambda t: False)
        holder = Holder(task)
        for t in (1.0, 2.0, 3.0):
            self.clock.now = t
            task.in_team()
        self.assertEqual(1.0, task.team_hud[1])
        self.clock.now = 4.0
        holder.macro_start()
        t, stopped = 4.0, None
        while t <= 40 and stopped is None:
            self.clock.now = t
            task.in_team()
            try:
                holder.macro_mark('x')
            except Stopped:
                stopped = t
            t = round(t + TICK, 6)
        self.assertAlmostEqual(4.0 + 16.0, stopped)


class RealRunTest(WatchdogCase):
    """按 11-27 两局实机的时刻重放 (coco-ww)。"""
    task_cls = CocoTask

    def replay(self, lost_at, mark_times, until):
        """HUD 在 lost_at 没了, 之后宏一直在循环里读 HUD (切人超时、等大招), 只在 mark_times 打标记。"""
        task = self.task_cls(self.clock, lambda t: t < lost_at)
        holder = Holder(task)
        self.clock.now = lost_at - 30
        holder.macro_start()
        events = sorted([(round(lost_at - 30 + i * 0.05, 3), 'read') for i in range(int((until - lost_at + 30) / 0.05))]
                        + [(m, 'mark') for m in mark_times])
        for t, kind in events:
            self.clock.now = t
            if kind == 'read':
                task.in_team()
                continue
            try:
                holder.macro_mark('x')
            except Stopped:
                return t
        return None

    def test_1714_quick_switch_timeouts(self):
        # 17:14 那局: 174.33 秒黑屏进结算, 之后每格切人都卡满 2.5 秒超时 (>LupFAIL 之类), 你在 193.8 秒手动关了。
        # 只算 MacroKit 自己读的, 176.80 才开始算、193.9 才停; 现在 174.33 起算, 停在 190.43 那个标记上。
        # 切人超时的标记时刻是日志里的, 中间几个 (连点、长按收尾) 按那几秒的按键推的
        marks = [176.80, 177.33, 179.93, 180.45, 181.18, 183.74, 185.04, 187.68, 187.89, 190.43, 191.32, 193.94]
        self.assertAlmostEqual(190.43, self.replay(174.33, marks, until=200))

    def test_1254_team_own_wait_loops(self):
        # 12:54 那局: 181.81 秒进结算, 宏卡在长离队自己的等大招循环里 (等满 8 秒超时), 标记很稀,
        # 照旧按到 267 秒。现在停在满 16 秒之后的第一个标记 201.52 上
        t0 = 176.67   # 第 5 圈开宏
        marks = [t0 + r for r in (5.13, 8.66, 8.73, 16.75, 16.78, 16.83, 24.85, 24.88, 25.68, 26.50, 29.20, 30.00)]
        self.assertAlmostEqual(201.52, self.replay(181.81, marks, until=210))


class TeamHudNoteTest(unittest.TestCase):
    """框架那一侧: BaseWWTask.note_team_hud 按画面截下来的时刻记。"""

    def setUp(self):
        self.clock = Clock()
        self.task = CocoTask(self.clock, lambda t: False)

    def note(self, at, visible):
        self.task.executor.frame_at = at
        BaseWWTask.note_team_hud(self.task, visible)
        return self.task.team_hud

    def test_same_frame_counts_once(self):
        self.note(1.0, True)
        self.assertEqual((1.0, 2.0, 2.0), self.note(2.0, False))
        self.clock.now = 9.0   # 一直读同一张旧图: 不往后延
        self.assertEqual((1.0, 2.0, 2.0), self.note(2.0, False))

    def test_seen_restarts_the_stretch(self):
        self.note(1.0, False)
        self.note(2.0, True)
        self.assertEqual((2.0, 3.0, 3.0), self.note(3.0, False))

    def test_long_gap_restarts_the_stretch(self):
        self.note(1.0, False)
        # 隔 TEAM_HUD_GAP 秒还接得上
        self.assertEqual((0.0, 1.0, 1.0 + TEAM_HUD_GAP), self.note(1.0 + TEAM_HUD_GAP, False))
        # 再隔得更久就从这一帧重新算
        later = 1.0 + 2 * TEAM_HUD_GAP + 0.5
        self.assertEqual((0.0, later, later), self.note(later, False))

    def test_in_team_notes_every_read(self):
        # 真的 BaseWWTask.in_team: 认 HUD 的三次 find_one 认到两个 = 在, 一个都没有 = 不在
        class Task(BaseWWTask):
            executor = None   # 框架里是只读属性, 这里换成普通字段

            def __init__(self, found):
                self.found = found
                self.executor = FakeExecutor(Clock())

            def find_one(self, name, threshold=0):
                return name if name in self.found else None

        with patch.object(BaseWWTask, 'logged_in', new=True, create=True):
            task = Task({'char_2_text', 'char_3_text'})
            task.executor.clock.now = 3.0
            self.assertEqual((True, 0, 3), task.in_team())
            self.assertEqual(3.0, task.team_hud[0])
            task.found = set()
            task.executor.clock.now = 4.0
            self.assertEqual((False, -1, 1), task.in_team())
            self.assertEqual((3.0, 4.0, 4.0), task.team_hud)


class HudReadsTest(unittest.TestCase):
    """MacroKit 自己读 HUD 的地方都要喂看门狗 (原版 OKWW 上只有这些): 只问在不在的走 hud_ok (在、不在都记),
    要 index/count 的看到在记 hud_seen_at、看到不在走 hud_ok 记一笔。

    17:14 那局结算画面上宏卡在切人超时里, 切人循环不记「不在」的话看门狗晚 2.5 秒才开始算。
    """

    def test_hud_reads_feed_the_watchdog(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        kit = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'MacroKit')
        checked = 0
        for method in kit.body:
            if not isinstance(method, ast.FunctionDef) or method.name == 'hud_ok':
                continue
            nodes = list(ast.walk(method))
            reads = [node for node in nodes if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == 'in_team']
            if not reads:
                continue
            checked += 1
            with self.subTest(method.name):
                only_flag = [node for node in nodes if isinstance(node, ast.Subscript) and node.value in reads]
                self.assertEqual([], only_flag, f'{method.name}: 只问 HUD 在不在, 用 self.hud_ok()')
                feeds = [node for node in nodes if isinstance(node, ast.Attribute) and node.attr == 'hud_seen_at'
                         and isinstance(node.ctx, ast.Store)]
                self.assertTrue(feeds, f'{method.name}: 看到 HUD 在要记 self.hud_seen_at')
                misses = [node for node in nodes if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                          and node.func.attr == 'hud_ok']
                self.assertTrue(misses, f'{method.name}: 看到 HUD 不在要走 self.hud_ok() 记一笔')
        self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()
