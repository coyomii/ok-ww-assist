"""Revive-popup handling used only by our own tasks; no native monkey patches."""
import re
from time import monotonic, time as wall_time

import numpy as np
import cv2


def compact(text):
    return re.sub(r'\s+', '', text)


class ReviveCooldown:
    """One 60-second limit for every medicine tier and all our task entries."""
    SECONDS = 60.0

    def __init__(self, config):
        self.config = config
        self.monotonic_until = 0.0

    def remaining(self):
        # Wall time survives app restarts. Monotonic time also protects against
        # the system clock moving forward while this app is running.
        stamp = self.config.get('last_confirm_epoch', 0.0)
        wall_remaining = float(stamp) + self.SECONDS - wall_time() if stamp else 0.0
        return max(0.0, self.monotonic_until - monotonic(), wall_remaining)

    def reserve(self):
        # Reserve even if the click's outcome is uncertain; a failed verification
        # must never allow another confirmation to bypass the medicine cooldown.
        self.monotonic_until = monotonic() + self.SECONDS
        self.config['last_confirm_epoch'] = wall_time()


class ReviveMedicine:
    TITLE = {'选择复苏物品', '選擇復甦物品', '選擇復蘇物品'}
    CONFIRM = {'确认', '確認'}
    ORDER = {'绿→蓝→紫': (0, 1, 2), '紫→蓝→绿': (2, 1, 0),
             '仅绿色': (0,), '仅蓝色': (1,), '仅紫色': (2,)}
    SLOTS = (0.403, 0.497, 0.591)

    def __init__(self, task):
        self.task = task
        self.handling = False
        self.last_probe = -999.0

    def cooldown(self):
        executor = self.task.executor
        state = getattr(executor, '_battle_assist_revive_cooldown', None)
        if state is None:
            from ok import Config
            state = ReviveCooldown(Config('BattleAssistMedicineCooldown', {'last_confirm_epoch': 0.0}))
            # Shared data only: do not replace any native method or callback.
            executor._battle_assist_revive_cooldown = state
        return state

    @staticmethod
    def crop(frame, bounds):
        h, w = frame.shape[:2]
        x, y, r, b = bounds
        return frame[int(y*h):int(b*h), int(x*w):int(r*w), :3]

    @classmethod
    def looks_like_panel(cls, frame):
        if frame is None or len(frame.shape) != 3:
            return False
        h, w = frame.shape[:2]
        # The supplied game screenshots use the standard 16:9 dialog layout.
        if abs(w / h - 16 / 9) > 0.03:
            return False
        for bounds in ((.20, .27, .30, .32), (.20, .69, .30, .73),
                       (.74, .30, .78, .48)):
            pixels = cls.crop(frame, bounds).astype(np.int16)
            if not pixels.size:
                return False
            white = (pixels.min(axis=2) >= 190) & (np.ptp(pixels, axis=2) < 30)
            if float(white.mean()) < .70:
                return False
        return True

    def snapshot(self):
        t = self.task
        frame = t.executor.frame
        if not self.looks_like_panel(frame):
            return None
        boxes = t.ocr(.17, .16, .83, .81, threshold=.75, frame=frame)
        h, w = frame.shape[:2]
        titles = [b for b in boxes if compact(b.name) in self.TITLE
                  and .17 < b.x / w < .40 and .16 < b.y / h < .27]
        buttons = [b for b in boxes if compact(b.name) in self.CONFIRM
                   and .59 < b.x / w < .80 and .68 < b.y / h < .78]
        text = compact(''.join(b.name for b in boxes))
        description = '恢复意识' in text or '恢復意識' in text
        if len(titles) != 1 or len(buttons) != 1 or not description:
            return None
        return {'frame': frame, 'button': buttons[0]}

    @classmethod
    def button_enabled(cls, frame):
        # Sample the button fill left of the white label, excluding its rim.
        pixels = cls.crop(frame, (.62, .714, .66, .738))
        return bool(pixels.size and (pixels.max(axis=2) < 90).mean() > .80)

    def quantity(self, slot, frame):
        x = self.SLOTS[slot]
        boxes = self.task.ocr(x + .018, .438, x + .047, .488,
                              threshold=.70, frame=frame)
        values = [int(compact(b.name)) for b in boxes
                  if re.fullmatch(r'\d{1,5}', compact(b.name))]
        if len(values) == 1:
            return values[0]
        boxes = self.task.ocr(x + .018, .438, x + .047, .488,
                              threshold=.70, frame=frame, frame_processor=self.quantity_image)
        values = [int(compact(b.name)) for b in boxes
                  if re.fullmatch(r'\d{1,5}', compact(b.name))]
        return values[0] if len(values) == 1 else None

    @staticmethod
    def quantity_image(image):
        # Single white digits can be missed by the text detector. Magnify and
        # add contrast/padding; never substitute letters for missing numbers.
        mask = (image.min(axis=2) > 175).astype(np.uint8) * 255
        text = cv2.cvtColor(255 - mask, cv2.COLOR_GRAY2BGR)
        text = cv2.resize(text, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        return cv2.copyMakeBorder(text, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=(255, 255, 255))

    def fresh(self):
        self.task.executor.check_enabled()
        self.task.next_frame()

    def release(self):
        t = self.task
        keys = {'w', 'a', 's', 'd', 'q', 'e', 'r', 't', 'f', 'space',
                'lshift', 'tab', '1', '2', '3'}
        keys.update(getattr(t, 'key_config', {}).values())
        for key in keys:
            if not isinstance(key, str) or not key:
                continue
            try:
                t.executor.interaction.send_key_up(key)
            except Exception:
                pass
        for key in ('left', 'right', 'middle'):
            try:
                t.executor.interaction.mouse_up(key=key)
            except Exception:
                pass

    def process(self, force=False):
        t = self.task
        if self.handling or not t.config.get('自动使用复活药', True):
            return False
        if not force and monotonic() - self.last_probe < .35:
            return False
        self.last_probe = monotonic()
        if not self.snapshot():
            return False
        self.handling = True
        old_skip = getattr(t, 'skip_combat_check', False)
        old_check = getattr(t, 'in_sleep_check', False)
        t.skip_combat_check = True
        t.in_sleep_check = True
        touched = False
        try:
            t.sleep(.15)
            self.fresh()
            panel = self.snapshot()
            if panel is None:
                return False
            # Only release battle input after confirming the popup twice.
            touched = True
            self.release()
            deadline = monotonic() + t.config.get('复活药等待上限（秒）', 75)
            order = self.ORDER[t.config.get('复活药优先级', '绿→蓝→紫')]
            chosen = None
            for attempt in range(3):
                quantities = {slot: self.quantity(slot, panel['frame']) for slot in order}
                chosen = next((s for s in order if quantities[s] is not None and quantities[s] > 0), None)
                if chosen is not None:
                    break
                if all(q == 0 for q in quantities.values()):
                    raise RuntimeError('复活药已用完：已停止，保留弹窗供手动处理。')
                t.sleep(.35)
                self.fresh()
                panel = self.snapshot()
                if panel is None:
                    return False
            if chosen is None:
                raise RuntimeError('无法识别所选复活药的库存：已停止，不盲点。')
            # Recheck on a fresh image immediately before selecting a card.
            self.fresh()
            panel = self.snapshot()
            if panel is None:
                return False
            count = self.quantity(chosen, panel['frame'])
            if count is None or count <= 0:
                raise RuntimeError('复活药库存发生变化：已停止，不继续点击。')
            t.log_info(f'ok-ww-assist-1.0.0 revive card={chosen + 1} stock={count}')
            t.executor.check_enabled()
            t.click(self.SLOTS[chosen], .417, down_time=.05)
            t.sleep(.30)
            cooldown = self.cooldown()
            if remaining := cooldown.remaining():
                t.log_info(f'ok-ww-assist-1.0.0 revive cooldown remaining={remaining:.1f}s; no confirmation yet')
            ready = 0
            while monotonic() < deadline:
                self.fresh()
                panel = self.snapshot()
                if panel is None:
                    # Somebody closed/handled it; never click an old location.
                    return False
                eligible = cooldown.remaining() <= 0 and self.button_enabled(panel['frame'])
                ready = ready + 1 if eligible else 0
                if ready >= 2:
                    # This is the sole confirmation click for this episode.
                    t.executor.check_enabled()
                    cooldown.reserve()
                    t.click(panel['button'], down_time=.05)
                    cooldown.reserve()  # Count conservatively from click completion.
                    break
                t.sleep(.50)
            else:
                raise RuntimeError('复活药确认按钮持续不可用（可能冷却中）：等待超时，已停止。')
            t.sleep(.40)
            verify_deadline = monotonic() + 5.0
            gone = 0
            while monotonic() < verify_deadline:
                self.fresh()
                frame = t.executor.frame
                # A failed OCR alone does not prove the popup has disappeared.
                gone = gone + 1 if not self.looks_like_panel(frame) else 0
                if gone >= 2 and t.in_team_and_world():
                    t.sleep(1.0)
                    self.fresh()
                    if self.looks_like_panel(t.executor.frame) or not t.in_team_and_world():
                        gone = 0
                        continue
                    t.info_set('复活弹窗处理次数', t.info.get('复活弹窗处理次数', 0) + 1)
                    t.log_info('ok-ww-assist-1.0.0 revive dialog closed; team HUD readable')
                    return True
                t.sleep(.25)
            raise RuntimeError('已点击一次用药确认，但未确认弹窗关闭及队伍HUD恢复；已停止，不重复用药。')
        except BaseException:
            # A stop during the initial two-frame check must also release input.
            self.release()
            raise
        finally:
            if touched:
                self.release()
            t.skip_combat_check = old_skip
            t.in_sleep_check = old_check
            self.handling = False
            self.last_probe = monotonic()


class ReviveMedicineMixin:
    """Overrides apply only to our derived task instances, never native classes."""
    def add_medicine_config(self):
        self.default_config.update({'自动使用复活药': True, '复活药优先级': '绿→蓝→紫',
                                    '复活药等待上限（秒）': 75})
        self.config_type['复活药优先级'] = {
            'type': 'drop_down', 'options': list(ReviveMedicine.ORDER)}
        self.config_description.update({
            '自动使用复活药': '仅识别选择复苏物品弹窗；确认按钮可用后自动消耗复活药。',
            '复活药等待上限（秒）': '需满足至少60秒用药间隔且确认按钮可用；超时停止，不退本或传送。'})

    def try_revive_medicine(self, force=False):
        helper = getattr(self, '_medicine_helper', None)
        if helper is None:
            helper = self._medicine_helper = ReviveMedicine(self)
        return helper.process(force=force)

    def in_combat(self, *args, **kwargs):
        if self.try_revive_medicine():
            # The active slot may have changed when a character died.
            self.load_chars()
        return super().in_combat(*args, **kwargs)

    def raise_not_in_combat(self, message):
        if self.try_revive_medicine(force=True):
            self.load_chars()
            if self.in_combat():
                return
        return super().raise_not_in_combat(message)

    def validate_medicine_config(self, key, value):
        if key == '复活药优先级' and value not in ReviveMedicine.ORDER:
            return '请选择列表中的复活药优先级'
        if key == '复活药等待上限（秒）' and (
                isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 180):
            return '复活药等待上限须为1至180秒的整数'
