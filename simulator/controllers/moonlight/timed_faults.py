"""Per-round faults for the virtual practice devices, driven by simulation time."""
from collections import deque

MAX_DELAYED_BYTES = 65536
EVENTS = frozenset(('wall_contact', 'block_dropped', 'member_error', 'firmware_error'))


def bounded_int(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def parse_plan(value, limit_s, mode, sonar_available):
    if not isinstance(value, dict) or value.keys() - {'sonar', 'uart'}:
        raise ValueError('未知的定时故障设置。')
    if not bounded_int(limit_s, 1, 180) or mode not in ('member', 'firmware'):
        raise ValueError('定时故障只用于指定时长的程序练习。')
    result = {}
    for kind in ('sonar', 'uart'):
        if kind not in value:
            continue
        if kind == 'sonar' and not sonar_available:
            raise ValueError('当前小车没有可测试的超声波。')
        if kind == 'uart' and mode != 'firmware':
            raise ValueError('虚拟 UART 故障需要 STM32 固件模式。')
        window = value[kind]
        if not isinstance(window, dict):
            raise ValueError('请填写有效的故障窗口。')
        expected = {'start_ms', 'duration_ms'}
        if kind == 'uart':
            expected.add('action')
            if window.get('action') == 'delay':
                expected.add('delay_ms')
        if window.keys() != expected:
            raise ValueError('故障窗口字段不正确。')
        start, length = window['start_ms'], window['duration_ms']
        if (not bounded_int(start, 0, limit_s*1000) or not bounded_int(length, 64, limit_s*1000)
                or start+length > limit_s*1000):
            raise ValueError('故障窗口至少 64 ms，且须完全位于本轮时长内。')
        clean = dict(start_ms=start, duration_ms=length)
        if kind == 'uart':
            action = window['action']
            if action not in ('drop', 'delay'):
                raise ValueError('虚拟 UART 支持中断或延迟。')
            clean['action'] = action
            if action == 'delay':
                if not bounded_int(window['delay_ms'], 1, 3000):
                    raise ValueError('虚拟 UART 延迟须在 1～3000 ms。')
                clean['delay_ms'] = window['delay_ms']
        result[kind] = clean
    return result


class RoundFaults:
    def __init__(self, plan, signature):
        self.plan = plan
        self.signature = signature
        self.delayed = deque()
        self.pending = 0
        self.sonar_first_ms = None
        self.sonar_samples = 0
        self.uart_first_ms = None
        self.uart_affected_bytes = 0
        self.uart_dropped_bytes = 0
        self.uart_overflow_bytes = 0
        self.first_event = None

    def active(self, kind, at_ms):
        if kind not in self.plan:
            return False
        window = self.plan[kind]
        return window['start_ms'] <= at_ms < window['start_ms']+window['duration_ms']

    def sample_sonar(self, at_ms):
        active = self.active('sonar', at_ms)
        if active:
            if self.sonar_first_ms is None:
                self.sonar_first_ms = at_ms
            self.sonar_samples += 1
        return active

    def virtual_uart_input(self, at_ms, data):
        """Gate bytes due to enter the simulated MCU, preserving serial order."""
        window = self.plan.get('uart')
        if window is None:
            return data
        active = self.active('uart', at_ms)
        if active:
            if self.uart_first_ms is None:
                self.uart_first_ms = at_ms
            self.uart_affected_bytes += len(data)
            if window['action'] == 'drop':
                self.uart_dropped_bytes += len(data)
                return b''
        # No change outside a delay window once all queued bytes are delivered.
        if not active and not self.delayed:
            return data
        output = bytearray()
        while self.delayed and self.delayed[0][0] <= at_ms:
            _, frame = self.delayed.popleft()
            self.pending -= len(frame)
            output.extend(frame)
        if not data:
            return output
        delay = window.get('delay_ms', 0) if active else 0
        if not delay and not self.delayed:
            output.extend(data)
            return output
        due = max(at_ms+delay, self.delayed[-1][0] if self.delayed else 0)
        if self.pending+len(data) > MAX_DELAYED_BYTES:
            self.uart_overflow_bytes += len(data)
        else:
            self.delayed.append((due, bytes(data)))
            self.pending += len(data)
        return output

    def observe(self, kind, at_ms, pose):
        if kind in EVENTS and self.first_event is None:
            self.first_event = dict(kind=kind, elapsed_ms=at_ms,
                                    position_mm=[round(coord*1000) for coord in pose[:2]])

    def report(self):
        return dict(version=1, scene_signature=self.signature, plan=self.plan,
                    sonar_first_ms=self.sonar_first_ms, sonar_samples=self.sonar_samples,
                    uart_first_ms=self.uart_first_ms, uart_affected_bytes=self.uart_affected_bytes,
                    uart_dropped_bytes=self.uart_dropped_bytes, uart_pending_bytes=self.pending,
                    uart_overflow_bytes=self.uart_overflow_bytes, first_event=self.first_event)
