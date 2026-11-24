"""后台 PostMessage 只在需要时投 WM_ACTIVATE (搬自 ok-script 66e5eaf, 2026-11-22)。

原来每次按键、每次点鼠标都先投一遍 WM_ACTIVATE, 游戏收到就把已经按住的左键/按键打断
(不少队伍代码里写着「按住期间别再 mouse_down」, 躲的就是它)。现在投过一次就记下,
直到游戏窗口前后台变了 (HwndWindow 回调 on_visible) 或者目标窗口换了才重投。

前 5 个测试照搬上游, 最后一个是 coco-ww 加的: 按住左键期间按 E、闪避, 按下左键之后不能再冒出激活。

在 coco-ww 根目录跑:  python\\python.exe -X utf8 tests\\test_post_message_activation.py
"""
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import win32con  # noqa: E402

from ok.device.interaction_methods.post_message import PostMessageInteraction  # noqa: E402

WIN32GUI = 'ok.device.interaction_methods.post_message.win32gui'


class TestPostMessageActivation(unittest.TestCase):

    def setUp(self):
        self.hwnd_window = Mock()
        self.hwnd_window.hwnd = 100
        self.hwnd_window.top_hwnd = 100
        self.hwnd_window.visible_monitors = []
        self.interaction = PostMessageInteraction(Mock(), self.hwnd_window)
        self.interaction.post = Mock()

    def activations(self):
        return [c for c in self.interaction.post.call_args_list if c.args[0] == win32con.WM_ACTIVATE]

    def test_registers_visibility_monitor(self):
        self.assertIn(self.interaction, self.hwnd_window.visible_monitors)

    def test_activates_once_until_visibility_changes(self):
        self.interaction.send_key('a')
        self.interaction.send_key('b')
        self.interaction.input_text('xy')
        self.assertEqual(len(self.activations()), 1)

        self.interaction.on_visible(False)
        self.interaction.send_key('a')
        self.assertEqual(len(self.activations()), 2)

    def test_reactivates_when_target_hwnd_changes(self):
        self.interaction.try_activate()
        with patch(f'{WIN32GUI}.IsWindow', return_value=True):
            self.interaction._dynamic_target_hwnd = 200
            self.interaction.try_activate()
            self.interaction.try_activate()
        targets = [c.kwargs['hwnd'] for c in self.activations()]
        self.assertEqual(targets, [100, 100, 200])

    def test_force_activation(self):
        self.interaction.try_activate()
        self.interaction.try_activate(force=True)
        self.assertEqual(len(self.activations()), 2)

    def test_activate_false_skips_activation(self):
        self.interaction.send_key_down('a', activate=False)
        self.interaction.input_text('x', activate=False)
        self.assertEqual(self.activations(), [])

    def test_holding_left_survives_skill_keys(self):
        """按住左键 -> 按 E -> 闪避 -> 松左键: 激活只在按下左键之前投一次。"""
        self.hwnd_window.hwnds = []
        with patch(f'{WIN32GUI}.ClientToScreen', return_value=(0, 0)), \
                patch(f'{WIN32GUI}.ScreenToClient', return_value=(0, 0)), \
                patch(f'{WIN32GUI}.IsWindow', return_value=True):
            self.interaction.mouse_down()
            self.interaction.send_key('e')
            self.interaction.send_key('lshift')
            self.interaction.mouse_up()
        messages = [c.args[0] for c in self.interaction.post.call_args_list]
        down = messages.index(win32con.WM_LBUTTONDOWN)
        self.assertEqual(messages.count(win32con.WM_ACTIVATE), 1)
        self.assertLess(messages.index(win32con.WM_ACTIVATE), down)
        self.assertNotIn(win32con.WM_ACTIVATE, messages[down:])
        self.assertEqual(messages.count(win32con.WM_KEYDOWN), 2)


if __name__ == '__main__':
    unittest.main()
