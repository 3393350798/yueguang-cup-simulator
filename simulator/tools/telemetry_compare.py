"""离线叠加实车与 Webots 波形；不连接串口，不执行队伍程序。"""
import argparse
from bisect import bisect_left
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import statistics
import sys


MAX_BYTES = 128 * 1024 * 1024
MAX_SAMPLES = 120_000
SIDES = ('left', 'right')
SONARS = ('front', 'left', 'right', 'rear')
SIDE_LABELS = ('左轮', '右轮')
SIGNALS = {
    **{f'pwm_{s}': (f'{label} PWM', '比例', -1, 1) for s, label in zip(SIDES, SIDE_LABELS)},
    **{f'speed_{s}_m_s': (f'{label}速度', 'm/s', -100, 100) for s, label in zip(SIDES, SIDE_LABELS)},
    **{f'encoder_{s}_rad': (f'{label}编码器角度', 'rad', -1e8, 1e8) for s, label in zip(SIDES, SIDE_LABELS)},
    **{f'sonar_{s}_mm': (f'{label}超声波', 'mm', 0, 100_000) for s, label in zip(SONARS, ('前', '左', '右', '后'))},
    **{f'ir{i}': (f'红外 {i}', '归一化', 0, 1) for i in range(32)},
}


@dataclass
class Data:
    times: list
    columns: dict
    gap: float


def numeric(raw, label, lower, upper):
    try:
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise ValueError()
        value = float(raw)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f'{label}必须为数字。') from error
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'{label}超出范围（{lower:g} 到 {upper:g}）。请检查单位。')
    return value


def time_value(raw):
    return numeric(raw, '时间（秒）', 0, 1e10)


def cell(raw, key, scale=1):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    _, _, lo, hi = SIGNALS[key]
    return numeric(raw, key, lo/scale, hi/scale)*scale


def finalize(times, columns):
    if len(times) < 2:
        raise ValueError('每份波形至少需要两个采样时刻。')
    gaps = [b-a for a, b in zip(times, times[1:])]
    if min(gaps) <= 0:
        raise ValueError('采样时间须严格递增；请先去除重复或乱序时间戳。')
    kept = {k: v for k, v in columns.items() if any(x is not None for x in v)}
    if not kept:
        raise ValueError('没有可以使用的波形通道，请核对 CSV 表头或导出新的调试波形。')
    return Data(times, kept, 3.01*statistics.median(gaps))


def load_trace(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, RecursionError) as error:
        raise ValueError('不能解析波形 JSON。') from error
    if (not isinstance(data, dict) or data.get('format') != 'moonlight-trace'
            or data.get('version') != 1 or not isinstance(data.get('samples'), list)):
        raise ValueError('需要「调试波形」导出的 moonlight-trace v1 文件；失败片段不适合完整对照。')
    rows = data['samples']
    if not 2 <= len(rows) <= MAX_SAMPLES:
        raise ValueError(f'波形须有 2 到 {MAX_SAMPLES} 行。')
    times = []
    columns = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('波形中有非数据行。')
        times.append(time_value(row.get('t')))
        found = {}
        for kind, names in (
            ('pwm', [f'pwm_{s}' for s in SIDES]),
            ('speed', [f'speed_{s}_m_s' for s in SIDES]),
            ('encoder', [f'encoder_{s}_rad' for s in SIDES]),
            ('ir', [f'ir{i}' for i in range(32)]),
        ):
            values = row.get(kind)
            if values is None:
                continue
            if not isinstance(values, list):
                raise ValueError(f'波形第 {idx+1} 点的 {kind} 不是采样数组。')
            found.update((key, cell(value, key)) for key, value in zip(names, values))
        sonar = row.get('sonar')
        if sonar is not None:
            if not isinstance(sonar, dict):
                raise ValueError(f'波形第 {idx+1} 点的 sonar 不是测距对象。')
            for name in SONARS:
                if name in sonar:
                    key = f'sonar_{name}_mm'
                    found[key] = cell(sonar[name], key, 1000)
        for key, value in found.items():
            if value is not None and key not in columns:
                columns[key] = [None]*idx
        for key, values in columns.items():
            values.append(found.get(key))
    return finalize(times, columns)


def load_csv(path):
    times = []
    try:
        with path.open(encoding='utf-8-sig', newline='') as src:
            rows = csv.reader(src, strict=True)
            headers = [x.strip() for x in next(rows, [])]
            if 'time_s' not in headers or len(headers) != len(set(headers)):
                raise ValueError('CSV 需有唯一列名和 time_s（秒）列。')
            time_at = headers.index('time_s')
            indices = {key: i for i, key in enumerate(headers) if key in SIGNALS}
            columns = {key: [] for key in indices}
            for row in rows:
                if not row or all(not value.strip() for value in row):
                    continue
                if len(times) >= MAX_SAMPLES:
                    raise ValueError(f'每份波形最多 {MAX_SAMPLES} 行。')
                if len(row) != len(headers):
                    raise ValueError(f'CSV 第 {rows.line_num} 行列数与表头不同；空值也须保留分隔符。')
                try:
                    times.append(time_value(row[time_at]))
                    for key, index in indices.items():
                        columns[key].append(cell(row[index], key))
                except ValueError as error:
                    raise ValueError(f'CSV 第 {rows.line_num} 行：{error}') from error
    except (UnicodeError, csv.Error) as error:
        raise ValueError('请保存为 UTF-8 编码、逗号分隔的 CSV 文件。') from error
    return finalize(times, columns)


def load_data(path, allow_trace=True):
    if not path.is_file():
        raise ValueError('找不到输入文件。')
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('单份文件不得超过 128 MiB，请只截取要比较的动作。')
    if path.suffix.lower() == '.csv':
        return load_csv(path)
    if allow_trace and path.suffix.lower() == '.json':
        return load_trace(path)
    raise ValueError('请选择波形 JSON 或规范 CSV；实车输入只接受 CSV。')


def write_template(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8-sig', newline='') as dest:
        csv.writer(dest).writerow(('time_s', *SIGNALS))


def write_normalized(data, path):
    columns = [k for k in SIGNALS if k in data.columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8-sig', newline='') as dest:
        writer = csv.writer(dest)
        writer.writerow(('time_s', *columns))
        writer.writerows(zip(data.times, *(data.columns[k] for k in columns)))


def first_crossing(data, key, threshold, start=None):
    values = data.columns.get(key)
    if values is None:
        return None
    sign = 1 if threshold > 0 else -1
    limit = abs(threshold)
    begin = 1 if start is None else max(1, bisect_left(data.times, start))
    for i in range(begin, len(values)):
        a, b = values[i-1:i+1]
        if start is not None and i == begin:
            if data.times[i] == start and b is not None and b*sign < limit:
                continue
            if a is not None and a*sign >= limit:
                # The wheel was already above the response threshold at the origin.
                return None
        if (a is not None and b is not None and a*sign < limit <= b*sign
                and data.times[i]-data.times[i-1] <= data.gap):
            return data.times[i], data.times[i]-data.times[i-1]
    return None


def origins(sim, real, method, sim_t, real_t, side, threshold):
    if method == 'start':
        return sim.times[0], real.times[0]
    if method == 'manual':
        answer = (time_value(sim_t), time_value(real_t))
        for name, data, mark in zip(('仿真', '实车'), (sim, real), answer):
            if not data.times[0] <= mark <= data.times[-1]:
                raise ValueError(f'{name}对齐点必须在录制时间 {data.times[0]:g} 到 {data.times[-1]:g} 秒内。')
        return answer
    threshold = numeric(threshold, '有符号 PWM 门槛', -1, 1)
    if threshold == 0:
        raise ValueError('PWM 门槛不能为 0；请填写同一方向上的非零比例。')
    key = f'pwm_{side}'
    values = [first_crossing(data, key, threshold) for data in (sim, real)]
    for name, data, crossing in zip(('仿真', '实车'), (sim, real), values):
        if crossing is None:
            cause = '没有这一路 PWM' if key not in data.columns else '没有记录完整的跨阈值动作'
            raise ValueError(f'{name}{cause}。请录下启动前后的样本，或改用手动对齐。')
    return values[0][0], values[1][0]


def correspondence(sim, t):
    pos = bisect_left(sim.times, t)
    for i in (pos, pos-1):
        if 0 <= i < len(sim.times) and abs(sim.times[i]-t) <= 1e-9:
            return i, i, 0
    if pos <= 0 or pos >= len(sim.times):
        return None
    gap = sim.times[pos]-sim.times[pos-1]
    if gap > sim.gap:
        return None
    return pos-1, pos, (t-sim.times[pos-1])/gap


def scores(sim, real, positions, key):
    simulated = sim.columns[key]
    step = key.startswith('pwm_')
    signed = absolute = squared = 0.0
    count = 0
    for where, measured in zip(positions, real.columns[key]):
        if where is None or measured is None:
            continue
        i, j, ratio = where
        a, b = simulated[i], simulated[j]
        if a is None or b is None:
            continue
        count += 1
        delta = measured-(a if step else a+(b-a)*ratio)
        signed += delta
        absolute += abs(delta)
        squared += delta*delta
    result = dict(points=count)
    if count >= 2:
        result.update(mean_error=signed/count, mae=absolute/count, rmse=math.sqrt(squared/count))
    return result


def response_timing(sim, real, zeros, side, pwm_threshold, speed_threshold):
    speed_threshold = numeric(speed_threshold, '轮速门槛（m/s）', 0.001, 100)
    signed_speed = math.copysign(speed_threshold, pwm_threshold)
    key = f'speed_{side}_m_s'
    values = [first_crossing(data, key, signed_speed, zero) for data, zero in zip((sim, real), zeros)]
    times = [round((pair[0]-zero)*1000, 6) if pair else None for pair, zero in zip(values, zeros)]
    spans = [round(pair[1]*1000, 6) if pair else None for pair in values]
    return dict(speed_threshold_m_s=signed_speed, sim_ms=times[0], real_ms=times[1],
                sim_capture_interval_ms=spans[0], real_capture_interval_ms=spans[1],
                real_minus_sim_ms=round(times[1]-times[0], 6) if all(t is not None for t in times) else None)


def compare(sim, real, method='start', sim_t=None, real_t=None, side='left', threshold=.15, speed_threshold=None):
    common = [k for k in SIGNALS if k in sim.columns and k in real.columns]
    if not common:
        raise ValueError('两份文件没有同名且有数据的通道。请选择同一传感器，或先转换实车表头。')
    if method == 'pwm':
        threshold = numeric(threshold, '有符号 PWM 门槛', -1, 1)
    zeros = origins(sim, real, method, sim_t, real_t, side, threshold)
    left = max(sim.times[0]-zeros[0], real.times[0]-zeros[1])
    right = min(sim.times[-1]-zeros[0], real.times[-1]-zeros[1])
    if right <= left:
        raise ValueError('对齐后没有重叠的时间区间，请检查两个零点。')
    positions = [correspondence(sim, t-zeros[1]+zeros[0]) for t in real.times]
    result = dict(format='moonlight-telemetry-compare', version=1,
                  alignment=dict(method=method, sim_t_s=zeros[0], real_t_s=zeros[1]),
                  overlap_s=[left, right], samples=dict(sim=len(sim.times), real=len(real.times)),
                  channels={k: scores(sim, real, positions, k) for k in common})
    if method == 'pwm':
        result['alignment'].update(pwm_side=side, pwm_threshold=threshold)
        if speed_threshold is not None:
            result['response'] = response_timing(sim, real, zeros, side, threshold, speed_threshold)
    return result


def write_report(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as dest:
        json.dump(report, dest, ensure_ascii=False, allow_nan=False, indent=2)
        dest.write('\n')


def plot_wave(canvas, sim, real, summary, key):
    canvas.delete('all')
    width, height = canvas.winfo_width(), canvas.winfo_height()
    if width < 200 or height < 120:
        return
    label, unit, _, _ = SIGNALS[key]
    alignment = summary['alignment']
    zeros = alignment['sim_t_s'], alignment['real_t_s']
    bounds = summary['overlap_s']
    before = max(sim.times[0]-zeros[0], real.times[0]-zeros[1])
    after = min(sim.times[-1]-zeros[0], real.times[-1]-zeros[1])
    x0, x1, y0, y1 = 82, width-20, 41, height-41
    domains = [
        (d, d.columns[key], zero, bisect_left(d.times, zero+before-1e-9),
         bisect_left(d.times, zero+after+1e-9), color)
        for d, zero, color in zip((sim, real), zeros, ('#076b83', '#b65a19'))
    ]
    extents = [v for _, values, _, first, last, _ in domains
               for v in values[first:last] if v is not None]
    if not extents:
        canvas.create_text(width/2, height/2, text='重叠区间内此通道没有采样值', fill='#506576')
        return
    low, high = min(extents), max(extents)
    pad = max(abs(high)*.02, .002) if high == low else (high-low)*.06
    low, high = low-pad, high+pad

    def x(t):
        return x0+(t-before)/(after-before)*(x1-x0)

    def y(value):
        return y1-(value-low)/(high-low)*(y1-y0)

    canvas.create_text(x0, 20, text=f'{label}（{unit}）', fill='#203747', anchor='w')
    canvas.create_text(x1, 20, text='蓝：仿真  ·  橙：实车', fill='#465c69', anchor='e')
    for i in range(5):
        v, t = low+(high-low)*i/4, before+(after-before)*i/4
        py, px = y(v), x(t)
        canvas.create_line(x0, py, x1, py, fill='#e0e8ed')
        canvas.create_line(px, y0, px, y1, fill='#e0e8ed')
        canvas.create_text(x0-7, py, text=f'{v:.4g}', fill='#465c69', anchor='e')
        canvas.create_text(px, y1+16, text=f'{t:.4g}', fill='#465c69')
    canvas.create_text(width/2, height-8, text='对齐后的时间（秒） · 仅展示两份波形重叠时段', fill='#465c69')
    if bounds[0] <= 0 <= bounds[1]:
        canvas.create_line(x(0), y0, x(0), y1, fill='#617786', dash=(3, 3))
    for data, values, zero, first, last, color in domains:
        pixels_drawn = set()
        segment = []
        bucket = []
        pixel = None

        def flush_bin():
            if bucket:
                # Keep extrema in time order instead of losing narrow peaks.
                indices = sorted({0, len(bucket)-1,
                                  min(range(len(bucket)), key=lambda i: bucket[i][1]),
                                  max(range(len(bucket)), key=lambda i: bucket[i][1])})
                segment.extend(bucket[i] for i in indices)
                bucket.clear()

        def flush_segment():
            flush_bin()
            if not segment:
                return
            start_px = int(segment[0][0])
            if len(segment) == 1 or start_px == int(segment[-1][0]):
                if start_px not in pixels_drawn:
                    xx = segment[0][0]
                    top, bottom = min(p[1] for p in segment), max(p[1] for p in segment)
                    canvas.create_line(xx, top-1, xx, bottom+1, fill=color, width=2)
                    pixels_drawn.add(start_px)
            else:
                points = list(segment[0])
                previous = segment[0][1]
                for xx, yy in segment[1:]:
                    if key.startswith('pwm_'):
                        points.extend((xx, previous))
                    points.extend((xx, yy))
                    previous = yy
                canvas.create_line(*points, fill=color, width=2)
            segment.clear()

        for i in range(first, last):
            value, when = values[i], data.times[i]
            if value is None or (i > first and when-data.times[i-1] > data.gap):
                flush_segment()
                pixel = None
            if value is not None:
                xx = x(when-zero)
                if pixel != int(xx):
                    flush_bin()
                    pixel = int(xx)
                bucket.append((xx, y(value)))
        flush_segment()


def timing_message(report):
    timing = report.get('response')
    if not timing:
        return ''
    def value(source):
        delay = timing[source+'_ms']
        span = timing[source+'_capture_interval_ms']
        return '未观测到' if delay is None else f'{delay:.1f} ms（跨越处采样间隔 {span:.1f} ms）'
    delta = timing['real_minus_sim_ms']
    suffix = '' if delta is None else f'；实车减仿真 {delta:+.1f} ms'
    return f"各自 PWM 门槛到轮速 {timing['speed_threshold_m_s']:g} m/s：仿真 {value('sim')}，实车 {value('real')}{suffix}"


class CompareWindow:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        root.title('月光杯 · 实车与仿真波形对照')
        root.geometry('1040x715')
        root.minsize(890, 610)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        self.sim_path, self.real_path = tk.StringVar(), tk.StringVar()
        self.method = tk.StringVar(value='各自首个采样点')
        self.sim_t, self.real_t = tk.StringVar(), tk.StringVar()
        self.side = tk.StringVar(value='左轮')
        self.threshold, self.speed_threshold = tk.StringVar(value='0.15'), tk.StringVar()
        self.status = tk.StringVar(value='先打开仿真波形和实车 CSV。所有文件在本机处理。')
        self.timing = tk.StringVar()
        self.data = None

        inputs = ttk.Frame(root, padding=(14, 9))
        inputs.grid(sticky='ew')
        inputs.columnconfigure(1, weight=1)
        for i, (label, field, sim) in enumerate((
            ('仿真波形（JSON 或 CSV）', self.sim_path, True),
            ('实车波形（CSV）', self.real_path, False),
        )):
            ttk.Label(inputs, text=label).grid(row=i, column=0, sticky='w')
            ttk.Entry(inputs, textvariable=field).grid(row=i, column=1, padx=8, pady=3, sticky='ew')
            ttk.Button(inputs, text='打开…', command=lambda s=sim, f=field: self.choose(s, f)).grid(row=i, column=2)
        ttk.Button(inputs, text='保存空白 CSV…', command=self.template).grid(row=1, column=3, padx=(8, 0))

        controls = ttk.Frame(root, padding=(14, 3, 14, 9))
        controls.grid(sticky='ew')
        ttk.Label(controls, text='时间对齐').grid(row=0, column=0, padx=(0, 8))
        self.methods = {'各自首个采样点': 'start', '手动指定时间': 'manual', '同路 PWM 首次过门槛': 'pwm'}
        menu = ttk.Combobox(controls, values=list(self.methods), textvariable=self.method, width=23, state='readonly')
        menu.grid(row=0, column=1, sticky='w')
        menu.bind('<<ComboboxSelected>>', lambda _: self.configure_method())
        self.manual = ttk.Frame(controls)
        self.manual.grid(row=0, column=2, sticky='w', padx=8)
        self.pwm = ttk.Frame(controls)
        self.pwm.grid(row=0, column=2, sticky='w', padx=8)
        for i, (label, field) in enumerate((('仿真秒', self.sim_t), ('实车秒', self.real_t))):
            ttk.Label(self.manual, text=label).grid(row=0, column=i*2, padx=3)
            ttk.Entry(self.manual, textvariable=field, width=8).grid(row=0, column=i*2+1)
        ttk.Combobox(self.pwm, values=SIDE_LABELS, textvariable=self.side, width=5, state='readonly').grid(row=0, column=0)
        for i, (label, field) in enumerate((('PWM 比例', self.threshold), ('测响应（m/s，可空）', self.speed_threshold))):
            ttk.Label(self.pwm, text=label).grid(row=0, column=i*2+1, padx=3)
            ttk.Entry(self.pwm, textvariable=field, width=6).grid(row=0, column=i*2+2)
        self.configure_method()
        ttk.Button(controls, text='开始对照', command=self.run).grid(row=0, column=3, padx=6)
        self.save_button = ttk.Button(controls, text='保存报告…', command=self.save, state='disabled')
        self.save_button.grid(row=0, column=4)
        ttk.Label(controls, textvariable=self.status, wraplength=830, justify='left').grid(
            row=1, column=0, columnspan=5, sticky='w', pady=(9, 0))

        body = ttk.Frame(root, padding=(14, 0, 14, 10))
        body.grid(sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.chart = tk.Canvas(body, bg='white', height=245, highlightthickness=1, highlightbackground='#cfdce4')
        self.chart.grid(row=0, column=0, columnspan=2, sticky='nsew')
        self.chart.bind('<Configure>', lambda _: self.draw())
        ttk.Label(body, textvariable=self.timing, wraplength=830).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(7, 4))
        headers = ('信号', '有效配对数', '平均偏差（实车减仿真）', '平均绝对误差', '均方根误差')
        columns = ('signal', 'points', 'error', 'mae', 'rmse')
        self.table = ttk.Treeview(body, columns=columns, show='headings', height=7, selectmode='browse')
        self.table.grid(row=2, column=0, sticky='ew')
        for col, label, width in zip(columns, headers, (230, 85, 185, 140, 140)):
            self.table.heading(col, text=label)
            self.table.column(col, anchor='w' if col == 'signal' else 'center', width=width)
        scrollbar = ttk.Scrollbar(body, command=self.table.yview)
        scrollbar.grid(row=2, column=1, sticky='ns')
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.bind('<<TreeviewSelect>>', lambda _: self.draw())
        ttk.Label(body, text='选择下表中的通道查看曲线；空值和长采样间断不参与配对。').grid(
            row=3, column=0, columnspan=2, sticky='w', pady=(5, 0))

    def configure_method(self):
        self.manual.grid_remove()
        self.pwm.grid_remove()
        mode = self.methods[self.method.get()]
        if mode == 'manual':
            self.manual.grid()
        if mode == 'pwm':
            self.pwm.grid()

    def choose(self, sim, field):
        from tkinter import filedialog
        kinds = [('Webots 仿真波形', '*.json')] if sim else []
        path = filedialog.askopenfilename(parent=self.root, filetypes=[*kinds, ('波形表格', '*.csv')])
        if path:
            field.set(path)

    def template(self):
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.csv',
                                           initialfile='telemetry-template.csv',
                                           filetypes=[('CSV', '*.csv')])
        if path:
            try:
                write_template(Path(path))
                self.status.set('已保存空白 CSV，只填本次要对照的信号列即可。')
            except OSError:
                messagebox.showerror('不能保存', '请选择可写且尚不存在的文件。', parent=self.root)

    def run(self):
        self.data = None
        self.save_button.configure(state='disabled')
        self.table.delete(*self.table.get_children())
        self.timing.set('')
        self.chart.delete('all')
        self.status.set('读取波形并配对中…')
        self.root.update_idletasks()
        try:
            sim = load_data(Path(self.sim_path.get().strip()))
            real = load_data(Path(self.real_path.get().strip()), allow_trace=False)
            result = compare(sim, real, self.methods[self.method.get()], self.sim_t.get(), self.real_t.get(),
                             SIDES[SIDE_LABELS.index(self.side.get())], self.threshold.get(),
                             self.speed_threshold.get().strip() or None)
        except (OSError, ValueError) as error:
            self.status.set(f'无法对照：{error}')
            return
        self.data = sim, real, result
        a = result['alignment']
        self.status.set(f"仿真 {a['sim_t_s']:.6g} s／实车 {a['real_t_s']:.6g} s 归零。"
                        f"仿真 {len(sim.times)} 点，实车 {len(real.times)} 点；"
                        f"时间重叠 {result['overlap_s'][1]-result['overlap_s'][0]:.4g} s。")
        self.timing.set(timing_message(result))
        for key, metrics in result['channels'].items():
            label, unit, _, _ = SIGNALS[key]
            values = [f'{metrics[k]:.5g}' if k in metrics else '—' for k in ('mean_error', 'mae', 'rmse')]
            self.table.insert('', 'end', iid=key, values=(f'{label}（{unit}）', metrics['points'], *values))
        self.table.selection_set(next(iter(result['channels'])))
        self.save_button.configure(state='normal')
        self.draw()

    def draw(self):
        if self.data and self.table.selection():
            plot_wave(self.chart, *self.data, self.table.selection()[0])

    def save(self):
        from tkinter import filedialog, messagebox
        if self.data is None:
            return
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json',
                                           initialfile='telemetry-compare.json',
                                           filetypes=[('JSON', '*.json')])
        if path:
            try:
                write_report(self.data[2], Path(path))
                messagebox.showinfo('已保存', '已写入对照指标；未复制原始波形。', parent=self.root)
            except OSError:
                messagebox.showerror('不能保存', '请选择可写且尚不存在的文件。', parent=self.root)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('gui', help='离线对照窗口（默认）')
    template = commands.add_parser('template', help='生成实车 CSV 空表')
    template.add_argument('destination', type=Path)
    normalized = commands.add_parser('normalize', help='从仿真调试波形 JSON 提取通道 CSV')
    normalized.add_argument('source', type=Path)
    normalized.add_argument('destination', type=Path)
    cli = commands.add_parser('compare', help='在终端计算对照指标')
    cli.add_argument('sim', type=Path)
    cli.add_argument('real', type=Path)
    cli.add_argument('--align', choices=('start', 'manual', 'pwm'), default='start')
    cli.add_argument('--sim-t', type=float, help='手动对齐的仿真时间（秒）')
    cli.add_argument('--real-t', type=float, help='手动对齐的实车时间（秒）')
    cli.add_argument('--side', choices=SIDES, default='left')
    cli.add_argument('--pwm-threshold', type=float, default=.15, help='PWM 对齐时同向有符号比例门槛')
    cli.add_argument('--speed-threshold', type=float, help='PWM 对齐后测响应用速度门槛的大小（m/s）')
    cli.add_argument('--report', type=Path, help='另存指标 JSON，不覆盖已有文件')
    args = parser.parse_args(argv)
    try:
        if args.command in (None, 'gui'):
            import tkinter as tk
            root = tk.Tk()
            CompareWindow(root)
            root.mainloop()
        elif args.command == 'template':
            write_template(args.destination)
            print('已保存空白 CSV。')
        elif args.command == 'normalize':
            write_normalized(load_data(args.source), args.destination)
            print('已提取波形通道，不含串口、图像或位置数据。')
        elif args.command == 'compare':
            if args.speed_threshold is not None and args.align != 'pwm':
                raise ValueError('速度响应时间只适用于 --align pwm。')
            sim = load_data(args.sim)
            real = load_data(args.real, allow_trace=False)
            result = compare(sim, real, args.align, args.sim_t, args.real_t,
                             args.side, args.pwm_threshold, args.speed_threshold)
            align = result['alignment']
            print(f"仿真 {align['sim_t_s']:g} s，实车 {align['real_t_s']:g} s 归零。")
            for channel, metric in result['channels'].items():
                name, unit, _, _ = SIGNALS[channel]
                if metric['points'] < 2:
                    print(f"{name}：有效配对 {metric['points']} 点，数据不足。")
                else:
                    print(f"{name}：{metric['points']} 点；偏差 {metric['mean_error']:+.5g}、"
                          f"MAE {metric['mae']:.5g}、RMSE {metric['rmse']:.5g} {unit}")
            if 'response' in result:
                print(timing_message(result))
            if args.report:
                write_report(result, args.report)
        return 0
    except (OSError, ValueError) as error:
        print(f'波形对照失败：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
