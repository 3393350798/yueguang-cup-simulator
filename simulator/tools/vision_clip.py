"""Record a short loopback camera clip and compare two team adapters locally."""
import argparse
from decimal import Decimal
from hashlib import sha256
from http.client import HTTPConnection, HTTPException
from pathlib import Path
import re
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from vision_replay import CHANNELS, COLORS, grade, read_json, write_json


FORMAT = 'moonlight-camera-clip'
PERIOD_US = 64_000  # Webots vision_link frames are published every 64 ms.
MAX_FRAMES = 160
MAX_PNG = 4 * 1024 * 1024
MAX_CLIP = 64 * 1024 * 1024
PNG_PREFIX = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'


def bounded_file(path, limit):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f'{path.name} 超过大小上限。')
    return data


def check_png(data):
    if len(data) < 45 or not data.startswith(PNG_PREFIX):
        raise ValueError('相机接口没有返回有效的 PNG 文件。')
    width, height = struct.unpack('>II', data[16:24])
    if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4_194_304):
        raise ValueError('相机图像尺寸超出录制上限。')


def fetch_frame(port):
    # A direct HTTP connection cannot use a proxy or follow a redirect.
    connection = HTTPConnection('127.0.0.1', port, timeout=0.4)
    try:
        connection.request('GET', '/frame.png', headers={'Cache-Control': 'no-cache'})
        with connection.getresponse() as response:
            if response.status == 503:
                return None
            if response.status != 200 or response.getheader('Content-Type') != 'image/png':
                raise ValueError('该端口没有返回仿真相机画面，请检查本机联调端口。')
            stamp = response.getheader('X-Sim-Time', '')
            if not re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,6})?', stamp):
                raise ValueError('相机缺少有效的 X-Sim-Time 仿真时间。')
            data = response.read(MAX_PNG + 1)
            if len(data) > MAX_PNG:
                raise ValueError('单帧 PNG 超过 4 MiB。')
            check_png(data)
            return int(Decimal(stamp) * 1_000_000), data
    finally:
        connection.close()


def frame_fields(index, stamp, first, previous):
    interval = stamp - previous if previous is not None else 0
    return {'slot': index, 'image': f'frame-{index:04d}.png', 'sim_time_us': stamp,
            'scheduled_ms': (stamp - first) / 1000,
            'capture_gap_ms': interval / 1000 if interval > PERIOD_US else None}


def record(destination, port, seconds):
    if not 1024 <= port <= 65535:
        raise ValueError('端口须在 1024 至 65535 之间。')
    if destination.exists():
        raise ValueError('录制目录已存在，请换一个新目录。')
    frames, images = [], []
    total = 0
    deadline = time.monotonic() + max(12, 4 * seconds)
    print(f'等待本机相机，录制 {seconds} 秒仿真时间；请保持 Webots 运行。', flush=True)
    while time.monotonic() < deadline:
        try:
            item = fetch_frame(port)
        except (OSError, HTTPException):
            item = None
        if item is not None:
            stamp, data = item
            previous = frames[-1]['sim_time_us'] if frames else None
            if previous is not None and stamp < previous:
                raise ValueError('录制中仿真时间倒退，请在同一次仿真中重新录制。')
            if previous != stamp:
                first = frames[0]['sim_time_us'] if frames else stamp
                row = frame_fields(len(frames), stamp, first, previous)
                total += len(data)
                if total > MAX_CLIP or len(frames) >= MAX_FRAMES:
                    raise ValueError('录制超过 64 MiB 或帧数上限，请录更短片段或缩小相机分辨率。')
                frames.append(dict(row, sha256=sha256(data).hexdigest()))
                images.append(data)
                if row['scheduled_ms'] >= seconds * 1000:
                    break
        time.sleep(0.02)
    else:
        if not frames:
            raise ValueError('未收到相机画面；请开启本机联调端口、核对端口号并让 Webots 运行。')
        raise ValueError('仿真时间未走完，未生成片段；请检查暂停状态或改用较短时长。')
    if len(frames) < 2 or frames[-1]['scheduled_ms'] > 9000:
        raise ValueError('相机采集时间跨度异常，请确认仿真连续运行后重试。')
    destination.mkdir(parents=True, exist_ok=False)
    for row, data in zip(frames, images):
        with (destination / row['image']).open('xb') as stream:
            stream.write(data)
    write_json(destination / 'clip.json', {'format': FORMAT, 'version': 1,
               'camera_interval_ms': PERIOD_US // 1000, 'frames': frames})
    gaps = sum(row['capture_gap_ms'] is not None for row in frames)
    print(f'已保存 {len(frames)} 张原始 PNG（{frames[-1]["scheduled_ms"]:.3f} ms），发现 {gaps} 处采集空档：{destination}')


def load_clip(directory):
    manifest = read_json(directory / 'clip.json')
    if (not isinstance(manifest, dict) or manifest.get('format') != FORMAT
            or type(manifest.get('version')) is not int or manifest['version'] != 1
            or manifest.get('camera_interval_ms') != PERIOD_US // 1000):
        raise ValueError('请选择 record 生成的相机片段目录。')
    frames = manifest.get('frames')
    if not isinstance(frames, list) or not 2 <= len(frames) <= MAX_FRAMES:
        raise ValueError('片段没有有效的帧表。')
    first = previous = None
    for index, row in enumerate(frames):
        stamp = row.get('sim_time_us') if isinstance(row, dict) else None
        if type(stamp) is not int or not 0 <= stamp < 10**16 or (previous is not None and stamp <= previous):
            raise ValueError(f'片段第 {index} 帧的时间不连续递增。')
        if first is None:
            first = stamp
        expected = frame_fields(index, stamp, first, previous)
        digest = row.get('sha256')
        if (row != dict(expected, sha256=digest) or expected['scheduled_ms'] > 9000
                or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise ValueError(f'片段第 {index} 帧的元数据不一致。')
        previous = stamp
    return frames


def slot_range(value):
    if not re.fullmatch(r'[0-9]{1,3}(?::[0-9]{1,3})?', value):
        raise argparse.ArgumentTypeError('用帧号或 起始帧:末帧 表示范围（含两端，从 0 开始）。')
    first, _, last = value.partition(':')
    start, end = int(first), int(last or first)
    if start > end:
        raise argparse.ArgumentTypeError('结束帧不能早于起始帧。')
    return start, end


def plan_for(frames, drop=None, hold=None):
    faults = {}
    for interval, state in ((drop, 'drop'), (hold, 'hold')):
        if interval is None:
            continue
        start, end = interval
        if start < 1 or end >= len(frames) - 1:
            raise ValueError('故障范围不能包含首帧或末帧，须留出正常开始和恢复的画面。')
        for index in range(start, end + 1):
            if index in faults:
                raise ValueError('主动丢帧与重发旧帧的范围不能重叠。')
            faults[index] = state
    last = 0
    for frame in frames:
        index = frame['slot']
        state = faults.get(index, 'recover' if index - 1 in faults else 'normal')
        if state == 'drop':
            source = None
        elif state == 'hold':
            source = frames[last]
        else:
            source = frame
            last = index
        yield {'slot': index, 'scheduled_ms': frame['scheduled_ms'], 'state': state,
               'sample_id': Path(source['image']).stem if source is not None else None,
               'source_slot': source['slot'] if source is not None else None,
               'captured_ms': source['scheduled_ms'] if source is not None else None,
               'source_sim_time_us': source['sim_time_us'] if source is not None else None,
               'capture_gap_ms': frame['capture_gap_ms']}


def observations(data):
    if not isinstance(data, dict) or set(data) - set(CHANNELS):
        raise ValueError('识别结果只接受 tag_id 和 block_color。')
    tag, color = data.get('tag_id'), data.get('block_color')
    if tag is not None and (type(tag) is not int or not 0 <= tag <= 65535):
        raise ValueError('tag_id 须为 0～65535 的整数或 null。')
    if color is not None and (not isinstance(color, str) or color not in COLORS):
        raise ValueError('block_color 须为 red、yellow、blue 或 null。')
    return data


def normalize(value):
    signals = {}
    if isinstance(value, dict):
        signals = observations({key: val for key, val in value.items() if key != 'uart'})
        value = value.get('uart')
    if value is not None and not isinstance(value, (bytes, bytearray)):
        raise ValueError('适配器须返回结构化结果、串口字节或 None。')
    if value is not None and len(value) > 256:
        raise ValueError('每帧串口输出不能超过 256 字节。')
    uart = None if not value else {'bytes': len(value), 'sha256': sha256(value).hexdigest()}
    return dict(signals, uart=uart)


def run_adapter(directory, frames, plan, adapter, paced):
    import cv2
    import numpy as np

    from vision_firmware import trusted_adapter
    from vision_sequence import wait_until

    images, total = [], 0
    for row in frames:
        data = bounded_file(directory / row['image'], MAX_PNG)
        total += len(data)
        if total > MAX_CLIP or sha256(data).hexdigest() != row['sha256']:
            raise ValueError(f'{row["image"]} 校验不符，或片段超过 64 MiB。')
        check_png(data)
        images.append(data)
    results = []
    with trusted_adapter(adapter) as callback:
        started = time.monotonic()
        for meta in plan:
            if paced:
                wait_until(started + meta['scheduled_ms'] / 1000)
            source = meta['source_slot']
            if source is None:
                results.append(None)
                continue
            image = cv2.imdecode(np.frombuffer(images[source], dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f'无法解码第 {source} 帧。')
            try:
                results.append(normalize(callback(image, dict(meta))))
            except Exception as error:
                raise ValueError(f'第 {meta["slot"]} 帧：{type(error).__name__}：{error}') from error
    return results


def read_labels(path, count):
    if path is None:
        return {}
    labels = read_json(path)
    if not isinstance(labels, dict) or any(not re.fullmatch(r'0|[1-9][0-9]{0,2}', key)
                                          or int(key) >= count for key in labels):
        raise ValueError('标注须用片段内的原始帧号作为 JSON 键。')
    return {int(key): observations(row) for key, row in labels.items()}


def score_results(plan, labels, first, second):
    details = []
    first_difference = agreement = None
    summary = {side: {field: {'first_problem': None, 'first_missed': None,
                              'first_recovery': None} for field in CHANNELS}
               for side in ('a', 'b')}
    missing = object()
    for row, a, b in zip(plan, first, second):
        index = row['slot']
        source = row['source_slot']
        expected = labels.get(source, {})
        details.append(dict(row, a=a, b=b, differences=[], expected=expected, checks={'a': {}, 'b': {}}))
        if source is None:
            continue
        differences = [field for field in (*CHANNELS, 'uart') if a.get(field, missing) != b.get(field, missing)]
        details[-1]['differences'] = differences
        marker = {'slot': index, 'scheduled_ms': row['scheduled_ms'], 'source_slot': source}
        if differences and first_difference is None:
            first_difference = dict(marker, fields=differences)
        elif not differences and first_difference is not None and agreement is None:
            agreement = marker
        for side, observed in (('a', a), ('b', b)):
            for field, truth in expected.items():
                verdict = grade(truth, observed.get(field), field in observed)
                details[-1]['checks'][side][field] = verdict
                result = summary[side][field]
                if verdict in ('missed', 'wrong_value', 'false_positive') and result['first_problem'] is None:
                    result['first_problem'] = dict(marker, result=verdict)
                if verdict == 'missed' and result['first_missed'] is None:
                    result['first_missed'] = marker
                problem = result['first_problem']
                if (verdict == 'match' and row['state'] != 'hold' and problem is not None
                        and truth == labels[problem['source_slot']][field] and result['first_recovery'] is None):
                    result['first_recovery'] = marker
    return {'first_difference': first_difference, 'first_agreement_after_difference': agreement,
            'observations': summary, 'rows': details}


def compare(args):
    directory = args.directory.resolve()
    if args.output.exists():
        raise ValueError('对照报告已存在，请换一个新文件名。')
    frames = load_clip(directory)
    plan = list(plan_for(frames, args.drop, args.hold))
    labels = read_labels(args.labels, len(frames))
    inputs = [path.resolve() for path in (args.adapter_a, args.adapter_b)]
    with TemporaryDirectory(prefix='moonlight-vision-') as temp:
        temp = Path(temp)
        write_json(temp / 'plan.json', {'frames': frames, 'plan': plan})
        results = []
        for side, adapter in zip(('A', 'B'), inputs):
            print(f'回放到 {side} 版适配器……', flush=True)
            report = temp / f'{side}.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(directory), str(temp / 'plan.json'),
                       str(adapter), str(report), str(int(args.paced))]
            try:
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=120)
            except subprocess.TimeoutExpired:
                raise ValueError(f'{side} 版适配器超过 120 秒未执行完。') from None
            if completed.returncode != 0:
                raise ValueError(f'{side} 版适配器未完成；请检查上方错误。')
            results.append(read_json(report))
    report = score_results(plan, labels, *results)
    gaps = [dict(slot=row['slot'], scheduled_ms=row['scheduled_ms'], interval_ms=row['capture_gap_ms'])
            for row in frames if row['capture_gap_ms'] is not None]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, dict(report, format='moonlight-camera-comparison', version=1, paced=args.paced,
                                capture_gaps=gaps, fault_plan={'drop': args.drop, 'hold': args.hold}))
    difference = report['first_difference']
    if difference is None:
        print('两版的已提交输出未发现差异。')
    else:
        print(f'首次差异：第 {difference["slot"]} 帧 / {difference["scheduled_ms"]:.3f} ms，'
              + '、'.join(difference['fields']))
        recovery = report['first_agreement_after_difference']
        if recovery is not None:
            print(f'首次重新一致：第 {recovery["slot"]} 帧 / {recovery["scheduled_ms"]:.3f} ms。')
    print(f'采集空档 {len(gaps)} 处；逐帧结果：{args.output}')
    if not labels:
        print('未提供人工标注，仅比较输出差异。')
    else:
        for side, observations_for_side in report['observations'].items():
            for field, observation in observations_for_side.items():
                missed, recovered = observation['first_missed'], observation['first_recovery']
                if missed is not None:
                    print(f'{side.upper()} 版{CHANNELS[field]}首次漏检：第 {missed["slot"]} 帧。')
                if recovered is not None:
                    print(f'{side.upper()} 版{CHANNELS[field]}首次恢复：第 {recovered["slot"]} 帧。')


def add_commands(commands):
    capture = commands.add_parser('record', help='录制本机 Webots 原始相机画面')
    capture.add_argument('destination', type=Path, help='新建本机录制目录')
    capture.add_argument('--port', type=int, default=8765)
    capture.add_argument('--seconds', type=int, choices=range(2, 9), default=4, metavar='2–8')
    capture.set_defaults(clip_action=lambda args: record(args.destination, args.port, args.seconds))
    comparison = commands.add_parser('compare-clip', help='用同一相机片段对照两版视觉程序')
    comparison.add_argument('directory', type=Path)
    comparison.add_argument('adapter_a', type=Path)
    comparison.add_argument('adapter_b', type=Path)
    comparison.add_argument('--output', required=True, type=Path, help='本机新建对照报告 JSON')
    comparison.add_argument('--labels', type=Path, help='队伍自建标注 JSON')
    comparison.add_argument('--drop', type=slot_range, help='主动丢帧的范围，如 5:7')
    comparison.add_argument('--hold', type=slot_range, help='主动重发最后一张已发送画面的范围，如 12:15')
    comparison.add_argument('--paced', action='store_true', help='按实际经过时间调度，用于比较超时处理')
    comparison.set_defaults(clip_action=compare)


if __name__ == '__main__':
    if len(sys.argv) != 7 or sys.argv[1] != '--worker':
        raise SystemExit('请通过 scripts/vision-sequence.bat 使用相机片段功能。')
    directory, plan_path, adapter, result_path = map(Path, sys.argv[2:6])
    try:
        inputs = read_json(plan_path)
        write_json(result_path, run_adapter(directory, inputs['frames'], inputs['plan'], adapter, sys.argv[6] == '1'))
    except (OSError, ValueError, ImportError) as error:
        print(f'视觉适配器回放失败：{error}', file=sys.stderr)
        raise SystemExit(1)
