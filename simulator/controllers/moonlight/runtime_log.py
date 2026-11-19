"""Bounded local diagnostics. Callers may only record structured status."""
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
from uuid import uuid4

SESSION_FORMAT = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}')
VERSION_FORMAT = re.compile(r'v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}')
EVENTS = frozenset((
    'startup_begin', 'config_ready', 'controller_ready', 'mode_changed',
    'algorithm_loaded', 'vision_link_ready', 'run_started', 'run_paused',
    'round_reset', 'round_finished', 'firmware_start_requested', 'firmware_started',
    'firmware_closed', 'gripper_holding', 'gripper_released', 'wall_contact',
    'error', 'shutdown', 'log_limit',
))
PHASES = frozenset((
    'dependency_load', 'scene_config', 'webots_setup', 'controller_ready',
    'vision_link', 'algorithm_load', 'panel_message', 'panel_start', 'panel_pause',
    'panel_mode', 'panel_reload', 'panel_drive', 'round_reset', 'round_end',
    'gripper', 'contact', 'simulation_step', 'member_vision', 'member_step',
    'firmware_start', 'firmware_step', 'firmware_stop', 'actuator', 'shutdown',
))
ERROR_CLASSES = tuple((
    json.JSONDecodeError, FileNotFoundError, PermissionError, ConnectionError,
    TimeoutError, OSError, ModuleNotFoundError, ImportError, SyntaxError,
    AttributeError, KeyError, IndexError, ValueError, TypeError, ArithmeticError,
    RuntimeError, MemoryError, AssertionError, KeyboardInterrupt,
))
ERROR_NAMES = frozenset(cls.__name__ for cls in ERROR_CLASSES) | {'OtherError'}
ERROR_TYPE_IDS = {id(cls):cls.__name__ for cls in ERROR_CLASSES}
CATEGORIES = {
    'dependency_load':'dependency', 'scene_config':'configuration', 'webots_setup':'configuration',
    'vision_link':'communication', 'algorithm_load':'member_code', 'member_vision':'member_code',
    'member_step':'member_code', 'firmware_start':'firmware', 'firmware_step':'firmware',
    'firmware_stop':'firmware',
}
CHOICES = {
    'mode': {'manual','member','firmware'},
    'field_revision': {'practice-legacy','rules-2000','custom'},
    'robot_kind': {'parametric','urdf','mesh','unknown'},
    'gripper_kind': {'parametric','urdf','none','unknown'},
    'stage': {'full','pickup','channel','parking'},
    'reason': {'reset','switch_mode','reload','manual_override','manual','time','shutdown',
               'firmware_missing','renode_missing','motor_pins_missing',
               'renode_launch_failed','renode_connection_closed'},
    'category': set(CATEGORIES.values()) | {'controller'},
    'error_class': ERROR_NAMES,
}
COUNTS = {'step_ms','physics_step_ms','sim_ms','round','ir_count','sonar_count','member_line'}
FLAGS = {'vision_link','firmware_configured','firmware_active'}


class RuntimeLog:
    MAX_RECORDS = 512
    MAX_BYTES = 256 * 1024

    def __init__(self, project):
        self.phase = 'dependency_load'
        self.mode = None
        self.sim_ms = None
        self.round = None
        self.last_error = None
        self.last_error_at = 0
        self.rows = 0
        self.bytes = 0
        self.path = None
        random_id = uuid4().hex[:8]
        supplied = os.environ.get('YUEGUANG_LOG_SESSION','')
        self.session = supplied if SESSION_FORMAT.fullmatch(supplied) else (
            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+random_id)
        try:
            directory = project/'output'/'logs'
            directory.mkdir(parents=True, exist_ok=True)
            path = directory/f'runtime-{self.session}-{uuid4().hex[:8]}.jsonl'
            with path.open('xb'):
                pass
            self.path = path
        except OSError:
            # Diagnostics cannot stop the simulator on a read-only project or full disk.
            pass

    def event(self, name, **fields):
        if self.path is None or name not in EVENTS or self.phase not in PHASES:
            return
        record = dict(schema=1, timestamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z'),
                      source='runtime', session=self.session, event=name, phase=self.phase)
        fields.update(mode=self.mode, sim_ms=self.sim_ms, round=self.round)
        for key,value in fields.items():
            if key in COUNTS and type(value) is int and 0 <= value <= 2**53-1:
                record[key] = value
            elif key in FLAGS and type(value) is bool:
                record[key] = value
            elif key in CHOICES and isinstance(value,str) and value in CHOICES[key]:
                record[key] = value
            elif key=='project_version' and isinstance(value,str) and (value=='unknown' or VERSION_FORMAT.fullmatch(value)):
                record[key] = value
        data = json.dumps(record,separators=(',',':'),ensure_ascii=True).encode('ascii')+b'\n'
        limited = self.rows >= self.MAX_RECORDS-1 or self.bytes+len(data) > self.MAX_BYTES-512
        if limited:
            record['event'] = 'log_limit'
            brief = {key:record[key] for key in ('schema','timestamp','source','session','event','phase')}
            data = json.dumps(brief,separators=(',',':')).encode('ascii')+b'\n'
        try:
            # Open and close each sparse event so an interrupted Webots exposes prior rows.
            with self.path.open('ab') as stream:
                stream.write(data)
            self.bytes += len(data)
            self.rows += 1
            if limited:
                self.path = None
        except OSError:
            self.path = None

    def error(self, error, **fields):
        # A user-defined class, exception text, and traceback may contain arbitrary data.
        inheritance = type.__getattribute__(type(error),'__mro__')
        builtin = next((ERROR_TYPE_IDS[id(t)] for t in inheritance if id(t) in ERROR_TYPE_IDS), 'OtherError')
        key = (self.phase,self.mode,builtin)
        now = time.monotonic()
        if self.last_error == key and now-self.last_error_at < 2:
            return
        self.last_error, self.last_error_at = key, now
        self.event('error',error_class=builtin,category=CATEGORIES.get(self.phase,'controller'),**fields)

    def install_exception_hook(self):
        prior = sys.excepthook
        def unhandled(error_type,error,traceback):
            self.error(error)
            prior(error_type,error,traceback)
        sys.excepthook = unhandled


def project_version(project):
    try:
        with (project/'VERSION').open('rb') as stream:
            value = stream.read(64).decode('ascii').strip()
        return value if VERSION_FORMAT.fullmatch(value) else 'unknown'
    except (OSError,UnicodeError):
        return 'unknown'


def choice(value, field, fallback):
    return value if isinstance(value,str) and value in CHOICES[field] else fallback
