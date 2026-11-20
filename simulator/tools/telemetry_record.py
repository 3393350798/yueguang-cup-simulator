"""按设备自身时钟读取实车日志，生成联调时间线和波形 CSV。"""
import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re
from statistics import median
import sys
from time import perf_counter_ns

from telemetry_compare import SIGNALS
from telemetry_log import EVENTS, IDENTIFIER


MAX_FILE = 48 * 1024 * 1024
MAX_ROWS = 120_000
MAX_COLS = 192
SOURCE_LABELS = {'pc': '电脑', 'openmv': 'OpenMV', 'stm32': 'STM32'}
EVENT_LABELS = {'frame_received': '到帧', 'vision_done': '识别完成', 'command_sent': '指令发送',
                'reply_observed': '收到回执', 'command_received': '收到指令', 'reply_sent': '发送回执'}
CHAINS = {'pc': EVENTS[:4], 'openmv': EVENTS[:3], 'stm32': EVENTS[4:6]}
TIME_UNITS = {'s': 0, 'ms': -3, 'us': -6, 'ns': -9}
CAPTURE_MAX_BYTES = 2_000_000
CAPTURE_MAX_LINE = 2048
CAPTURE_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,47}\Z')
CAPTURE_TOKEN = re.compile(r'(?:[A-Za-z_][A-Za-z0-9_.:-]{0,95}|[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d{1,3})?)?\Z', re.ASCII)


@dataclass
class Source:
    role: str
    timestamps: list
    events: list
    samples: list
    unspecified: int
    unknown: int
    case_samples: dict | None = None


def bounded(path, size):
    path = Path(path)
    if not path.is_file():
        raise ValueError('找不到输入文件。')
    if path.stat().st_size > size:
        raise ValueError(f'输入文件过大（上限 {size // 1024} KiB）。')
    return path


def finite(value, name):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f'{name}应为有限数值。') from error
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError(f'{name}应为有限数值。')
    return result


def mapping_profile(path):
    if path is None:
        return None
    try:
        raw = json.loads(bounded(path, 64 * 1024).read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('字段映射应为 UTF-8 JSON。') from error
    if not isinstance(raw, dict) or set(raw) - {'time', 'event', 'case_id', 'events', 'signals'}:
        raise ValueError('字段映射含不支持的字段，参照空白模板。')
    return raw


def mapping_for(headers, supplied):
    cols = set(headers)
    data = supplied if supplied is not None else {
        'time': {'column': 'time_s', 'unit': 's'},
        'event': 'event' if 'event' in cols else None,
        'case_id': 'case_id' if 'case_id' in cols else None,
        'signals': {key: key for key in SIGNALS if key in cols},
    }

    def column(name):
        if not isinstance(name, str) or name not in cols:
            raise ValueError(f'映射的列在 CSV 表头中找不到：{str(name)[:100]}')
        return headers.index(name)

    time = data.get('time')
    if (not isinstance(time, dict) or set(time) != {'column', 'unit'}
            or not isinstance(time['unit'], str) or time['unit'] not in TIME_UNITS):
        raise ValueError('time 应指定 column、unit；单位可选 s/ms/us/ns。')
    event = data.get('event')
    case_id = data.get('case_id')
    events = data.get('events', {})
    if (not isinstance(events, dict) or len(events) > 64 or any(
            not key or len(key) > 64 or value not in EVENTS for key, value in events.items())):
        raise ValueError('events 应将原事件名称映射为文档中的标准事件。')
    raw_signals = data.get('signals', {})
    if not isinstance(raw_signals, dict) or set(raw_signals) - set(SIGNALS):
        raise ValueError('signals 的键应为波形工具支持的信号名。')
    signals = {}
    for name, entry in raw_signals.items():
        if isinstance(entry, str):
            entry = {'column': entry}
        if not isinstance(entry, dict) or set(entry) - {'column', 'scale', 'offset'}:
            raise ValueError('信号映射应为列名或含 column/scale/offset 的对象。')
        scale, offset = finite(entry.get('scale', 1), '倍率'), finite(entry.get('offset', 0), '偏移')
        if scale == 0:
            raise ValueError('信号倍率不能为空或零。')
        signals[name] = column(entry.get('column')), scale, offset
    return (column(time['column']), TIME_UNITS[time['unit']],
            column(event) if event is not None else None,
            column(case_id) if case_id is not None else None, events, signals)


def load_source(path, role, mapping=None, *, retain_case_samples=False):
    if role not in SOURCE_LABELS:
        raise ValueError('记录来源应是 pc、openmv 或 stm32。')
    timestamps, events, samples = [], [], []
    case_samples = {} if retain_case_samples else None
    unspecified = unknown = 0
    first = previous = None
    with bounded(path, MAX_FILE).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.reader(stream, strict=True)
        try:
            header = [field.strip() for field in next(reader, [])]
            if (not header or len(header) > MAX_COLS or not all(header)
                    or len(set(header)) != len(header)):
                raise ValueError('CSV 表头需非空、列名唯一，且最多 192 列。')
            t_col, shift, ev_col, id_col, aliases, signals = mapping_for(header, mapping_profile(mapping))
            for row in reader:
                if not row or not any(v.strip() for v in row):
                    continue
                if len(timestamps) >= MAX_ROWS:
                    raise ValueError(f'CSV 最多 {MAX_ROWS} 行；请只保留待对照的赛段。')
                if len(row) != len(header):
                    raise ValueError(f'第 {reader.line_num} 行列数与表头不符。')
                try:
                    field = row[t_col].strip()
                    if not field or len(field) > 64:
                        raise ValueError('时间为空或过长。')
                    try:
                        ts = Decimal(field)
                        if not ts.is_finite() or not 0 <= ts <= Decimal('1e20'):
                            raise ValueError('时间必须是非负有限数值。')
                        if ts and ts.adjusted() < -30:
                            raise ValueError('时间精度超出支持范围。')
                    except InvalidOperation as error:
                        raise ValueError('时间不是有效数字。') from error
                    if previous is not None and ts < previous:
                        raise ValueError('时间倒退；请按单一设备和单次开机拆分记录，再按时序输入。')
                    previous = ts
                    if first is None:
                        first = ts
                    elapsed = (ts - first).scaleb(shift)
                    if elapsed > 1e10:
                        raise ValueError('录制跨度超出支持范围；请检查时间单位。')
                    timestamps.append(elapsed)
                    raw_event = row[ev_col].strip() if ev_col is not None else ''
                    event = aliases.get(raw_event, raw_event)
                    if event in EVENT_LABELS:
                        ident = row[id_col].strip() if id_col is not None else ''
                        if not IDENTIFIER.fullmatch(ident):
                            ident = None
                            unspecified += 1
                        events.append((elapsed, event, ident))
                    elif event and event != 'sample':
                        unknown += 1

                    values = {}
                    for name, (col, scale, offset) in signals.items():
                        raw = row[col].strip()
                        if not raw:
                            continue
                        value = finite(raw, name) * scale + offset
                        _, _, lo, hi = SIGNALS[name]
                        if not lo <= value <= hi:
                            if not lo - 1e-12 <= value <= hi + 1e-12:
                                raise ValueError(f'{name}换算后不在 {lo:g} 到 {hi:g} 内。')
                            value = min(hi, max(lo, value))
                        values[name] = value
                    if case_samples is not None and event == 'sample' and id_col is not None and values:
                        ident = row[id_col].strip()
                        if IDENTIFIER.fullmatch(ident):
                            case_samples.setdefault(ident, []).append((elapsed, values.copy()))
                    if values or (event == 'sample' and signals) or (ev_col is None and signals):
                        if samples and samples[-1][0] == elapsed:
                            merged = samples[-1][1]
                            if any(key in merged and merged[key] != number for key, number in values.items()):
                                raise ValueError('同一时间/信号有冲突值，请检查记录精度。')
                            merged.update(values)
                        else:
                            samples.append((elapsed, values))
                except ValueError as error:
                    raise ValueError(f'第 {reader.line_num} 行：{error}') from error
        except (UnicodeError, csv.Error) as error:
            raise ValueError('请输入 UTF-8 编码的逗号分隔 CSV。') from error
    if not timestamps:
        raise ValueError('CSV 没有录制行。')
    return Source(role, timestamps, events, samples, unspecified, unknown, case_samples)


def seconds(value):
    return float(value)


def timeline(source):
    chain = CHAINS[source.role]
    cases = {}
    totals = dict.fromkeys(EVENT_LABELS, 0)
    for elapsed, event, key in source.events:
        totals[event] += 1
        if key and event in chain:
            cases.setdefault(key, {}).setdefault(event, []).append(elapsed)
    result = []
    timings = {}
    for ident, marks in cases.items():
        record = {'id': ident,
                  'events_s': {key: [seconds(x) for x in marks.get(key, [])] for key in chain},
                  'segments': []}
        for start, end in zip(chain, chain[1:]):
            x, y = marks.get(start, []), marks.get(end, [])
            if len(x) > 1 or len(y) > 1:
                state, delta = 'ambiguous', None
            elif not x or not y:
                state, delta = 'unobserved', None
            elif y[0] < x[0]:
                state, delta = 'out_of_order', None
            else:
                state, delta = 'measured', round(seconds(y[0] - x[0]) * 1000, 9)
                timings.setdefault((start, end), []).append(delta)
            record['segments'].append(dict(start=start, end=end, state=state, ms=delta))
        result.append(record)
    stats = []
    for start, end in zip(chain, chain[1:]):
        values = timings.get((start, end), [])
        stats.append(dict(start=start, end=end, measured=len(values), median_ms=median(values) if values else None,
                          max_ms=max(values) if values else None))
    return dict(source=source.role, clock='source_local_elapsed_s', rows=len(source.timestamps),
                duration_s=seconds(source.timestamps[-1]), sample_rows=len(source.samples), event_counts=totals,
                unpaired_events=source.unspecified, unknown_events=source.unknown, stats=stats, cases=result)


def save_wave(source, path):
    seen = {name for _, values in source.samples for name in values}
    names = [name for name in SIGNALS if name in seen]
    if len(source.samples) < 2 or not names:
        raise ValueError('需要至少两个采样时刻，且至少一路有值，才能用于波形对照。')
    previous = None
    for elapsed, _ in source.samples:
        now = seconds(elapsed)
        if previous is not None and previous >= now:
            raise ValueError('采样时间精度不足以写入波形，请截取较短的动作记录。')
        previous = now
    with Path(path).open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('time_s', *names))
        for elapsed, values in source.samples:
            writer.writerow((str(elapsed), *(values.get(name) for name in names)))


def save_report(records, out):
    report = dict(schema='moonlight-real-timeline-v1', cross_device_delay_measured=False, sources=records)
    with Path(out).open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def template(path):
    data = {'time': {'column': 'tick_ms', 'unit': 'ms'}, 'event': 'action', 'case_id': 'seq',
            'events': {'frame': 'frame_received', 'processed': 'vision_done', 'sent': 'command_sent',
                       'reply': 'reply_observed', 'rx': 'command_received', 'tx': 'reply_sent'},
            'signals': {'pwm_left': {'column': 'left_percent', 'scale': .01},
                        'speed_left_m_s': 'left_speed'}}
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def capture_debug(port, baud, duration, fields, output):
    """接收独立调试口的文本 CSV 行；不使用队伍控制口，不写串口。"""
    if not re.fullmatch(r'COM[1-9][0-9]{0,2}', port, re.IGNORECASE):
        raise ValueError('请填写 Windows 的独立调试 COM 口，例如 COM5。')
    if not 300 <= baud <= 3_000_000 or not 1 <= duration <= 60:
        raise ValueError('波特率须为 300～3000000，录制时长须为 1～60 秒。')
    columns = [name.strip() for name in fields.split(',')]
    if (not 1 <= len(columns) <= 64 or len(set(columns)) != len(columns) or 'rx_time_s' in columns
            or any(not CAPTURE_NAME.fullmatch(name) for name in columns)):
        raise ValueError('请给出逗号分隔、唯一的英文列名；rx_time_s 为上位机保留列。')
    out = Path(output)
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError('输出须在已存在目录中，文件名不能已存在。')
    try:
        import serial
    except ImportError as error:
        raise ValueError('独立调试口录制需要安装 requirements/serial.txt。') from error
    channel = serial.Serial(port=None, baudrate=baud, timeout=.05, rtscts=False, dsrdtr=False, xonxoff=False)
    channel.dtr = channel.rts = False
    channel.port = port.upper()
    recorded = []
    data = bytearray()
    discard = True  # 可能从一行中间开始，先丢弃第一个换行前的片段。
    bad = count = 0
    stop = 'time'
    try:
        with channel:
            start = perf_counter_ns()
            while perf_counter_ns() - start < duration * 10**9:
                raw = channel.read(min(1024, max(channel.in_waiting, 1)))
                rx = perf_counter_ns() - start
                count += len(raw)
                for value in raw:
                    if value != 10:
                        if not discard:
                            if len(data) >= CAPTURE_MAX_LINE:
                                data.clear()
                                discard = True
                                bad += 1
                            else:
                                data.append(value)
                        continue
                    if discard:
                        discard = False
                        continue
                    try:
                        row = [part.strip() for part in data.decode('ascii').split(',')]
                    except UnicodeDecodeError:
                        row = []
                    data.clear()
                    if row == columns:
                        continue
                    if len(row) != len(columns) or not any(row) or any(not CAPTURE_TOKEN.fullmatch(x) for x in row):
                        bad += 1
                        continue
                    recorded.append((f'{rx // 10**9}.{rx % 10**9:09d}', *row))
                if count >= CAPTURE_MAX_BYTES or len(recorded) >= MAX_ROWS:
                    stop = 'limit'
                    break
    except serial.SerialException as error:
        raise OSError('独立调试口打开或读取失败；请核对端口权限、连接和占用。不会尝试其他端口。') from error
    if not recorded:
        raise ValueError(f'未录到完整且符合列数的文本行（不合格 {bad} 行）；未创建文件。')
    with out.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(('rx_time_s', *columns))
        writer.writerows(recorded[:MAX_ROWS])
    print(f'已保存 {min(len(recorded), MAX_ROWS)} 行；不合格 {bad} 行。' +
          ('达到录制上限。' if stop == 'limit' else '录制结束。'))


STATES = {'unobserved': '未观测', 'ambiguous': '有重复事件', 'out_of_order': '顺序异常'}


def describe(records, limit=80):
    text = ['每份记录各用自己的时钟；本工具不计算跨设备的延迟。',
            '只按同一文件、相同动作编号配对；未观测不等于没有发生。']
    for row in records:
        text.extend(('', f"{SOURCE_LABELS[row['source']]}｜{row['rows']} 行；"
                     f"{row['duration_s']:.3f} 秒；波形采样 {row['sample_rows']} 点",
                     f"缺少有效动作编号的事件：{row['unpaired_events']}；未映射事件：{row['unknown_events']}"))
        for stat in row['stats']:
            label = EVENT_LABELS[stat['start']] + ' → ' + EVENT_LABELS[stat['end']]
            if stat['measured']:
                text.append(f"  {label}：{stat['measured']} 次，中位 {stat['median_ms']:.3f} ms，"
                            f"最大 {stat['max_ms']:.3f} ms")
            else:
                text.append(f'  {label}：未观测到可配对的事件')
        for case in row['cases'][-limit:]:
            events = ', '.join(f'{EVENT_LABELS[event]} {times[0]:.6f}s'+(f'×{len(times)}' if len(times)>1 else '')
                               for event, times in case['events_s'].items() if times)
            segments = '；'.join(f"{EVENT_LABELS[seg['start']]} → {EVENT_LABELS[seg['end']]} " +
                                 (f"{seg['ms']:.3f} ms" if seg['state'] == 'measured' else STATES[seg['state']])
                                 for seg in case['segments'])
            text.extend((f"  #{case['id']}：{events}", f'    {segments}'))
        if len(row['cases']) > limit:
            text.append(f'仅预览最近 {limit} 个动作；JSON 报告包含全部动作。')
    return '\n'.join(text)


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('月光杯 · 实车录制与联调')
    root.minsize(800, 510)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    form = ttk.Frame(root, padding=12)
    form.grid(sticky='ew')
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)
    paths = {role: (tk.StringVar(), tk.StringVar()) for role in SOURCE_LABELS}
    sources = []
    reports = []
    status = tk.StringVar(value='请选择已保存的 CSV。为空的来源不会读取；字段映射可留空。')

    def choose(var, mapping=False):
        suffix = ('字段映射', '*.json') if mapping else ('CSV 日志', '*.csv')
        chosen = filedialog.askopenfilename(parent=root, filetypes=[suffix])
        if chosen:
            var.set(chosen)

    ttk.Label(form, text='各设备的 CSV 记录').grid(row=0, column=1, sticky='w')
    ttk.Label(form, text='字段映射 JSON（标准列名时可留空）').grid(row=0, column=3, sticky='w')
    for n, (role, (log, mapping)) in enumerate(paths.items(), 1):
        ttk.Label(form, text=SOURCE_LABELS[role]).grid(row=n, column=0, sticky='w', padx=(0, 8))
        for i, var in enumerate((log, mapping)):
            ttk.Entry(form, textvariable=var, width=26).grid(row=n, column=1+2*i, sticky='ew', pady=3)
            ttk.Button(form, text='打开…', command=lambda v=var, m=bool(i): choose(v, m)).grid(
                row=n, column=2+2*i, padx=(4, 8))

    body = ttk.Frame(root, padding=(12, 0, 12, 0))
    body.grid(sticky='nsew')
    body.rowconfigure(0, weight=1)
    body.columnconfigure(0, weight=1)
    panel = tk.Text(body, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    panel.grid(sticky='nsew')
    scroll = ttk.Scrollbar(body, command=panel.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    panel.configure(yscrollcommand=scroll.set)
    control = ttk.Frame(root, padding=12)
    control.grid(sticky='ew')

    def render(value):
        panel.configure(state='normal')
        panel.delete('1.0', 'end')
        panel.insert('1.0', value)
        panel.configure(state='disabled')

    def invalidate(*_):
        sources.clear()
        reports.clear()
        wave_roles.configure(values=(), state='disabled')
        wave_role.set('')
        report_btn.configure(state='disabled')
        export_btn.configure(state='disabled')
        render('')
        status.set('输入已更改，点击「读取时间线」。')

    def check():
        invalidate()
        try:
            selected = [load_source(log.get().strip(), role, mapping.get().strip() or None)
                        for role, (log, mapping) in paths.items() if log.get().strip()]
            if not selected:
                raise ValueError('请至少选择一份日志。')
            sources.extend(selected)
            reports.extend(map(timeline, selected))
            render(describe(reports))
            eligible = [SOURCE_LABELS[s.role] for s in sources
                        if len(s.samples) >= 2 and any(values for _, values in s.samples)]
            report_btn.configure(state='normal')
            if eligible:
                wave_roles.configure(values=eligible, state='readonly')
                wave_role.set(eligible[0])
                export_btn.configure(state='normal')
            status.set(f'已读取 {len(sources)} 份日志。波形 CSV 一次导出一个来源。')
        except (ValueError, OSError) as error:
            status.set('读取失败：'+str(error))

    def save(suggestion, suffix, callback):
        path = filedialog.asksaveasfilename(parent=root, initialfile=suggestion, defaultextension=suffix)
        if path:
            try:
                callback(path)
                status.set('已保存在本机。')
            except (OSError, ValueError) as error:
                messagebox.showerror('未保存', str(error), parent=root)

    def export():
        source = next(s for s in sources if SOURCE_LABELS[s.role] == wave_role.get())
        save('car-wave.csv', '.csv', lambda dst: save_wave(source, dst))

    ttk.Button(control, text='读取时间线', command=check).pack(side='left')
    report_btn = ttk.Button(control, text='保存报告…', state='disabled',
                            command=lambda: save('car-timeline.json', '.json', lambda dst: save_report(reports, dst)))
    report_btn.pack(side='left', padx=6)
    wave_role = tk.StringVar()
    wave_roles = ttk.Combobox(control, textvariable=wave_role, width=9, state='disabled')
    wave_roles.pack(side='left', padx=(10, 4))
    export_btn = ttk.Button(control, text='导出波形 CSV…', command=export, state='disabled')
    export_btn.pack(side='left')
    ttk.Button(control, text='保存映射模板…', command=lambda: save('telemetry-map.json', '.json', template)).pack(side='right')
    ttk.Label(root, textvariable=status, wraplength=780, padding=(12, 0, 12, 10)).grid(sticky='w')
    for log, profile in paths.values():
        log.trace_add('write', invalidate)
        profile.trace_add('write', invalidate)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command')
    subs.add_parser('gui', help='打开本地窗口（默认）')
    make = subs.add_parser('template', help='生成可编辑的字段映射模板')
    make.add_argument('mapping', type=Path)
    normalize = subs.add_parser('normalize', help='转换单一来源为波形 CSV')
    normalize.add_argument('source', type=Path)
    normalize.add_argument('out', type=Path)
    normalize.add_argument('--mapping', type=Path)
    report = subs.add_parser('timeline', help='生成同钟时间线；可指定多个来源')
    report.add_argument('--source', action='append', required=True, nargs=3, metavar=('设备', 'CSV', '映射'),
                        help='设备为 pc/openmv/stm32；标准表头可用 - 代替映射')
    report.add_argument('--report', type=Path)
    capture = subs.add_parser('capture-debug', help='仅接收独立 Windows 调试口的文本遥测')
    capture.add_argument('--port', required=True)
    capture.add_argument('--baud', type=int, default=115200)
    capture.add_argument('--seconds', type=int, default=10)
    capture.add_argument('--columns', required=True, help='调试口实际输出的逗号分隔列名，不含 rx_time_s')
    capture.add_argument('--independent-debug-port', required=True, action='store_true',
                         help='确认指定的是自己的独立调试口，允许工具打开它')
    capture.add_argument('out', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in (None, 'gui'):
            launch_gui()
        elif args.command == 'template':
            template(args.mapping)
        elif args.command == 'normalize':
            save_wave(load_source(args.source, 'stm32', args.mapping), args.out)
            print('已生成用于波形对照的实车 CSV。')
        elif args.command == 'capture-debug':
            capture_debug(args.port, args.baud, args.seconds, args.columns, args.out)
        else:
            if len(args.source) > 3 or len({role for role, _, _ in args.source}) != len(args.source):
                raise ValueError('每种设备最多选择一份文件。')
            records = [timeline(load_source(path, role, None if mapping == '-' else mapping))
                       for role, path, mapping in args.source]
            print(describe(records))
            if args.report:
                save_report(records, args.report)
        return 0
    except (OSError, ValueError) as error:
        print('实车录制：'+str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
