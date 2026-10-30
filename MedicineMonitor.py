"""Optional popup watcher scheduled by OKWW's trigger-task executor."""
from ok import TriggerTask
from ArMedicine import ReviveMedicineMixin
from src.task.BaseWWTask import BaseWWTask


class MedicineMonitor(ReviveMedicineMixin, TriggerTask, BaseWWTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '自动用药监测'
        self.description = '在实时触发页启用后监测复苏弹窗并用药；默认关闭，其他任务占用执行器时检测会延后。'
        self.default_config['_enabled'] = False
        self.trigger_interval = .40
        self.add_medicine_config()

    def validate_config(self, key, value):
        return self.validate_medicine_config(key, value) or super().validate_config(key, value)

    def sleep_check(self):
        # Monitoring does not inherit a combat/dungeon routine.
        pass

    def run(self):
        if not self.enabled:
            return False
        self.executor.check_enabled()
        self.next_frame()
        # Return after each probe so other triggers can run. The existing
        # medicine handler owns a confirmed popup until completion or stop.
        return self.try_revive_medicine(force=True)
