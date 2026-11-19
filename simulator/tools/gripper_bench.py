"""独立的 Webots 夹爪测试台启动器。"""
import argparse
import csv
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


SOURCE = Path(__file__).resolve().parents[1]
TOOLS = ('build_scene.py', 'hardware_import.py', 'imported_robot.py',
         'competition_block.py', 'gripper_model.py', 'imperfections.py')
AXES = {
    'x_mm': ('前后偏移 (mm)', '0', -100, 100),
    'y_mm': ('左右偏移 (mm)', '0,-8,8', -100, 100),
    'yaw_deg': ('物块偏角 (°)', '0', -180, 180),
    'tilt_deg': ('物块初始倾角 (°)', '0', -30, 30),
    'friction': ('夹持摩擦系数', '', 0, 5),
    'mass_g': ('物块质量 (g)', '', 1, 500),
}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path, value):
    with path.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def number(value, low, high, label):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{label}须为数字。') from error
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'{label}须介于 {low} 和 {high} 之间。')
    return result


def cases(values, repeat, robot):
    if not 1 <= repeat <= 10:
        raise ValueError('重复次数须介于 1 和 10 之间。')
    axes = []
    grip_default = robot.get('model', {}).get('gripper', {}) if robot.get('model', {}).get('kind') == 'urdf' else robot.get('gripper', {})
    defaults = {'friction': grip_default.get('friction', .9 if robot.get('model', {}).get('kind') == 'urdf' else .85),
                'mass_g': 1000*robot.get('block', {}).get('mass_kg', .04)}
    for key, (label, preset, low, high) in AXES.items():
        raw = values[key].strip()
        if raw:
            options = raw.replace('，', ',').split(',')
        elif key in defaults:
            options = [defaults[key]]
        else:
            options = preset.split(',')
        axes.append(list(dict.fromkeys(number(item, low, high, label) for item in options)))
    if math.prod(map(len, axes))*repeat > 64:
        raise ValueError('笛卡尔组合乘以重复次数最多 64 轮。')
    return [dict(zip(AXES, combination), repeat=i+1)
            for combination in itertools.product(*axes) for i in range(repeat)]


def find_webots(hint=''):
    if hint:
        entries = [Path(hint)]
    else:
        system = os.environ.get('ProgramFiles')
        path_hit = shutil.which('webots.exe' if os.name == 'nt' else 'webots')
        entries = [Path(item) for item in (os.environ.get('WEBOTS_HOME'), path_hit) if item]
        if os.name == 'nt':
            import winreg
            for base in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(base, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Webots_is1') as key:
                        entries.append(Path(winreg.QueryValueEx(key, 'InstallLocation')[0]))
                except OSError:
                    pass
        if system:
            entries.append(Path(system)/'Webots')
    for path in entries:
        for candidate in (path, path/'msys64'/'mingw64'/'bin'/'webots.exe', path/'webots'):
            if candidate.is_file():
                return candidate.resolve()
    raise ValueError('找不到 Webots；请填写安装目录，或设置 WEBOTS_HOME。')


def snapshot_file(root, dest, path):
    root = root.resolve()
    source = (root/path).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('模型引用需位于仿真目录下，找不到时请重新导入。')
    relative = source.relative_to(root)
    target = dest/relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return target


def build_snapshot(root, dest, robot, bench):
    tools = dest/'tools'
    tools.mkdir(parents=True)
    for name in TOOLS:
        shutil.copyfile(SOURCE/'tools'/name, tools/name)
    # Camera, sensor, board and firmware data do not participate in this fixture.
    write_json(dest/'robot.json', robot)
    write_json(dest/'board.json', {})
    (dest/'assets').mkdir()
    shutil.copyfile(SOURCE/'assets'/'competition_field.json', dest/'assets'/'competition_field.json')
    attachment = robot.get('model', {}).get('source')
    model_assets = []
    if robot.get('model', {}).get('kind') in ('mesh', 'urdf') and attachment:
        model_assets.append(snapshot_file(root, dest, attachment))
        if robot['model']['kind'] == 'urdf':
            for mesh in ET.parse(model_assets[0]).iter('mesh'):
                model_assets.append(snapshot_file(root, dest, Path(attachment).parent/mesh.attrib['filename']))
    design = hashlib.sha256()
    design.update((dest/'robot.json').read_bytes())
    for file in sorted(set(model_assets)):
        with file.open('rb') as source:
            design.update(hashlib.file_digest(source, 'sha256').digest())
    bench['design_sha256'] = design.hexdigest()
    builder = subprocess.run([sys.executable, str(tools/'build_scene.py')], cwd=dest, capture_output=True,
                             text=True, encoding='utf-8', errors='replace', timeout=40, check=False)
    if builder.returncode:
        lines = (builder.stderr.strip() or builder.stdout.strip()).splitlines()
        raise RuntimeError('台架场景生成失败：'+(lines[-1] if lines else f'返回码 {builder.returncode}'))
    scene = read_json(dest/'scene.json')
    if not scene['gripper']['jaws'] or not scene['gripper']['lift']:
        raise ValueError('先在硬件配置中绑定夹爪开合、升降关节和至少两个夹持面。')
    write_json(dest/'bench.json', bench)
    controller = dest/'controllers'/'gripper_bench'
    controller.mkdir(parents=True)
    for file in (SOURCE/'controllers'/'gripper_bench'/'gripper_bench.py',
                 SOURCE/'controllers'/'moonlight'/'physical_gripper.py'):
        shutil.copyfile(file, controller/file.name)
    world_dir = dest/'worlds'
    world = (world_dir/'moonlight.wbt').read_text(encoding='utf-8')
    # Reuse the generated contact materials, block and robot (always the final node).
    header, separator, _ = world.partition('DEF WALL_0 Solid')
    _, block_sep, other = world.partition('DEF BLOCK Solid')
    if not separator or not block_sep or 'DEF CAR ' not in other or 'controller "moonlight"' not in other:
        raise ValueError('生成的赛场结构与夹爪台架不兼容。')
    header = (header.replace('WorldInfo {', 'DEF BENCH_WORLD WorldInfo {', 1)
              .replace('position 0 0 6.1 fieldOfView', 'position 0 0 0.8 fieldOfView', 1)
              .replace('baseColorMap ImageTexture { url [ "textures/track.png" ] filtering 5 }', 'baseColor 0.83 0.86 0.88', 1))
    other = other.replace('name "Moonlight" controller "moonlight" supervisor TRUE window "moonlight"',
                          'name "Gripper bench" controller "gripper_bench" supervisor TRUE', 1)
    if 'controller "gripper_bench"' not in other:
        raise ValueError('生成的车体未能绑定夹爪台架控制器。')
    world_path = world_dir/'gripper_bench.wbt'
    world_path.write_text(header+block_sep+other, encoding='utf-8')
    return world_path


def stop_webots(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        try:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'], capture_output=True,
                           timeout=5, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def run_bench(options, root=SOURCE, on_status=print, cancelled=lambda: False):
    root = root.resolve()
    robot = read_json(root/'robot.json')
    test_cases = cases(vars(options), options.repeat, robot)
    center = {key: (number(getattr(options, key), -500, 1000, label) if getattr(options, key) else None)
              for key, label in [('center_x_mm', '中心 X'), ('center_y_mm', '中心 Y')]}
    bench = dict(cases=test_cases, **center)
    webots = find_webots(options.webots_home)
    output = SOURCE.parent/'output'
    output.mkdir(exist_ok=True)
    dest = Path(tempfile.mkdtemp(prefix='gripper-bench-', dir=output))
    on_status(f'正在生成 {len(test_cases)} 轮测试。')
    world = build_snapshot(root, dest, robot, bench)
    if cancelled():
        raise RuntimeError('已取消测试。')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1', YUEGUANG_PYTHON=sys.executable)
    env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH', '')
    flags = ['--mode=realtime'] if options.show else ['--mode=fast', '--no-rendering']
    result = dest/'report.json'
    progress = dest/'progress.json'
    on_status('正在启动 Webots 并加载夹爪。')
    with (dest/'webots-out.log').open('wb') as out, (dest/'webots-err.log').open('wb') as err:
        process = subprocess.Popen([str(webots), '--batch', '--stdout', '--stderr', *flags, str(world)],
                                   env=env, cwd=dest, stdout=out, stderr=err, start_new_session=os.name != 'nt')
        deadline = time.monotonic()+40+45*len(test_cases)
        previous = None
        try:
            while not result.is_file():
                if cancelled():
                    raise RuntimeError('已取消测试。')
                if time.monotonic() >= deadline:
                    raise RuntimeError(f'台架超时，请查看本地日志：{dest}')
                try:
                    value = read_json(progress)['completed']
                except OSError:
                    pass
                else:
                    if value != previous:
                        previous = value
                        on_status(f'已完成 {value} / {len(test_cases)} 轮。')
                try:
                    process.wait(timeout=.2)
                except subprocess.TimeoutExpired:
                    continue
                if not result.is_file():
                    raise RuntimeError(f'Webots 在台架报告生成前结束，请查看本地日志：{dest}')
            # Wait for the controller to quit cleanly after saving its report.
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                pass
        finally:
            stop_webots(process)
    report = read_json(result)
    if 'error' in report:
        raise RuntimeError(report['error'])
    if len(report['trials']) != len(test_cases):
        raise RuntimeError('台架报告的轮数不完整。')
    with (dest/'report.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, list(report['trials'][0]))
        writer.writeheader()
        writer.writerows(report['trials'])
    return dest, report


def gui(defaults):
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, ttk

    window = tk.Tk()
    window.title('月光杯 · 夹爪测试台')
    window.minsize(930, 590)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    form = ttk.Frame(window, padding=14)
    form.grid(sticky='ew')
    form.columnconfigure(1, weight=1)
    form.columnconfigure(3, weight=1)
    fields = {}
    inputs = [(key, label) for key, (label,*_) in AXES.items()]+[
        ('repeat', '重复次数'), ('center_x_mm', '抓取中心 X (mm)'), ('center_y_mm', '抓取中心 Y (mm)')]
    for index,(key,label) in enumerate(inputs):
        row,col = divmod(index,2)
        if key in ('friction','mass_g','center_x_mm','center_y_mm'):
            label += ' · 留空自动'
        ttk.Label(form,text=label).grid(row=row,column=col*2,padx=(0,8),pady=4,sticky='w')
        fields[key] = tk.StringVar(value=getattr(defaults,key))
        ttk.Entry(form,textvariable=fields[key],width=20).grid(row=row,column=col*2+1,padx=(0,16),sticky='ew')
    path = tk.StringVar(value=defaults.webots_home)
    ttk.Label(form,text='Webots 安装目录').grid(row=5,column=0,sticky='w',pady=4)
    ttk.Entry(form,textvariable=path).grid(row=5,column=1,columnspan=2,sticky='ew',padx=(0,16))
    ttk.Button(form,text='选择目录',command=lambda: (folder:=filedialog.askdirectory()) and path.set(folder)).grid(row=5,column=3,sticky='w')
    show = tk.BooleanVar(value=defaults.show)
    ttk.Checkbutton(form,text='显示 Webots 场景',variable=show).grid(row=6,column=0,sticky='w')
    status = tk.StringVar(value='等待测试')
    ttk.Label(form,textvariable=status).grid(row=7,column=0,columnspan=4,pady=(8,0),sticky='w')
    columns = {'x_mm':'前后 mm','y_mm':'左右 mm','yaw_deg':'偏角 °','tilt_deg':'倾角 °',
               'friction':'摩擦','mass_g':'质量 g','repeat':'轮次','lifted':'提起','dropped':'掉落',
               'max_clearance_mm':'最高 mm','landing_stable':'落稳',
               'landing_offset_mm':'落点偏差 mm'}
    frame = ttk.Frame(window,padding=(14,0,14,14))
    frame.grid(row=1,sticky='nsew')
    frame.columnconfigure(0,weight=1)
    frame.rowconfigure(0,weight=1)
    table = ttk.Treeview(frame,columns=list(columns),show='headings',selectmode='browse')
    table.grid(row=0,column=0,sticky='nsew')
    scrollbar = ttk.Scrollbar(frame,command=table.yview)
    scrollbar.grid(row=0,column=1,sticky='ns')
    table.configure(yscrollcommand=scrollbar.set)
    for key,label in columns.items():
        table.heading(key,text=label)
        table.column(key,width=91 if key=='landing_offset_mm' else 66,anchor='center')
    mailbox = queue.Queue()
    last = None
    active = False
    cancelled = threading.Event()
    closing = False

    def open_result():
        if last and os.name == 'nt':
            os.startfile(last)
        elif last:
            subprocess.Popen(['xdg-open',str(last)])

    folder_button = ttk.Button(form,text='打开结果目录',command=open_result,state='disabled')
    folder_button.grid(row=6,column=3,sticky='w')

    def receive():
        nonlocal last,active
        try:
            kind,value = mailbox.get_nowait()
        except queue.Empty:
            window.after(150,receive)
            return
        if kind=='status':
            status.set(value)
            window.after(150,receive)
            return
        active = False
        if closing:
            window.destroy()
            return
        start.configure(state='normal')
        cancel.configure(state='disabled')
        if kind=='error':
            status.set('测试失败：'+str(value))
            return
        last,report = value
        for row in report['trials']:
            table.insert('','end',values=[('是' if row[key] else '否') if isinstance(row[key],bool)
                                          else '—' if row[key] is None else row[key] for key in columns])
        summary = report['summary']
        status.set(f"完成 {summary['trials']} 轮 · 提起 {summary['lifted']} · 掉落 {summary['dropped']} · 已生成 JSON 和 CSV")
        folder_button.configure(state='normal')

    def launch():
        nonlocal active
        options = argparse.Namespace(**{key:value.get() for key,value in fields.items()},
                                     webots_home=path.get(),show=show.get())
        try:
            options.repeat = int(options.repeat)
        except ValueError:
            status.set('重复次数须为整数。')
            return
        active = True
        cancelled.clear()
        table.delete(*table.get_children())
        start.configure(state='disabled')
        cancel.configure(state='normal')
        folder_button.configure(state='disabled')

        def worker():
            try:
                result = run_bench(options, on_status=lambda note:mailbox.put(('status',note)), cancelled=cancelled.is_set)
            except Exception as error:
                mailbox.put(('error',error))
            else:
                mailbox.put(('result',result))

        threading.Thread(target=worker,daemon=True).start()
        window.after(150,receive)

    start = ttk.Button(form,text='开始测试',command=launch)
    start.grid(row=6,column=1,sticky='w')
    def stop():
        cancelled.set()
        status.set('正在停止本次测试。')
        cancel.configure(state='disabled')

    cancel = ttk.Button(form,text='停止测试',command=stop,state='disabled')
    cancel.grid(row=6,column=2,sticky='w')

    def close():
        nonlocal closing
        if active:
            closing = True
            stop()
        else:
            window.destroy()

    window.protocol('WM_DELETE_WINDOW', close)
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui', action='store_true')
    parser.add_argument('--webots-home', default='', help='Webots 安装目录')
    parser.add_argument('--show', action='store_true', help='显示 Webots 场景')
    parser.add_argument('--repeat', default=1,type=int,help='每个组合的重复轮数')
    parser.add_argument('--center-x-mm', default='',help='覆盖默认抓取中心 X')
    parser.add_argument('--center-y-mm', default='',help='覆盖默认抓取中心 Y')
    for key,(label,preset,*_) in AXES.items():
        parser.add_argument('--'+key.replace('_','-'),default=preset,help=label+'；逗号分隔')
    options = parser.parse_args()
    if options.gui:
        gui(options)
        return 0
    try:
        folder,report = run_bench(options)
        summary = report['summary']
        print(f"完成 {summary['trials']} 轮 · 提起 {summary['lifted']} · 掉落 {summary['dropped']}")
        print(f'报告：{folder}')
    except KeyboardInterrupt:
        print('已取消测试。', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'测试失败：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
