"""跑轴记录: 读屏记在谁头上 (2026-11-27)。

持宏引擎 MacroKit 在 src/combat 下, 以前跟判脱战 (CombatCheck) 一起算 plumb,
经它读的协奏、冷却一律不记: 持宏队伍的复盘页里看不到协奏读数, 读协奏时也存不到协奏框原图。
它是持宏队伍代码的基类, 跟 BaseChar 一样算经由, 读屏记在调它的队伍代码头上。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_recorder_origin.py
"""
import functools
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.recorder.recorder import Recorder  # noqa: E402

TEAM = str(ROOT / 'configs' / 'custom_teams' / 'Brant__Changli__Lupa' / 'Changli.py')
MACROKIT = str(ROOT / 'src' / 'combat' / 'MacroKit.py')
COMBAT_CHECK = str(ROOT / 'src' / 'combat' / 'CombatCheck.py')
BASE_CHAR = str(ROOT / 'src' / 'char' / 'BaseChar.py')


def layer(filename, name):
    """造一个函数, 它的代码文件名是 filename: 调它就调传进来的下一层。"""
    namespace = {}
    exec(compile(f'def {name}(inner):\n    return inner()\n', filename, 'exec'), namespace)
    return namespace[name]


def origin_through(recorder, layers, for_read):
    """按 layers 的顺序 (外层在前) 一层层调下去, 在最里层问 origin, 跟读屏钩子拿 sys._getframe(1) 一样。"""
    result = []

    def innermost():
        result.append(recorder.origin(sys._getframe(1), for_read))

    call = innermost
    for filename, name in reversed(layers):
        call = functools.partial(layer(filename, name), call)
    call()
    return result[0]


def describe(where):
    """origin 的结果 -> [(文件名, 函数名) 或 None] x 3: 代码1、代码2、经由。"""
    if where is None:
        return None
    return [None if code is None else (os.path.basename(code.co_filename), code.co_name)
            for code in where[0::2]]


class RecorderOriginTest(unittest.TestCase):
    def setUp(self):
        self.recorder = Recorder(out_dir=str(ROOT / 'logs' / 'axis_runs'))
        self.recorder._paths = self.recorder._compute_paths()

    def test_classify(self):
        classify = self.recorder.classify
        self.assertEqual('base', classify(MACROKIT))
        self.assertEqual('base', classify(BASE_CHAR))
        self.assertEqual('plumb', classify(COMBAT_CHECK))
        self.assertEqual('char', classify(TEAM))

    def test_con_read_through_macrokit_is_kept(self):
        # 长离 (持宏) 的循环轴: run_loop_body -> loop_1_pass -> MacroKit.con_wait -> con_now -> BaseChar.get_current_con
        where = origin_through(self.recorder, [
            (TEAM, 'run_loop_body'), (TEAM, 'loop_1_pass'),
            (MACROKIT, 'con_wait'), (MACROKIT, 'con_now'), (BASE_CHAR, 'get_current_con'),
        ], for_read=True)
        self.assertEqual([('Changli.py', 'loop_1_pass'), ('Changli.py', 'run_loop_body'),
                          ('BaseChar.py', 'get_current_con')], describe(where))

    def test_read_from_macrokit_itself_names_it_as_via(self):
        where = origin_through(self.recorder, [
            (TEAM, 'run_loop_body'), (TEAM, 'loop_4_changli_r'), (MACROKIT, 'read_con'),
        ], for_read=True)
        self.assertEqual([('Changli.py', 'loop_4_changli_r'), ('Changli.py', 'run_loop_body'),
                          ('MacroKit.py', 'read_con')], describe(where))

    def test_combat_check_reads_still_dropped(self):
        # 判脱战/找怪的读屏照旧不记, 哪怕是队伍代码里调起来的
        where = origin_through(self.recorder, [
            (TEAM, 'loop_1_pass'), (COMBAT_CHECK, 'in_combat'),
        ], for_read=True)
        self.assertIsNone(where)

    def test_input_through_macrokit_unchanged(self):
        # 按键本来就记: 经由是 MacroKit 那层
        where = origin_through(self.recorder, [
            (TEAM, 'run_loop_body'), (TEAM, 'loop_4_changli_r'), (MACROKIT, 'quick_switch'),
        ], for_read=False)
        self.assertEqual([('Changli.py', 'loop_4_changli_r'), ('Changli.py', 'run_loop_body'),
                          ('MacroKit.py', 'quick_switch')], describe(where))


if __name__ == '__main__':
    unittest.main()
