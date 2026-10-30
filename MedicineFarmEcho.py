"""Optional native farm subclass; all native files/classes stay untouched."""
from ArMedicine import ReviveMedicineMixin
from src.task.FarmEchoTask import FarmEchoTask


class MedicineFarmEcho(ReviveMedicineMixin, FarmEchoTask):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '刷4C声骸（自动用药）'
        self.description = '继承OKWW刷4C声骸流程，额外处理复苏用药弹窗；参数请在本入口单独设置。'
        self.add_medicine_config()

    def validate_config(self, key, value):
        return self.validate_medicine_config(key, value) or super().validate_config(key, value)

    def run(self, *args, **kwargs):
        self.next_frame()
        self.try_revive_medicine(force=True)
        return super().run(*args, **kwargs)
