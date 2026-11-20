"""在独立 Webots 场景中检查当前车模的装配方向、感知和夹爪行程。"""
import argparse
from collections import Counter
from pathlib import Path
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from gripper_bench import TOOLS, find_webots, read_json, snapshot_file, stop_webots, write_json


SOURCE = Path(__file__).resolve().parents[1]
STATUSES = {'pass': '符合', 'review': '需核对', 'unobserved': '未观测'}
MARKER = '''
DEF ASSEMBLY_TARGET Solid {
  name "Assembly reference"
  translation 0 0 -3
  children [
    Shape {
      appearance PBRAppearance {
        baseColor 1 0.01 0.75
        emissiveColor 1 0.01 0.75 emissiveIntensity 1 roughness 1 metalness 0
      }
      geometry Box { size 0.007 0.10 0.10 }
    }
  ]
}
'''


def copy_asset(root, dest, reference):
    relative = Path(reference)
    if relative.parts[:2] != ('hardware', 'imported') or '..' in relative.parts:
        raise ValueError('请先通过硬件窗口导入模型；体检只复制项目内的导入资产。')
    copied = snapshot_file(root, dest, relative)
    if copied != dest/relative:
        raise ValueError('导入资产不能通过链接引用；请在硬件窗口重新导入。')
    return copied


def build_snapshot(root, dest, target_mm, report_dir):
    robot = read_json(root/'robot.json')
    board_path = root/'board.json'
    board = read_json(board_path) if board_path.is_file() else {}
    # Keep geometry, including any board shape visible to the camera; use a
    # clean reference environment and never import member code or firmware.
    robot['imperfections'] = {}
    robot['vision_link_port'] = 0
    board = {key: board[key] for key in ('model', 'position_mm') if key in board}
    write_json(dest/'robot.json', robot)
    write_json(dest/'board.json', board)
    (dest/'assets').mkdir()
    shutil.copyfile(SOURCE/'assets'/'competition_field.json', dest/'assets'/'competition_field.json')
    (dest/'tools').mkdir()
    for name in TOOLS:
        shutil.copyfile(SOURCE/'tools'/name, dest/'tools'/name)
    model = robot.get('model', {})
    if model.get('kind') in ('urdf', 'mesh'):
        relative = Path(model['source'])
        asset = copy_asset(root, dest, relative)
        if model['kind'] == 'urdf':
            for mesh in ET.parse(asset).iter('mesh'):
                copy_asset(root, dest, relative.parent/mesh.attrib['filename'])
    if board.get('model'):
        copy_asset(root, dest, board['model']['source'])

    builder = subprocess.run([sys.executable, str(dest/'tools'/'build_scene.py')], cwd=dest,
                             capture_output=True, timeout=45, check=False)
    (report_dir/'build.log').write_bytes(builder.stdout+builder.stderr)
    if builder.returncode:
        raise RuntimeError('无法生成装配体检场景。请先核对车轮绑定、关节限位和模型引用。日志：'+str(report_dir))

    scene = read_json(dest/'scene.json')
    write_json(dest/'assembly_settings.json', {'target_distance_mm': target_mm})
    controller = dest/'controllers'/'assembly_probe'
    controller.mkdir(parents=True)
    shutil.copyfile(SOURCE/'controllers'/'assembly_probe'/'assembly_probe.py', controller/'assembly_probe.py')
    (controller/'runtime.ini').write_text('[python]\nCOMMAND=$(YUEGUANG_PYTHON)\n', encoding='ascii')
    world_dir = dest/'worlds'
    world = (world_dir/'moonlight.wbt').read_text(encoding='utf-8')
    header, wall, _ = world.partition('DEF WALL_0 Solid')
    _, car_def, car = world.partition('DEF CAR ')
    floor = 'baseColorMap ImageTexture { url [ "textures/track.png" ] filtering 5 }'
    controller_binding = 'name "Moonlight" controller "moonlight" supervisor TRUE window "moonlight"'
    if not wall or not car_def or floor not in header or controller_binding not in car:
        raise RuntimeError('赛场生成器与装配体检场景不兼容。')
    header = (header.replace('position 0 0 6.1 fieldOfView', 'position 0 0 0.8 fieldOfView', 1)
                    .replace('appearance PBRAppearance {\n    '+floor+'\n    roughness 1',
                             'appearance DEF ASSEMBLY_FLOOR PBRAppearance {\n    baseColor 1 1 1\n    roughness 0', 1))
    if 'DEF ASSEMBLY_FLOOR PBRAppearance' not in header:
        raise RuntimeError('无法绑定黑白体检地面。')
    car = car.replace(controller_binding,
                      'name "Assembly probe" controller "assembly_probe" supervisor TRUE window ""', 1)
    # Import already enables self-collision when a gripper is present. Enable
    # it only in this copy for built-in physical fingertips as well.
    if scene['gripper']['kind'] != 'none' and 'selfCollision TRUE' not in car:
        car = car.replace('supervisor TRUE window ""', 'supervisor TRUE window "" selfCollision TRUE', 1)
    world_path = world_dir/'assembly_probe.wbt'
    world_path.write_text(header+MARKER+car_def+car, encoding='utf-8')
    return world_path


def choose_port(requested):
    # There is no streaming flag: the native simulation's backend is isolated
    # from the training panel, including when that panel is already open.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', requested))
        except OSError as error:
            raise ValueError(f'端口 {requested} 不可用，请改用其他端口或留空自动分配。') from error
        return probe.getsockname()[1]


def run_check(options, root=SOURCE, on_status=print, cancelled=lambda: False):
    webots = find_webots(options.webots_home)
    target_mm = float(options.target_mm)
    if not math.isfinite(target_mm) or not 150 <= target_mm <= 1000:
        raise ValueError('标靶离车体原点应为 150～1000 mm。')
    port = choose_port(options.port)
    output = SOURCE.parent/'output'
    output.mkdir(exist_ok=True)
    report_dir = Path(tempfile.mkdtemp(prefix='assembly-check-', dir=output))
    with tempfile.TemporaryDirectory(prefix='.assembly-scene-', dir=output) as tmp:
        dest = Path(tmp)
        on_status('正在生成隔离场景和车模副本…')
        world = build_snapshot(root.resolve(), dest, target_mm, report_dir)
        if cancelled():
            raise RuntimeError('已取消体检。')
        env = dict(os.environ, PYTHONUTF8='1', PYTHONUNBUFFERED='1', YUEGUANG_PYTHON=sys.executable)
        env['PATH'] = str(Path(sys.executable).parent)+os.pathsep+env.get('PATH', '')
        flags = ['--mode=realtime'] if options.show else ['--mode=fast', '--no-rendering']
        creation = subprocess.CREATE_NO_WINDOW if os.name=='nt' and not options.show else 0
        result = dest/'report.json'
        on_status('正在启动 Webots 体检；当前练习进程不会关闭。')
        with (report_dir/'webots-out.log').open('wb') as out, (report_dir/'webots-err.log').open('wb') as err:
            process = subprocess.Popen([str(webots), '--batch', '--stdout', '--stderr', f'--port={port}',
                                        *flags, str(world)], cwd=dest, env=env, stdout=out, stderr=err,
                                       creationflags=creation, start_new_session=os.name!='nt')
            try:
                deadline = time.monotonic()+110
                previous = None
                while not result.is_file():
                    if cancelled():
                        raise RuntimeError('已取消体检。')
                    if time.monotonic() >= deadline:
                        raise RuntimeError('等待体检超时，请查看本机日志：'+str(report_dir))
                    try:
                        stage = read_json(dest/'progress.json')['stage']
                    except (OSError, ValueError, KeyError):
                        pass
                    else:
                        if stage != previous:
                            on_status(stage)
                            previous = stage
                    try:
                        process.wait(timeout=.20)
                    except subprocess.TimeoutExpired:
                        continue
                    if not result.is_file():
                        raise RuntimeError('Webots 提前退出，请查看本机日志：'+str(report_dir))
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            finally:
                stop_webots(process)
        report = read_json(result)
        if 'error' in report:
            raise RuntimeError(report['error']+'（本机日志：'+str(report_dir)+'）')
        if report.get('format') != 'moonlight-assembly-check' or report.get('version') != 1:
            raise RuntimeError('装配体检报告格式不符。')
        for name in ('camera-center.png', 'camera-left.png', 'camera-right.png', 'camera-empty.png'):
            if (dest/name).is_file():
                shutil.copyfile(dest/name, report_dir/name)
        write_json(report_dir/'report.json', report)
    return report_dir, report


def summary(report):
    counts = Counter(row['status'] for row in report['checks'])
    return ' · '.join(f'{label} {counts[key]}' for key, label in STATUSES.items())


def gui(options):
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, ttk

    window = tk.Tk()
    window.title('月光杯 · 装配体检')
    window.minsize(790, 480)
    main = ttk.Frame(window, padding=14)
    main.pack(fill='both', expand=True)
    main.columnconfigure(1, weight=1)
    main.rowconfigure(5, weight=1)
    status = tk.StringVar(value='按下开始后，在独立 Webots 场景测试当前工程的车模。')
    home = tk.StringVar(value=options.webots_home)
    target = tk.StringVar(value=f'{options.target_mm:g}')
    show = tk.BooleanVar(value=options.show)
    ttk.Label(main, text='Webots 安装目录（留空自动找）').grid(row=0, column=0, padx=(0, 10), sticky='w')
    ttk.Entry(main, textvariable=home).grid(row=0, column=1, sticky='ew')
    ttk.Button(main, text='选择…', command=lambda: (path:=filedialog.askdirectory()) and home.set(path)).grid(
        row=0, column=2, padx=(8, 0), sticky='ew')
    ttk.Label(main, text='标靶离车体原点（mm）').grid(row=1, column=0, sticky='w', pady=6)
    ttk.Entry(main, textvariable=target, width=12).grid(row=1, column=1, sticky='w')
    ttk.Checkbutton(main, text='显示 Webots 场景', variable=show).grid(row=1, column=2, sticky='e')
    actions = ttk.Frame(main)
    actions.grid(row=3, column=0, columnspan=3, pady=(3, 10), sticky='ew')
    ttk.Label(main, textvariable=status, wraplength=740).grid(row=4, column=0, columnspan=3, pady=(0, 8), sticky='w')
    frame = ttk.Frame(main)
    frame.grid(row=5, column=0, columnspan=3, sticky='nsew')
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    cols = [('item', '检查项', 145), ('status', '结果', 75), ('detail', '观测和建议', 515)]
    table = ttk.Treeview(frame, columns=[c[0] for c in cols], show='headings', selectmode='browse')
    table.grid(row=0, column=0, sticky='nsew')
    for key, label, width in cols:
        table.heading(key, text=label)
        table.column(key, width=width, stretch=key=='detail')
    scroll = ttk.Scrollbar(frame, orient='vertical', command=table.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    table.configure(yscrollcommand=scroll.set)
    mailbox = queue.Queue()
    cancel_event = threading.Event()
    pending = closing = False
    result_path = None

    def receive():
        nonlocal pending, result_path
        if not pending:
            return
        try:
            key, msg = mailbox.get_nowait()
        except queue.Empty:
            window.after(120, receive)
            return
        if key == 'status':
            if not cancel_event.is_set():
                status.set(msg)
            window.after(120, receive)
            return
        pending = False
        if closing:
            window.destroy()
            return
        launch_btn.configure(state='normal')
        cancel_btn.configure(state='disabled')
        if key == 'error':
            status.set(str(msg))
            return
        result_path, report = msg
        for row in report['checks']:
            table.insert('', 'end', values=(row['label'], STATUSES[row['status']], row['detail']))
        folder_btn.configure(state='normal')
        status.set(summary(report)+'。详情见结果目录中的报告和相机图片。')

    def launch():
        nonlocal pending, result_path
        chosen = argparse.Namespace(webots_home=home.get(), port=options.port, show=show.get(), target_mm=target.get())
        pending = True
        result_path = None
        cancel_event.clear()
        table.delete(*table.get_children())
        launch_btn.configure(state='disabled')
        cancel_btn.configure(state='normal')
        folder_btn.configure(state='disabled')

        def worker():
            try:
                result = run_check(chosen, on_status=lambda s: mailbox.put(('status', s)), cancelled=cancel_event.is_set)
            except Exception as error:
                mailbox.put(('error', error))
            else:
                mailbox.put(('result', result))

        threading.Thread(target=worker, daemon=True).start()
        window.after(120, receive)

    def stop():
        cancel_event.set()
        cancel_btn.configure(state='disabled')
        status.set('正在停止本次体检…')

    def close():
        nonlocal closing
        if pending:
            closing = True
            stop()
        else:
            window.destroy()

    def open_folder():
        if result_path:
            if os.name == 'nt':
                os.startfile(result_path)
            else:
                subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open', str(result_path)])

    launch_btn = ttk.Button(actions, text='开始体检', command=launch)
    launch_btn.pack(side='left')
    cancel_btn = ttk.Button(actions, text='停止', command=stop, state='disabled')
    cancel_btn.pack(side='left', padx=8)
    folder_btn = ttk.Button(actions, text='打开结果目录', command=open_folder, state='disabled')
    folder_btn.pack(side='left')
    window.protocol('WM_DELETE_WINDOW', close)
    window.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui', action='store_true', help='打开体检窗口')
    parser.add_argument('--webots-home', default='', help='Webots 安装目录')
    parser.add_argument('--show', action='store_true', help='显示 Webots 场景')
    parser.add_argument('--port', type=int, default=0, help='独立 Webots 端口，0 自动分配')
    parser.add_argument('--target-mm', type=float, default=350, help='标靶离车体原点的前向距离，150～1000 mm')
    options = parser.parse_args(argv)
    if not 0 <= options.port <= 65535:
        parser.error('端口须在 0～65535 之间。')
    try:
        if options.gui:
            gui(options)
        else:
            folder, report = run_check(options)
            print(summary(report))
            for row in report['checks']:
                print(f"{row['label']}：{STATUSES[row['status']]}。{row['detail']}")
            print('本地报告：'+str(folder))
    except KeyboardInterrupt:
        print('已取消体检。', file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, RuntimeError, ET.ParseError, subprocess.TimeoutExpired) as error:
        print('未完成体检：'+str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
