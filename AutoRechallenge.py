"""Automatic rechallenge task distributed with the ok-ww-assist plugin."""
import re
from time import monotonic

from src.task.BaseCombatTask import BaseCombatTask, CharDeadException, NotInCombatException
from src.task.WWOneTimeTask import WWOneTimeTask


class AutoRechallenge(WWOneTimeTask, BaseCombatTask):
    VERSION = '1.0.0'
    BUILD = 'ok-ww-assist-1.0.0/rechallenge'
    RETRY_TEXT = re.compile(r'^\s*(?:重新挑战|重新挑戰|再次挑战|再次挑戰|Retry|Challenge\s+Again)\s*$', re.I)
    START_TEXT = re.compile(r'开始|開始|开启|開啟|\bStart\b', re.I)
    POLL = 0.20
    WALK_STEP = 0.20
    WALK_SETTLE = 1.00
    RETRY_BUTTON_SETTLE = 0.80
    RETRY_CLICK_GAP = 1.50
    RETRY_CLICK_TRIES = 4
    ENVIRONMENT_CLOSE_TIMEOUT = 5.0
    owns_switch_healer_config = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '通用自动重新挑战'
        self.description = '结算页或挑战入口运行；自动重新挑战、等待加载、短步找F开战，调用当前启用的角色或队伍代码。'
        self.default_config = {
            '挑战次数（0为持续）': 0,
            '加载超时（秒）': 120,
            '前进超时（秒）': 15,
            '开战超时（秒）': 30,
            '单场超时（秒）': 600,
            '结算超时（秒）': 90,
            'Use Liberation': True,
            'Check Levitator': True,
            'Switch to Healer before and after Combat': False,
        }
        self.config_description = {
            '挑战次数（0为持续）': '到达指定场数后停在结算页；0持续运行，用OK-WW停止按钮结束。',
            '单场超时（秒）': '在角色或队伍动作返回时检查；长动作期间可能延后。',
            'Use Liberation': '允许角色逻辑使用共鸣解放；自定义角色须遵守框架开关。',
            'Switch to Healer before and after Combat': '开战前切治疗；战后仅在队伍HUD仍可读且没有结算按钮时切换。',
        }
        self._testing_combat = False

    def validate_config(self, key, value):
        if key not in {'挑战次数（0为持续）', '加载超时（秒）', '前进超时（秒）',
                       '开战超时（秒）', '单场超时（秒）', '结算超时（秒）'}:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            return '请输入整数'
        if value < (0 if key == '挑战次数（0为持续）' else 1):
            return '场数须≥0，超时须≥1秒'

    def sleep_check(self):
        # Menu/loading sleeps must not inherit a stale battle's combat checks.
        if self._testing_combat:
            BaseCombatTask.sleep_check(self)

    def _mark(self, text):
        self.log_info(f'{self.BUILD} {text}')

    def _fresh(self):
        self.executor.check_enabled()
        self.next_frame()
        if not self._testing_combat:
            self._dismiss_environment()

    @staticmethod
    def _environment_hint(frame):
        # Cheap prefilter for the supplied 16:9 tower overlay. Avoid running
        # extra OCR on every normal loading/world frame.
        if frame is None or len(frame.shape) != 3:
            return False
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0 or abs(w / h - 16 / 9) > .03:
            return False
        title = frame[int(.29*h):int(.34*h), int(.07*w):int(.17*w), :3]
        effect = frame[int(.37*h):int(.42*h), int(.035*w):int(.105*w), :3]
        if not title.size or not effect.size:
            return False
        white = (title.min(axis=2) > 180).mean()
        gold = ((effect[:, :, 2] > 150) & (effect[:, :, 1] > 100)
                & (effect[:, :, 0] < 160)).mean()
        return bool(white > .025 and gold > .015)

    def _environment_panel(self):
        frame = getattr(self, 'frame', None)
        if not self._environment_hint(frame):
            return False
        h, w = frame.shape[:2]
        boxes = self.ocr(.02, .28, .45, .72, threshold=.75, frame=frame)
        def texts_in(bounds):
            x0, y0, x1, y1 = bounds
            return [re.sub(r'\s+', '', b.name) for b in boxes
                    if x0 <= b.x / w <= x1 and y0 <= b.y / h <= y1]
        title = texts_in((.06, .28, .20, .34))
        effect = texts_in((.025, .37, .13, .43))
        body = texts_in((.02, .43, .45, .72))
        return (any(t in {'环境特性', '環境特性'} for t in title)
                and '效果' in effect
                and any(re.search(r'伤害|傷害|抗性|战斗|戰鬥', t) for t in body))

    def _dismiss_environment(self):
        # This is a transition handler for our own retry task, never a global
        # hook or an Esc action inside character/axis combat.
        if not self._environment_panel():
            return False
        self.sleep(.15)
        self.executor.check_enabled()
        self.next_frame()
        if not self._environment_panel():
            return False
        self._release()
        try:
            self.executor.check_enabled()
            self.send_key_down('esc')
            self.sleep(.05)
        finally:
            self.executor.interaction.send_key_up('esc')
        self._mark('tower environment panel confirmed twice; Esc sent once')
        deadline = monotonic() + self.ENVIRONMENT_CLOSE_TIMEOUT
        stable = 0
        while monotonic() < deadline:
            self.executor.check_enabled()
            self.next_frame()
            gone = not self._environment_panel()
            stable = stable + 1 if gone and self.in_team_and_world() else 0
            if stable >= 2:
                self._mark('tower environment panel closed; HUD ready; resume retry/start flow')
                return True
            self.sleep(self.POLL)
        raise RuntimeError('环境特性弹窗按Esc后未确认关闭及HUD恢复；已停止，不连续按Esc。')

    def _retry_box(self):
        # Only accept a whole button label in the lower part of the screen.
        boxes = self.ocr(0.05, 0.45, 0.98, 0.98, match=self.RETRY_TEXT, threshold=0.80)
        return boxes[0] if len(boxes) == 1 else None

    def _release(self):
        # The framework's stop exception must not prevent key-up cleanup.
        keys = {'w', 'a', 's', 'd', 'q', 'e', 'r', 't', 'f',
                'space', 'lshift', 'tab', 'esc', '1', '2', '3'}
        hotkeys = getattr(self, 'key_config', {})
        keys.update(hotkeys.get(name, fallback) for name, fallback in
                    (('Resonance Key', 'e'), ('Liberation Key', 'r'), ('Echo Key', 'q'),
                     ('Tool Key', 't'), ('Jump Key', 'space'), ('Dodge Key', 'lshift'),
                     ('Wheel Key', 'tab')))
        keys.discard(None)
        interaction = self.executor.interaction
        for key in keys:
            try:
                interaction.send_key_up(key)
            except Exception:
                pass
        for key in ('left', 'right', 'middle'):
            try:
                interaction.mouse_up(key=key)
            except Exception:
                pass

    def _wait_retry(self, timeout):
        deadline = monotonic() + timeout
        hits = 0
        while monotonic() < deadline:
            self._fresh()
            box = self._retry_box()
            hits = hits + 1 if box is not None else 0
            if hits >= 2:
                return box
            self.sleep(self.POLL)
        raise RuntimeError('等待“重新挑战”超时；已停止，请检查结算画面。')

    def _initial_screen(self):
        deadline = monotonic() + self.config['加载超时（秒）']
        retry_hits = hud_hits = 0
        while monotonic() < deadline:
            self._fresh()
            box = self._retry_box()
            retry_hits = retry_hits + 1 if box is not None else 0
            hud_hits = hud_hits + 1 if box is None and self.in_team_and_world() else 0
            if retry_hits >= 2:
                return box
            if hud_hits >= 3:
                return None
            self.sleep(self.POLL)
        raise RuntimeError('未找到结算页或稳定队伍HUD。')

    def _restart(self, box):
        self._release()
        self.combat_end()
        self.do_reset_to_false()
        # The label can appear before the result animation accepts a click.
        # Discard the old OCR box; every attempt uses a newly observed label.
        started = monotonic()
        deadline = started + self.config['加载超时（秒）']
        next_click = started + self.RETRY_BUTTON_SETTLE
        clicks = retry_hits = absent_hits = hud_hits = 0
        departed = False
        while monotonic() < deadline:
            self._fresh()
            retry = self._retry_box()
            retry_hits = retry_hits + 1 if retry is not None else 0
            absent_hits = absent_hits + 1 if retry is None else 0
            hud_hits = hud_hits + 1 if retry is None and self.in_team_and_world() else 0
            if absent_hits >= 2:
                departed = True  # Loading began: never send a further menu click.
            if absent_hits >= 3 and hud_hits >= 3:
                self._mark(f'HUD ready after retry; clicks={clicks}; reset character state')
                return
            if (not departed and retry_hits >= 2 and monotonic() >= next_click
                    and clicks < self.RETRY_CLICK_TRIES):
                clicks += 1
                self._mark(f'retry click attempt={clicks}/{self.RETRY_CLICK_TRIES}; '
                           f'fresh button; elapsed={monotonic() - started:.2f}s')
                self.click(retry)
                next_click = monotonic() + self.RETRY_CLICK_GAP
                retry_hits = 0
            if retry is not None and not departed and monotonic() - started >= 15:
                raise RuntimeError(f'重新挑战按钮仍未消失；已尝试{clicks}次，停止。')
            self.sleep(self.POLL)
        raise RuntimeError('重新挑战后等待HUD超时。')

    def _check_runtime(self):
        # Report missing built-in templates before walking or starting a challenge.
        self._fresh()
        for name in ('char_1_text', 'box_char_1', 'has_target'):
            try:
                self.get_box_by_name(name)
            except (ValueError, KeyError) as exc:
                raise RuntimeError(f'OKWW基础识别模板不可用：{name}。请先运行问题诊断，'
                                   '或使用完整的OKWW安装；本脚本不替换内置模板。') from exc

    def _check_team(self):
        if not self.load_chars():
            raise RuntimeError('无法读取队伍。')
        if not self.chars or any(c is None or not callable(getattr(c, 'perform', None))
                                 for c in self.chars):
            raise RuntimeError('队伍角色未完整识别，请先确认OKWW能识别当前队伍。')
        if self.get_current_char() is None:
            raise RuntimeError('无法确定当前角色，请确认队伍HUD正常显示。')
        # Standard combat cleanup invokes each character's reset_state, including
        # custom team controllers. Never reach into a specific team's driver.
        self.combat_end()
        names = ', '.join(c.name for c in self.chars)
        self._mark(f'team ready: {names}; built-in/custom character code selected by OKWW')

    def _walk_and_start(self):
        deadline = monotonic() + self.config['前进超时（秒）']
        try:
            while monotonic() < deadline:
                self._fresh()
                if not self.in_team_and_world():
                    raise RuntimeError('前进时HUD消失，已松开W并停止。')
                probe_started = monotonic()
                start_f = self.find_f_with_text(target_text=self.START_TEXT)
                probe_ms = (monotonic() - probe_started) * 1000
                if start_f:
                    self.executor.check_enabled()
                    self.send_key('f')
                    self._mark(f'start F sent; probe={probe_ms:.0f}ms; waiting combat')
                    return
                self._mark(f'start F not found; probe={probe_ms:.0f}ms; W released')
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                # Never hold W through capture/OCR. Move one short step, release
                # even on stop, then let the scene settle before the next probe.
                try:
                    self.send_key_down('w')
                    self.sleep(min(self.WALK_STEP, remaining))
                finally:
                    self.executor.interaction.send_key_up('w')
                self._mark('walk W step released; settle 1.00s then check F containing Start')
                self.sleep(self.WALK_SETTLE)
        finally:
            self._release()
        raise RuntimeError('前进超时，未找到包含“开始/开启”的F提示。')

    def _wait_combat(self):
        deadline = monotonic() + self.config['开战超时（秒）']
        while monotonic() < deadline:
            self._fresh()
            if self._retry_box() is not None:
                raise RuntimeError('F后直接出现结算；未确认开战。')
            if self.in_combat():
                return
            self.sleep(self.POLL)
        raise RuntimeError('按F后未确认开战；不重复按F。')

    def _fight(self):
        self._mark('combat begin')
        started = monotonic()
        ended = False
        self._testing_combat = True
        switched_to_healer = False
        try:
            while monotonic() - started < self.config['单场超时（秒）']:
                self._fresh()
                if self._retry_box() is not None:
                    ended = True
                    break
                if not self.in_combat():
                    ended = True
                    break
                if not switched_to_healer:
                    self.switch_healer()
                    switched_to_healer = True
                char = self.get_current_char()
                if char is None:
                    raise RuntimeError('战斗中无法确定当前角色。')
                char.perform()
        except CharDeadException:
            ended = True
            self._mark('character dead; waiting explicit retry screen')
        except NotInCombatException:
            ended = True
            self._mark('axis returned out of combat; waiting explicit retry screen')
        finally:
            self._testing_combat = False
            self._release()
            self.combat_end()
            self.do_reset_to_false()
        if not ended:
            raise RuntimeError('单场超时；请检查战斗轴检查点及游戏画面。')
        self._fresh()
        if self.in_team_and_world() and self._retry_box() is None:
            self.switch_healer()
        return self._wait_retry(self.config['结算超时（秒）'])

    def run(self):
        # Do not enable the ordinary AutoCombat trigger: this one-time task owns
        # both transitions and combat, on the existing single task executor.
        completed = 0
        self._testing_combat = False
        try:
            super().run()
            self._mark('run begin')
            self.use_liberation = bool(self.config.get('Use Liberation', True))
            self._check_runtime()
            self.do_reset_to_false()
            box = self._initial_screen()
            while True:
                if box is not None:
                    self._restart(box)
                self._check_team()
                self._fresh()
                if not self.in_combat():
                    self._walk_and_start()
                    self._wait_combat()
                box = self._fight()
                completed += 1
                self.info_set('已完成挑战次数', completed)
                self._mark(f'round complete={completed}; explicit retry screen confirmed')
                limit = self.config['挑战次数（0为持续）']
                if limit and completed >= limit:
                    self._mark('challenge limit reached; stopped on result screen')
                    return
        finally:
            self._testing_combat = False
            self._release()
            self._mark(f'run stopped; completed={completed}')
