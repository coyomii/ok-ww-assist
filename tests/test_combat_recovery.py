"""Transient combat exits must not strand a still-active challenge."""
import unittest

from test_rechallenge import make_task, OutOfCombat, Dead


class CombatRecoveryTests(unittest.TestCase):
    def test_axis_false_exit_resumes_same_attempt_without_f_or_extra_count(self):
        task = make_task()
        task.phase = 'combat'
        task.config['挑战次数（0为持续）'] = 1
        original = task.perform
        calls = []

        def perform():
            calls.append(1)
            if len(calls) == 1:
                task.held.update(('w', 'left', 'e'))
                raise OutOfCombat()
            original()

        task.perform = perform
        task.run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(task.info['已完成挑战次数'], 1)
        self.assertEqual(task.info['战斗恢复次数'], 1)
        self.assertFalse(any(e[0] in {'retry', 'down', 'tap'} for e in task.events))
        self.assertFalse(task.held)

    def test_native_false_exit_can_recover_after_two_fresh_checks(self):
        task = make_task()
        task.phase = 'combat'
        task.config['挑战次数（0为持续）'] = 1
        original = task.in_combat
        lost_once = []

        def in_combat():
            if task._testing_combat and not lost_once:
                lost_once.append(1)
                return False
            return original()

        task.in_combat = in_combat
        task.run()
        self.assertEqual(task.info['战斗恢复次数'], 1)
        self.assertEqual(task.info['已完成挑战次数'], 1)
        self.assertFalse(any(e[0] in {'retry', 'down', 'tap'} for e in task.events))

    def test_delayed_enemy_reacquisition_resumes_without_restarting_challenge(self):
        task = make_task()
        task.phase = 'combat'
        task.config['挑战次数（0为持续）'] = 1
        original_frame, original_perform = task.next_frame, task.perform
        calls = []

        def next_frame():
            original_frame()
            if task.phase == 'reacquiring' and task.now >= .8:
                task.phase = 'combat'

        def perform():
            calls.append(1)
            if len(calls) == 1:
                task.phase = 'reacquiring'
                raise OutOfCombat()
            original_perform()

        task.next_frame, task.perform = next_frame, perform
        task.run()
        self.assertEqual(task.info['战斗恢复次数'], 1)
        self.assertEqual(task.info['已完成挑战次数'], 1)
        self.assertFalse(any(e[0] in {'retry', 'down', 'tap'} for e in task.events))
        self.assertFalse(task.held)

    def test_real_death_does_not_resume_before_failure_screen(self):
        task = make_task()
        task.phase = 'combat'
        task.config['挑战次数（0为持续）'] = 1
        original_frame = task.next_frame
        calls = []

        def next_frame():
            original_frame()
            if task.now >= .8:
                task.phase = 'retry'

        def perform():
            calls.append(1)
            task.held.add('left')
            raise Dead()

        task.next_frame, task.perform = next_frame, perform
        task.run()
        self.assertEqual(len(calls), 1)
        self.assertEqual(task.info['已完成挑战次数'], 1)
        self.assertNotIn('战斗恢复次数', task.info)
        self.assertFalse(task.held)

    def test_one_positive_combat_frame_does_not_override_explicit_retry(self):
        task = make_task()
        task.phase = 'combat'
        original_frame = task.next_frame
        checks, frames = [], []

        def next_frame():
            original_frame()
            frames.append(1)
            if len(frames) >= 4:
                task.phase = 'retry'

        def in_combat():
            checks.append(1)
            return len(checks) == 1

        task.next_frame, task.in_combat = next_frame, in_combat
        box = task._wait_retry(2, allow_combat_resume=True)
        self.assertEqual(box.name, '重新挑战')
        self.assertNotIn('战斗恢复次数', task.info)
        self.assertFalse(any(e[0] in {'retry', 'down', 'tap'} for e in task.events))

    def test_repeated_false_exits_keep_original_single_fight_deadline(self):
        task = make_task()
        task.phase = 'combat'
        task.config['单场超时（秒）'] = 1
        task.fail_perform = OutOfCombat
        with self.assertRaisesRegex(RuntimeError, '单场超时'):
            task.run()
        self.assertGreater(task.info['战斗恢复次数'], 1)
        self.assertLess(task.now, 2)
        self.assertNotIn('已完成挑战次数', task.info)
        self.assertFalse(task.held)
