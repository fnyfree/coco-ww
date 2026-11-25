"""跑轴复盘页(C1 影院布局): 分圈、事件跳转、此刻信息、播放、展开收起。"""
import json
import os
import shutil
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src.recorder.runs import RunData

TEAM = [{'i': 0, 'n': 'Hsin'}, {'i': 1, 'n': 'Suoming'}, {'i': 2, 'n': 'Lily'}]
NAME = '20261126_120000_Hsin__Suoming__Lily.jsonl'


def run_events():
    """三人轮换: Hsin 上场开一圈。第 3 圈 Suoming 那段慢 4 秒。"""
    events = [{'e': 'start', 't': 0, 'wall': 1795000000.0, 'size': [1920, 1080], 'team': TEAM}]
    t, lap = 1.0, 0
    for lap in range(5):
        for slot, length in ((0, 3.0), (1, 2.0 + (4.0 if lap == 2 else 0.0)), (2, 2.0)):
            events.append({'e': 'field', 't': round(t, 3), 'team': 1, 'i': slot, 'cnt': 3})
            events.append({'e': 'in', 't': round(t + 0.5, 3), 'a': 'key', 'k': 'e'})
            t += length
    events.append({'e': 'field', 't': round(t, 3), 'team': 1, 'i': 0, 'cnt': 3})
    # 按住左键 1.5 秒(算按住 A), 一句 WARN 连刷三次, 一个读屏段, 一个打点
    events += [
        {'e': 'in', 't': 2.0, 'a': 'md', 'k': 'left'},
        {'e': 'in', 't': 3.5, 'a': 'mu', 'k': 'left'},
        {'e': 'log', 't': 2.2, 'l': 30, 'm': 'found multiple rings 2'},
        {'e': 'log', 't': 2.3, 'l': 30, 'm': 'found multiple rings 2'},
        {'e': 'log', 't': 2.4, 'l': 30, 'm': 'found multiple rings 2'},
        {'e': 'log', 't': 2.6, 'l': 20, 'm': 'hello'},
        {'e': 'read', 't': 2.0, 't2': 2.9, 'k': 'find', 'name': 'forte_full', 'ok': False, 'n': 4,
         'src': 'Hsin.py:10 check'},
        {'e': 'mark', 't': 5.0, 'name': 'boss_hp', 'v': 0.5},
    ]
    events.append({'e': 'end', 't': round(t + 1.0, 3), 'reason': '测试'})
    return events


def write_run(folder):
    path = os.path.join(folder, NAME)
    with open(path, 'w', encoding='utf-8') as f:
        for event in run_events():
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
    return path


class LapInfoTest(unittest.TestCase):
    def setUp(self):
        from src.recorder.RecorderTab import LapInfo
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.run = RunData(write_run(folder))
        self.info = LapInfo(self.run, 0)

    def test_laps_and_position(self):
        self.assertEqual(len(self.info.laps), 5)
        self.assertEqual(self.info.lap_at(0.5), -1)
        self.assertEqual(self.info.lap_at(1.5), 0)
        self.assertEqual(self.info.lap_at(self.run.duration), 5)

    def test_slow_lap_is_marked(self):
        self.assertAlmostEqual(self.info.over(2), 4.0, places=3)
        self.assertAlmostEqual(self.info.over(0), 0.0, places=3)

    def test_segment_has_median(self):
        start = self.info.laps[2][0]
        t0, duration, median = self.info.segment_at(2, start + 3.5)
        self.assertAlmostEqual(t0, start + 3.0, places=3)
        self.assertAlmostEqual(duration, 6.0, places=3)
        self.assertAlmostEqual(median, 2.0, places=3)

    def test_no_slot_means_no_laps(self):
        from src.recorder.RecorderTab import LapInfo
        info = LapInfo(self.run, None)
        self.assertIsNone(info.lap_at(3.0))
        self.assertIsNone(info.segment_at(None, 3.0))


class TabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from src.recorder.RecorderTab import AxisRecorderTab
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        write_run(self.folder)
        self.tab = AxisRecorderTab(out_dir=self.folder)
        self.tab.resize(1176, 700)
        self.tab.show()
        self.addCleanup(self.tab.deleteLater)
        self.addCleanup(self.tab.dispose)
        for _ in range(5):
            self.app.processEvents()
        # 默认按上场次数最少的角色(这里是 Suoming)分圈; 测试按 Hsin 分, 一圈从 Hsin 上场算起
        self.default_laps = len(self.tab.lap_info.laps)
        self.tab.lap_box.setCurrentIndex(self.tab.lap_box.findData(0))

    def test_loads_newest_run_at_start(self):
        tab = self.tab
        self.assertIsNotNone(tab.run)
        self.assertEqual(tab.t, 0.0)
        self.assertEqual(self.default_laps, 4)
        self.assertEqual(len(tab.lap_info.laps), 5)
        self.assertFalse(tab.drawer.isVisible())
        self.assertFalse(tab.canvas.full)
        self.assertEqual([lane.kind for lane in tab.canvas.lanes], ['field', 'keys', 'alerts'])

    def test_overlay_shows_lap_field_and_hold(self):
        tab = self.tab
        tab._set_time(3.0, 'strip')
        overlay = tab.frame_panel.view.overlay
        self.assertEqual(overlay['title'], '第 1 圈')
        self.assertEqual(overlay['time'], '3.000s')
        name, _, text = overlay['field']
        self.assertEqual(name, 'Hsin')
        self.assertIn('本段 2.00s / 3.00s', text)
        self.assertIn('中位 3.00', text)
        label, frac, _ = overlay['hold']
        self.assertEqual(label, '按住A')
        self.assertAlmostEqual(frac, 1 / 1.5, places=3)
        self.assertEqual(tab.canvas.cursor_t, 3.0)
        self.assertEqual(tab.lap_strip.cursor_t, 3.0)

    def test_now_panel_lists_reads_logs_and_next(self):
        tab = self.tab
        tab._set_time(2.5, 'strip')
        panel = tab.now_panel
        self.assertIn('forte_full', panel.reads_label.text())
        self.assertIn('没认到', panel.reads_label.text())
        self.assertIn('×3', panel.logs_label.text())  # 连刷三次合成一条
        self.assertIn('hello', panel.logs_label.text())
        self.assertIn('松开 A', panel.next_label.text())

    def test_event_steps(self):
        tab = self.tab
        tab._set_time(2.5, 'strip')
        tab._step_event(1)
        self.assertAlmostEqual(tab.t, 3.5)  # 松开左键
        tab._step_event(1)
        self.assertAlmostEqual(tab.t, 4.0)  # Suoming 上场
        tab._step_event(-1)
        self.assertAlmostEqual(tab.t, 3.5)

    def test_play_moves_cursor_and_stops_at_end(self):
        tab = self.tab
        tab._set_time(tab.run.duration - 0.05, 'strip')
        tab._toggle_play()
        self.assertTrue(tab.play_timer.isActive())
        tab._last_tick -= 1.0  # 假装过了 1 秒
        tab._play_tick()
        self.assertEqual(tab.t, tab.run.duration)
        self.assertFalse(tab.play_timer.isActive())
        tab._toggle_play()  # 到头了再播从头开始
        self.assertTrue(tab.play_timer.isActive())
        self.assertLess(tab.t, 0.1)
        tab._stop_play()

    def test_toggle_lanes_and_drawer(self):
        tab = self.tab
        compact = tab.progress.maximumHeight()
        tab.now_panel.set_lanes(True)
        self.assertTrue(tab.canvas.full)
        self.assertIn('read', [lane.kind for lane in tab.canvas.lanes])
        self.assertGreater(tab.progress.maximumHeight(), compact)
        tab.now_panel.set_lanes(False)
        self.assertEqual(tab.progress.maximumHeight(), compact)
        tab.now_panel.set_drawer(True)
        self.assertTrue(tab.drawer.isVisible())
        tab._set_time(5.0, 'strip')
        self.assertTrue(tab.table.selectionModel().hasSelection())
        tab.now_panel.set_drawer(False)
        self.assertFalse(tab.drawer.isVisible())

    def test_lap_change_updates_strip(self):
        tab = self.tab
        tab.lap_box.setCurrentIndex(0)  # 不分圈
        self.assertEqual(tab.lap_info.laps, [])
        self.assertTrue(all(segment[5] is not None for segment in tab.lap_strip.segments))  # 按谁在场上色
        tab._set_time(3.0, 'strip')
        self.assertEqual(tab.frame_panel.view.overlay['title'], '')

    def test_renders(self):
        tab = self.tab
        tab._set_time(10.0, 'strip')
        self.assertFalse(tab.grab().isNull())


if __name__ == '__main__':
    unittest.main()
