"""回血传送: 地图上按距离从近到远试传送点 (搬自 ok-ww #1849 / 9581fb0, 2026-11-19)。

传送点和别的图标叠在一起时, 点下去游戏先弹图标选择列表, 等不到「传送」按钮。
原来 wait_feature 是 raise_if_not_found=True, 等不到直接抛异常中断 (死亡回血、刷4C 回血都走这里)。
现在等不到就点地图空白关掉列表, 换下一个传送点。

只把 _travel_to_nearest_waypoint 这一个方法从源码里抠出来跑, 不起游戏也不导入整个战斗任务。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_travel_waypoint.py
"""
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parent.parent / 'src' / 'task' / 'BaseCombatTask.py'


class FakeBox:
    def __init__(self, name, x=0, y=0, width=10, height=10):
        self.name = name
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.confidence = 1.0


def travel_method_node():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    class_node = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef) and node.name == 'BaseCombatTask')
    return next((node for node in class_node.body
                 if isinstance(node, ast.FunctionDef) and node.name == '_travel_to_nearest_waypoint'), None)


class TestTravelToNearestWaypoint(unittest.TestCase):
    def setUp(self):
        self.method_node = travel_method_node()
        self.assertIsNotNone(self.method_node, 'BaseCombatTask 里要有 _travel_to_nearest_waypoint')

    def make_task(self):
        module = ast.fix_missing_locations(ast.Module(body=[
            ast.ClassDef(name='MethodOwner', bases=[], keywords=[], decorator_list=[], body=[self.method_node])
        ], type_ignores=[]))
        namespace = {}
        exec(compile(module, str(SOURCE), 'exec'), namespace)
        task = namespace['MethodOwner']()
        task.sleep = Mock()
        task.width = 1920
        task.height = 1080
        task.box_of_screen = Mock(return_value=FakeBox('map'))
        task.tr = Mock(side_effect=lambda text: text)
        task.click = Mock()
        task.click_box = Mock()
        task.find_feature = Mock(return_value=[])
        task.wait_feature = Mock(return_value=None)
        task.wait_in_team_and_world = Mock()
        return task

    def test_waits_for_travel_button_without_raising(self):
        waits = [
            node for node in ast.walk(self.method_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == 'self'
            and node.func.attr == 'wait_feature'
        ]
        self.assertEqual(1, len(waits))
        keywords = {keyword.arg: keyword.value for keyword in waits[0].keywords}
        self.assertIn('raise_if_not_found', keywords)
        self.assertIsInstance(keywords['raise_if_not_found'], ast.Constant)
        self.assertFalse(keywords['raise_if_not_found'].value)

    def test_tries_nearest_candidate_first(self):
        task = self.make_task()
        far = FakeBox('map_way_point', 1700, 900)
        near = FakeBox('map_way_point', 940, 520)
        task.find_feature = Mock(side_effect=[[far, near], []])
        travel = FakeBox('gray_teleport', 1300, 900)
        task.wait_feature = Mock(return_value=travel)

        task._travel_to_nearest_waypoint()

        self.assertEqual(near, task.click.call_args_list[0].args[0])
        task.click_box.assert_called_once_with(travel, relative_x=1.5)
        task.wait_in_team_and_world.assert_called_once()

    def test_skips_candidate_when_choice_list_is_shown(self):
        task = self.make_task()
        overlap = FakeBox('map_way_point', 930, 500)
        other = FakeBox('map_way_point', 1000, 600)
        task.find_feature = Mock(side_effect=[[overlap, other], []])
        travel = FakeBox('gray_teleport', 1300, 900)
        task.wait_feature = Mock(side_effect=[None, travel])

        task._travel_to_nearest_waypoint()

        clicked = [call.args for call in task.click.call_args_list]
        self.assertEqual([(overlap,), (0.06, 0.5), (other,)], clicked)
        task.click_box.assert_called_once_with(travel, relative_x=1.5)

    def test_small_and_big_icon_of_same_waypoint_tried_once(self):
        task = self.make_task()
        small = FakeBox('map_way_point', 930, 500)
        big = FakeBox('map_way_point_big', 925, 495, 20, 20)
        task.find_feature = Mock(side_effect=[[small], [big]])

        with self.assertRaises(RuntimeError):
            task._travel_to_nearest_waypoint()

        self.assertEqual([(small,), (0.06, 0.5)], [call.args for call in task.click.call_args_list])

    def test_raises_when_all_candidates_fail(self):
        task = self.make_task()
        overlap = FakeBox('map_way_point', 930, 500)
        task.find_feature = Mock(side_effect=[[overlap], []])

        with self.assertRaises(RuntimeError):
            task._travel_to_nearest_waypoint()

        task.click_box.assert_not_called()

    def test_raises_when_no_waypoint_on_map(self):
        task = self.make_task()

        with self.assertRaises(RuntimeError):
            task._travel_to_nearest_waypoint()

        task.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
