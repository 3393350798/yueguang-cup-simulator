"""在本机打包队伍配置，并在独立目录还原。"""
from pathlib import Path, PurePosixPath
import json
import re
import shutil
import stat
import tempfile
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ('robot.json', 'board.json', 'firmware.json')
ALGORITHM = 'controllers/moonlight/member_algorithm.py'
MANIFEST = 'manifest.json'
FORMAT = {'format': 'moonlight-team', 'version': 1}
ASSET = re.compile(r'hardware/imported/[a-z0-9_-]{1,64}/[a-z0-9_-]{1,64}\.(urdf|stl|json|elf|axf|hex|bin)')
RESERVED = re.compile(r'(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?')
LIMIT = 512 * 1024 * 1024
APP_FILES = ('.gitignore', 'LICENSE', 'README.md', 'setup.bat', 'start.bat', 'start.sh')
APP_DIRS = ('docs', 'requirements', 'scripts', 'simulator')


def asset_path(name, extensions):
    if not isinstance(name, str) or not ASSET.fullmatch(name) or PurePosixPath(name).suffix not in extensions:
        raise ValueError('资产路径无效，请在硬件配置窗口重新导入：'+repr(name))
    if any(RESERVED.fullmatch(part) for part in name.split('/')):
        raise ValueError('资产路径使用了系统保留名称：'+name)
    return name


def member_assets(configs, read):
    robot, board, firmware = (configs[name] for name in CONFIGS)
    members = set()
    model = robot.get('model') or {}
    mechanical = model.get('kind', 'parametric')
    if mechanical == 'urdf':
        source = asset_path(model.get('source'), {'.urdf'})
        members.add(source)
        assembly = ET.fromstring(read(source))
        for mesh in assembly.iter('mesh'):
            filename = mesh.get('filename', '')
            if not filename or PurePosixPath(filename).name != filename:
                raise ValueError('装配体网格必须是同目录下的本机 STL：'+filename)
            path = (PurePosixPath(source).parent/filename).as_posix()
            members.add(asset_path(path, {'.stl'}))
    elif mechanical == 'mesh':
        members.add(asset_path(model.get('source'), {'.stl'}))
    elif mechanical != 'parametric':
        raise ValueError('未知车模类型：'+str(mechanical))
    for value, extensions in (
        ((board.get('model') or {}).get('source'), {'.stl'}),
        (board.get('netlist'), {'.json'}),
        (firmware.get('image'), {'.elf', '.axf', '.hex', '.bin'}),
    ):
        if value:
            members.add(asset_path(value, extensions))
    return members


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2)+'\n').encode('utf-8')


def export_project(target, root=ROOT):
    root, target = Path(root), Path(target)
    imported = (root/'hardware/imported').resolve()

    def local_asset(name):
        path = (root/name).resolve()
        if not path.is_relative_to(imported) or not path.is_file():
            raise ValueError('找不到当前项目正在使用的资产：'+name)
        return path

    configs = {name:json.loads((root/name).read_bytes()) for name in CONFIGS}
    configs['firmware.json']['renode'] = ''
    assets = {name:local_asset(name) for name in member_assets(configs, lambda name: local_asset(name).read_bytes())}
    algorithm = root/ALGORITHM
    metadata = {MANIFEST:encode(FORMAT), **{name:encode(data) for name,data in configs.items()}}
    if sum(len(data) for data in metadata.values())+sum(p.stat().st_size for p in [algorithm,*assets.values()]) > LIMIT:
        raise ValueError('队伍项目包解压后不能超过 512 MiB。')
    with tempfile.TemporaryDirectory(prefix='.moonlight-', dir=target.parent) as folder:
        temporary = Path(folder)/'team.zip'
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in metadata.items():
                archive.writestr(name,data)
            archive.write(algorithm,ALGORITHM)
            for name,path in sorted(assets.items()):
                archive.write(path,name)
        temporary.replace(target)
    return target


def archive_members(archive):
    allowed = {MANIFEST, ALGORITHM, *CONFIGS}
    entries = {}
    total = 0
    for entry in archive.infolist():
        name = entry.filename
        if name not in allowed:
            asset_path(name, {'.urdf', '.stl', '.json', '.elf', '.axf', '.hex', '.bin'})
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if entry.orig_filename != name or entry.is_dir() or mode not in (0, stat.S_IFREG) or name in entries:
            raise ValueError('压缩包条目无效或重复：'+name)
        entries[name] = entry
        total += entry.file_size
    if not allowed <= entries.keys() or total > LIMIT:
        raise ValueError('队伍项目包缺少必需文件，或解压后超过 512 MiB。')
    return entries


def ignore_copy(folder, names):
    excluded = {'.git', '.local', '.venv', 'output', '__pycache__', 'imported',
                'conversation-notes.md', '验证记录.md', 'scene.json', 'scene.js', 'moonlight.wbt'}
    return [name for name in names if name in excluded or name.startswith('.') or name.endswith('.log')]


def import_project(source, target, app=ROOT.parent):
    source, target, app = Path(source), Path(target), Path(app)
    if target.exists() or target.is_symlink():
        raise FileExistsError('目标项目目录已存在：'+str(target))
    # 创建在当前仓库之外，避免递归复制工程或误把成员文件加入原仓库。
    if target.resolve().is_relative_to(app.resolve()):
        raise ValueError('请选择当前仿真目录之外的位置。')
    with zipfile.ZipFile(source) as archive:
        entries = archive_members(archive)
        if json.loads(archive.read(MANIFEST)) != FORMAT:
            raise ValueError('这个队伍项目包的格式版本无法打开。')
        configs = {name:json.loads(archive.read(name)) for name in CONFIGS}
        assets = member_assets(configs, archive.read)
        expected = {MANIFEST, ALGORITHM, *CONFIGS, *assets}
        if expected != entries.keys():
            raise ValueError('项目包的文件和配置引用不一致。')
        configs['firmware.json']['renode'] = ''
        with tempfile.TemporaryDirectory(prefix='.moonlight-', dir=target.parent) as folder:
            project = Path(folder)/'project'
            project.mkdir()
            for name in APP_FILES:
                shutil.copy2(app/name, project/name)
            for name in APP_DIRS:
                shutil.copytree(app/name, project/name, ignore=ignore_copy)
            simulator = project/'simulator'
            for name,data in configs.items():
                (simulator/name).write_bytes(encode(data))
            for name in sorted(assets | {ALGORITHM}):
                destination = simulator/name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entries[name]) as stream, destination.open('wb') as out:
                    shutil.copyfileobj(stream,out)
            project.rename(target)
    return target


def main():
    window = tk.Tk()
    window.title('月光杯 · 队伍项目')
    window.minsize(510, 180)
    status = tk.StringVar(value=str(ROOT.parent))
    frame = ttk.Frame(window, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='队伍项目', font=('Microsoft YaHei UI', 16)).pack(anchor='w')
    bar = ttk.Frame(frame)
    bar.pack(anchor='w', pady=14)
    formats = [('队伍项目包', '*.moonlight.zip'), ('ZIP 文件', '*.zip')]

    def action(callback, success):
        try:
            result = callback()
            if result:
                status.set(success+'：'+str(result))
        except Exception as error:
            messagebox.showerror('队伍项目', str(error), parent=window)

    def export():
        path = filedialog.asksaveasfilename(parent=window, title='导出队伍项目', filetypes=formats,
                                            initialfile='队伍项目.moonlight.zip', defaultextension='.moonlight.zip')
        return export_project(path) if path else None

    def restore():
        path = filedialog.askopenfilename(parent=window, title='打开队伍项目包', filetypes=formats)
        if not path:
            return None
        folder = filedialog.askdirectory(parent=window, title='选择新项目所在的上级目录')
        name = Path(path).stem.removesuffix('.moonlight')
        return import_project(path, Path(folder)/name) if folder else None

    ttk.Button(bar, text='导出项目包…', command=lambda:action(export,'已导出')).pack(side='left')
    ttk.Button(bar, text='导入到新目录…', command=lambda:action(restore,'已创建')).pack(side='left', padx=8)
    ttk.Label(frame, textvariable=status, wraplength=560).pack(anchor='w')
    window.mainloop()


if __name__ == '__main__':
    main()
