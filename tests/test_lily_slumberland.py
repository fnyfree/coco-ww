"""莉莉放完大招进梦乡后别切回来 (搬自 ok-ww ef5f68d / #1877, 2026-11-27)。

梦乡 50 秒: 普攻、共鸣技能、声骸都没了, 平切进来什么也做不了。
同一下大招给的摇篮曲 25 秒, 莉莉一回场就没了, 所以前 25 秒连变奏入场也不要; 之后她的变奏会结束梦乡, 变奏可以进。
在场上看到她在睡但不知道睡了多久, 只拦 10 秒。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_lily_slumberland.py
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.char.BaseChar import CharType, SwitchPriority  # noqa: E402
from src.char.Lily import Lily  # noqa: E402


class FakeTask:
    now = 1000.0
    freeze = 0.0

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return self.now - start - self.freeze


class LilySlumberlandTest(unittest.TestCase):
    def setUp(self):
        self.task = FakeTask()
        self.lily = Lily(self.task, 0, char_type=CharType.HEALER)

    def asleep_for(self, elapsed, block=Lily.SLUMBERLAND_DURATION):
        self.lily.slumberland_start = self.task.now - elapsed
        self.lily.slumberland_block = block

    def priority(self, has_intro):
        return self.lily.get_switch_priority(has_intro=has_intro)

    def test_awake_lily_is_a_normal_target(self):
        self.assertFalse(self.lily.slumberland_block_active())
        self.assertGreater(self.priority(False), SwitchPriority.NO)
        self.assertGreater(self.priority(True), SwitchPriority.NO)

    def test_plain_switch_refused_for_the_whole_slumberland(self):
        for elapsed in (1, 25.1, 49.9):
            with self.subTest(elapsed=elapsed):
                self.asleep_for(elapsed)
                self.assertEqual(SwitchPriority.NO, self.priority(False))
        self.asleep_for(50.1)
        self.assertGreater(self.priority(False), SwitchPriority.NO)

    def test_intro_refused_only_while_cradle_lullaby_lasts(self):
        self.asleep_for(24.9)
        self.assertEqual(SwitchPriority.NO, self.priority(True))
        self.asleep_for(25.1)
        self.assertGreater(self.priority(True), SwitchPriority.NO)

    def test_sighting_blocks_briefly(self):
        self.asleep_for(9.9, block=Lily.SLUMBERLAND_SIGHTING_BLOCK)
        self.assertEqual(SwitchPriority.NO, self.priority(False))
        self.asleep_for(10.1, block=Lily.SLUMBERLAND_SIGHTING_BLOCK)
        self.assertGreater(self.priority(False), SwitchPriority.NO)

    def test_freeze_does_not_use_up_the_window(self):
        # 时停/卡肉不算时间
        self.asleep_for(55)
        self.task.freeze = 10
        self.assertEqual(SwitchPriority.NO, self.priority(False))


if __name__ == '__main__':
    unittest.main()
