"""Preserve and recognize the initial conditions of one timed practice round."""
from math import hypot, isfinite
import re


def finite(value):
    return type(value) in (int, float) and abs(value) <= 1e9 and isfinite(value)


def valid_observation(value):
    if not isinstance(value, dict):
        return False
    for name, size in (('start_pose_mm_deg', 3), ('block_xy_mm', 2)):
        item = value.get(name)
        if not isinstance(item, list) or len(item) != size or not all(finite(v) for v in item):
            return False
    light = value.get('light')
    return (isinstance(light, dict) and set(light) in (set(), {'intensity', 'ambientIntensity'})
            and all(finite(v) for v in light.values()))


def start_conflict(original, current):
    if not valid_observation(original) or not valid_observation(current):
        return 'missing_observation'
    a, b = (item['start_pose_mm_deg'] for item in (original, current))
    angle = abs(a[2] - b[2]) % 360
    if hypot(a[0] - b[0], a[1] - b[1]) > .500000001 or min(angle, 360 - angle) > .500000001:
        return 'start_pose'
    a, b = (item['block_xy_mm'] for item in (original, current))
    if hypot(a[0] - b[0], a[1] - b[1]) > .500000001:
        return 'block_pose'
    a, b = (item['light'] for item in (original, current))
    if a.keys() != b.keys() or any(abs(a[k] - b[k]) > .000100001 for k in a):
        return 'light'
    return None


class RoundReplay:
    def __init__(self, limit, mode, expected=None):
        if expected is not None and (
                not isinstance(expected, dict)
                or not isinstance(expected.get('scene_signature'), str)
                or re.fullmatch(r'[0-9a-f]{64}', expected['scene_signature']) is None
                or not isinstance(expected.get('ticket'), str)
                or re.fullmatch(r'[0-9a-f]{32}', expected['ticket']) is None
                or not valid_observation(expected.get('observed'))):
            raise ValueError('历史轮次缺少可核对的开跑条件。')
        self.expected = expected
        self.rejection = None
        self.started = False
        self.evidence = dict(version=1, limit_s=limit, mode=mode, scene_signature=None,
                             observed=None, interrupted=False, external_uart=False)

    def prepare(self, signature, observed):
        if self.expected is not None:
            if self.evidence['interrupted']:
                self.rejection = 'manual_input'
            elif not signature or signature != self.expected['scene_signature']:
                self.rejection = 'scene_signature'
            else:
                self.rejection = start_conflict(self.expected['observed'], observed)
            if self.rejection:
                return False
        self.evidence.update(scene_signature=signature, observed=observed)
        self.started = True
        return True

    def interrupt(self):
        self.evidence['interrupted'] = True

    def external_uart(self):
        self.evidence['external_uart'] = True

    def report(self):
        return {**self.evidence, 'rejection':self.rejection, 'ticket':self.expected['ticket'] if self.expected else None}
