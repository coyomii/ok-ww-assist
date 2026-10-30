"""Post-success tower flow with synthetic frames, OCR and input only."""
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

from test_rechallenge import make_task, Stopped


def menu_frame(phase):
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    if phase == 'success':
        frame[190:230, 570:710] = (185, 220, 255)
        frame[600:630, 540:740] = 245
    elif phase == 'record':
        frame[110:575, 220:1060] = 245
    elif phase in {'floor', 'party'}:
        frame[640:678, 980:1160] = 245
    return frame


def result_task(with_record=True, stuck=None):
    task = make_task()
    task.phase = 'success'
    task.frame = menu_frame(task.phase)
    task.held.update(('w', 'left', 'e'))
    task.success_title = '挑战成功'
    task.success_button = '确认成绩'
    task.success_title_x = .445
    task.success_button_x = .47
    task.record_prompt = '是否覆盖深境之塔的挑战成绩'
    task.record_title = '确认记录'
    task.record_old = '原记录'
    task.record_new = '新记录'
    task.config['挑战次数（0为持续）'] = 2
    task.ocr_calls = []
    task.last_boxes = {}
    original_frame = task.next_frame
    original_ocr = task.ocr
    original_click = task.click

    def next_frame():
        original_frame()
        task.frame = menu_frame(task.phase)

    def ocr(x, y, right, bottom, match=None, **kwargs):
        task.ocr_calls.append(task.now)
        if match is not None:
            return original_ocr(match=match)
        labels = {
            'success': [(task.success_title, task.success_title_x, .28),
                        (task.success_button, task.success_button_x, .84)],
            'record': [(task.record_title, .186, .184), (task.record_prompt, .35, .285),
                       (task.record_old, .22, .38), (task.record_new, .52, .38),
                       ('取消', .28, .715), ('确认', .68, .715)],
            'floor': [('深境区·深境之塔', .055, .055), ('挑战目标', .415, .49),
                      ('挑战开始', .80, .90)],
            'party': [('深境之塔·第3层', .055, .055), ('角色列表', .105, .125),
                      ('开启挑战', .77, .90)],
        }.get(task.phase, [])
        h, w = task.frame.shape[:2]
        boxes = [SimpleNamespace(name=text, x=px*w, y=py*h)
                 for text, px, py in labels if x <= px <= right and y <= py <= bottom]
        task.last_boxes.update({b.name: b for b in boxes})
        return boxes

    def click(button):
        task.check_enabled()
        if task.held:
            raise AssertionError('Battle inputs must be released before a menu click')
        if button.name == '重新挑战':
            return original_click(button)
        if button is not task.last_boxes[button.name]:
            raise AssertionError('Must click a fresh OCR button')
        task.events.append((button.name, task.now))
        if task.phase == stuck:
            return
        if task.phase == 'success':
            task.phase = 'record' if with_record else 'floor'
        elif task.phase == 'record' and button.name == '取消':
            task.phase = 'floor'
        elif task.phase == 'floor':
            task.phase = 'party'
        elif task.phase == 'party':
            task.phase = 'loading'
            task.load_at = task.now + .3
        else:
            raise AssertionError('Unexpected menu click')

    task.next_frame = next_frame
    task.ocr = ocr
    task.click = click
    return task


def menu_clicks(task):
    return [e[0] for e in task.events if e[0] in {'确认成绩', '取消', '确认', '挑战开始', '开启挑战'}]


class SuccessTests(unittest.TestCase):
    def test_starting_from_record_floor_or_party_menu_resumes_existing_selection(self):
        for phase, expected in (
                ('record', ['取消', '挑战开始', '开启挑战']),
                ('floor', ['挑战开始', '开启挑战']),
                ('party', ['开启挑战'])):
            with self.subTest(phase=phase):
                task = result_task()
                task.phase = phase
                task.config['挑战次数（0为持续）'] = 1
                task.run()
                self.assertEqual(menu_clicks(task), expected)
                self.assertEqual(task.info['已完成挑战次数'], 1)
                self.assertFalse(task.held)

    def test_success_cancels_record_then_reenters_and_resumes_f_combat(self):
        task = result_task()
        task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩', '取消', '挑战开始', '开启挑战'])
        self.assertEqual(task.info['已完成挑战次数'], 2)
        self.assertEqual(task.info['挑战成功次数'], 1)
        self.assertEqual(task.rounds, 1)
        self.assertEqual(len([e for e in task.events if e[:2] == ('tap', 'f')]), 1)
        self.assertFalse(task.held)

    def test_optional_record_dialog_can_be_absent(self):
        task = result_task(with_record=False)
        task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩', '挑战开始', '开启挑战'])
        self.assertEqual(task.info['已完成挑战次数'], 2)
        self.assertFalse(task.held)

    def test_success_after_combat_unwinds_and_resumes_next_attempt(self):
        task = result_task()
        task.phase = 'combat'
        original = task.perform
        wins = []

        def perform():
            if not wins:
                wins.append(1)
                task.phase = 'success'
            else:
                original()

        task.perform = perform
        task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩', '取消', '挑战开始', '开启挑战'])
        self.assertEqual(task.info['已完成挑战次数'], 2)
        self.assertEqual(task.info['挑战成功次数'], 1)
        self.assertFalse(task._testing_combat)
        self.assertFalse(task.held)

    def test_count_limit_cancels_record_but_does_not_open_next_challenge(self):
        for record in (True, False):
            task = result_task(with_record=record)
            task.config['挑战次数（0为持续）'] = 1
            task.run()
            self.assertEqual(menu_clicks(task), ['确认成绩', '取消'] if record else ['确认成绩'])
            self.assertEqual(task.info['已完成挑战次数'], 1)
            self.assertFalse(any(e[0] in {'retry', 'down', 'tap'} for e in task.events))
            self.assertFalse(task.held)

    def test_consecutive_successes_each_count_once_and_obey_limit(self):
        task = result_task()

        def win():
            task.phase = 'success'

        task.perform = win
        task.run()
        self.assertEqual(task.info['已完成挑战次数'], 2)
        self.assertEqual(task.info['挑战成功次数'], 2)
        self.assertEqual(menu_clicks(task), ['确认成绩', '取消', '挑战开始', '开启挑战', '确认成绩', '取消'])
        self.assertFalse(task.held)

    def test_unlimited_count_continues_after_success_until_stopped(self):
        task = result_task()
        task.config['挑战次数（0为持续）'] = 0
        task.cancel_at = 15
        with self.assertRaises(Stopped):
            task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩', '取消', '挑战开始', '开启挑战'])
        self.assertGreaterEqual(task.info['已完成挑战次数'], 2)
        self.assertFalse(task.held)

    def test_result_appearing_while_waiting_for_retry_unwinds_combat(self):
        task = result_task()
        with self.assertRaises(task.ChallengeSucceeded):
            task._wait_retry(2)
        self.assertEqual(menu_clicks(task), ['确认成绩'])

    def test_wrong_success_title_button_or_location_does_not_click(self):
        for field, value in [('success_title', '挑战失败'), ('success_button', '确认'),
                             ('success_button', '确认购买'), ('success_title_x', .1),
                             ('success_button_x', .1)]:
            with self.subTest(field=field, value=value):
                task = result_task()
                setattr(task, field, value)
                task._fresh()
                self.assertFalse(menu_clicks(task))

    def test_record_prompt_must_be_score_overwrite_and_never_confirms(self):
        task = result_task()
        task.record_prompt = '是否购买物品'
        task.config['加载超时（秒）'] = 1
        with self.assertRaisesRegex(RuntimeError, '等待菜单或加载超时'):
            task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩'])
        self.assertFalse(task.held)

    def test_record_ocr_spelling_and_traditional_variants(self):
        for title, old, new, prompt in (
                ('确认纪录', '原纪录', '新纪录', '是否覆盖深境之塔的挑战成绩'),
                ('確認紀錄', '原紀錄', '新紀錄', '是否覆蓋深境之塔的挑戰成績')):
            task = result_task()
            task.phase = 'record'
            task.frame = menu_frame('record')
            task.record_title, task.record_old, task.record_new = title, old, new
            task.record_prompt = prompt
            self.assertEqual(task._tower_action()[0], 'cancel_record')

    def test_single_frame_result_does_not_click(self):
        task = result_task()
        original = task.next_frame
        calls = []

        def next_frame():
            original()
            calls.append(1)
            if len(calls) >= 2:
                task.frame[:] = 0

        task.next_frame = next_frame
        task._fresh()
        self.assertFalse(menu_clicks(task))

    def test_stuck_result_stops_without_repeat_even_when_ocr_drops_out(self):
        task = result_task(stuck='success')
        original_ocr = task.ocr
        task.ocr = lambda *a, **k: [] if task.now > .2 else original_ocr(*a, **k)
        with self.assertRaisesRegex(RuntimeError, '不重复点击或重开'):
            task.run()
        self.assertEqual(menu_clicks(task), ['确认成绩'])
        self.assertFalse(task.held)

    def test_each_menu_clicks_once_and_stops_if_transition_does_not_happen(self):
        for phase in ('record', 'floor', 'party'):
            task = result_task(stuck=phase)
            with self.assertRaisesRegex(RuntimeError, '不重复点击'):
                task.run()
            clicks = menu_clicks(task)
            self.assertEqual(len(clicks), len(set(clicks)))
            self.assertNotIn('确认', clicks)
            self.assertFalse(task.held)

    def test_stop_during_confirmation_releases_input_without_clicking(self):
        task = result_task()
        task.cancel_at = .1
        with self.assertRaises(Stopped):
            task.run()
        self.assertFalse(menu_clicks(task))
        self.assertFalse(task.held)

    def test_scaled_and_traditional_success_and_normal_frame_prefilter(self):
        for size in ((1280, 720), (1920, 1080), (2560, 1440)):
            task = result_task()
            task.success_title = '挑 戰 成 功'
            task.success_button = '確認成績'
            task.frame = cv2.resize(task.frame, size)
            self.assertIsNotNone(task._success_box())
        task = result_task()
        task.frame[:] = 0
        task.ocr = lambda *a, **k: self.fail('No result hint: skip OCR')
        self.assertIsNone(task._success_box())
        self.assertIsNone(task._tower_action())
        task.frame = np.full((720, 1000, 3), 245, dtype=np.uint8)
        self.assertIsNone(task._success_box())
        self.assertIsNone(task._tower_action())
