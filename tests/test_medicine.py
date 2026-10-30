"""State-machine tests with fake OCR, frames, input and time; never touch a game."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('medicine_under_test', ROOT / 'ArMedicine.py')
medicine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(medicine)


class Stopped(Exception):
    pass


def box(text, x, y, width=.04, height=.035):
    return SimpleNamespace(name=text, x=int(x*1600), y=int(y*900),
                           width=int(width*1600), height=int(height*900))


def panel_frame(enabled=True):
    frame = np.zeros((900, 1600, 3), dtype=np.uint8)
    frame[150:730, 270:1330] = 240
    frame[625:685, 960:1260] = 30 if enabled else 140
    return frame


class FakeTask:
    def __init__(self):
        self.now = 0.0
        self.config = {'自动使用复活药': True, '复活药优先级': '绿→蓝→紫',
                       '复活药等待上限（秒）': 75}
        self.info = {}
        self.events = []
        self.logs = []
        self.skip_combat_check = False
        self.in_sleep_check = False
        self.key_config = {'Resonance Key': 'g'}
        self.held = {'left', 'right', 'g', 'w'}
        self.stock = [9, 9, 14]
        self.enabled_at = 0
        self.stop_at = float('inf')
        self.close_at = float('inf')
        self.disappear_at = float('inf')
        self.no_close = False
        self.bad_ocr_after_click = False
        self.hud = True
        self.valid_title = self.valid_description = self.valid_button = True
        self.count_unknown = False
        self.executor = SimpleNamespace(frame=panel_frame(), check_enabled=self.check_enabled)
        self.executor._battle_assist_revive_cooldown = medicine.ReviveCooldown({})
        self.executor.interaction = SimpleNamespace(send_key_up=self.key_up, mouse_up=self.mouse_up)

    def key_up(self, key):
        self.held.discard(key)

    def mouse_up(self, key):
        self.held.discard(key)

    def check_enabled(self):
        if self.now >= self.stop_at:
            raise Stopped()

    def sleep(self, duration):
        self.now += duration
        self.check_enabled()

    def next_frame(self):
        self.now += .01
        self.check_enabled()
        gone = self.now >= min(self.close_at, self.disappear_at)
        self.executor.frame = np.zeros((900, 1600, 3), dtype=np.uint8) if gone else panel_frame(self.now >= self.enabled_at)

    def ocr(self, x, y, r, b, **kwargs):
        if r-x < .10:
            if self.count_unknown:
                return []
            index = min(range(3), key=lambda i: abs(x - medicine.ReviveMedicine.SLOTS[i] - .018))
            count = self.stock[index]
            return [] if count is None else [box(str(count), x, y)]
        if self.bad_ocr_after_click and any(e[0] == 'confirm' for e in self.events):
            return []
        return [box('选择复苏物品' if self.valid_title else '选择物品', .185, .19),
                box('确认' if self.valid_button else '取消', .68, .71),
                box('可使队伍中指定共鸣者即时恢复意识' if self.valid_description else '购买商品', .24, .55)]

    def click(self, target, y=None, **kwargs):
        self.check_enabled()
        assert self.skip_combat_check and self.in_sleep_check
        if y is None:
            self.events.append(('confirm', self.now))
            if not self.no_close:
                self.close_at = self.now + .30
        else:
            self.events.append(('select', target, self.now))

    def in_team_and_world(self):
        return self.hud and self.now >= self.close_at

    def info_set(self, key, value):
        self.info[key] = value

    def log_info(self, text):
        self.logs.append(text)


class MedicineTests(unittest.TestCase):
    def process(self, task):
        with patch.object(medicine, 'monotonic', lambda: task.now), \
                patch.object(medicine, 'wall_time', lambda: 1800000000 + task.now):
            return medicine.ReviveMedicine(task).process(force=True)

    def test_bright_button_cannot_bypass_sixty_second_limit(self):
        t = FakeTask()
        self.process(t)
        first = next(e[1] for e in t.events if e[0] == 'confirm')
        t.close_at = float('inf')
        t.executor.frame = panel_frame(True)
        t.now += 10
        self.process(t)  # New helper instance, same task/executor.
        clicks = [e[1] for e in t.events if e[0] == 'confirm']
        self.assertEqual(len(clicks), 2)
        self.assertGreaterEqual(clicks[1] - first, 60)

    def test_switching_task_and_medicine_tier_keeps_shared_cooldown(self):
        first = FakeTask()
        self.process(first)
        stamp = next(e[1] for e in first.events if e[0] == 'confirm')
        second = FakeTask()
        second.now = first.now + 5
        second.config['复活药优先级'] = '仅紫色'
        second.executor._battle_assist_revive_cooldown = first.executor._battle_assist_revive_cooldown
        self.process(second)
        self.assertEqual(second.events[0][1], .591)
        self.assertGreaterEqual(next(e[1] for e in second.events if e[0] == 'confirm') - stamp, 60)

    def test_restart_restores_remaining_cooldown_from_saved_timestamp(self):
        t = FakeTask()
        self.process(t)
        state = dict(t.executor._battle_assist_revive_cooldown.config)
        last = state['last_confirm_epoch']
        restarted = FakeTask()
        restarted.now = t.now + 12
        restarted.executor._battle_assist_revive_cooldown = medicine.ReviveCooldown(state)
        self.process(restarted)
        click = next(e[1] for e in restarted.events if e[0] == 'confirm')
        self.assertGreaterEqual(1800000000 + click - last, 60)

    def test_uncertain_confirmation_retains_cooldown(self):
        t = FakeTask()
        t.no_close = True
        with self.assertRaisesRegex(RuntimeError, '不重复用药'):
            self.process(t)
        first = next(e[1] for e in t.events if e[0] == 'confirm')
        t.no_close = False
        self.process(t)
        clicks = [e[1] for e in t.events if e[0] == 'confirm']
        self.assertGreaterEqual(clicks[1] - first, 60)

    def test_elapsed_time_does_not_override_grey_button(self):
        t = FakeTask()
        t.executor._battle_assist_revive_cooldown.config['last_confirm_epoch'] = 1799999940
        t.enabled_at = 2
        self.process(t)
        self.assertGreaterEqual(next(e[1] for e in t.events if e[0] == 'confirm'), 2)

    def test_short_wait_limit_stops_without_confirmation_during_cooldown(self):
        t = FakeTask()
        t.executor._battle_assist_revive_cooldown.config['last_confirm_epoch'] = 1800000000
        t.config['复活药等待上限（秒）'] = 2
        with self.assertRaisesRegex(RuntimeError, '等待超时'):
            self.process(t)
        self.assertEqual([e[0] for e in t.events], ['select'])

    def test_system_clock_forward_cannot_bypass_live_cooldown(self):
        clocks = {'monotonic': 100.0, 'wall': 1800000000.0}
        state = medicine.ReviveCooldown({})
        with patch.object(medicine, 'monotonic', lambda: clocks['monotonic']), \
                patch.object(medicine, 'wall_time', lambda: clocks['wall']):
            state.reserve()
            clocks['monotonic'] += 10
            clocks['wall'] += 3600
            self.assertEqual(state.remaining(), 50)
            clocks['monotonic'] += 50
            self.assertEqual(state.remaining(), 0)

    def test_green_first_and_single_confirmation(self):
        t = FakeTask()
        self.assertTrue(self.process(t))
        self.assertEqual([e[0] for e in t.events], ['select', 'confirm'])
        self.assertEqual(t.events[0][1], .403)
        self.assertEqual(t.info['复活弹窗处理次数'], 1)
        self.assertFalse(t.held)
        self.assertFalse(t.skip_combat_check)
        self.assertFalse(t.in_sleep_check)

    def test_priority_and_empty_stock_fallback(self):
        for order, stock, expected in [('绿→蓝→紫', [0, 9, 14], .497),
                                       ('紫→蓝→绿', [9, 9, 14], .591),
                                       ('仅蓝色', [9, 9, 14], .497)]:
            with self.subTest(order=order):
                t = FakeTask()
                t.config['复活药优先级'] = order
                t.stock = stock
                self.process(t)
                self.assertEqual(t.events[0][1], expected)

    def test_disabled_no_ocr_or_inputs(self):
        t = FakeTask()
        t.config['自动使用复活药'] = False
        t.ocr = lambda *a, **k: self.fail('OCR must not run')
        self.assertFalse(self.process(t))
        self.assertEqual(t.events, [])

    def test_all_three_anchors_required(self):
        for field in ('valid_title', 'valid_description', 'valid_button'):
            t = FakeTask()
            setattr(t, field, False)
            self.assertFalse(self.process(t))
            self.assertEqual(t.events, [])
            self.assertTrue(t.held)

    def test_white_world_without_title_does_not_click(self):
        t = FakeTask()
        t.executor.frame[:] = 240
        t.ocr = lambda *a, **k: []
        self.assertFalse(self.process(t))
        self.assertEqual(t.events, [])

    def test_unsupported_aspect_ratio_does_not_click(self):
        t = FakeTask()
        t.executor.frame = np.full((900, 1200, 3), 240, dtype=np.uint8)
        self.assertFalse(self.process(t))
        self.assertEqual(t.events, [])

    def test_no_stock_stops_before_selection(self):
        t = FakeTask()
        t.stock = [0, 0, 0]
        with self.assertRaisesRegex(RuntimeError, '用完'):
            self.process(t)
        self.assertEqual(t.events, [])
        self.assertFalse(t.held)

    def test_unknown_stock_does_not_guess(self):
        t = FakeTask()
        t.count_unknown = True
        with self.assertRaisesRegex(RuntimeError, '无法识别'):
            self.process(t)
        self.assertEqual(t.events, [])

    def test_grey_confirmation_waits_for_cooldown(self):
        t = FakeTask()
        t.enabled_at = 60
        self.process(t)
        clicks = [e for e in t.events if e[0] == 'confirm']
        self.assertEqual(len(clicks), 1)
        self.assertGreaterEqual(clicks[0][1], 60)

    def test_cooldown_timeout_never_confirms(self):
        t = FakeTask()
        t.enabled_at = 100
        t.config['复活药等待上限（秒）'] = 2
        with self.assertRaisesRegex(RuntimeError, '等待超时'):
            self.process(t)
        self.assertEqual([e[0] for e in t.events], ['select'])

    def test_confirmation_does_not_retry(self):
        t = FakeTask()
        t.no_close = True
        with self.assertRaisesRegex(RuntimeError, '不重复用药'):
            self.process(t)
        self.assertEqual(len([e for e in t.events if e[0] == 'confirm']), 1)

    def test_ocr_dropout_is_not_success(self):
        t = FakeTask()
        t.no_close = t.bad_ocr_after_click = True
        with self.assertRaisesRegex(RuntimeError, '未确认弹窗关闭'):
            self.process(t)
        self.assertNotIn('复活弹窗处理次数', t.info)

    def test_hud_required_after_popup_closes(self):
        t = FakeTask()
        t.hud = False
        with self.assertRaisesRegex(RuntimeError, '队伍HUD恢复'):
            self.process(t)
        self.assertNotIn('复活弹窗处理次数', t.info)

    def test_user_closes_popup_during_wait_no_confirmation(self):
        t = FakeTask()
        t.enabled_at = 60
        t.disappear_at = 1
        self.assertFalse(self.process(t))
        self.assertEqual([e[0] for e in t.events], ['select'])

    def test_stop_releases_inputs_and_restores_flags(self):
        t = FakeTask()
        t.enabled_at = 60
        t.stop_at = 1
        with self.assertRaises(Stopped):
            self.process(t)
        self.assertFalse(t.held)
        self.assertFalse(t.skip_combat_check)
        self.assertFalse(t.in_sleep_check)

    def test_stop_during_initial_confirmation_releases_inputs(self):
        t = FakeTask()
        t.stop_at = .1
        with self.assertRaises(Stopped):
            self.process(t)
        self.assertFalse(t.held)
        self.assertEqual(t.events, [])
        self.assertFalse(t.skip_combat_check)

    def test_reentrancy_guard(self):
        t = FakeTask()
        helper = medicine.ReviveMedicine(t)
        helper.handling = True
        self.assertFalse(helper.process(force=True))
        self.assertEqual(t.events, [])

    def test_native_classes_are_not_assigned_or_patched(self):
        for name in ('ArMedicine.py', 'MedicineMonitor.py', 'MedicineFarmEcho.py', 'AutoRechallenge.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            for n in ast.walk(tree):
                self.assertFalse(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                                 and n.func.id in {'setattr', 'exec'}, name)
                if isinstance(n, (ast.Assign, ast.AnnAssign)):
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    for target in targets:
                        self.assertFalse(isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                                         and target.value.id in {'BaseCombatTask', 'FarmEchoTask'}, name)


class IntegrationTests(unittest.TestCase):
    def test_framework_loader_limits_medicine_to_monitor_and_farm(self):
        class Base:
            def __init__(self, *args, **kwargs):
                self.default_config = {}
                self.config_description = {}
                self.config_type = {}
            def in_combat(self, target=False):
                return True
            def validate_config(self, key, value):
                return None
        class OneTime:
            def run(self):
                pass
        class Combat(Base):
            pass
        class Trigger(Base):
            pass
        class Farm(OneTime, Combat):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.default_config['Repeat Farm Count'] = 10000
        original_classes = {cls: dict(vars(cls)) for cls in (Base, Combat, Farm)}
        env = {'ReviveMedicineMixin': medicine.ReviveMedicineMixin,
               'WWOneTimeTask': OneTime, 'BaseCombatTask': Combat, 'FarmEchoTask': Farm,
               'BaseWWTask': Base, 'TriggerTask': Trigger, 're': __import__('re')}
        found = []
        for filename in ('AutoRechallenge.py', 'MedicineMonitor.py', 'MedicineFarmEcho.py'):
            tree = ast.parse((ROOT / filename).read_text(encoding='utf-8'))
            tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef)]
            exec(compile(tree, filename, 'exec'), env)
            cls = env[tree.body[0].name]
            self.assertTrue(issubclass(cls, Base), filename)
            task = cls()
            if filename == 'MedicineMonitor.py':
                self.assertIsInstance(task, Trigger)
                self.assertNotIsInstance(task, OneTime)
                self.assertFalse(task.default_config['_enabled'])
            if filename == 'AutoRechallenge.py':
                self.assertNotIsInstance(task, medicine.ReviveMedicineMixin)
                self.assertFalse(hasattr(task, 'try_revive_medicine'))
                self.assertFalse(any('药' in key for key in task.default_config))
                self.assertNotIn('复活药优先级', task.config_type)
            else:
                self.assertIsInstance(task, medicine.ReviveMedicineMixin)
                self.assertTrue(task.default_config['自动使用复活药'])
                self.assertEqual(task.default_config['复活药优先级'], '绿→蓝→紫')
                self.assertIn('复活药优先级', task.config_type)
            found.append(task.name)
            task.config = task.default_config.copy()
            task.executor = SimpleNamespace(frame=None)
            if isinstance(task, Combat):
                self.assertTrue(task.in_combat())
        self.assertEqual(found, ['通用自动重新挑战', '自动用药监测', '刷4C声骸（自动用药）'])
        for cls, before in original_classes.items():
            self.assertEqual(dict(vars(cls)), before)

    def test_mixin_detects_before_native_combat_check(self):
        events = []
        class Native:
            def in_combat(self, target=False):
                events.append(('native', target))
                return True
            def load_chars(self):
                events.append('reload')
        class Derived(medicine.ReviveMedicineMixin, Native):
            def try_revive_medicine(self, force=False):
                events.append('medicine')
                return True
        original = Native.in_combat
        self.assertTrue(Derived().in_combat(target=True))
        self.assertEqual(events, ['medicine', 'reload', ('native', True)])
        self.assertIs(Native.in_combat, original)

    def test_out_of_combat_fallback_delegates_without_popup(self):
        class Native:
            def raise_not_in_combat(self, message):
                raise ValueError(message)
        class Derived(medicine.ReviveMedicineMixin, Native):
            def try_revive_medicine(self, force=False):
                return False
        with self.assertRaisesRegex(ValueError, 'original'):
            Derived().raise_not_in_combat('original')


if __name__ == '__main__':
    unittest.main()
