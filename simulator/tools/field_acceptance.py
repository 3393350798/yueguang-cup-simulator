"""离线核对真机链路证据，不打开相机、串口或电机。"""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
import csv
from decimal import Decimal
import json
from pathlib import Path
import sys

from telemetry_log import IDENTIFIER
from telemetry_record import SOURCE_LABELS, finite, load_source


PROFILES = {
    'openmv': ('OpenMV → STM32 直连', 'openmv'),
    'phone': ('手机串流 → 电脑 → 无线串口 → STM32', 'pc'),
}
CAPTURE_EVENTS = ('frame_received', 'vision_done', 'command_sent')
BOARD_EVENTS = ('command_received', 'reply_sent')
LABELS = dict(frame_received='取得画面', vision_done='视觉处理完成', command_sent='发送指令',
              command_received='STM32 收到并解析指令', motion_left='左轮实测响应',
              motion_right='右轮实测响应', reply_sent='STM32 发出回执', reply_observed='电脑观察到回执')
STATUS = dict(observed='已观测', missing='未观测', duplicate='同编号有重复事件',
              out_of_order='同设备上事件顺序异常', unknown_receive='缺少唯一的板端接收证据',
              ambiguous_receive='同一时刻有多条板端接收记录',
              missing_baseline='接收前缺少本动作的静止基线',
              already_moving='接收前已达到所设阈值', missing_after='有效窗口无本动作轮速样本',
              below_threshold='有效窗口内的样本未达到阈值')
KINDS = dict(unspecified='未标注来源', synthetic='合成或仿真记录', physical='提交者标注为实体记录')
MAX_CASES = 200


def _local_ms(after, before):
    return round(float((after - before) * 1000), 9)


def _evidence(name, role, status, **values):
    return dict(step=name, source=role, status=status, **values)


def _event(name, role, marks, predecessors=()):
    times = marks.get(name, [])
    if not times:
        return _evidence(name, role, 'missing')
    if len(times) > 1:
        return _evidence(name, role, 'duplicate', count=len(times))
    current, = times
    ts, _ = current
    for prev in predecessors:
        if len(marks.get(prev, [])) == 1 and current < marks[prev][0]:
            return _evidence(name, role, 'out_of_order', elapsed_s=float(ts))
    evidence = _evidence(name, role, 'observed', elapsed_s=float(ts))
    if predecessors and len(marks.get(predecessors[-1], [])) == 1:
        evidence['previous_step_local_ms'] = _local_ms(ts, marks[predecessors[-1]][0][0])
    return evidence


def _motion(side, marks, samples, all_receives, threshold, window):
    name, signal, board = 'motion_' + side, f'speed_{side}_m_s', 'stm32'
    evidence = lambda status, **values: _evidence(name, board, status, **values)
    receives = marks.get('command_received', [])
    if len(receives) != 1:
        return evidence('unknown_receive')
    start = receives[0][0]
    after_ix = bisect_right(all_receives, start)
    before_ix = bisect_left(all_receives, start)
    if after_ix - before_ix != 1:
        return evidence('ambiguous_receive')
    previous_receive = all_receives[before_ix - 1] if before_ix else None
    next_receive = all_receives[after_ix] if after_ix < len(all_receives) else None
    baseline = after_count = 0
    last_before = None
    first_hit = None
    for ts, row in samples:
        if ts < start - window or (previous_receive is not None and ts <= previous_receive) or ts == start:
            continue
        if ts > start + window or (next_receive is not None and ts >= next_receive):
            break
        if signal not in row:
            continue
        speed = row[signal]
        if ts < start:
            last_before = (ts, speed)
            baseline += 1
        else:
            after_count += 1
            if first_hit is None and abs(speed) >= threshold:
                first_hit = (ts, speed)
    if not baseline:
        return evidence('missing_baseline')
    if abs(last_before[1]) >= threshold:
        return evidence('already_moving', baseline_speed_m_s=last_before[1])
    if not after_count:
        return evidence('missing_after')
    if first_hit is None:
        return evidence('below_threshold', after_samples=after_count)
    ts, speed = first_hit
    return evidence('observed', elapsed_s=float(ts), since_receive_local_ms=_local_ms(ts, start),
                    speed_m_s=speed, baseline_speed_m_s=last_before[1], after_samples=after_count)


def build_report(profile, files, *, case_ids=(), threshold=.03, window_s=.5, kind='unspecified'):
    if profile not in PROFILES or kind not in KINDS:
        raise ValueError('方案或日志来源标注无效。')
    threshold, window_s = finite(threshold, '轮速阈值'), finite(window_s, '采样窗口')
    if not .001 <= threshold <= 100 or not .01 <= window_s <= 30:
        raise ValueError('轮速阈值为 0.001～100 m/s；采样窗口为 0.01～30 s。')
    case_ids = list(case_ids)
    if (len(case_ids) > MAX_CASES or any(not IDENTIFIER.fullmatch(key) for key in case_ids)
            or len(set(case_ids)) != len(case_ids)):
        raise ValueError(f'动作编号须符合文档格式、不可重复，单次最多 {MAX_CASES} 个。')
    _, capture = PROFILES[profile]
    roles = (capture, 'stm32')
    if set(files) - set(roles):
        raise ValueError('请选择当前接线使用的两类设备之一。')
    if not files and not case_ids:
        raise ValueError('请至少提供一份日志或一个预期动作编号。')
    if len(files) == 2 and Path(files[capture][0]).samefile(files['stm32'][0]):
        raise ValueError('两台设备不能使用同一份日志。')
    marks = {role: {} for role in roles}
    counts, all_receives, samples = {}, [], {}
    for role in roles:
        if role not in files:
            counts[role] = None
            continue
        src = load_source(files[role][0], role, files[role][1], retain_case_samples=role == 'stm32')
        allowed = BOARD_EVENTS if role == 'stm32' else CAPTURE_EVENTS + (('reply_observed',) if role == 'pc' else ())
        counts[role] = dict(rows=len(src.timestamps), events_without_case=src.unspecified, unknown_events=src.unknown)
        for sequence, (ts, event, case_id) in enumerate(src.events):
            if role == 'stm32' and event == 'command_received':
                all_receives.append(ts)
            if case_id and event in allowed:
                marks[role].setdefault(case_id, {}).setdefault(event, []).append((ts, sequence))
        if role == 'stm32':
            samples = src.case_samples
    if not case_ids:
        case_ids = list(dict.fromkeys([*marks[capture], *marks['stm32'], *samples]))
        if len(case_ids) > MAX_CASES:
            raise ValueError(f'识别到超过 {MAX_CASES} 个动作，请指定所需动作编号，或截取较短日志。')
    cases = []
    for key in case_ids:
        cap_marks, board_marks = marks[capture].get(key, {}), marks['stm32'].get(key, {})
        steps = [_event(name, capture, cap_marks, CAPTURE_EVENTS[:i]) for i, name in enumerate(CAPTURE_EVENTS)]
        steps.append(_event('command_received', 'stm32', board_marks))
        steps += [_motion(side, board_marks, samples.get(key, []), all_receives, threshold, Decimal(str(window_s)))
                  for side in ('left', 'right')]
        return_events = [_event('reply_sent', 'stm32', board_marks, ('command_received',))]
        if capture == 'pc':
            return_events.append(_event('reply_observed', 'pc', cap_marks, CAPTURE_EVENTS))
        first_gap = next((i for i, step in enumerate(steps) if step['status'] != 'observed'), len(steps))
        observed = [step['step'] for step in steps if step['status'] == 'observed']
        cases.append(dict(id=key, evidence_complete=first_gap == len(steps), steps=steps,
                          optional_return_events=return_events,
                          first_unresolved_step=steps[first_gap]['step'] if first_gap < len(steps) else None,
                          last_contiguous_step=steps[first_gap - 1]['step'] if first_gap else None,
                          last_step_with_evidence=observed[-1] if observed else None))
    return dict(schema='moonlight-field-acceptance-v1', profile=profile, source_kind=kind,
                independent_hardware_verification=False, cross_device_delay_measured=False,
                all_selected_cases_complete=bool(cases) and all(case['evidence_complete'] for case in cases),
                speed_threshold_m_s=threshold, response_window_s=window_s, sources=counts, cases=cases)


def describe(report):
    cases = report['cases']
    complete = sum(case['evidence_complete'] for case in cases)
    lines = [
        PROFILES[report['profile']][0] + '｜' + KINDS[report['source_kind']],
        f'{complete}/{len(cases)} 个动作的必看证据齐全。工具只核对日志，不认证真机。',
        f"双轮阈值 ±{report['speed_threshold_m_s']:g} m/s；接收前后各 {report['response_window_s']:g} s。",
        '各时间以所在设备文件首行为零，只计算同设备间隔；未观测不等于没有发生。', '',
    ]
    for role, counts in report['sources'].items():
        label = SOURCE_LABELS[role]
        lines.append(f"{label}：{'未提供日志' if counts is None else str(counts['rows']) + ' 行，'
                     + str(counts['events_without_case']) + ' 条无有效编号事件，'
                     + str(counts['unknown_events']) + ' 条未映射事件'}")
    if not cases:
        lines.extend(('', '没有可配对的动作。检查 case_id 字段映射，或填写预期动作编号。'))
    for case in cases:
        lines.extend(('', f"#{case['id']}  {'证据齐全' if case['evidence_complete'] else '证据待补'}"))
        if case['first_unresolved_step']:
            contiguous = LABELS.get(case['last_contiguous_step'], '无')
            lines.append(f"  首个待查：{LABELS[case['first_unresolved_step']]}；"
                         f"从头连续确认到：{contiguous}；"
                         f"最靠后的已有证据：{LABELS.get(case['last_step_with_evidence'], '无')}")
        for step in (*case['steps'], *case['optional_return_events']):
            key, status = step['step'], STATUS[step['status']]
            optional = '（可选）' if step in case['optional_return_events'] else ''
            lines.append(f"  {LABELS[key]}{optional} / {SOURCE_LABELS[step['source']]}：{status}")
            if 'since_receive_local_ms' in step:
                lines.append(f"    接收后 {step['since_receive_local_ms']:.3f} ms 首次达阈值；"
                             f"轮速 {step['speed_m_s']:g} m/s（仅 STM32 时钟）")
            elif 'previous_step_local_ms' in step:
                interval = '电脑发出到见回执（含处理）' if key == 'reply_observed' else '距同设备前一步'
                lines.append(f"    {interval} {step['previous_step_local_ms']:.3f} ms")
    return '\n'.join(lines)


def save_report(report, path):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def make_templates(profile, path):
    _, capture = PROFILES[profile]
    root = Path(path)
    root.mkdir(exist_ok=False)
    for role in (capture, 'stm32'):
        with (root / f'{role}.csv').open('x', encoding='utf-8-sig', newline='') as stream:
            cols = ('speed_left_m_s', 'speed_right_m_s') if role == 'stm32' else ()
            csv.writer(stream).writerow(('time_s', 'event', 'case_id', *cols))


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title('月光杯 · 真机链路验收')
    root.minsize(800, 550)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    form = ttk.Frame(root, padding=12)
    form.grid(sticky='ew')
    for col in (1, 3):
        form.columnconfigure(col, weight=1)
    names = {label: key for key, (label, _) in PROFILES.items()}
    kinds = {label: key for key, label in KINDS.items()}
    profile = tk.StringVar(value=next(iter(names)))
    kind = tk.StringVar(value=KINDS['unspecified'])
    capture_label = tk.StringVar(value='OpenMV')
    case_ids, threshold, window = tk.StringVar(), tk.StringVar(value='0.03'), tk.StringVar(value='0.5')
    paths = [[tk.StringVar() for _ in range(2)] for _ in range(2)]
    status = tk.StringVar(value='请选择已保存的本机日志，或输入预期动作编号查看缺项。')
    current = None

    def pick(var, is_map):
        selected = filedialog.askopenfilename(parent=root, filetypes=[('字段映射' if is_map else 'CSV 日志',
                                                                     '*.json' if is_map else '*.csv')])
        if selected:
            var.set(selected)

    ttk.Label(form, text='接线方案').grid(row=0, column=0, sticky='w')
    ttk.Combobox(form, state='readonly', values=tuple(names), textvariable=profile, width=37).grid(
        row=0, column=1, columnspan=2, sticky='ew')
    ttk.Combobox(form, state='readonly', values=tuple(kinds), textvariable=kind, width=24).grid(
        row=0, column=3, columnspan=2, sticky='ew', padx=(8, 0))
    for i, (log_path, map_path) in enumerate(paths, 2):
        label = ttk.Label(form, textvariable=capture_label) if i == 2 else ttk.Label(form, text='STM32')
        label.grid(row=i, column=0, sticky='w', padx=(0, 8))
        for col, var in ((1, log_path), (3, map_path)):
            if i == 2:
                ttk.Label(form, text='CSV 日志' if col == 1 else '字段映射 JSON（可空）').grid(
                    row=1, column=col, sticky='w', pady=(8, 0))
            ttk.Entry(form, textvariable=var, width=22).grid(row=i, column=col, sticky='ew', pady=2)
            ttk.Button(form, text='选择…', command=lambda v=var, m=col == 3: pick(v, m)).grid(
                row=i, column=col + 1, padx=(4, 8))
    params = ttk.Frame(form)
    params.grid(row=4, column=0, columnspan=5, sticky='ew', pady=(8, 0))
    for label, value, width in (('动作编号（逗号分隔，可空）', case_ids, 18),
                                ('轮速阈值 m/s', threshold, 7), ('采样窗口 s', window, 7)):
        ttk.Label(params, text=label).pack(side='left', padx=(0, 3))
        ttk.Entry(params, textvariable=value, width=width).pack(side='left', padx=(0, 8))
    body = ttk.Frame(root, padding=(12, 0, 12, 0))
    body.grid(sticky='nsew')
    body.rowconfigure(0, weight=1)
    body.columnconfigure(0, weight=1)
    display = tk.Text(body, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    display.grid(sticky='nsew')
    scroll = ttk.Scrollbar(body, command=display.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    display.configure(yscrollcommand=scroll.set)
    actions = ttk.Frame(root, padding=12)
    actions.grid(sticky='ew')

    def render(content):
        display.configure(state='normal')
        display.delete('1.0', 'end')
        display.insert('1.0', content)
        display.configure(state='disabled')

    def invalidate(*_):
        nonlocal current
        current = None
        save_button.configure(state='disabled')
        render('')
        status.set('选项已改变，请重新检查证据。')

    def change_profile(*_):
        capture_label.set(SOURCE_LABELS[PROFILES[names[profile.get()]][1]])
        for var in paths[0]:
            var.set('')
        invalidate()

    def check():
        nonlocal current
        invalidate()
        try:
            selected = names[profile.get()]
            files = {}
            for role, (log, mapping) in zip((PROFILES[selected][1], 'stm32'), paths):
                if log.get().strip():
                    files[role] = (log.get().strip(), mapping.get().strip() or None)
                elif mapping.get().strip():
                    raise ValueError(f'{SOURCE_LABELS[role]}选了映射，但没有选日志。')
            ids = [item.strip() for item in case_ids.get().replace('，', ',').split(',') if item.strip()]
            current = build_report(selected, files, case_ids=ids, threshold=threshold.get(),
                                   window_s=window.get(), kind=kinds[kind.get()])
            render(describe(current))
            save_button.configure(state='normal')
            status.set('已完成本地证据检查。')
        except (ValueError, OSError) as error:
            status.set(f'未完成检查：{error}')

    def save():
        out = filedialog.asksaveasfilename(parent=root, initialfile='field-report.json', defaultextension='.json')
        if out and current is not None:
            try:
                save_report(current, out)
                status.set('报告已保存在本机。')
            except OSError as error:
                messagebox.showerror('未保存', str(error), parent=root)

    def template():
        parent = filedialog.askdirectory(parent=root, mustexist=True)
        if parent:
            selected = names[profile.get()]
            out = Path(parent) / f'field-acceptance-{selected}'
            try:
                make_templates(selected, out)
                status.set(f'已创建空白表：{out}')
            except OSError as error:
                messagebox.showerror('未创建（模板目录不能已存在）', str(error), parent=root)

    ttk.Button(actions, text='检查证据', command=check).pack(side='left')
    save_button = ttk.Button(actions, text='保存报告…', command=save, state='disabled')
    save_button.pack(side='left', padx=8)
    ttk.Button(actions, text='创建空白 CSV 模板…', command=template).pack(side='right')
    ttk.Label(root, textvariable=status, wraplength=760, padding=(12, 0, 12, 10)).grid(sticky='w')
    profile.trace_add('write', change_profile)
    for var in (kind, case_ids, threshold, window, *paths[0], *paths[1]):
        var.trace_add('write', invalidate)
    root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('gui', help='打开本地窗口（默认）')
    template = sub.add_parser('template', help='生成空白 CSV 文件')
    template.add_argument('--profile', choices=PROFILES, required=True)
    template.add_argument('directory', type=Path, help='不存在的目标目录')
    check = sub.add_parser('check', help='核对指定接线的日志证据')
    check.add_argument('--profile', choices=PROFILES, required=True)
    for role in SOURCE_LABELS:
        check.add_argument(f'--{role}', type=Path, metavar='CSV')
        check.add_argument(f'--{role}-map', type=Path, metavar='JSON')
    check.add_argument('--case', action='append', default=[], help='指定预期动作编号，可重复提供')
    check.add_argument('--kind', choices=KINDS, default='unspecified', help='自行标注日志来源')
    check.add_argument('--threshold', type=float, default=.03, help='轮速阈值（m/s）')
    check.add_argument('--window', type=float, default=.5, help='板端采样窗口（秒）')
    check.add_argument('--report', type=Path, help='保存为本机报告 JSON，禁止覆盖')
    check.add_argument('--require-complete', action='store_true', help='存在缺项或无动作时返回 2')
    args = parser.parse_args(argv)
    try:
        if args.command in (None, 'gui'):
            launch_gui()
        elif args.command == 'template':
            make_templates(args.profile, args.directory)
            print('已创建空白 CSV。')
        else:
            files = {}
            for role, name in SOURCE_LABELS.items():
                log, mapping = getattr(args, role), getattr(args, role + '_map')
                if log is not None:
                    files[role] = (log, mapping)
                elif mapping is not None:
                    raise ValueError(f'{name}提供了映射，但没有提供日志。')
            report = build_report(args.profile, files, case_ids=args.case, threshold=args.threshold,
                                  window_s=args.window, kind=args.kind)
            print(describe(report))
            if args.report:
                save_report(report, args.report)
            if args.require_complete and not report['all_selected_cases_complete']:
                return 2
        return 0
    except (OSError, ValueError) as error:
        print(f'真机验收：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
