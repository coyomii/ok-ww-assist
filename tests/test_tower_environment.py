"""Tower popup/retry-label checks with fake frames and input; no game actions."""
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from test_rechallenge import make_task, Stopped


def popup_frame():
    f = np.zeros((720, 1280, 3), dtype=np.uint8)
    f[210:240, 95:200] = 255
    f[270:295, 50:100] = (50, 180, 230)
    return f


def labels(title='环境特性', effect='效果', body='敌方全属性抗性提升15%'):
    return [SimpleNamespace(name=title, x=100, y=215),
            SimpleNamespace(name=effect, x=50, y=280),
            SimpleNamespace(name=body, x=50, y=330)]


def task_with_panel(stuck=False):
    t = make_task()
    t.phase = 'hud'
    t.frame = popup_frame()
    t.ocr = lambda *a, **k: labels()
    old_up = t.send_key_up
    def release(key):
        was_held = key in t.held
        old_up(key)
        if key == 'esc' and was_held and not stuck:
            t.frame = np.zeros_like(t.frame)
    t.executor.interaction.send_key_up = release
    return t


class TowerEnvironmentTests(unittest.TestCase):
    def test_prefilter_and_recognition(self):
        t = task_with_panel()
        self.assertTrue(t._environment_panel())
        for title in ('环境特性', '環境特性', '环 境 特 性'):
            t.ocr = lambda *a, title=title, **k: labels(title=title)
            self.assertTrue(t._environment_panel())

    def test_unrelated_text_or_wrong_layout_does_not_close(self):
        t = task_with_panel()
        for args in ({'title': '挑战成功'}, {'effect': '奖励'}, {'body': '请选择复苏物品'}):
            t.ocr = lambda *a, args=args, **k: labels(**args)
            self.assertFalse(t._dismiss_environment())
        t.ocr = lambda *a, **k: [SimpleNamespace(name=b.name, x=900, y=b.y) for b in labels()]
        self.assertFalse(t._dismiss_environment())
        self.assertFalse(any(e[:2] == ('down', 'esc') for e in t.events))

    def test_normal_frame_skips_extra_ocr(self):
        t = make_task()
        t.frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        t.ocr = lambda *a, **k: self.fail('no popup: no additional OCR')
        self.assertFalse(t._environment_panel())
        t.frame = np.zeros((720, 1000, 3), dtype=np.uint8)
        self.assertFalse(t._environment_panel())

    def test_scaled_popup_prefilter_at_three_resolutions(self):
        f = popup_frame()
        t = make_task()
        for size in ((1280, 720), (1920, 1080), (2560, 1440)):
            self.assertTrue(t._environment_hint(cv2.resize(f, size)))

    def test_close_once_then_continue_walk_and_f(self):
        t = task_with_panel()
        t._walk_and_start()
        downs = [e for e in t.events if e[:2] == ('down', 'esc')]
        self.assertEqual(len(downs), 1)
        self.assertTrue(any(e[:2] == ('tap', 'f') for e in t.events))
        first_walk = next(e[2] for e in t.events if e[:2] == ('down', 'w'))
        esc_release = next(e[2] for e in t.events if e[:2] == ('up', 'esc') and e[2] > downs[0][2])
        self.assertGreater(first_walk, esc_release)
        self.assertFalse(t.held)

    def test_persistent_popup_stops_after_one_esc_without_w_or_f(self):
        t = task_with_panel(stuck=True)
        with self.assertRaisesRegex(RuntimeError, '不连续按Esc'):
            t._walk_and_start()
        self.assertEqual(len([e for e in t.events if e[:2] == ('down', 'esc')]), 1)
        self.assertFalse(any(e[:2] in (('down', 'w'), ('tap', 'f')) for e in t.events))
        self.assertFalse(t.held)

    def test_one_frame_popup_does_not_send_esc(self):
        t = task_with_panel()
        old_frame = t.next_frame
        def next_frame():
            old_frame()
            t.frame = np.zeros_like(t.frame)
        t.next_frame = next_frame
        self.assertFalse(t._dismiss_environment())
        self.assertFalse(any(e[:2] == ('down', 'esc') for e in t.events))

    def test_stop_while_esc_held_releases_key(self):
        t = task_with_panel()
        t.cancel_at = .18
        with self.assertRaises(Stopped):
            t._walk_and_start()
        self.assertEqual(len([e for e in t.events if e[:2] == ('down', 'esc')]), 1)
        self.assertFalse(t.held)

    def test_combat_fresh_does_not_send_esc(self):
        t = task_with_panel()
        t._testing_combat = True
        t._fresh()
        self.assertFalse(any(e[:2] == ('down', 'esc') for e in t.events))

    def test_popup_after_retry_loading_closes_before_f(self):
        t = task_with_panel()
        t.frame[:] = 0
        t.phase = 'retry'
        old_frame = t.next_frame
        def next_frame():
            phase_before = t.phase
            old_frame()
            if phase_before == 'loading' and t.phase == 'hud':
                t.frame = popup_frame()
        t.next_frame = next_frame
        def ocr(*a, match=None, **k):
            if match is not None:
                return [SimpleNamespace(name='再次挑战')] if t.phase == 'retry' else []
            return labels()
        t.ocr = ocr
        t.config['挑战次数（0为持续）'] = 2
        t.run()
        self.assertEqual(t.info['已完成挑战次数'], 2)
        self.assertEqual(len([e for e in t.events if e[0] == 'retry']), 2)
        self.assertEqual(len([e for e in t.events if e[:2] == ('down', 'esc')]), 2)
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 2)
        self.assertFalse(t.held)

    def test_both_retry_labels_use_actual_restart_flow(self):
        for label in ('重新挑战', '再次挑战', '重新挑戰', '再次挑戰', 'Retry', 'Challenge Again'):
            t = make_task()
            def ocr(*a, match=None, **k):
                return [SimpleNamespace(name=label)] if t.phase == 'retry' and match.search(label) else []
            t.ocr = ocr
            t.config['挑战次数（0为持续）'] = 1
            t.run()
            self.assertEqual(t.info['已完成挑战次数'], 1)
            self.assertEqual(len([e for e in t.events if e[0] == 'retry']), 1)
        for unrelated in ('退出挑战', '重新挑战说明', '再次挑战奖励', '领取奖励'):
            self.assertIsNone(t.RETRY_TEXT.fullmatch(unrelated))
