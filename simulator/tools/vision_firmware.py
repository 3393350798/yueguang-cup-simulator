"""Compare two virtual STM32 runs with the same UART and optional sensor inputs."""
import argparse
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
from io import StringIO
import json
import math
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

from firmware_bench import FirmwareBridge, ROOT, load_setup
from preflight import validate_image
from vision_replay import ID, SAMPLES, load_dataset
from vision_sequence import MODES, timeline, wait_until


LIMIT_MS = 12_000
MAX_TRACE = 524_288
MAX_ROWS = 1024
MAX_UART_PER_STEP = 256
MAX_REPLY = 1_048_576
SENSOR_LIMIT_MS = 5000
MAX_SENSOR_TRACE = 4 * 1024 * 1024
SENSOR_STEPS = (4, 8, 16, 32)
SONAR_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,63}\Z')
COLUMNS = ('time_ms', 'hex', 'slot', 'source_slot', 'state', 'sample_id', 'captured_ms')
STATES = ('normal', 'drop', 'hold', 'recover')
SIDES = ('left', 'right')
SIGNALS = ('uart', 'left_pwm', 'right_pwm', 'left_drive', 'right_drive')
SIGNAL_NAMES = {'uart': 'USART 发出数据', 'left_pwm': '左轮 PWM', 'right_pwm': '右轮 PWM',
                'left_drive': '左轮带方向驱动', 'right_drive': '右轮带方向驱动'}


@dataclass
class TraceRow:
    time_us: int
    payload: bytes
    marker: dict
    sensors: dict | None = None


def parse_time(value):
    # Use integer microseconds so sampling at a period boundary is exact.
    if not re.fullmatch(r'[0-9]{1,5}(?:\.[0-9]{1,3})?', value):
        raise ValueError('时间须为非负毫秒数，最多三位小数。')
    whole, _, fraction = value.partition('.')
    micros = int(whole)*1000 + int(fraction.ljust(3, '0'))
    if micros > LIMIT_MS*1000:
        raise ValueError(f'时间最长 {LIMIT_MS} ms，请截取一段短记录。')
    return micros


def parse_marker(fields, stamp):
    marker = {}
    for key in ('slot', 'source_slot'):
        if value := fields.get(key):
            if not re.fullmatch(r'[0-9]{1,7}', value):
                raise ValueError(f'{key} 须为非负整数。')
            marker[key] = int(value)
    if value := fields.get('state'):
        if value not in STATES:
            raise ValueError('state 只接受 normal、drop、hold 或 recover。')
        marker['state'] = value
    if value := fields.get('sample_id'):
        if not ID.fullmatch(value):
            raise ValueError('sample_id 须使用样本表中的短标识。')
        marker['sample_id'] = value
    if value := fields.get('captured_ms'):
        micros = parse_time(value)
        if micros > stamp:
            raise ValueError('同一时钟的 captured_ms 不能晚于 time_ms。')
        marker['captured_ms'] = micros / 1000
    return marker


def load_trace(path):
    with path.open('rb') as file:
        raw = file.read(MAX_TRACE + 1)
    if len(raw) > MAX_TRACE:
        raise ValueError('记录不能大于 512 KiB。')
    source = csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    header = source.fieldnames or []
    if len(set(header)) != len(header) or set(header) - set(COLUMNS) or not {'time_ms', 'hex'} <= set(header):
        raise ValueError('CSV 须含 time_ms、hex，其他可选列参见说明，列名不能重复。')
    entries = []
    for row in source:
        try:
            if len(entries) >= MAX_ROWS:
                raise ValueError(f'最多 {MAX_ROWS} 条记录。')
            if None in row or None in row.values():
                raise ValueError('该行的列数与表头不符。')
            row = {key: value.strip() for key, value in row.items()}
            stamp = parse_time(row['time_ms'])
            if entries and stamp < entries[-1].time_us:
                raise ValueError('时间不能倒退；同一时刻按 CSV 原顺序保留。')
            try:
                data = bytes.fromhex(row['hex'])
            except ValueError:
                raise ValueError('hex 须为成对十六进制；无发送时可留空。') from None
            if len(data) > MAX_UART_PER_STEP:
                raise ValueError(f'每条记录最多 {MAX_UART_PER_STEP} 字节。')
            entries.append(TraceRow(stamp, data, parse_marker(row, stamp)))
        except ValueError as error:
            raise ValueError(f'CSV 第 {source.line_num} 行：{error}') from None
    if not entries:
        raise ValueError('CSV 没有测试记录。')
    return entries


def unique_keys(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError('固件输入 JSON 含重复字段。')
    return value


def bounded_numbers(value, size, low, high):
    return (isinstance(value, list) and len(value) == size
            and all(type(n) in (float, int) and low <= n <= high for n in value))


def load_sensor_trace(path):
    with path.open('rb') as file:
        raw = file.read(MAX_SENSOR_TRACE + 1)
    if len(raw) > MAX_SENSOR_TRACE:
        raise ValueError('固件输入片段不能大于 4 MiB。')
    try:
        trace = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_keys)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ValueError('固件输入 JSON 无法读取。') from error
    if (not isinstance(trace, dict) or trace.get('format') != 'moonlight-firmware-trace'
            or type(trace.get('version')) is not int or trace['version'] != 1):
        raise ValueError('请选择波形面板「导出固件输入片段」所得 JSON。')
    step = trace.get('step_ms')
    source_start = trace.get('source_start_ms')
    rows = trace.get('samples')
    if (type(step) is not int or step not in SENSOR_STEPS or type(source_start) not in (float, int)
            or not 0 <= source_start <= 1e9 or not isinstance(rows, list)
            or not 1 <= len(rows) <= SENSOR_LIMIT_MS // step):
        raise ValueError('片段周期须为 4、8、16 或 32 ms 且连续覆盖 1 个周期至 5 秒。')
    entries = []
    for index, row in enumerate(rows):
        prefix = f'固件输入片段第 {index+1} 条：'
        if not isinstance(row, dict) or type(row.get('time_ms')) is not int or row['time_ms'] != index * step:
            raise ValueError(prefix + '时间必须从 0 起，严格按采集周期连续排列。')
        ir, sonar, encoder, payload = (row.get(key) for key in ('ir', 'sonar_m', 'encoder_rad', 'uart_in'))
        if not isinstance(ir, list) or len(ir) > 32 or not bounded_numbers(ir, len(ir), 0, 1):
            raise ValueError(prefix + '红外须为 0～1，最多 32 路。')
        if (not isinstance(sonar, dict) or len(sonar) > 16 or any(not SONAR_NAME.fullmatch(name)
                or type(n) not in (float, int) or not 0 <= n <= 100 for name, n in sonar.items())):
            raise ValueError(prefix + '超声波须为 0～100 米，最多 16 路，名称须为英文标识。')
        if not bounded_numbers(encoder, 2, -1e6, 1e6):
            raise ValueError(prefix + '编码器须为有效的左右轮角度（rad）。')
        if (not isinstance(payload, list) or len(payload) > MAX_UART_PER_STEP
                or any(type(n) is not int or not 0 <= n <= 255 for n in payload)):
            raise ValueError(prefix + 'UART 须为 0～255 的字节列表，每周期不超过 256 字节。')
        if index and (len(ir) != len(entries[0].sensors['ir']) or sonar.keys() != entries[0].sensors['sonar'].keys()):
            raise ValueError(prefix + '红外和超声波的通道必须与首个周期一致。')
        entries.append(TraceRow(row['time_ms'] * 1000, bytes(payload), {},
                                dict(ir=ir, sonar=sonar, encoder=dict(zip(SIDES, encoder)))))
    return entries, step, source_start


def validate_sensor_setup(entries, firmware, board):
    first = entries[0].sensors
    bindings = board['bindings']
    for role in bindings:
        if role.startswith('ir') and role[2:].isdigit() and int(role[2:]) >= len(first['ir']):
            raise ValueError(f'所选引脚绑定包含未录制的通道 {role}，请使用录制时的硬件配置。')
        if role.startswith('sonar_') and role.endswith('_trig') and role[:-5]+'_echo' in bindings:
            if role[6:-5] not in first['sonar']:
                raise ValueError('所选引脚绑定包含未录制的超声波通道，请使用录制时的硬件配置。')
    # Renode schedules one GPIO event per encoder edge. Reject obviously
    # inconsistent angles/CPR rather than letting a malformed trace flood it.
    sides = [side for side in SIDES if all(f'encoder_{side}_{suffix}' in bindings for suffix in ('a', 'b'))]
    counts = [round(first['encoder'][side] * firmware['encoder_cpr'] / math.tau) for side in sides]
    total = 0
    for entry in entries[1:]:
        reached = [round(entry.sensors['encoder'][side] * firmware['encoder_cpr'] / math.tau) for side in sides]
        edges = [abs(new-old) for old, new in zip(counts, reached)]
        total += sum(edges)
        if any(n > 4096 for n in edges) or total > 100_000:
            raise ValueError('片段的编码器边沿过多，请核对角度单位、每转计数或缩短片段。')
        counts = reached


@contextmanager
def trusted_adapter(path):
    path = path.resolve()
    if path.suffix.lower() != '.py' or not path.is_file():
        raise ValueError('请选择本队的 Python .py 适配脚本。')
    name = '_moonlight_vision_adapter_' + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError('无法载入适配脚本。')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    sys.modules[name] = module
    try:
        try:
            spec.loader.exec_module(module)
        except Exception as error:
            raise ValueError(f'无法启动适配脚本（{type(error).__name__}）：{error}') from error
        callback = getattr(module, 'on_slot', None)
        if not callable(callback):
            raise ValueError('适配脚本须定义 on_slot(image_bgr, metadata)。')
        yield callback
    finally:
        sys.modules.pop(name, None)
        if str(path.parent) in sys.path:
            sys.path.remove(str(path.parent))


def capture(path, mode, paced=False):
    import cv2
    import numpy as np

    dataset = load_dataset()
    files = {item['id']: SAMPLES/item['image'] for item in dataset['samples']}
    images = {}
    entries = []
    with trusted_adapter(path) as callback:
        started = time.monotonic()
        for meta in timeline(mode):
            key = meta['sample_id']
            if key is not None and key not in images:
                images[key] = cv2.imdecode(np.frombuffer(files[key].read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
                if images[key] is None or images[key].shape != (240, 320, 3):
                    raise ValueError(f'无法解码测试图像 {key}。')
            if paced:
                wait_until(started + meta['scheduled_ms']/1000)
            image = None if key is None else images[key].copy()
            try:
                output = callback(image, dict(meta))
            except Exception as error:
                raise ValueError(f"适配脚本在时隙 {meta['slot']} 失败（{type(error).__name__}）：{error}") from error
            if output is not None and (not isinstance(output, (bytes, bytearray)) or len(output) > MAX_UART_PER_STEP):
                raise ValueError(f"时隙 {meta['slot']}：on_slot 须返回 None 或不超过 {MAX_UART_PER_STEP} 字节的 bytes/bytearray。")
            marker = {key: meta[key] for key in COLUMNS[2:] if meta[key] is not None}
            entries.append(TraceRow(meta['scheduled_ms']*1000, bytes(output or b''), marker))
    return entries


def write_trace(path, entries):
    with path.open('x', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, COLUMNS)
        writer.writeheader()
        for row in entries:
            writer.writerow(dict(time_ms=f'{row.time_us/1000:.3f}', hex=row.payload.hex(' ').upper(), **row.marker))


def plan_timeline(entries, step, warmup, tail):
    if not 0 <= warmup <= 2048 or not step <= tail <= 2048 or warmup % step or tail % step:
        raise ValueError('预热须为 0～2048 ms，末段观察须为一步至 2048 ms，且均为步长的整数倍。')
    timeline = {}
    period = step * 1000
    for index, row in enumerate(entries, 1):
        tick = ((row.time_us + period - 1) // period) * step
        bucket = timeline.setdefault(tick, [bytearray(), []])
        bucket[0].extend(row.payload)
        bucket[1].append(index)
        if len(bucket[0]) > MAX_UART_PER_STEP:
            raise ValueError(f'{tick} ms 周期注入超过 {MAX_UART_PER_STEP} 字节；请改用更小步长或短记录。')
    stop = max(timeline) + tail
    if (warmup + stop)//step > 1824:
        raise ValueError('本次模拟周期数过多；请增大步长或缩短记录。')
    return timeline, stop


def capture_virtual(firmware, board, schedule, stop, step, warmup, wall_timeout, label, sensor_rows=None):
    print(f'正在运行{label}：独立的虚拟 STM32。', flush=True)
    neutral = dict(ir=[0]*32, sonar={}, encoder=dict(left=0, right=0))
    if sensor_rows:
        first = sensor_rows[0].sensors
        neutral = dict(ir=[0]*len(first['ir']), sonar={}, encoder=first['encoder'])
    bridge = FirmwareBridge(firmware, board, bench=True, normalized_ir=bool(sensor_rows))
    started, rx_count = time.monotonic(), 0
    next_progress = started + 12
    observed = []
    try:
        for start in range(-warmup, stop, step):
            now = time.monotonic()
            if now - started > wall_timeout:
                raise RuntimeError(f'{label}在 {start} ms 处超过 {wall_timeout} 秒的电脑等待上限；'
                                   '请缩短记录或用 --timeout-s 放宽。')
            if now >= next_progress:
                print(f'{label}已观察到 {start} / {stop} ms（虚拟回放时间）。', flush=True)
                next_progress = now + 12
            payload, _ = schedule.get(start, (b'', ()))
            sensors = (sensor_rows[min(start // step, len(sensor_rows)-1)].sensors
                       if sensor_rows and start >= 0 else neutral)
            output = bridge.step(sensors, step/1000, payload)
            reply = bytes(output['uart'])
            rx_count += len(reply)
            if rx_count > MAX_REPLY:
                raise RuntimeError(f'{label}虚拟输出超过 1 MiB，请缩短记录。')
            end = start + step
            if abs(output['virtual_s']*1000 - (end + warmup)) > 0.1:
                raise RuntimeError(f'{label}的 Renode 虚拟时间与计划不同。')
            observed.append((reply, output['motors']))
    finally:
        bridge.close()
    return observed


def visible(sample):
    reply, motors = sample
    return dict(uart_tx_bytes=len(reply), motors=motors)


def view_pwm(motor):
    return dict(duty=motor['duty'], mode=motor['modes']['pwm'], unavailable_reason=motor['fault'])


def view_drive(motor):
    return dict(value=motor['signed_drive'], in1=motor['in1'], in2=motor['in2'],
                in1_mode=motor['modes']['in1'], in2_mode=motor['modes']['in2'])


def uart_difference(a, b):
    for offset, (left, right) in enumerate(zip(a, b), 1):
        if left != right:
            return offset
    return min(len(a), len(b)) + 1  # First missing byte if one reply is a prefix.


def build_report(entries, schedule, step, warmup, tail, origin, peripheral, fingerprints, a, b, sensor_trace=None):
    first = dict.fromkeys(SIGNALS)
    first_replay = dict.fromkeys(SIGNALS)
    first_any = None
    first_replay_any = None
    visible_output = {'a': dict.fromkeys(SIGNALS, False), 'b': dict.fromkeys(SIGNALS, False)}
    history, last_marker, last_uart = [], None, None
    for index, (first_run, second_run) in enumerate(zip(a, b, strict=True)):
        start = index*step - warmup
        payload, rows = schedule.get(start, (b'', ()))
        for row_number in rows:
            row = entries[row_number-1]
            if row.marker:
                last_marker = dict(row=row_number, time_ms=row.time_us/1000, replay_ms=start, **row.marker)
            if row.payload:
                last_uart = dict(row=row_number, time_ms=row.time_us/1000, injection_ms=start, **row.marker)
        values_a, values_b = {'uart': first_run[0]}, {'uart': second_run[0]}
        for side in SIDES:
            for samples, values in ((first_run, values_a), (second_run, values_b)):
                values[side+'_pwm'] = view_pwm(samples[1][side])
                values[side+'_drive'] = view_drive(samples[1][side])
        changed = [signal for signal in SIGNALS if values_a[signal] != values_b[signal]]
        stamp = dict(sample_end_ms=start+step, phase='warmup' if start < 0 else 'replay',
                     last_frame_marker=last_marker, last_uart_input=last_uart)
        sensor_row = None
        if sensor_trace is not None:
            if start >= 0:
                sensor_row = min(start // step, len(entries)-1) + 1
            stamp.update(sensor_row=sensor_row, sensor_phase=(
                'warmup' if start < 0 else 'recorded' if start // step < len(entries) else 'hold'))
        context = stamp
        if changed and sensor_row is not None:
            row = entries[sensor_row-1]
            context = dict(stamp, sensor_input=dict(ir=row.sensors['ir'], sonar_m=row.sensors['sonar'],
                                                    encoder_rad=row.sensors['encoder']))
        if changed and first_any is None:
            first_any = dict(context, signals=changed)
        if changed and start >= 0 and first_replay_any is None:
            first_replay_any = dict(context, signals=changed)
        for signal in changed:
            if first[signal] is None or (start >= 0 and first_replay[signal] is None):
                before, after = values_a[signal], values_b[signal]
                entry = dict(context)
                if signal == 'uart':
                    entry['first_different_byte_1based'] = uart_difference(before, after)
                    before, after = {'byte_count': len(before)}, {'byte_count': len(after)}
                entry.update(a=before, b=after)
                if first[signal] is None:
                    first[signal] = entry
                if start >= 0 and first_replay[signal] is None:
                    first_replay[signal] = entry
        for key, values in (('a', values_a), ('b', values_b)):
            visible_output[key]['uart'] |= bool(values['uart'])
            for side in SIDES:
                visible_output[key][side+'_pwm'] |= values[side+'_pwm']['duty'] is not None
                visible_output[key][side+'_drive'] |= values[side+'_drive']['value'] is not None
        history.append(dict(stamp, injection_ms=start, trace_rows=list(rows), queued_uart_bytes=len(payload),
                            a=visible(first_run), b=visible(second_run), changed=changed))
    report = dict(schema='moonlight-vision-firmware-v1', model='STM32F103C8T6/Renode', source=origin,
                  step_ms=step, warmup_ms=warmup, tail_ms=tail, peripheral=peripheral,
                  clock='trace-relative-to-virtual-replay-start', image_sha256=fingerprints,
                  input_rows=[dict(row=i, time_ms=row.time_us/1000, byte_count=len(row.payload), **row.marker)
                              for i, row in enumerate(entries, 1)],
                  sampled_outputs=visible_output, first_difference=first_any, first_by_signal=first,
                  first_replay_difference=first_replay_any, first_replay_by_signal=first_replay, samples=history)
    if sensor_trace is not None:
        report['sensor_trace'] = sensor_trace
    return report


def compare(args, entries, origin, report_path, source_start=None):
    schedule, stop = plan_timeline(entries, args.step_ms, args.warmup_ms, args.tail_ms)
    if not 10 <= args.timeout_s <= 600:
        raise ValueError('每版固件的电脑等待上限须为 10～600 秒。')
    if report_path.exists():
        raise FileExistsError('报告目标已存在，请另选文件名。')
    if not report_path.parent.is_dir():
        raise ValueError('请先创建报告的上级目录。')
    firmware, board = load_setup(args, image_override=args.baseline)
    second, second_board = load_setup(args, image_override=args.candidate)
    if board != second_board or {k: v for k, v in firmware.items() if k != 'image'} != {k: v for k, v in second.items() if k != 'image'}:
        raise ValueError('读取时配置已变更；请在两个固件共享同一设置时重试。')
    sensor_trace, sensor_rows = None, None
    if source_start is not None:
        sensor_rows = entries
        validate_sensor_setup(entries, firmware, board)
        first = entries[0].sensors
        bindings = board['bindings']
        sensor_trace = dict(source_start_ms=source_start, recorded_cycles=len(entries),
                            recorded_ir_channels=len(first['ir']), recorded_sonars=list(first['sonar']),
                            bound_ir_channels=[i for i in range(len(first['ir'])) if f'ir{i}' in bindings],
                            bound_sonars=[name for name in first['sonar'] if all(
                                f'sonar_{name}_{suffix}' in bindings for suffix in ('trig','echo'))],
                            bound_encoders=[side for side in SIDES if all(
                                f'encoder_{side}_{suffix}' in bindings for suffix in ('a','b'))])
    with TemporaryDirectory(prefix='moonlight-vision-firmware-') as tmp:
        fingerprints, snapshots = {}, []
        for key, config in (('a', firmware), ('b', second)):
            source = Path(config['image'])
            image = Path(tmp)/(key + source.suffix.lower())
            with source.open('rb') as handle:
                content = handle.read(16*1024*1024 + 1)
            if not content or len(content) > 16*1024*1024:
                raise ValueError('镜像文件为空或超过 16 MiB。')
            image.write_bytes(content)
            validate_image(image, config)
            fingerprints[key] = sha256(content).hexdigest()
            snapshots.append(dict(config, image=str(image)))
        a = capture_virtual(snapshots[0], board, schedule, stop, args.step_ms, args.warmup_ms, args.timeout_s, '基线 A', sensor_rows)
        b = capture_virtual(snapshots[1], board, schedule, stop, args.step_ms, args.warmup_ms, args.timeout_s, '候选 B', sensor_rows)
    report = build_report(entries, schedule, args.step_ms, args.warmup_ms, args.tail_ms, origin,
                          firmware['vision_uart'], fingerprints, a, b, sensor_trace)
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    if difference := report['first_difference']:
        def describe(label, entry):
            if entry:
                print(f"{label}：{entry['sample_end_ms']} ms 采样，"
                      + '、'.join(SIGNAL_NAMES[key] for key in entry['signals']) + '。')
            else:
                print(f'{label}：本阶段未见差异。')

        in_replay = report['first_replay_difference']
        describe('最早差异（预热）' if difference['phase'] == 'warmup' else '最早差异（回放）', difference)
        if difference['phase'] == 'warmup':
            describe('回放开始后首个差异', in_replay)
        context = in_replay or difference
        marker, uart = context['last_frame_marker'], context['last_uart_input']
        if marker and 'slot' in marker:
            print(f"最近时隙 {marker['slot']}（不是因果判定）。")
        if uart:
            print(f"最近注入：记录 {uart['row']}，原时间 {uart['time_ms']:g} ms，虚拟入队 {uart['injection_ms']} ms。")
        if context.get('sensor_row') is not None:
            print(f"同周期传感器来自片段第 {context['sensor_row']} 条（不是因果判定）。")
        first_uart = report['first_replay_by_signal']['uart'] or report['first_by_signal']['uart']
        if first_uart:
            print(f"USART 差异：在 {first_uart['sample_end_ms']} ms 采样周期中，"
                  f"第 {first_uart['first_different_byte_1based']} 个发送字节首次不同或缺失。")
    else:
        print('两版在本次采样中未见差异。')
    for key, found in report['sampled_outputs'].items():
        missing = [SIGNAL_NAMES[signal] for signal in SIGNALS if not found[signal]]
        if missing:
            print(f'{key.upper()} 未观察到：' + '、'.join(missing) + '。')
    print(f'报告已保存：{report_path}。仅模拟信号，不代表车辆实际动作。')
    return 2 if report['first_difference'] else 0


def session_dir():
    folder = ROOT.parent/'output'/('vision-firmware-'+uuid4().hex[:10])
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def run_capture(args, destination):
    if destination.exists():
        raise FileExistsError('轨迹目标已存在，请另选文件名。')
    if not destination.parent.is_dir():
        raise ValueError('请先创建轨迹的上级目录。')
    results = capture(args.adapter, args.mode, args.paced)
    write_trace(destination, results)
    print(f'已保存 {len(results)} 条时隙、{sum(bool(row.payload) for row in results)} 条发送记录至本机：{destination}。')
    return results


def interactive():
    print('月光杯 · 视觉到固件联测')
    print('1. 用本队视觉程序采集并对照两版固件\n2. 导入已录好的串口 CSV 并对照\n3. 仅采集视觉输出'
          '\n4. 导入 Webots 固件输入片段并对照')
    choice = input('选择功能（1/2/3/4）：').strip()

    def path(prompt):
        value = input(prompt).strip().strip('"')
        if not value:
            raise ValueError('请指定文件。')
        return value

    if choice == '2':
        argv = ['compare', '--trace', path('串口 CSV：')]
        if input('由 OpenMV 记录？（y/N）：').strip().lower() == 'y':
            argv += ['--source', 'openmv']
    elif choice == '4':
        argv = ['compare-sensors', '--trace', path('Webots 固件输入片段（.json）：')]
    elif choice in ('1', '3'):
        print('适配脚本会作为本地 Python 代码执行，请只选择本队可信脚本。')
        adapter = path('视觉适配脚本（.py）：')
        mode = 'baseline' if input('是否使用正常轮？（y/N，默认故障轮）：').strip().lower() == 'y' else 'fault'
        argv = ['run' if choice == '1' else 'capture', '--adapter', adapter, '--mode', mode]
        if choice == '3':
            argv += ['--out', str(session_dir()/'input.csv')]
    else:
        raise ValueError('请输入 1、2、3 或 4。')
    if choice != '3':
        argv += ['--baseline', path('基线 A 固件镜像：'), '--candidate', path('候选 B 固件镜像：')]
    return argv


def main(argv=None):
    parser = argparse.ArgumentParser(description='仅在本机采集视觉输出或导入固件输入，并在两个独立虚拟 STM32 会话对照。')
    commands = parser.add_subparsers(dest='command', required=True)
    recording = commands.add_parser('capture', help='从已有连续帧采集本队视觉程序输出')
    replay = commands.add_parser('compare', help='导入本机串口 CSV，按同一时序对照两版固件')
    sensors = commands.add_parser('compare-sensors', help='导入 Webots 固件输入 JSON，对照同周期的传感器和串口')
    both = commands.add_parser('run', help='一次完成视觉采集和虚拟固件对照')
    for command in (recording, both):
        command.add_argument('--adapter', required=True, type=Path, help='本队可信 Python 适配脚本')
        command.add_argument('--mode', choices=MODES, default='fault')
        command.add_argument('--paced', action='store_true', help='按名义时隙节拍调用，仍记录计划时间')
    recording.add_argument('--out', type=Path, required=True)
    replay.add_argument('--source', choices=('recorded', 'openmv', 'computer'), default='recorded')
    for command in (replay, sensors):
        command.add_argument('--trace', type=Path, required=True)
        command.add_argument('--report', type=Path, help='指定报告，不能覆盖已有文件')
    for command in (replay, both, sensors):
        command.add_argument('--baseline', type=Path, required=True, help='基线 A 固件镜像')
        command.add_argument('--candidate', type=Path, required=True, help='候选 B 固件镜像')
        if command is not sensors:
            command.add_argument('--step-ms', type=int, choices=(8, 16, 32), default=32)
        command.add_argument('--warmup-ms', type=int, default=128)
        command.add_argument('--tail-ms', type=int, default=256)
        command.add_argument('--timeout-s', type=int, default=180, help='每版运行的电脑等待上限，单位秒（并非虚拟时间）')
        command.add_argument('--firmware-config', type=Path, default=ROOT/'firmware.json')
        command.add_argument('--board-config', type=Path, default=ROOT/'board.json')
    try:
        argv = sys.argv[1:] if argv is None else argv
        args = parser.parse_args(argv or interactive())
        if args.command == 'capture':
            run_capture(args, args.out)
            return 0
        if args.command == 'compare':
            entries = load_trace(args.trace)
            return compare(args, entries, args.source, args.report or session_dir()/'report.json')
        if args.command == 'compare-sensors':
            entries, args.step_ms, source_start = load_sensor_trace(args.trace)
            return compare(args, entries, 'webots-sensors', args.report or session_dir()/'report.json',
                           source_start=source_start)
        folder = session_dir()
        entries = run_capture(args, folder/'input.csv')
        return compare(args, entries, 'vision-'+args.mode, folder/'report.json')
    except (KeyboardInterrupt, EOFError):
        print('已取消联测。', file=sys.stderr)
        return 130
    except (ValueError, KeyError, ImportError, OSError, RuntimeError, csv.Error) as error:
        print(f'联测未完成：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
