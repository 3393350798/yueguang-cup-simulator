"""从实测 CSV 拟合电机与传感器参数，生成、查看和应用本机标定档案。"""
import copy
import csv
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from hardware_import import ROOT, read_json, write_json


FORMAT = 'moonlight-calibration-v1'
COLUMNS = ('kind', 'channel', 'input', 'measured')
SIDES = ('left', 'right')


def numeric(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('测量值必须是有限数值。')
    return number


def motor_fit(points):
    if not any(x == 0 and y == 0 for x,y in points):
        raise ValueError('电机每侧需包含 PWM=0、轮速=0 的测量。')
    if any(not 0 <= x <= 1 or not 0 <= y <= 10 for x,y in points):
        raise ValueError('电机 PWM 应为 0～1，轮速应为 0～10 m/s。')
    moving = {x for x,y in points if y > 0}
    if len(moving) < 2 or min(moving) == 0:
        raise ValueError('电机每侧至少需要两种能转动的非零 PWM。')
    if not any(x == 1 and y > 0 for x,y in points):
        raise ValueError('电机每侧需包含 PWM=1 时的轮速，以免外推满速。')
    best = None
    for i in range(1001):
        deadzone = min(moving)*i/1001
        dx = [max(0, x-deadzone) for x,_ in points]
        slope = sum(d*y for d,(_,y) in zip(dx,points))/sum(d*d for d in dx)
        error = sum((d*slope-y)**2 for d,(_,y) in zip(dx,points))
        if best is None or error < best[0]:
            best = (error, slope*(1-deadzone), deadzone)
    error, full_speed, deadzone = best
    return dict(full_speed_m_s=round(full_speed,6), deadzone=round(deadzone,6),
                rms_m_s=round(math.sqrt(error/len(points)),6))


def sonar_fit(points):
    if any(not 0 < x <= 10000 or not 0 <= y <= 10000 for x,y in points):
        raise ValueError('超声波距离应以 mm 表示，实际距离需大于零。')
    mean_x = sum(x for x,_ in points)/len(points)
    mean_y = sum(y for _,y in points)/len(points)
    variance = sum((x-mean_x)**2 for x,_ in points)
    if variance <= 0:
        raise ValueError('每路超声波至少需两种不同的实际距离。')
    gain = sum((x-mean_x)*(y-mean_y) for x,y in points)/variance
    if gain <= 0:
        raise ValueError('超声波读数应随实际距离增加。')
    offset = mean_y-gain*mean_x
    error = sum((gain*x+offset-y)**2 for x,y in points)
    return dict(gain=round(gain,8), offset_mm=round(offset,6),
                rms_mm=round(math.sqrt(error/len(points)),6))


def fit_measurements(source):
    with Path(source).open(encoding='utf-8-sig', newline='') as stream:
        table = csv.DictReader(stream)
        if not table.fieldnames or not set(COLUMNS) <= set(table.fieldnames):
            raise ValueError('CSV 表头必须包含 kind,channel,input,measured。')
        groups = {}
        for row in table:
            if not any(row.get(key) for key in COLUMNS):
                continue
            try:
                if any(row[key] is None or not row[key].strip() for key in COLUMNS):
                    raise ValueError('请填写本行的 kind、channel、input、measured。')
                key = (row['kind'].strip().lower(), row['channel'].strip().lower())
                pair = (numeric(row['input']), numeric(row['measured']))
            except (TypeError, ValueError) as error:
                raise ValueError(f'第 {table.line_num} 行：{error}') from error
            groups.setdefault(key, []).append(pair)
    if not groups:
        raise ValueError('CSV 还没有测量数据。')
    profile = dict(format=FORMAT, motor={}, sonar={}, ir={})
    for (kind,channel), points in groups.items():
        if kind == 'motor' and channel in SIDES:
            profile['motor'][channel] = motor_fit(points)
        elif kind == 'sonar' and channel:
            profile['sonar'][channel] = sonar_fit(points)
        elif kind == 'ir' and channel.isdecimal():
            if {x for x,_ in points} != {0,1} or any(not 0 <= y <= 1 for _,y in points):
                raise ValueError(f'红外 {channel}：同时测量黑色 0 和白色 1，读数归一化到 0～1。')
            endpoints = [[y for x,y in points if x == i] for i in (0,1)]
            black, white = (sum(values)/len(values) for values in endpoints)
            profile['ir'][str(int(channel))] = dict(black=round(black,8), white=round(white,8))
        elif kind == 't63' and channel == 'both' and len(points) == 1:
            if points[0][0] != 0 or not .001 <= points[0][1] <= 10:
                raise ValueError('响应时间 t63 应在 0.001～10 秒。')
            profile['response_s'] = points[0][1]
        elif kind == 'encoder' and channel == 'both' and len(points) == 1:
            rounds, counts = points[0]
            if rounds <= 0 or counts <= 0 or not counts.is_integer() or not 1 <= counts/rounds <= 10000000:
                raise ValueError('编码器须提供正的轮圈数和计数增量。')
            profile['encoder_cpr'] = round(counts/rounds)
        else:
            raise ValueError(f'未识别或重复的测量项：{kind}/{channel}')
    if profile['motor'] and set(profile['motor']) != set(SIDES):
        raise ValueError('电机标定需同时测量左、右轮；也可只标定传感器。')
    return profile


def apply_profile(profile, robot, firmware):
    if not isinstance(profile,dict) or profile.get('format') != FORMAT:
        raise ValueError('不是受支持的月光杯标定档案。')
    robot, firmware = copy.deepcopy(robot), copy.deepcopy(firmware)
    motors = profile.get('motor', {})
    if motors:
        if set(motors) != set(SIDES):
            raise ValueError('电机档案必须包含左右轮。')
        speeds = [numeric(motors[side]['full_speed_m_s']) for side in SIDES]
        deadzones = [numeric(motors[side]['deadzone']) for side in SIDES]
        if any(not 0 < v <= 10 for v in speeds) or any(not 0 <= v < 1 for v in deadzones):
            raise ValueError('电机档案中的满速或死区超出范围。')
        robot['wheels']['max_speed_m_s'] = max(speeds)
        robot.setdefault('imperfections', {}).update(
            motor_gain=[v/max(speeds) for v in speeds], motor_deadzone=deadzones)
    if 'response_s' in profile:
        value = numeric(profile['response_s'])
        if not .001 <= value <= 10:
            raise ValueError('响应时间范围为 0.001～10 秒。')
        robot['wheels']['response_s'] = value
    if 'encoder_cpr' in profile:
        value = numeric(profile['encoder_cpr'])
        if not value.is_integer() or not 1 <= value <= 10000000:
            raise ValueError('编码器每轮计数应为正整数。')
        firmware['encoder_cpr'] = int(value)
    sonars = profile.get('sonar', {})
    if sonars:
        names = {sensor['name'] for sensor in robot['sonar']['sensors']}
        options = {}
        for name, values in sonars.items():
            if name not in names:
                raise ValueError('当前小车找不到超声波通道：'+name)
            gain, offset = numeric(values['gain']), numeric(values['offset_mm'])
            if not 0 < gain <= 20 or abs(offset) > 10000:
                raise ValueError('超声波档案的比例或偏移超出范围：'+name)
            options[name] = dict(gain=gain, offset_mm=offset)
        robot.setdefault('calibration', {}).setdefault('sonar', {}).update(options)
    infrared = profile.get('ir', {})
    if infrared:
        count = len(robot['ir']['positions_mm'])
        existing = robot.setdefault('calibration', {}).get('ir', [])
        entries = (existing+[dict(black=0,white=1) for _ in range(count)])[:count]
        for label, values in infrared.items():
            if not label.isdecimal() or not 0 <= int(label) < count:
                raise ValueError('当前小车找不到红外通道：'+label)
            black, white = numeric(values['black']), numeric(values['white'])
            if not 0 <= black <= 1 or not 0 <= white <= 1:
                raise ValueError('红外档案的黑白读数应在 0～1。')
            entries[int(label)] = dict(black=black,white=white)
        robot['calibration']['ir'] = entries
    return robot, firmware


def main():
    window = tk.Tk()
    window.title('月光杯 · 实测数据标定')
    window.geometry('700x500')
    pane = ttk.Frame(window, padding=14)
    pane.pack(fill='both', expand=True)
    profile = None
    ttk.Label(pane, text='测量 → 拟合 → 确认并写入当前小车',
              font=('Microsoft YaHei UI',15)).pack(anchor='w')
    ttk.Label(pane, text='关闭 Webots 后操作。可留空未测项目。CSV 填写说明见 docs/标定与实车迁移.md。').pack(anchor='w', pady=(6,8))
    actions = ttk.Frame(pane)
    actions.pack(anchor='w', pady=(0,6))
    preview = tk.Text(pane, wrap='none', font=('Consolas',10), state='disabled')
    preview.pack(fill='both', expand=True)
    status = tk.StringVar(value='请选择实测 CSV 或已有标定档案。')
    ttk.Label(pane,textvariable=status,wraplength=650).pack(anchor='w',pady=(8,0))

    def template():
        path = filedialog.asksaveasfilename(parent=window, defaultextension='.csv',
                                            initialfile='moonlight-measurements.csv')
        if path:
            Path(path).write_text(','.join(COLUMNS)+'\n',encoding='utf-8-sig')
            status.set('已保存空白 CSV。')

    def set_profile(value):
        nonlocal profile
        apply_profile(value, read_json(ROOT/'robot.json'), read_json(ROOT/'firmware.json'))
        profile = value
        preview.configure(state='normal')
        preview.delete('1.0','end')
        preview.insert('1.0',json.dumps(profile,ensure_ascii=False,indent=2))
        preview.configure(state='disabled')
        save_button.configure(state='normal')
        apply_button.configure(state='normal')
        status.set('已载入可应用的档案，请核对结果。')

    def fit():
        path = filedialog.askopenfilename(parent=window,filetypes=[('测量 CSV','*.csv')])
        if path:
            set_profile(fit_measurements(path))

    def load():
        path = filedialog.askopenfilename(parent=window,filetypes=[('标定 JSON','*.json')])
        if path:
            set_profile(read_json(path))

    def save():
        path = filedialog.asksaveasfilename(parent=window,defaultextension='.json',
                                            initialfile='moonlight-calibration.json')
        if path:
            write_json(path,profile)
            status.set('已保存标定档案。')

    def apply():
        if messagebox.askokcancel('应用标定', '将更新当前小车相应的电机、传感器或编码器配置。', parent=window):
            robot_file, firmware_file = ROOT/'robot.json', ROOT/'firmware.json'
            robot, firmware = apply_profile(profile, read_json(robot_file), read_json(firmware_file))
            write_json(robot_file,robot)
            write_json(firmware_file,firmware)
            status.set('标定已写入。重新启动仿真以生效。')

    def button(label, callback, *, enabled=True):
        def run():
            try:
                callback()
            except (OSError, ValueError, KeyError, TypeError) as error:
                messagebox.showerror('标定档案', str(error), parent=window)
        item = ttk.Button(actions,text=label,command=run,state='normal' if enabled else 'disabled')
        item.pack(side='left',padx=(0,5))
        return item

    button('空白 CSV', template)
    button('拟合 CSV…', fit)
    button('打开档案…', load)
    save_button = button('另存档案…', save, enabled=False)
    apply_button = button('应用到小车', apply, enabled=False)
    window.mainloop()


if __name__ == '__main__':
    main()
