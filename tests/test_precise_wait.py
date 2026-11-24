"""BaseChar.precise_wait: 宏用的精确等待, 不插框架的战斗判定, 停止马上生效, 暂停时松键并让宏从头来。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_precise_wait.py
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ok import FinishedException, TaskDisabledException
from src.char.BaseChar import BaseChar, PreciseWaitPaused


class FakeInteraction:
    def __init__(self):
        self.capturable = True

    def should_capture(self):
        return self.capturable


class FakeExecutor:
    def __init__(self):
        self.exit_event = threading.Event()
        self.debug_mode = False
        self.paused = False
        self.pause_start = 100.0
        self.current_task = None
        self.interaction = FakeInteraction()
        self.disabled = False
        self.next_frames = 0
        self.framework_sleeps = 0

    def blocked_by_pause(self):
        return self.paused and not self.debug_mode

    def check_enabled(self, check_pause=True):
        if check_pause:
            raise AssertionError('精确等待里不能原地等暂停')
        if self.disabled:
            raise TaskDisabledException()

    def sleep(self, timeout):
        self.framework_sleeps += 1

    def next_frame(self):
        # 真的执行器在这里一直等到解除暂停
        self.next_frames += 1
        self.paused = False

    def pause(self):
        self.paused = True
        self.pause_start = time.time()


class FakeTask:
    def __init__(self):
        self.executor = FakeExecutor()
        self.executor.current_task = self
        self.paused = False
        self.in_liberation = True
        self.perform_watch = None
        self.released = 0
        self.release_waits = []

    def release_held_inputs(self, wait_pause=True):
        self.released += 1
        self.release_waits.append(wait_pause)
        return []


class MacroChar(BaseChar):
    """do_perform 依次跑 steps 里的每一步, 每步拿到自己。"""

    def __init__(self, task, steps=()):
        super().__init__(task, 0, char_name='macro')
        self.steps = list(steps)
        self.finished = False

    def do_perform(self):
        for step in self.steps:
            step(self)
        self.finished = True


def later(seconds, action):
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()
    return timer


class PreciseWaitTest(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()
        self.executor = self.task.executor
        self.char = MacroChar(self.task)

    def test_waits_without_framework_sleep(self):
        start = time.perf_counter()
        self.char.precise_wait(0.03)
        elapsed = time.perf_counter() - start
        self.assertGreaterEqual(elapsed, 0.03)
        self.assertLess(elapsed, 0.03 + 0.1)
        self.assertEqual(self.executor.framework_sleeps, 0, '不能走框架的 sleep (会插战斗判定)')

    def test_zero_or_negative_still_checks_stop(self):
        self.char.precise_wait(-1)
        self.executor.disabled = True
        with self.assertRaises(TaskDisabledException):
            self.char.precise_wait(0)

    def test_stop_takes_effect_within_a_slice(self):
        later(0.02, lambda: setattr(self.executor, 'disabled', True))
        start = time.perf_counter()
        with self.assertRaises(TaskDisabledException):
            self.char.precise_wait(2)
        self.assertLess(time.perf_counter() - start, 0.5)

    def test_app_exit_raises_finished(self):
        self.executor.exit_event.set()
        with self.assertRaises(FinishedException):
            self.char.precise_wait(1)

    def test_outside_perform_pause_is_ignored(self):
        # 没在 perform 里就没人接 PreciseWaitPaused, 和原来各队的 macro_wait 一样不管暂停
        self.executor.paused = True
        self.char.precise_wait(0.02)
        self.assertEqual(self.task.released, 0)

    def test_pause_releases_inputs_and_perform_starts_over(self):
        self.char.steps = [lambda c: c.precise_wait(2)]
        later(0.02, self.executor.pause)
        start = time.perf_counter()
        self.char.perform()  # 不往外抛
        self.assertLess(time.perf_counter() - start, 0.5)
        self.assertFalse(self.char.finished, '暂停后这段宏不该接着跑完')
        self.assertGreaterEqual(self.task.released, 1)
        self.assertEqual(self.task.release_waits[0], False, '暂停中要马上松键, 不能等到解除暂停')
        self.assertFalse(self.task.in_liberation)
        self.assertEqual(self.executor.next_frames, 1, 'perform 要等到解除暂停再返回')
        self.assertIsNone(self.task.precise_perform)

    def test_pause_that_already_ended_still_starts_over(self):
        def paused_inside_a_key_press(char):
            # 框架的按键函数发键前会等暂停: 暂停被它等过去了, 宏回来时已经解除
            self.executor.pause_start += 5

        self.char.steps = [paused_inside_a_key_press, lambda c: c.precise_wait(2)]
        start = time.perf_counter()
        self.char.perform()
        self.assertLess(time.perf_counter() - start, 0.5)
        self.assertFalse(self.char.finished)
        self.assertGreaterEqual(self.task.released, 1)

    def test_game_not_in_foreground_counts_as_pause(self):
        self.executor.interaction.capturable = False
        self.char.steps = [lambda c: c.precise_wait(2)]
        self.char.perform()
        self.assertFalse(self.char.finished)
        self.assertGreaterEqual(self.task.released, 1)

    def test_task_level_pause_counts(self):
        self.task.paused = True
        self.char.steps = [lambda c: c.precise_wait(2)]
        self.char.perform()
        self.assertFalse(self.char.finished)

    def test_debug_mode_ignores_pause(self):
        self.executor.debug_mode = True
        self.executor.paused = True
        self.char.steps = [lambda c: c.precise_wait(0.02)]
        self.char.perform()
        self.assertTrue(self.char.finished)

    def test_macro_holder_waiting_for_another_char(self):
        # 心锁莉: 莉莉上场, 莉莉的 do_perform 交给心跑宏, 精确等待是心调的
        holder = MacroChar(self.task)
        on_field = MacroChar(self.task, [lambda c: holder.precise_wait(2)])
        later(0.02, self.executor.pause)
        on_field.perform()
        self.assertFalse(on_field.finished)
        self.assertGreaterEqual(self.task.released, 1)

    def test_team_code_except_exception_cannot_swallow_it(self):
        # 队伍代码里很多 except Exception 兜底; 吞掉的话宏会接着跑, 后面每次等待又马上抛, 一下不等地连按
        reached = []

        def wait_inside_broad_except(char):
            try:
                char.precise_wait(2)
            except Exception:
                pass
            reached.append(True)

        self.char.steps = [wait_inside_broad_except]
        later(0.02, self.executor.pause)
        self.char.perform()
        self.assertEqual(reached, [])
        self.assertFalse(self.char.finished)

    def test_stop_inside_perform_still_propagates(self):
        self.char.steps = [lambda c: c.precise_wait(2)]
        later(0.02, lambda: setattr(self.executor, 'disabled', True))
        with self.assertRaises(TaskDisabledException):
            self.char.perform()
        self.assertGreaterEqual(self.task.released, 1)
        self.assertIsNone(self.task.precise_perform)

    def test_normal_perform_runs_to_the_end(self):
        self.char.steps = [lambda c: c.precise_wait(0.01), lambda c: c.precise_wait(0)]
        self.char.perform()
        self.assertTrue(self.char.finished)
        self.assertEqual(self.task.released, 0)
        self.assertEqual(self.executor.next_frames, 0)


class ReleaseHeldInputsTest(unittest.TestCase):
    """BaseCombatTask.release_held_inputs(wait_pause=False): 暂停中直接发松键, 不走会等暂停的框架函数。"""

    def make_task(self):
        from src.task.BaseCombatTask import BaseCombatTask

        class Interaction:
            def __init__(self):
                self.sent = []

            def send_key_up(self, key):
                self.sent.append(('key', key))

            def mouse_up(self, key='left'):
                self.sent.append(('mouse', key))

        class Task:
            _held = BaseCombatTask._held
            release_held_inputs = BaseCombatTask.release_held_inputs
            validate_key = BaseCombatTask.validate_key

            def __init__(self):
                self.executor = type('Executor', (), {})()
                self.executor.interaction = Interaction()
                self.framework = []

            def send_key_up(self, key, after_sleep=0):
                self.framework.append(('key', key))

            def mouse_up(self, name=None, key='left'):
                self.framework.append(('mouse', key))

        return Task()

    def test_release_now_goes_straight_to_the_interaction(self):
        task = self.make_task()
        task._held().update({('key', 'w'), ('mouse', 'left')})
        released = task.release_held_inputs(wait_pause=False)
        self.assertEqual(sorted(task.executor.interaction.sent), [('key', 'w'), ('mouse', 'left')])
        self.assertEqual(task.framework, [])
        self.assertEqual(task._held(), set())
        self.assertEqual(len(released), 2)

    def test_default_still_uses_the_framework_functions(self):
        task = self.make_task()
        task._held().add(('key', 'e'))
        task.release_held_inputs()
        self.assertEqual(task.framework, [('key', 'e')])
        self.assertEqual(task.executor.interaction.sent, [])


if __name__ == '__main__':
    unittest.main()
