"""认技能键时不盖掉鼠标侧键 (搬自 ok-ww 072b30c / #1742, 2026-11-27)。

load_hotkey 按技能图标上的字认 E/Q/R 用的键, 跟配置不一样就改配置 (set_key)。
技能设在鼠标侧键 (mouse4/mouse5/xbutton1/xbutton2) 上时图标上没有字母, 以前会被认成别的键盖掉。

只把 set_key 从源码里抠出来跑, 不起游戏也不导入整个战斗任务。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_set_key.py
"""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parent.parent / 'src' / 'task' / 'BaseCombatTask.py'


def set_key_node():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'BaseCombatTask')
    return next(node for node in class_node.body
                if isinstance(node, ast.FunctionDef) and node.name == 'set_key')


class TestSetKey(unittest.TestCase):
    def make_task(self, configured, recognized):
        module = ast.fix_missing_locations(ast.Module(body=[
            ast.ClassDef(name='MethodOwner', bases=[], keywords=[], decorator_list=[], body=[set_key_node()])
        ], type_ignores=[]))
        namespace = {'logger': Mock()}
        exec(compile(module, str(SOURCE), 'exec'), namespace)
        task = namespace['MethodOwner']()
        task.key_config = {'Resonance Key': configured}
        task.find_best_match_in_box = Mock(return_value=SimpleNamespace(name=recognized))
        task.log_info = Mock()
        task.tr = Mock(side_effect=lambda text: text)
        return task

    def test_mouse_side_buttons_are_kept(self):
        for button in ('mouse4', 'mouse5', 'xbutton1', 'XButton2'):
            with self.subTest(button):
                task = self.make_task(button, 'e')
                task.set_key('Resonance Key', 'box_resonance')
                self.assertEqual(button, task.key_config['Resonance Key'])
                task.find_best_match_in_box.assert_not_called()

    def test_keyboard_key_still_follows_the_icon(self):
        task = self.make_task('q', 'e')
        task.set_key('Resonance Key', 'box_resonance')
        self.assertEqual('e', task.key_config['Resonance Key'])


if __name__ == '__main__':
    unittest.main()
