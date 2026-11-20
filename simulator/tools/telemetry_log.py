"""只记录传入的事件和数值；不读取相机、串口或算法。可单文件复制。"""
import csv
import math
import re
from threading import Lock
from time import perf_counter_ns


EVENTS = ('frame_received', 'vision_done', 'command_sent', 'reply_observed',
          'command_received', 'reply_sent', 'sample')
IDENTIFIER = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')


class TelemetryLog:
    """使用当前电脑的单调时钟；case_id 应能贯穿同一次动作。"""

    def __init__(self, path, signals=()):
        self.signals = tuple(signals)
        if (len(self.signals) > 42 or any(not isinstance(key, str) or not IDENTIFIER.fullmatch(key)
                                          or key in ('time_s', 'event', 'case_id') for key in self.signals)
                or len(self.signals) != len(set(self.signals))):
            raise ValueError('信号列名无效或重复。')
        self._lock = Lock()
        self._start = perf_counter_ns()
        self._stream = open(path, 'x', encoding='utf-8', newline='')
        self._writer = csv.writer(self._stream)
        self._writer.writerow(('time_s', 'event', 'case_id', *self.signals))

    def event(self, event, case_id=None, **values):
        if event not in EVENTS or set(values) - set(self.signals):
            raise ValueError('请使用已知事件及事先声明的信号列。')
        key = '' if case_id is None else str(case_id)
        if key and not IDENTIFIER.fullmatch(key):
            raise ValueError('动作编号仅可使用 1～48 位字母、数字、下划线或短横。')
        if any(value is not None and (type(value) not in (int, float)
                                      or (type(value) is float and not math.isfinite(value)))
               for value in values.values()):
            raise ValueError('信号只接受有限数值或 None。')
        with self._lock:
            elapsed = perf_counter_ns() - self._start
            self._writer.writerow((f'{elapsed // 10**9}.{elapsed % 10**9:09d}', event, key,
                                   *(values.get(name) for name in self.signals)))

    def sample(self, **values):
        self.event('sample', **values)

    def close(self):
        with self._lock:
            self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
