"""Fake-clock transition/input tests; no game, OCR engine, or GUI imports."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'AutoRechallenge.py'


class Stopped(Exception):
    pass


class OutOfCombat(Exception):
    pass


class Dead(OutOfCombat):
    pass


class FakeBase:
    def __init__(self):
        self.now = 0.0
        self.phase = 'retry'
        self.events = []
        self.held = set()
        self.logs = []
        self.info = {}
        self.cancel_at = float('inf')
        self.executor = SimpleNamespace(check_enabled=self.check_enabled)
        self.executor.interaction = SimpleNamespace(send_key_up=self.send_key_up, mouse_up=self.mouse_up)
        self.chars = []
        self.loading_time = 0.8
        self.stuck_button = False
        self.hud_delay = 0.0
        self.result_delay = 0.0
        self.no_start = False
        self.no_combat = False
        self.no_result = False
        self.rounds = 0
        self.reset_count = 0
        self.fail_perform = None
        self.load_ok = True
        self.walked = 0.0
        self.walk_at = 0.0
        self.start_label = '开始挑战'
        self.team_names = ('Jinhsi', 'Sanhua', 'ShoreKeeper')
        self.missing_box = None
        self.reset_members = []

    def check_enabled(self):
        if self.now >= self.cancel_at:
            raise Stopped()

    def next_frame(self):
        self.now += 0.01
        self.check_enabled()
        if self.phase == 'loading' and self.now >= self.load_at:
            self.phase = 'hud'
        if self.phase == 'countdown' and not self.no_combat and self.now >= self.combat_at:
            self.phase = 'combat'
        if self.phase == 'ending' and not self.no_result and self.now >= self.result_at:
            self.phase = 'retry'

    def sleep(self, seconds):
        self.now += seconds
        self.check_enabled()

    def log_info(self, text):
        self.logs.append(text)

    def info_set(self, name, value):
        self.info[name] = value

    def ocr(self, *args, match=None, **kwargs):
        return [SimpleNamespace(name='重新挑战')] if self.phase == 'retry' else []

    def in_team_and_world(self):
        return self.phase in ('hud', 'countdown', 'combat') and self.now >= self.hud_delay

    def click(self, box):
        self.events.append(('retry', self.now))
        if not self.stuck_button:
            self.phase = 'loading'
            self.load_at = self.now + self.loading_time

    def send_key_up(self, key):
        if key == 'w' and key in self.held:
            self.walked += self.now - self.walk_at
        self.held.discard(key)
        self.events.append(('up', key, self.now))

    def send_key_down(self, key):
        self.check_enabled()
        self.held.add(key)
        self.events.append(('down', key, self.now))
        self.walk_at = self.now

    def send_key(self, key):
        self.check_enabled()
        self.events.append(('tap', key, self.now))
        assert 'w' not in self.held, 'F must follow W release'
        self.phase = 'countdown'
        self.combat_at = self.now + 0.4

    def mouse_up(self, key='left'):
        self.held.discard(key)

    def find_f_with_text(self, target_text):
        assert 'w' not in self.held, 'F detection must happen with W released'
        self.events.append(('probe_f', self.now))
        assert target_text.search('开始挑战')
        assert not target_text.search('领取奖励')
        return not self.no_start and self.walked >= 0.59 and bool(target_text.search(self.start_label))

    def in_combat(self):
        return self.phase == 'combat'

    def load_chars(self):
        self.chars = [SimpleNamespace(name=n, reset_state=lambda n=n: self.reset_members.append(n))
                      for n in self.team_names]
        for char in self.chars:
            char.perform = self.perform
        return self.load_ok

    def get_box_by_name(self, name):
        if name == self.missing_box:
            raise ValueError('No box found for category ' + name)
        return SimpleNamespace(name=name)

    def switch_healer(self):
        if self.config.get('Switch to Healer before and after Combat'):
            self.events.append(('switch_healer', self.now))

    def get_current_char(self):
        return next((c for c in self.chars if c is not None), None)

    def perform(self):
        self.held.update(('left', 'e', 'r'))
        if self.fail_perform:
            raise self.fail_perform()
        self.rounds += 1
        self.phase = 'ending'
        self.result_at = self.now + self.result_delay

    def combat_end(self):
        self.events.append(('combat_end', self.now))
        for char in self.chars:
            if char is not None:
                char.reset_state()
        if self.chars:
            self.reset_count += 1

    def do_reset_to_false(self):
        self.events.append(('reset_combat', self.now))

    def sleep_check(self):
        self.events.append(('battle_sleep_check', self.now))


class FakeOneTime:
    def run(self):
        pass


def make_task():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    slot = {}
    env = {'re': re, 'monotonic': lambda: slot['task'].now,
           'BaseCombatTask': FakeBase, 'WWOneTimeTask': FakeOneTime,
           'CharDeadException': Dead, 'NotInCombatException': OutOfCombat}
    exec(compile(tree, str(SOURCE), 'exec'), env)
    task = env['AutoRechallenge']()
    slot['task'] = task
    task.config = dict(task.default_config)
    return task


class RechallengeTests(unittest.TestCase):
    def test_two_rounds_reset_and_stop_before_next_retry(self):
        t = make_task()
        t.config['挑战次数（0为持续）'] = 2
        t.run()
        self.assertEqual(t.info['已完成挑战次数'], 2)
        self.assertGreaterEqual(t.reset_count, 2)
        self.assertEqual(set(t.reset_members), set(t.team_names))
        self.assertEqual(len([e for e in t.events if e[0] == 'retry']), 2)
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 2)
        self.assertEqual(t.phase, 'retry')
        self.assertFalse(t.held)
        for event in t.events:
            if event[:2] == ('down', 'w'):
                self.assertGreaterEqual(event[2], t.loading_time)

    def test_entry_hud_and_entry_combat(self):
        for phase in ('hud', 'combat'):
            with self.subTest(phase=phase):
                t = make_task()
                t.phase = phase
                t.config['挑战次数（0为持续）'] = 1
                t.run()
                self.assertEqual(t.rounds, 1)
                self.assertFalse(any(e[0] == 'retry' for e in t.events))
                if phase == 'combat':
                    self.assertFalse(any(e[0] == 'tap' for e in t.events))
                self.assertFalse(t.held)

    def test_stuck_retry_has_bounded_attempts_and_never_walks(self):
        t = make_task()
        t.stuck_button = True
        with self.assertRaisesRegex(RuntimeError, '未消失'):
            t.run()
        self.assertEqual(len([e for e in t.events if e[0] == 'retry']), t.RETRY_CLICK_TRIES)
        self.assertFalse(any(e[0] in ('down', 'tap') for e in t.events))

    def test_swallowed_first_click_retries_only_fresh_button(self):
        t = make_task()
        original_click = t.click
        latest = [None]
        def fresh_ocr(*args, **kwargs):
            box = SimpleNamespace(name='重新挑战', observed=t.now)
            latest[0] = box
            return [box] if t.phase == 'retry' else []
        def swallow_first(box):
            self.assertIs(box, latest[0])
            if not any(e[0] == 'retry' for e in t.events):
                t.events.append(('retry', t.now))
            else:
                original_click(box)
        t.ocr, t.click = fresh_ocr, swallow_first
        t._restart(SimpleNamespace(name='stale'))
        clicks = [e for e in t.events if e[0] == 'retry']
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[0][1], t.RETRY_BUTTON_SETTLE)
        self.assertGreaterEqual(clicks[1][1] - clicks[0][1], t.RETRY_CLICK_GAP)
        self.assertEqual(t.phase, 'hud')
        self.assertFalse(t.held)

    def test_early_result_label_accepts_click_only_after_animation(self):
        t = make_task()
        original_click = t.click
        def late_click(box):
            if t.now < 2.0:
                t.events.append(('retry', t.now))
            else:
                original_click(box)
        t.click = late_click
        t._restart(SimpleNamespace(name='重新挑战'))
        self.assertEqual(len([e for e in t.events if e[0] == 'retry']), 2)
        self.assertEqual(t.phase, 'hud')

    def test_loading_departure_latches_no_more_clicks_on_ocr_reappearance(self):
        t = make_task()
        t.loading_time = 3.0
        original_ocr = t.ocr
        def flickering_ocr(*args, **kwargs):
            if t.phase == 'loading' and t.now >= t.load_at - 1.0:
                return [SimpleNamespace(name='重新挑战')]
            return original_ocr(*args, **kwargs)
        t.ocr = flickering_ocr
        t._restart(SimpleNamespace(name='重新挑战'))
        self.assertEqual(len([e for e in t.events if e[0] == 'retry']), 1)
        self.assertEqual(t.phase, 'hud')

    def test_hud_delay_and_fast_loading(self):
        for delay in (0.0, 2.5):
            t = make_task()
            t.loading_time = 0
            t.hud_delay = delay
            t.config['挑战次数（0为持续）'] = 1
            t.run()
            down = next(e for e in t.events if e[:2] == ('down', 'w'))
            self.assertGreaterEqual(down[2], delay)
            self.assertFalse(t.held)

    def test_loading_timeout_never_walks(self):
        t = make_task()
        t.loading_time = 100
        t.config['加载超时（秒）'] = 2
        with self.assertRaisesRegex(RuntimeError, '等待HUD超时'):
            t.run()
        self.assertFalse(any(e[0] == 'down' for e in t.events))
        self.assertFalse(t.held)

    def test_no_f_times_out_and_releases_w(self):
        t = make_task()
        t.no_start = True
        t.config['前进超时（秒）'] = 1
        with self.assertRaisesRegex(RuntimeError, '前进超时'):
            t.run()
        self.assertFalse(any(e[0] == 'tap' for e in t.events))
        self.assertFalse(t.held)

    def test_hud_loss_while_walking_releases_w(self):
        t = make_task()
        t.phase = 'hud'
        t.load_chars()
        original = t.in_team_and_world
        t.in_team_and_world = lambda: original() and t.walked < 0.19
        with self.assertRaisesRegex(RuntimeError, 'HUD消失'):
            t._walk_and_start()
        self.assertFalse(t.held)

    def test_no_combat_does_not_spam_f(self):
        t = make_task()
        t.no_combat = True
        t.config['开战超时（秒）'] = 1
        with self.assertRaisesRegex(RuntimeError, '未确认开战'):
            t.run()
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 1)
        self.assertFalse(t.held)

    def test_no_result_stops_without_walking_again(self):
        t = make_task()
        t.no_result = True
        t.config['结算超时（秒）'] = 1
        with self.assertRaisesRegex(RuntimeError, '重新挑战.*超时'):
            t.run()
        self.assertEqual(t.rounds, 1)
        self.assertFalse(t.held)

    def test_stop_during_loading_walking_and_combat(self):
        for when in (0.6, 1.8, 2.9):
            t = make_task()
            t.no_start = True
            t.cancel_at = when
            with self.assertRaises(Stopped):
                t.run()
            self.assertFalse(t.held)
        t = make_task()
        t.fail_perform = Stopped
        with self.assertRaises(Stopped):
            t.run()
        self.assertFalse(t.held)
        self.assertEqual(t.rounds, 0)

    def test_axis_failure_stops_and_releases(self):
        t = make_task()
        t.fail_perform = ValueError
        with self.assertRaises(ValueError):
            t.run()
        self.assertFalse(t.held)
        self.assertEqual(t.rounds, 0)

    def test_invalid_team_stops_before_input(self):
        t = make_task()
        t.phase = 'hud'
        t.load_ok = False
        with self.assertRaisesRegex(RuntimeError, '无法读取队伍'):
            t.run()
        self.assertFalse(any(e[0] in ('down', 'tap') for e in t.events))

    def test_button_text_and_config_validation(self):
        t = make_task()
        for text in ('重新挑战', '重新挑戰', 'Retry', 'Challenge Again'):
            self.assertIsNotNone(t.RETRY_TEXT.fullmatch(text))
        for text in ('退出挑战', '领取奖励', '是否重新挑战？', 'Retry purchase'):
            self.assertIsNone(t.RETRY_TEXT.fullmatch(text))
        for value in (-1, True, 1.2, '2'):
            self.assertIsNotNone(t.validate_config('挑战次数（0为持续）', value))
        self.assertIsNone(t.validate_config('挑战次数（0为持续）', 0))
        self.assertIsNone(t.validate_config('Exit After Task', False))

    def test_single_frame_retry_is_rejected(self):
        t = make_task()
        t.phase = 'hud'
        t.config['结算超时（秒）'] = 1
        original = t.ocr
        t.ocr = lambda *a, **kw: [SimpleNamespace(name='重新挑战')] if t.now < 0.1 else []
        with self.assertRaises(RuntimeError):
            t._wait_retry(1)
        self.assertFalse(t.events)

    def test_old_hud_behind_retry_cannot_release_loading_gate(self):
        t = make_task()
        t.stuck_button = True
        t.in_team_and_world = lambda: True
        with self.assertRaisesRegex(RuntimeError, '未消失'):
            t.run()
        self.assertFalse(any(e[0] == 'down' for e in t.events))

    def test_delayed_result_and_character_death(self):
        for death in (False, True):
            t = make_task()
            t.result_delay = 1.2
            t.config['挑战次数（0为持续）'] = 1
            original = t.perform
            if death:
                def perform():
                    original()
                    raise Dead()
                t.perform = perform
            t.run()
            self.assertEqual(t.info['已完成挑战次数'], 1)
            self.assertFalse(t.held)

    def test_combat_timeout_and_configured_key_cleanup(self):
        t = make_task()
        t.phase = 'combat'
        t.config['单场超时（秒）'] = 1
        t.key_config = {'Resonance Key': 'x', 'Liberation Key': 'c'}
        def stalled():
            t.held.update(('x', 'c', 'w', 'left', 'right'))
            t.now += 0.4
        t.perform = stalled
        with self.assertRaisesRegex(RuntimeError, '单场超时'):
            t.run()
        self.assertFalse(t.held)

    def test_sleep_check_only_while_fighting(self):
        t = make_task()
        t.sleep_check()
        self.assertFalse(t.events)
        t._testing_combat = True
        t.sleep_check()
        self.assertEqual(t.events[0][0], 'battle_sleep_check')

    def test_disabled_task_wrapper_cannot_block_raw_release(self):
        t = make_task()
        t.held.update(('w', 'e', 'r', 'left', 'right'))
        def stopped(*args, **kwargs):
            raise Stopped()
        t.send_key_up = stopped
        t.mouse_up = stopped
        t._release()
        self.assertFalse(t.held)

    def test_each_step_releases_before_probe_and_f_stops_steps(self):
        t = make_task()
        t.phase = 'hud'
        t._walk_and_start()
        steps = [e for e in t.events if e[:2] == ('down', 'w')]
        self.assertEqual(len(steps), 3)
        for down in steps:
            i = t.events.index(down)
            up = t.events[i + 1]
            self.assertEqual(up[:2], ('up', 'w'))
            self.assertAlmostEqual(up[2] - down[2], 0.20)
            self.assertEqual(t.events[i + 2][0], 'probe_f')
            self.assertGreaterEqual(t.events[i + 2][1] - up[2], 1.00)
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 1)
        self.assertFalse(t.held)

    def test_slow_detection_never_extends_w_hold(self):
        t = make_task()
        t.phase = 'hud'
        original = t.find_f_with_text
        def slow_probe(target_text):
            self.assertNotIn('w', t.held)
            t.now += 0.8
            return original(target_text)
        t.find_f_with_text = slow_probe
        t._walk_and_start()
        for i, event in enumerate(t.events):
            if event[:2] == ('down', 'w'):
                self.assertAlmostEqual(t.events[i + 1][2] - event[2], 0.20)
        self.assertFalse(t.held)

    def test_existing_f_sends_no_w(self):
        t = make_task()
        t.phase = 'hud'
        t.walked = 1
        t._walk_and_start()
        self.assertFalse(any(e[:2] == ('down', 'w') for e in t.events))
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 1)

    def test_stop_mid_step_releases_w(self):
        t = make_task()
        t.phase = 'hud'
        t.cancel_at = 0.1
        with self.assertRaises(Stopped):
            t._walk_and_start()
        self.assertFalse(t.held)

    def test_f_label_containing_start_immediately_ends_walking(self):
        for label in ('开始', '开始战斗', '开始 挑战', '准备开始挑战', '開始挑戰',
                      '开启挑战', '开启', '開啟挑戰', 'Start Battle'):
            with self.subTest(label=label):
                t = make_task()
                t.phase = 'hud'
                t.walked = 1
                t.start_label = label
                t._walk_and_start()
                self.assertFalse(any(e[:2] == ('down', 'w') for e in t.events))
                self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 1)

    def test_enable_challenge_after_a_step_sends_f_before_another_step(self):
        t = make_task()
        t.phase = 'hud'
        t.walked = 0.40
        t.start_label = '开启挑战'
        t._walk_and_start()
        self.assertEqual(len([e for e in t.events if e[:2] == ('down', 'w')]), 1)
        self.assertEqual(len([e for e in t.events if e[:2] == ('tap', 'f')]), 1)
        self.assertFalse(t.held)
        self.assertTrue(any('start F sent; probe=' in line for line in t.logs))

    def test_f_without_start_does_not_trigger(self):
        t = make_task()
        t.phase = 'hud'
        t.start_label = '领取奖励'
        t.config['前进超时（秒）'] = 3
        with self.assertRaisesRegex(RuntimeError, '前进超时'):
            t._walk_and_start()
        self.assertFalse(any(e[:2] == ('tap', 'f') for e in t.events))
        self.assertFalse(t.held)

    def test_stop_during_one_second_settle_sends_no_extra_input(self):
        t = make_task()
        t.phase = 'hud'
        t.cancel_at = 0.7
        with self.assertRaises(Stopped):
            t._walk_and_start()
        self.assertEqual(len([e for e in t.events if e[:2] == ('down', 'w')]), 1)
        self.assertFalse(any(e[:2] == ('tap', 'f') for e in t.events))
        self.assertFalse(t.held)
        self.assertFalse(any(e[:2] == ('tap', 'f') for e in t.events))
        # Cancellation during W must preserve Stopped and still release W.
        t.phase = 'hud'
        t.cancel_at = 0.4
        with self.assertRaises(Stopped):
            t._walk_and_start()
        self.assertFalse(t.held)


class UniversalTests(unittest.TestCase):
    def test_arbitrary_teams_and_two_character_team(self):
        for names in [('Jinhsi', 'Sanhua', 'ShoreKeeper'), ('Rover', 'Baizhi'),
                      ('Chisa', 'Hiyuki', 'Lucilla')]:
            with self.subTest(names=names):
                t = make_task()
                t.team_names = names
                t.config['挑战次数（0为持续）'] = 2
                t.run()
                self.assertEqual(t.info['已完成挑战次数'], 2)
                self.assertEqual(set(t.reset_members), set(names))
                self.assertFalse(t.held)

    def test_no_driver_methods_required(self):
        t = make_task()
        t.load_chars()
        for char in t.chars:
            char.drive_team_axis = None
            def forbidden():
                raise AssertionError('team-specific driver must never be called')
            char.reset_team_axis = forbidden
        t.load_chars = lambda: True
        t._check_team()
        self.assertEqual(t.reset_count, 1)
        self.assertEqual(len(t.reset_members), len(t.chars))

    def test_missing_character_and_current_character_stop(self):
        for mode in ('empty', 'partial', 'no_current'):
            t = make_task()
            t.load_chars()
            t.load_chars = lambda: True
            if mode == 'empty':
                t.chars = []
            elif mode == 'partial':
                t.chars[0] = None
            else:
                t.get_current_char = lambda: None
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                t._check_team()
            self.assertFalse(any(e[0] in ('down', 'tap') for e in t.events))

    def test_all_common_and_configured_combat_inputs_released(self):
        t = make_task()
        t.key_config = {'Echo Key': 'z', 'Jump Key': 'j', 'Dodge Key': 'x',
                        'Tool Key': 'v', 'Wheel Key': 'c', 'Resonance Key': 'g',
                        'Liberation Key': 'h'}
        t.held.update(('w', 'a', 's', 'd', 'space', 'lshift', 'q', 'e', 'r',
                       't', 'f', 'tab', '1', '2', '3', 'left', 'right', 'middle',
                       'z', 'j', 'x', 'v', 'c', 'g', 'h'))
        t._release()
        self.assertFalse(t.held)

    def test_liberation_option_is_forwarded(self):
        for enabled in (True, False):
            t = make_task()
            t.config['Use Liberation'] = enabled
            t.config['挑战次数（0为持续）'] = 1
            t.run()
            self.assertIs(t.use_liberation, enabled)

    def test_healer_setting_default_off_and_no_switch_on_results(self):
        for enabled in (True, False):
            t = make_task()
            t.config['Switch to Healer before and after Combat'] = enabled
            t.config['挑战次数（0为持续）'] = 1
            t.run()
            self.assertEqual(len([e for e in t.events if e[0] == 'switch_healer']),
                             1 if enabled else 0)

    def test_missing_builtin_templates_stop_before_retry_or_walk(self):
        for name in ('has_target', 'char_1_text', 'box_char_1'):
            t = make_task()
            t.missing_box = name
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, name):
                t.run()
            self.assertFalse(any(e[0] in ('retry', 'down', 'tap') for e in t.events))
            self.assertFalse(t.held)

    def test_stop_during_other_character_action_releases_all_keys(self):
        t = make_task()
        def stop_action():
            t.held.update(('a', 'space', 'q', 'tab', 'middle'))
            raise Stopped()
        t.perform = stop_action
        with self.assertRaises(Stopped):
            t.run()
        self.assertFalse(t.held)


if __name__ == '__main__':
    unittest.main(verbosity=2)
