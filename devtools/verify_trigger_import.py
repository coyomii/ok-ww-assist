"""Verify release import, real task registration and Qt switch without game input.

Usage: <OKWW python> -B devtools/verify_trigger_import.py <OKWW working>
All framework configuration writes go to a temporary working directory.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def verify(working):
    sys.path.insert(0, str(working))
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='battle-assist-trigger-') as temp:
        os.chdir(temp)
        try:
            return verify_isolated(Path(temp))
        finally:
            os.chdir(previous)


def verify_isolated(temp):
    import numpy as np
    from ok import TriggerTask, TaskDisabledException, og
    from ok.core.script_packager import import_script, inspect_script_archive
    from ok.core.task_manager import TaskManager
    from ok.task.TaskExecutor import TaskExecutor
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSignalBlocker
    from ok.ui.qt.tasks.TriggerTaskTab import TriggerTaskTab

    qt = QApplication.instance() or QApplication([])
    version = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))['version']
    legacy_config = temp / 'configs/MedicineMonitor.json'
    legacy_config.parent.mkdir(exist_ok=True)
    legacy_config.write_text(json.dumps({'自动使用复活药': True,
                             '复活药优先级': '仅蓝色', '复活药等待上限（秒）': 90}),
                             encoding='utf-8')
    legacy_retry = temp / 'configs/AutoRechallenge.json'
    legacy_retry.write_text(json.dumps({'挑战次数（0为持续）': 7, '前进超时（秒）': 19,
                            '自动使用复活药': True, '复活药优先级': '仅紫色',
                            '复活药等待上限（秒）': 100}), encoding='utf-8')
    reports = []
    for suffix in ('.okscript', '.zip'):
        archive = ROOT / 'release' / f'ok-ww-assist-{version}{suffix}'
        manifest = inspect_script_archive(archive)
        assert manifest['version'] == '1.0.0'
        assert manifest['script_name'] == '通用辅助'
        assert manifest['file_name'] == 'AutoRechallenge'
        # A lower unified version must still replace the previous development
        # plugin through local import, keeping its stable package identity.
        previous_install = temp / suffix[1:] / 'AutoRechallenge'
        previous_install.mkdir(parents=True)
        (previous_install / 'manifest.json').write_text(json.dumps({
            'file_name': 'AutoRechallenge', 'script_name': '通用战斗辅助',
            'version': '1.2.1'}), encoding='utf-8')
        success, message, folder = import_script(archive, import_base=temp / suffix[1:])
        assert success, message
        assert Path(folder) == previous_install
        with ZipFile(archive) as package:
            prefix = 'AutoRechallenge/' if suffix == '.zip' else ''
            for name in ('AutoRechallenge.py', 'ArMedicine.py', 'MedicineMonitor.py',
                         'MedicineFarmEcho.py', 'manifest.json'):
                assert (Path(folder) / name).read_bytes() == package.read(prefix + name)

        executor = SimpleNamespace(
            scene=None, debug=False, paused=False, current_task=None,
            global_config=SimpleNamespace(get_config=lambda name: {}),
            text_fix={}, trigger_tasks=[], onetime_tasks=[],
            onetime_task_queue=[], trigger_task_index=-1,
            exit_event=threading.Event(), lock=threading.Lock(),
            # No real interaction or capture backend is constructed.
            interaction=SimpleNamespace(on_run=lambda: None),
            enqueue_onetime_task=lambda task: None,
            remove_onetime_task=lambda task: None,
            _wake_executor=lambda: None, trigger_sleep=lambda: None,
            frame=np.zeros((900, 1600, 3), dtype=np.uint8),
        )
        frame_requests = []

        def check_enabled():
            if executor.current_task and not executor.current_task.enabled:
                raise TaskDisabledException()

        def next_frame():
            check_enabled()
            frame_requests.append(1)
            return executor.frame

        executor.check_enabled = check_enabled
        executor.next_frame = next_frame
        app = SimpleNamespace(debug=False, tr=lambda text: text)
        manager = TaskManager.__new__(TaskManager)
        manager.app = app
        manager.scene = None
        manager.task_executor = executor
        manager.task_errors = {}
        manager.task_map = {}
        manager.imported_scripts = {}
        manager.defer_plugin_validation = True
        entry = manager._prepare_import_entry({**manifest, 'folder': folder})
        assert not entry['errors'], entry['errors']
        manager._register_import_entry(manifest['file_name'], entry)
        assert len(executor.onetime_tasks) == 2
        assert len(executor.trigger_tasks) == 1
        retry = next(task for task in executor.onetime_tasks
                     if type(task).__name__ == 'AutoRechallenge')
        farm = next(task for task in executor.onetime_tasks
                    if type(task).__name__ == 'MedicineFarmEcho')
        assert not hasattr(retry, 'try_revive_medicine')
        assert not any('药' in key for key in retry.default_config)
        assert not any('药' in key for key in retry.config)
        assert retry.config['挑战次数（0为持续）'] == 7
        assert retry.config['前进超时（秒）'] == 19
        saved_retry = json.loads(legacy_retry.read_text(encoding='utf-8'))
        assert not any('药' in key for key in saved_retry)
        assert callable(farm.try_revive_medicine)
        assert farm.default_config['自动使用复活药'] is True
        monitor = executor.trigger_tasks[0]
        assert type(monitor).__name__ == 'MedicineMonitor'
        assert isinstance(monitor, TriggerTask) and monitor.visible
        assert not monitor.enabled
        assert monitor.config['复活药优先级'] == '仅蓝色'
        assert monitor.config['复活药等待上限（秒）'] == 90

        # Construct the actual trigger page with its real native task card.
        og.executor = executor
        og.app = app
        tab = TriggerTaskTab()
        assert tab.in_current_list(monitor)
        assert len(tab.card_widgets) == 1
        card = tab.card_widgets[0]
        assert card.task is monitor and card.enable_button is not None
        assert card.start_button is None
        assert not card.enable_button.isChecked()

        # Keep checkbox notifications blocked while exercising the framework
        # lifecycle directly; no worker thread or game backend is started.
        blocker = QSignalBlocker(card.enable_button)
        monitor.config['复活药优先级'] = '仅蓝色'
        monitor.enable()
        card.update_buttons(monitor)
        assert monitor.enabled and card.enable_button.isChecked()
        selected, _, is_trigger = TaskExecutor.next_task(executor)
        assert selected is monitor and is_trigger
        executor.current_task = monitor
        assert monitor.run() is False  # No popup: returns after one frame.
        executor.current_task = None
        assert len(frame_requests) == 1
        sentinel = SimpleNamespace(enabled=True, should_trigger=lambda: True)
        executor.trigger_tasks.append(sentinel)
        assert TaskExecutor.next_task(executor)[0] is sentinel
        executor.trigger_tasks.pop()
        executor.trigger_task_index = -1

        # Recreate with real Config to verify enable state and settings persist.
        restored = type(monitor)(executor=executor, app=app)
        restored.after_init(executor=executor)
        assert restored.enabled and restored.config['复活药优先级'] == '仅蓝色'
        monitor.disable()
        card.update_buttons(monitor)
        assert not monitor.enabled and not card.enable_button.isChecked()
        assert monitor.run() is False
        assert len(frame_requests) == 1  # Disabled: no capture/probe.
        assert TaskExecutor.next_task(executor)[0] is None
        restored = type(monitor)(executor=executor, app=app)
        restored.after_init(executor=executor)
        assert not restored.enabled
        assert restored.config['复活药优先级'] == '仅蓝色'
        config = json.loads((temp / 'configs/MedicineMonitor.json').read_text(encoding='utf-8'))
        assert config['_enabled'] is False
        del blocker
        for widget in tab.card_widgets:
            widget.dispose()
        tab.timer.stop()
        tab.close()
        tab.deleteLater()
        qt.processEvents()
        reports.append({'format': suffix, 'imported': True, 'one_time_tasks': 2,
                        'previous_development_plugin_replaced': True,
                        'trigger_tasks': 1, 'trigger_page_switch': True,
                        'default_enabled': False, 'enable_disable_persisted': True,
                        'medicine_settings_preserved': True,
                        'legacy_config_migrated': True,
                        'retry_medicine_removed': True, 'retry_settings_preserved': True,
                        'farm_medicine_retained': True,
                        'single_probe_returns': True, 'next_trigger_can_run': True,
                        'disabled_skips_probe': True})
    return {'project': 'ok-ww-assist', 'version': version,
            'display_name': '通用辅助', 'package_id': 'AutoRechallenge',
            'game_input_executed': False, 'packages': reports}


if __name__ == '__main__':
    report = verify(Path(sys.argv[1]).resolve())
    output = ROOT / 'docs/trigger_import.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
