"""跑轴记录的整局画面: 清晰度设置、打包反馈压回标清、复盘页画面缩放。"""
import json
import os
import tempfile
import unittest
import zipfile
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import cv2
import numpy as np

from src.recorder import media as m


def hud_frame(width=2560, height=1440):
    """像游戏画面的假图: 渐变底 + 一些字, JPEG 体积和真画面差不多一个量级。"""
    x = np.linspace(0, 255, width, dtype=np.uint8)
    frame = np.dstack([np.tile(x, (height, 1)), np.tile(x[::-1], (height, 1)),
                       np.full((height, width), 90, np.uint8)])
    for i in range(12):
        cv2.putText(frame, f'Skill {i} 12.5s', (60 + i * 200, height - 80 - (i % 3) * 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    return frame


def write_run(folder, quality, frames=3, crop=True):
    """用写盘线程的 _handle 同步写一局, 返回 .jsonl 路径。"""
    run_path = os.path.join(folder, '20261126_101500_Hsin__Suoming__Lily.jsonl')
    with open(run_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps({'e': 'start', 't': 0, 'wall': 1795000000.0, 'size': [2560, 1440],
                            'team': [{'i': 0, 'name': 'Hsin'}]}) + '\n')
        f.write(json.dumps({'e': 'end', 't': 3.0, 'reason': '测试'}) + '\n')
    run = SimpleNamespace(path=run_path, t0=100.0, reads=True)
    sink = m.FrameSink(run, True, crop, 0, quality)
    writer = m.MediaWriter(lambda *_: 'test.py:1')
    image = hud_frame()
    for i in range(frames):
        writer._handle(('f', sink, 100.0 + i * m.FRAME_INTERVAL, image))
    if crop:
        writer._handle(('c', sink, 100.3, 'find', 'skill_e', (None, None), (10, 20, 30, 40),
                        image[20:60, 10:40].copy(), 0.93, True))
    writer._close(sink)
    return run_path


class ShrinkTest(unittest.TestCase):
    def test_width_limits_the_frame(self):
        self.assertEqual(m.shrink(hud_frame(), 1280).shape, (720, 1280, 3))
        self.assertEqual(m.shrink(hud_frame(), 960).shape, (540, 960, 3))

    def test_none_keeps_full_size(self):
        self.assertEqual(m.shrink(hud_frame(), None).shape, (1440, 2560, 3))

    def test_smaller_frame_is_not_enlarged(self):
        self.assertEqual(m.shrink(hud_frame(1280, 720), 1920).shape, (720, 1280, 3))

    def test_bgra_becomes_bgr(self):
        bgra = cv2.cvtColor(hud_frame(1920, 1080), cv2.COLOR_BGR2BGRA)
        self.assertEqual(m.shrink(bgra, None).shape, (1080, 1920, 3))


class QualityTest(unittest.TestCase):
    def test_default_is_high(self):
        self.assertEqual(m.DEFAULT_QUALITY, m.QUALITY_HIGH)
        self.assertEqual(m.frame_quality(m.DEFAULT_QUALITY), (1280, 85))

    def test_unknown_name_falls_back_to_default(self):
        self.assertEqual(m.frame_quality('不认识的'), m.frame_quality(m.DEFAULT_QUALITY))
        self.assertEqual(m.frame_quality(None), m.frame_quality(m.DEFAULT_QUALITY))

    def test_standard_matches_old_behaviour(self):
        self.assertEqual(m.frame_quality(m.QUALITY_STANDARD), (m.LOW_WIDTH, m.JPEG_QUALITY))

    def test_sink_takes_quality(self):
        run = SimpleNamespace(path=None, t0=0.0, reads=False)
        sink = m.FrameSink(run, True, False, 0, '原分辨率')
        self.assertEqual((sink.frame_width, sink.jpeg_quality), (None, 85))
        sink = m.FrameSink(run, True, False, 0)
        self.assertEqual(sink.frame_width, 1280)

    def test_task_setting_key(self):
        # 只看类上的键名(recorder._option 按这个属性名取设置), 不跑框架的 __init__
        from src.recorder.AxisRecorder import AxisRecorderTask
        self.assertEqual(AxisRecorderTask.FRAMES_QUALITY_KEY, '整局画面清晰度')


class WriterTest(unittest.TestCase):
    def test_frames_are_stored_at_the_chosen_width(self):
        for quality, width in ((m.QUALITY_STANDARD, 960), (m.QUALITY_HIGH, 1280), ('原分辨率', 2560)):
            with self.subTest(quality=quality), tempfile.TemporaryDirectory() as folder:
                index = m.MediaIndex(write_run(folder, quality))
                self.assertEqual(len(index.frames), 3)
                self.assertEqual({e['w'] for e in index.frames}, {width})
                image = cv2.imdecode(np.frombuffer(index.read(index.frames[0]), np.uint8), cv2.IMREAD_COLOR)
                self.assertEqual(image.shape[1], width)


class StandardMediaTest(unittest.TestCase):
    def test_round_trip_shrinks_frames_and_keeps_crops(self):
        with tempfile.TemporaryDirectory() as folder:
            run_path = write_run(folder, m.QUALITY_HIGH)
            before = m.MediaIndex(run_path)
            out_dir = os.path.join(folder, 'out')
            os.makedirs(out_dir)
            out_run = os.path.join(out_dir, os.path.basename(run_path))
            media_path, index_path = m.media_paths(out_run)
            with open(media_path, 'wb') as out:
                text = m.standard_media(run_path, out)
            with open(index_path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
            after = m.MediaIndex(out_run)
            self.assertEqual(json.loads(text.splitlines()[0]), {'v': m.MEDIA_VERSION, 'interval': m.FRAME_INTERVAL})
            self.assertEqual(len(after.frames), len(before.frames))
            self.assertEqual({e['w'] for e in after.frames}, {960})
            self.assertEqual({e['h'] for e in after.frames}, {540})
            self.assertEqual([e['t'] for e in after.frames], [e['t'] for e in before.frames])
            image = cv2.imdecode(np.frombuffer(after.read(after.frames[-1]), np.uint8), cv2.IMREAD_COLOR)
            self.assertEqual(image.shape, (540, 960, 3))
            (key, crops), = after.crops.items()
            self.assertEqual(after.read(crops[0]), before.read(before.crops[key][0]))
            self.assertLess(os.path.getsize(media_path), os.path.getsize(m.media_paths(run_path)[0]))

    def test_estimate_is_close_to_real_size(self):
        with tempfile.TemporaryDirectory() as folder:
            run_path = write_run(folder, m.QUALITY_HIGH, frames=8)
            estimate = m.estimate_standard_bytes(run_path)
            with tempfile.TemporaryFile() as out:
                m.standard_media(run_path, out)
                real = out.tell()
            self.assertAlmostEqual(estimate, real, delta=real * 0.1)

    def test_standard_run_has_nothing_to_shrink(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(m.estimate_standard_bytes(write_run(folder, m.QUALITY_STANDARD)))


class FeedbackFramesTest(unittest.TestCase):
    def pack(self, quality, mode):
        from src.recorder import feedback
        folder = tempfile.mkdtemp()
        self.addCleanup(__import__('shutil').rmtree, folder, True)
        run_path = write_run(folder, quality)
        plan = feedback.FeedbackPlan(run_path, folder)
        path = feedback.build_feedback(plan, log_path=os.path.join(folder, 'none.log'), frames_mode=mode)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            info = json.loads(zf.read('feedback.json'))
            summary = zf.read(feedback.SUMMARY_NAME).decode('utf-8')
            stem = 'axis_runs/' + os.path.basename(run_path)[:-6]
            media = zf.read(stem + '.media') if stem + '.media' in names else None
            index = zf.read(stem + '.media.idx').decode('utf-8') if stem + '.media.idx' in names else None
        return plan, info, summary, media, index

    def test_plan_knows_stored_size(self):
        from src.recorder import feedback
        with tempfile.TemporaryDirectory() as folder:
            plan = feedback.FeedbackPlan(write_run(folder, m.QUALITY_HIGH), folder)
            self.assertEqual((plan.frames, plan.frame_width, plan.frame_height), (3, 1280, 720))
            self.assertTrue(plan.can_standard)
            standard = plan.standard_bytes()
            self.assertLess(standard, plan.frames_bytes)
            self.assertEqual(plan.total_bytes(feedback.FRAMES_NONE), plan.base_bytes - plan.frames_bytes)
            self.assertEqual(plan.total_bytes(feedback.FRAMES_STANDARD),
                             plan.base_bytes - plan.frames_bytes + standard)

    def test_keep(self):
        from src.recorder import feedback
        plan, info, summary, media, index = self.pack(m.QUALITY_HIGH, feedback.FRAMES_KEEP)
        self.assertEqual(info['frames_size'], [1280, 720])
        self.assertIn('整局画面 3 帧(1280x720)', summary)
        self.assertEqual({json.loads(l).get('w') for l in index.splitlines()[1:] if '"f"' in l}, {1280})

    def test_standard(self):
        from src.recorder import feedback
        plan, info, summary, media, index = self.pack(m.QUALITY_HIGH, feedback.FRAMES_STANDARD)
        self.assertEqual(info['frames_mode'], feedback.FRAMES_STANDARD)
        self.assertIn('压回了标清', summary)
        entries = [json.loads(l) for l in index.splitlines()[1:]]
        self.assertEqual({e['w'] for e in entries if e.get('f')}, {960})
        self.assertEqual(sum(e['n'] for e in entries), len(media))
        last = max(entries, key=lambda e: e['o'])
        self.assertEqual(last['o'] + last['n'], len(media))

    def test_none(self):
        from src.recorder import feedback
        plan, info, summary, media, index = self.pack(m.QUALITY_HIGH, feedback.FRAMES_NONE)
        self.assertIsNone(media)
        self.assertIsNone(index)
        self.assertIn('打包时没带', summary)

    def test_standard_on_standard_run_keeps_it(self):
        from src.recorder import feedback
        plan, info, summary, media, index = self.pack(m.QUALITY_STANDARD, feedback.FRAMES_STANDARD)
        self.assertFalse(plan.can_standard)
        self.assertEqual(info['frames_mode'], feedback.FRAMES_KEEP)
        self.assertIsNotNone(media)

    def test_old_pack_summary_still_reads(self):
        from src.recorder import feedback
        line = feedback.frames_line({'frames': 5, 'run': '20261124_105258_x.jsonl'})
        self.assertIn('5 帧(960x540)', line)


class FrameViewZoomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def view(self, image_w=1280, image_h=720):
        from PySide6.QtGui import QImage
        from src.recorder.RecorderTab import FrameView
        view = FrameView()
        view.resize(640, 360)
        image = QImage(image_w, image_h, QImage.Format_RGB888)
        image.fill(0)
        view.set_frame(image, 2560)
        self.addCleanup(view.deleteLater)
        return view

    def test_fit_by_default(self):
        view = self.view()
        self.assertEqual(view.zoom, 1.0)
        rect = view.image_rect()
        self.assertEqual((rect.x(), rect.y(), rect.width(), rect.height()), (0, 0, 640, 360))
        self.assertIsNone(view.minimap_rect())

    def test_zoom_around_anchor_keeps_point_still(self):
        from PySide6.QtCore import QPointF
        view = self.view()
        anchor = QPointF(480, 270)
        before = view.image_rect()
        u = (anchor.x() - before.x()) / before.width()
        view.set_zoom(2.0, anchor=anchor)
        after = view.image_rect()
        self.assertAlmostEqual(after.x() + u * after.width(), anchor.x(), places=3)
        self.assertEqual(after.width(), 1280)

    def test_center_is_clamped_to_the_image(self):
        view = self.view()
        view.set_zoom(4.0, center=(1.0, 1.0))
        rect = view.image_rect()
        # 右下角对齐控件右下角, 不露出空白
        self.assertAlmostEqual(rect.right(), 640, places=3)
        self.assertAlmostEqual(rect.bottom(), 360, places=3)

    def test_zoom_limits(self):
        view = self.view()
        view.set_zoom(100)
        self.assertEqual(view.zoom, 8.0)
        view.set_zoom(0.1)
        self.assertEqual(view.zoom, 1.0)
        self.assertEqual(view.center, (0.5, 0.5))

    def test_preset_fits_the_region(self):
        view = self.view()
        view.zoom_to((0.70, 0.84, 0.30, 0.16))
        rect = view.image_rect()
        # 技能栏那块(右下角 30%×16%)刚好装下: 宽是限制, 放大 1/0.3 倍, 右边贴住
        self.assertAlmostEqual(view.zoom, 1 / 0.30, places=3)
        self.assertAlmostEqual(rect.right(), 640, places=3)
        self.assertAlmostEqual(rect.bottom(), 360, places=3)

    def test_minimap_shows_when_zoomed_and_reset_hides_it(self):
        view = self.view()
        view.set_zoom(2.0)
        mini = view.minimap_rect()
        self.assertIsNotNone(mini)
        self.assertLessEqual(mini.right(), 640)
        x, y, w, h = view._visible_part()
        self.assertAlmostEqual(w, 0.5, places=3)
        self.assertAlmostEqual(h, 0.5, places=3)
        view.reset_zoom()
        self.assertIsNone(view.minimap_rect())

    def test_zoom_survives_new_frame(self):
        from PySide6.QtGui import QImage
        view = self.view()
        view.set_zoom(3.0, center=(0.2, 0.3))
        image = QImage(1280, 720, QImage.Format_RGB888)
        image.fill(0)
        view.set_frame(image, 2560)
        self.assertEqual(view.zoom, 3.0)
        self.assertNotEqual(view.center, (0.5, 0.5))

    def test_paint_does_not_crash(self):
        view = self.view(960, 540)
        view.set_frame(view.image, 2560, [((100, 100, 200, 80), '#3DB37A', 'skill_e 0.93')])
        for zoom in (1.0, 2.5):
            view.set_zoom(zoom)
            pixmap = view.grab()
            self.assertFalse(pixmap.isNull())


if __name__ == '__main__':
    unittest.main()
