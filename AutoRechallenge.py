"""Automatic rechallenge task distributed with the ok-ww-assist plugin."""
import re
from time import monotonic

from src.task.BaseCombatTask import BaseCombatTask, CharDeadException, NotInCombatException
from src.task.WWOneTimeTask import WWOneTimeTask


class AutoRechallenge(WWOneTimeTask, BaseCombatTask):
    VERSION = '1.0.1'
    BUILD = 'ok-ww-assist-1.0.1/rechallenge'
    RETRY_TEXT = re.compile(r'^\s*(?:重新挑战|重新挑戰|再次挑战|再次挑戰|Retry|Challenge\s+Again)\s*$', re.I)
    START_TEXT = re.compile(r'开始|開始|开启|開啟|\bStart\b', re.I)
    POLL = 0.20
    WALK_STEP = 0.20
    WALK_SETTLE = 1.00
    RETRY_BUTTON_SETTLE = 0.80
    RETRY_CLICK_GAP = 1.50
    RETRY_CLICK_TRIES = 4
    ENVIRONMENT_CLOSE_TIMEOUT = 5.0
    SUCCESS_CLOSE_TIMEOUT = 5.0
    owns_switch_healer_config = True

    class ChallengeSucceeded(Exception):
        """Unwind combat before handling the tower's post-success menus."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '通用自动重新挑战'
        self.description = '自动重新挑战、加载、找F开战；深塔成功后确认成绩或返回深塔，取消覆盖记录，并沿用已选角色继续挑战。'
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
            '挑战次数（0为持续）': '0持续运行；成功后继续挑战；达到指定场数则不再开启下一场。',
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
        self._confirm_success()
        if not self._testing_combat:
            self._dismiss_environment()

    @staticmethod
    def _success_hint(frame):
        # Standard 16:9 success overlay: gold title and either the centered
        # confirmation button or the first-clear return button on the left.
        if frame is None or len(frame.shape) != 3:
            return False
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0 or abs(w / h - 16 / 9) > .03:
            return False
        title = frame[int(.26*h):int(.33*h), int(.43*w):int(.57*w), :3]
        if not title.size:
            return False
        gold = ((title[:, :, 2] > 200) & (title[:, :, 1] > 170)
                & (title[:, :, 0] < 230)).mean()
        if gold <= .01:
            return False
        for x0, x1 in ((.42, .58), (.30, .44)):
            button = frame[int(.83*h):int(.88*h), int(x0*w):int(x1*w), :3]
            if button.size and (button.min(axis=2) > 180).mean() > .25:
                return True
        return False

    def _success_box(self):
        frame = getattr(self, 'frame', None)
        if not self._success_hint(frame):
            return None
        h, w = frame.shape[:2]
        boxes = self.ocr(.30, .22, .70, .92, threshold=.80, frame=frame)
        titles = [b for b in boxes
                  if re.sub(r'\s+', '', b.name) in {'挑战成功', '挑戰成功'}
                  and .38 <= b.x / w <= .60 and .24 <= b.y / h <= .35]
        buttons = []
        for b in boxes:
            label = re.sub(r'\s+', '', b.name)
            if not .80 <= b.y / h <= .90:
                continue
            if ((label in {'确认成绩', '確認成績'} and .40 <= b.x / w <= .60)
                    or (label == '返回深塔' and .30 <= b.x / w <= .45)):
                buttons.append(b)
        return buttons[0] if len(titles) == len(buttons) == 1 else None

    def _confirm_success(self):
        if self._success_box() is None:
            return
        # Leave combat before handling the post-success tower menu sequence.
        was_combat = self._testing_combat
        self._testing_combat = False
        try:
            self.sleep(.15)
            self.executor.check_enabled()
            self.next_frame()
            button = self._success_box()
            if button is None:
                return
            self._release()
            self.info_set('挑战成功', True)
            self.executor.check_enabled()
            self.click(button)
            self._mark(f'challenge success confirmed twice; {button.name} clicked once')
            deadline = monotonic() + self.SUCCESS_CLOSE_TIMEOUT
            gone = 0
            while monotonic() < deadline:
                self.sleep(self.POLL)
                self.executor.check_enabled()
                self.next_frame()
                # Failed OCR alone must not permit another click or restart.
                gone = gone + 1 if not self._success_hint(self.frame) else 0
                if gone >= 2:
                    raise self.ChallengeSucceeded()
            raise RuntimeError(f'挑战成功后已点击一次“{button.name}”，但未确认结算页关闭；已停止，不重复点击或重开。')
        finally:
            self._testing_combat = was_combat

    @staticmethod
    def _tower_menu_hint(frame):
        if frame is None or len(frame.shape) != 3:
            return False
        h, w = frame.shape[:2]
        if h <= 0 or w <= 0 or abs(w / h - 16 / 9) > .03:
            return False
        # White record-dialog header or the tower's white bottom-right button.
        for x0, y0, x1, y1 in ((.18, .17, .25, .23), (.78, .89, .90, .94)):
            pixels = frame[int(y0*h):int(y1*h), int(x0*w):int(x1*w), :3]
            if pixels.size and (pixels.min(axis=2) > 180).mean() > .40:
                return True
        return False

    def _tower_action(self):
        frame = getattr(self, 'frame', None)
        if not self._tower_menu_hint(frame):
            return None
        h, w = frame.shape[:2]

        def read(bounds):
            return self.ocr(*bounds, threshold=.80, frame=frame)

        def texts(boxes, bounds):
            x0, y0, x1, y1 = bounds
            return ''.join(re.sub(r'\s+', '', b.name) for b in boxes
                           if x0 <= b.x / w <= x1 and y0 <= b.y / h <= y1)

        def button(boxes, names, bounds):
            x0, y0, x1, y1 = bounds
            found = [b for b in boxes if re.sub(r'\s+', '', b.name) in names
                     and x0 <= b.x / w <= x1 and y0 <= b.y / h <= y1]
            return found[0] if len(found) == 1 else None

        panel = read((.17, .15, .83, .80))

        def record_text(bounds):
            # OCR may render the game's 记录 as the synonymous 纪录.
            return texts(panel, bounds).replace('纪录', '记录').replace('紀錄', '記錄')

        title = record_text((.18, .17, .35, .24))
        prompt = texts(panel, (.27, .26, .77, .33))
        old = record_text((.21, .36, .35, .42))
        new = record_text((.51, .36, .65, .42))
        cancel = button(panel, {'取消'}, (.20, .69, .39, .76))
        if (title in {'确认记录', '確認記錄'} and cancel is not None
                and any(t in prompt for t in ('是否覆盖', '是否覆蓋'))
                and any(t in prompt for t in ('挑战成绩', '挑戰成績'))
                and old in {'原记录', '原記錄'} and new in {'新记录', '新記錄'}):
            return 'cancel_record', cancel
        # An unrecognized modal must not expose clickable controls behind it.
        header_pixels = frame[int(.17*h):int(.23*h), int(.18*w):int(.25*w), :3]
        if (header_pixels.min(axis=2) > 180).mean() > .40:
            return None
        header = read((.02, .03, .39, .18))
        header_text = texts(header, (.02, .03, .39, .18))
        if '深境之塔' not in header_text:
            return None
        buttons = read((.70, .87, .94, .96))
        start = button(buttons, {'挑战开始', '挑戰開始'}, (.75, .88, .91, .95))
        if start is not None:
            targets = read((.40, .43, .70, .69))
            if any(t in texts(targets, (.40, .43, .70, .69))
                   for t in ('挑战目标', '挑戰目標')):
                return 'challenge_start', start
        enter = button(buttons, {'开启挑战', '開啟挑戰'}, (.70, .88, .91, .95))
        if enter is not None and any(t in header_text for t in ('角色列表',)):
            return 'enter_challenge', enter
        return None

    def _tower_reenter(self, start_next=True):
        # Optional record dialog -> selected floor -> existing selected party
        # -> loading/world. Each recognized menu gets at most one click.
        self._release()
        deadline = monotonic() + self.config['加载超时（秒）']
        clicked = {}
        previous = None
        stable = hud_hits = 0
        order = {'cancel_record': 0, 'challenge_start': 1, 'enter_challenge': 2}
        last_order = -1
        while monotonic() < deadline:
            self.executor.check_enabled()
            self.next_frame()
            action = self._tower_action()
            if action is not None:
                name, box = action
                hud_hits = 0
                if not start_next and name in {'challenge_start', 'enter_challenge'}:
                    return
                if name in clicked:
                    if monotonic() - clicked[name] >= 15:
                        raise RuntimeError('深塔菜单点击后15秒未切换；已停止，不重复点击。')
                    stable = 0
                elif order[name] < last_order:
                    raise RuntimeError('深塔菜单出现非预期回退；已停止，请检查画面。')
                else:
                    stable = stable + 1 if previous == name else 1
                    if stable >= 2:
                        self.executor.check_enabled()
                        self._release()
                        self.click(box)
                        clicked[name] = monotonic()
                        last_order = order[name]
                        stable = 0
                        self._mark(f'tower menu {name}; fresh button clicked once')
                previous = name
            else:
                previous = None
                stable = 0
                # Do not mistake a world HUD behind a modal for loading done.
                ready = (not self._tower_menu_hint(getattr(self, 'frame', None))
                         and self.in_team_and_world())
                hud_hits = hud_hits + 1 if ready else 0
                if hud_hits >= 3:
                    self._mark('tower loading finished; HUD ready; resume original F/combat flow')
                    return
            self.sleep(self.POLL)
        raise RuntimeError('深塔成功后等待菜单或加载超时；已停止，请检查画面。')

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
            if box is None and self._tower_action() is not None:
                self._tower_reenter()
                return None
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
            need_initial = True
            box = None
            while True:
                try:
                    if need_initial:
                        self._check_runtime()
                        self.do_reset_to_false()
                        box = self._initial_screen()
                        need_initial = False
                    if box is not None:
                        self._restart(box)
                    self._check_team()
                    self._fresh()
                    if not self.in_combat():
                        self._walk_and_start()
                        self._wait_combat()
                    box = self._fight()
                except self.ChallengeSucceeded:
                    completed += 1
                    self.info_set('已完成挑战次数', completed)
                    self.info_set('挑战成功次数', self.info.get('挑战成功次数', 0) + 1)
                    self._mark(f'round complete={completed}; challenge success confirmed')
                    limit = self.config['挑战次数（0为持续）']
                    reached = bool(limit and completed >= limit)
                    self._tower_reenter(start_next=not reached)
                    if reached:
                        self._mark('challenge limit reached; stopped before opening next challenge')
                        return
                    self._check_runtime()
                    need_initial = False
                    box = None
                    continue
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
