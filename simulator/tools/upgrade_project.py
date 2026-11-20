"""预览并将队伍配置迁移到一份独立的新版项目。"""
import argparse
import ast
from dataclasses import dataclass
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from xml.etree.ElementTree import ParseError

from team_project import ALGORITHM, APP_DIRS, APP_FILES, CONFIGS, LIMIT, encode, member_assets


APP = Path(__file__).resolve().parents[2]
CONTROLLER = PurePosixPath(ALGORITHM).parent
SAFE_APP_SUFFIXES = {
    'docs': {'.md', '.png', '.jpg', '.jpeg', '.svg', '.webp'},
    'requirements': {'.txt'},
    'scripts': {'.bat', '.ps1', '.sh', '.py'},
    'simulator': {'.py', '.ini', '.png', '.jpg', '.jpeg', '.svg', '.json', '.txt',
                  '.urdf', '.net', '.js', '.css', '.html', '.cs', '.repl', '.wbt', '.proto'},
}
SKIP = {'output', '__pycache__', 'imported', 'conversation-notes.md', '验证记录.md'}
GENERATED = {'simulator/scene.json', 'simulator/worlds/moonlight.wbt',
             'simulator/plugins/robot_windows/moonlight/scene.js'}
REQUIRED_APP = {'VERSION', 'setup.bat', 'simulator/tools/upgrade_project.py',
                'simulator/tools/build_scene.py', 'simulator/assets/competition_field.json',
                'scripts/upgrade-project.bat', f'simulator/{ALGORITHM}'}
CORE_MODULES = {'numpy', 'cv2', 'PIL', 'urdf2webots', 'trimesh', 'collada'}
RESERVED = re.compile(r'(?i)(con|conin\$|conout\$|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?\Z')
VERSION_RE = re.compile(r'v\d{1,3}\.\d{1,3}\.\d{1,3}\Z')


def is_link(info):
    tag = getattr(info, 'st_reparse_tag', 0)
    return stat.S_ISLNK(info.st_mode) or bool(tag and tag & 0x20000000)


def valid_part(value):
    return (bool(value) and not value.endswith(('.', ' ')) and not value.startswith('.') and len(value) <= 160
            and not RESERVED.fullmatch(value) and all(c not in '<>:"/\\|?*' and ord(c) >= 32 for c in value))


def checked_file(root, relative):
    current = root
    for component in PurePosixPath(relative).parts:
        if component in ('..', '.') or '/' in component or '\\' in component:
            raise ValueError(f'文件超出项目：{relative}')
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            raise ValueError(f'找不到需要的文件：{relative}') from None
        if is_link(info):
            raise ValueError(f'请把链接改为普通项目文件：{relative}')
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f'需要普通文件：{relative}')
    return current, info.st_size


def walk(root):
    pending = [root]
    while pending:
        folder = pending.pop()
        for item in sorted(folder.iterdir()):
            if item.name.startswith('.') or item.name in SKIP:
                continue
            info = item.lstat()
            if is_link(info):
                yield item, False
            elif stat.S_ISDIR(info.st_mode):
                if valid_part(item.name):
                    pending.append(item)
                else:
                    yield item, False
            elif stat.S_ISREG(info.st_mode):
                yield item, valid_part(item.name)


@dataclass(frozen=True)
class Copy:
    source_root: Path
    source: str
    destination: str
    size: int
    digest: str


def snapshot(root, relative, destination):
    from hashlib import file_digest
    path, size = checked_file(root, relative)
    with path.open('rb') as stream:
        digest = file_digest(stream, 'sha256').hexdigest()
    return Copy(root, relative, destination, size, digest)


def read_limited(root, relative, limit):
    path, size = checked_file(root, relative)
    if size > limit:
        raise ValueError(f'文件过大：{relative}')
    with path.open('rb') as file:
        value = file.read(limit + 1)
    if len(value) != size:
        raise ValueError(f'文件正在变化，请保存并关闭旧项目：{relative}')
    return value


def object_from(data, filename):
    def invalid(_):
        raise ValueError(f'{filename} 不能包含 NaN 或 Infinity。')
    try:
        value = json.loads(data, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError(f'{filename} 不是有效 JSON。') from None
    if not isinstance(value, dict):
        raise ValueError(f'{filename} 应为 JSON 对象。')
    return value


def version(root, required=False):
    if not required and not (root/'VERSION').exists():
        return '旧版（没有 VERSION 文件）'
    value = read_limited(root, 'VERSION', 80).decode('utf-8-sig').strip()
    if not VERSION_RE.fullmatch(value):
        raise ValueError('无法识别项目 VERSION。')
    return value


def version_tuple(value):
    return tuple(int(piece) for piece in value[1:].split('.'))


def protected_destination(target, source, app):
    target = Path(target)
    if not target.name or not valid_part(target.name):
        raise ValueError('请输入普通的新目录名，不得使用系统保留名、点或空格结尾。')
    if target.exists() or target.is_symlink():
        raise FileExistsError('目标目录已存在；请另取名称。')
    parent = target.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('请选择已存在的上级目录。')
    target = parent / target.name
    if target.exists() or target.is_symlink():
        raise FileExistsError('目标目录已存在；请另取名称。')
    if target.is_relative_to(source) or target.is_relative_to(app):
        raise ValueError('不能在旧工程或新版底稿里面创建新项目。请选外部目录。')
    return target


def app_snapshot(app):
    names = set(APP_FILES)
    for directory in APP_DIRS:
        folder = app/directory
        if is_link(folder.lstat()):
            raise ValueError('新版底稿目录不能是链接。请重新解压干净发行包。')
        for path, regular in walk(folder):
            relative = path.relative_to(app).as_posix()
            if relative in GENERATED or relative == 'requirements/member.txt':
                continue
            if path.suffix.lower() in SAFE_APP_SUFFIXES[directory]:
                if not regular:
                    raise ValueError(f'新版底稿包含链接或不安全文件：{relative}。请重新解压干净发行包。')
                names.add(relative)
    if not REQUIRED_APP <= names:
        raise ValueError('新版底稿缺少升级入口、版本或场地文件。请重新解压干净发行包。')
    return tuple(snapshot(app, name, name) for name in sorted(names))


def dependency_preview(code, old_members, app_files, installed):
    local = {item.destination.split('/', 3)[-1].split('/')[0].removesuffix('.py') for item in old_members}
    local |= {PurePosixPath(f.destination).stem for f in app_files
              if PurePosixPath(f.destination).parent.as_posix() in ('simulator/tools', 'simulator/'+CONTROLLER.as_posix())}
    requires = set()
    notes = set()
    for relative, data in code:
        try:
            tree = ast.parse(data, filename=relative)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f'会员 Python 文件无法解析：{relative}（第 {getattr(error, "lineno", None) or "?"} 行）') from None
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                requires.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    requires.add(node.module.split('.')[0])
                elif node.level:
                    notes.add('含相对导入；迁移后请确认这些模块也能由会员入口正常加载。')
    external = sorted(requires - local - sys.stdlib_module_names - CORE_MODULES)
    checks = [(name, installed(name)) for name in external]
    return tuple(checks), tuple(sorted(notes))


def locally_installed(module):
    # Only check bare names. Finding a spec does not execute the old program or a dependency.
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@dataclass(frozen=True)
class Plan:
    old: Path
    app: Path
    target: Path
    old_version: str
    new_version: str
    app_files: tuple
    old_files: tuple
    configs: tuple
    assets: tuple
    code: tuple
    dependencies: tuple
    omitted: tuple
    notes: tuple
    pin_count: int
    model: str
    has_firmware: bool
    has_member_requirements: bool


def make_plan(source, destination, app=APP):
    app = Path(app).resolve(strict=True)
    source = Path(source).resolve(strict=True)
    if not source.is_dir() or source == app:
        raise ValueError('请选择与新版底稿不同的旧项目目录。')
    target = protected_destination(destination, source, app)
    old_version, new_version = version(source), version(app, required=True)
    if old_version.startswith('v') and version_tuple(old_version) > version_tuple(new_version):
        raise ValueError('旧工程版本比当前发行包更新；请选择对应或更高版本的底稿。')
    layout = source/'simulator' if (source/'simulator/robot.json').is_file() else source
    if not all((layout/name).is_file() for name in (*CONFIGS, ALGORITHM)):
        raise ValueError('旧工程缺少机器人、板卡、固件配置或会员入口。请选 v0.4 或更新的项目根目录。')
    if is_link(layout.lstat()):
        raise ValueError('旧工程的 simulator 不能是链接。')
    platform = app_snapshot(app)
    platform_names = {item.destination.casefold() for item in platform}
    old_configs = []
    root_prefix = layout.relative_to(source).as_posix()

    def old_name(name):
        return (PurePosixPath(root_prefix)/name).as_posix()

    checked_file(source, old_name(ALGORITHM))
    configs = {}
    for name in CONFIGS:
        relative = old_name(name)
        data = read_limited(source, relative, 2*1024*1024)
        old_configs.append(Copy(source, relative, '', len(data), sha256(data).hexdigest()))
        configs[name] = object_from(data, name)
    robot, board, firmware = (configs[name] for name in CONFIGS)
    if not isinstance(robot.get('model') or {}, dict) or not isinstance(board.get('model') or {}, dict):
        raise ValueError('车模或板卡模型配置格式不正确。')
    bindings = board.get('bindings')
    if not isinstance(bindings, dict) or not all(isinstance(pin, str) for pin in bindings.values()):
        raise ValueError('引脚绑定格式不正确；请先在旧工程中保存硬件配置。')
    assets = sorted(member_assets(configs, lambda path: read_limited(source, old_name(path), 8*1024*1024)))
    for path in assets:
        old_configs.append(snapshot(source, old_name(path), 'simulator/'+path))
    if firmware.get('renode'):
        notes = ['Renode 路径会清空，在新项目重新选择本机 Renode。']
    else:
        notes = []
    firmware['renode'] = ''

    old_members = []
    omitted = []
    code = []
    # Never overwrite any distributed code other than the documented member entry.
    names = set()
    entry_root = layout/CONTROLLER
    reserved_modules = {PurePosixPath(f.destination).stem.casefold() for f in platform
                        if PurePosixPath(f.destination).parent.as_posix() == 'simulator/tools'}
    reserved_modules.update(s.casefold() for s in CORE_MODULES | sys.stdlib_module_names | {'controller'})
    for path, regular in walk(entry_root):
        relative = path.relative_to(layout).as_posix()
        destination = 'simulator/'+relative
        folded = destination.casefold()
        if relative != ALGORITHM and folded in platform_names:
            omitted.append(relative + '（与新版系统文件冲突；保留新版文件，未迁移旧文件）')
            continue
        if not regular or path.suffix.lower() != '.py' or any(not valid_part(part) for part in PurePosixPath(relative).parts):
            omitted.append(relative)
            continue
        top = path.relative_to(entry_root).parts[0].removesuffix('.py').casefold()
        if relative != ALGORITHM and top in reserved_modules:
            raise ValueError(f'同目录模块会覆盖新版或 Python 模块：{relative}。请先改名。')
        if folded in names:
            raise ValueError(f'存在文件名大小写冲突：{relative}')
        names.add(folded)
        old_relative = old_name(relative)
        data = read_limited(source, old_relative, 4*1024*1024)
        item = Copy(source, old_relative, destination, len(data), sha256(data).hexdigest())
        old_members.append(item)
        code.append((relative, data))
        if len(old_members) > 500:
            raise ValueError('同目录会员 Python 文件超过 500 个，请先整理旧工程。')
    # A user may maintain a separate requirements file. Copy it, but never install or run it.
    member_requirements = 'requirements/member.txt'
    has_requirements = (source/member_requirements).exists() or (source/member_requirements).is_symlink()
    if has_requirements:
        data = read_limited(source, member_requirements, 256*1024)
        old_configs.append(Copy(source, member_requirements, member_requirements, len(data), sha256(data).hexdigest()))
    if not any(item.destination == 'simulator/'+ALGORITHM for item in old_members):
        raise ValueError('没有可迁移的会员入口文件。')
    dependencies, import_notes = dependency_preview(code, old_members, platform, locally_installed)
    if any(key in (robot.get('gripper') or {}) for key in ('carry_height_mm', 'grab_distance_mm')):
        notes.append('旧版夹爪的携带高度/抓取距离不会指挥新版物理夹爪，请复核夹爪几何与行程。')
    if len(omitted) > 500:
        raise ValueError('控制器下不迁移的文件超过 500 个，请先整理控制器目录。')
    if sum(item.size for item in (*old_members, *old_configs)) > LIMIT:
        raise ValueError('队伍迁移文件超过 512 MiB，请整理模型或程序。')
    return Plan(source, app, target, old_version, new_version, platform,
                tuple(old_configs+old_members), tuple((name, encode(cfg)) for name,cfg in configs.items()),
                tuple(assets), tuple(item.destination for item in old_members), dependencies,
                tuple(sorted(omitted)), tuple(notes)+import_notes, len(bindings),
                (robot.get('model') or {}).get('kind','parametric'), bool(firmware.get('image')), has_requirements)


def describe(plan):
    sections = [f'旧工程：{plan.old}（{plan.old_version}）',
                f'新版底稿：{plan.app}（{plan.new_version}）', f'新工程：{plan.target}',
                f'\n车模：{plan.model}；板卡引脚绑定：{plan.pin_count} 项；'
                f'固件：{"有，保留已引用的镜像" if plan.has_firmware else "未导入"}。',
                '复制机器人、板卡和固件配置；新版赛场及系统文件使用新版底稿。',
                '\n引用的模型、网表和固件：']
    sections.extend('  '+name for name in plan.assets or ('无自定义资产',))
    sections.append('\n会员 Python 文件：')
    sections.extend('  '+name for name in plan.code)
    if plan.omitted:
        sections.append('\n控制器目录中未自动迁移的文件（请自行核对是否需要手动合并）：')
        sections.extend('  '+name for name in plan.omitted)
    sections.append('\nPython 依赖预览：静态检查 import，不运行队伍代码。新版虚拟环境不继承旧环境。')
    if not plan.dependencies:
        sections.append('  未发现超出本次模块、标准库和基础依赖的静态导入。')
    for name, found in plan.dependencies:
        status = '当前环境可找到；新版环境需单独安装' if found else '当前环境找不到；请核对模块名或安装来源'
        sections.append(f'  {name}：{status}')
    if plan.has_member_requirements:
        sections.append('  保留 requirements/member.txt；新版 setup.bat 不自动安装该文件。')
    sections.extend('  '+message for message in plan.notes)
    sections.append('\n动态导入、其他目录里的代码、资源与调试记录需手动核对。生成后先在新目录运行 setup.bat，再运行启动自检。')
    return '\n'.join(sections)


def copy_checked(item, root):
    from hashlib import file_digest
    source, size = checked_file(item.source_root, item.source)
    if size != item.size:
        raise ValueError(f'预览后文件已变更，请重新预览：{item.source}')
    target = root/item.destination if item.destination else None
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256()
    with source.open('rb') as file:
        if target is None:
            digest = file_digest(file, 'sha256')
        else:
            with target.open('xb') as output:
                count = 0
                while chunk := file.read(1024*1024):
                    count += len(chunk)
                    if count > item.size:
                        raise ValueError(f'预览后文件已变大，请重新预览：{item.source}')
                    digest.update(chunk)
                    output.write(chunk)
    if digest.hexdigest() != item.digest:
        raise ValueError(f'预览后文件已变更，请重新预览：{item.source}')


def publish(source, target):
    if os.name == 'nt':
        # os.rename on Windows fails rather than replacing an existing destination.
        source.rename(target)
        return
    # On POSIX ordinary rename can replace an empty directory. Require an
    # exclusive rename on systems that provide it; otherwise stop safely.
    import ctypes
    import errno
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'linux' and hasattr(libc, 'renameat2'):
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    elif sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        result = libc.renamex_np(os.fsencode(source), os.fsencode(target), 4)
    else:
        raise OSError('系统不支持安全重命名，请在 Windows 上使用。')
    if result:
        code = ctypes.get_errno() or errno.EIO
        raise OSError(code, os.strerror(code), target)


def migrate(plan):
    target = protected_destination(plan.target, plan.old, plan.app)
    overlay = {f.destination for f in plan.old_files if f.destination}
    overlay.update('simulator/'+name for name,_ in plan.configs)
    with tempfile.TemporaryDirectory(prefix='.moonlight-upgrade-', dir=target.parent) as folder:
        stage = Path(folder)/'project'
        stage.mkdir()
        for item in plan.app_files:
            if item.destination not in overlay:
                copy_checked(item, stage)
        for item in plan.old_files:
            copy_checked(item, stage)
        for name, content in plan.configs:
            (stage/'simulator'/name).write_bytes(content)
        if version(stage, required=True) != plan.new_version:
            raise ValueError('新版底稿版本已变化，请重新预览。')
        if protected_destination(target, plan.old, plan.app) != target:
            raise ValueError('目标上级目录已变化，请重新选择。')
        publish(stage, target)
    return target


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('月光杯 · 队伍项目升级')
    root.minsize(720, 510)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)
    form = ttk.Frame(root, padding=12)
    form.grid(sticky='ew')
    form.columnconfigure(1, weight=1)
    old, parent, name = (tk.StringVar() for _ in range(3))
    previewed = None

    def directory(var, title):
        choice = filedialog.askdirectory(parent=root, title=title, mustexist=True)
        if choice:
            var.set(choice)

    for i, (label, var) in enumerate((('旧工程目录', old), ('新工程上级目录', parent), ('新工程名称', name))):
        ttk.Label(form, text=label).grid(row=i, column=0, sticky='w', pady=3)
        ttk.Entry(form, textvariable=var).grid(row=i, column=1, sticky='ew', padx=8)
        if i < 2:
            ttk.Button(form, text='选择…', command=lambda v=var,b=label:directory(v,b)).grid(row=i, column=2)
    panel = ttk.Frame(root, padding=(12,0,12,0))
    panel.grid(row=1, sticky='nsew')
    panel.rowconfigure(0, weight=1)
    panel.columnconfigure(0, weight=1)
    screen = tk.Text(panel, wrap='word', state='disabled', font=('Microsoft YaHei UI', 10))
    scroll = ttk.Scrollbar(panel, orient='vertical', command=screen.yview)
    screen.configure(yscrollcommand=scroll.set)
    screen.grid(row=0, column=0, sticky='nsew')
    scroll.grid(row=0, column=1, sticky='ns')

    def show(value):
        screen.configure(state='normal')
        screen.delete('1.0', 'end')
        screen.insert('1.0', value)
        screen.configure(state='disabled')

    def invalidate(*_):
        nonlocal previewed
        previewed = None
        create_button.configure(state='disabled')
        show('请选择旧工程、独立的上级目录和新名称，然后点击「预览迁移内容」。')

    def review():
        nonlocal previewed
        invalidate()
        try:
            label = name.get().strip()
            if not old.get().strip() or not parent.get().strip() or not valid_part(label) or not label:
                raise ValueError('请选择两个目录并填写普通的新工程名称。')
            previewed = make_plan(old.get(), Path(parent.get())/label)
            show(describe(previewed))
            create_button.configure(state='normal')
        except (OSError, ValueError, ParseError) as error:
            messagebox.showerror('无法预览', str(error), parent=root)

    def create():
        if previewed is None:
            return
        try:
            target = migrate(previewed)
        except (OSError, ValueError, ParseError) as error:
            invalidate()
            messagebox.showerror('未创建新工程', str(error), parent=root)
            return
        invalidate()
        show(f'已创建：{target}\n\n旧工程保留。请在新目录先运行 setup.bat，再运行 scripts/check-setup.bat。')

    bar = ttk.Frame(root, padding=12)
    bar.grid(row=2, sticky='ew')
    ttk.Button(bar, text='预览迁移内容', command=review).pack(side='left')
    create_button = ttk.Button(bar, text='确认并生成新工程', command=create, state='disabled')
    create_button.pack(side='left', padx=8)
    for field in (old, parent, name):
        field.trace_add('write', invalidate)
    invalidate()
    root.mainloop()


def main(argv=None):
    if not (3, 11) <= sys.version_info[:2] <= (3, 13):
        print('需要 Python 3.11、3.12 或 3.13，请先运行 setup.bat。', file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gui', action='store_true', help='打开本地升级窗口')
    parser.add_argument('--source', type=Path, help='旧项目目录')
    parser.add_argument('--target', type=Path, help='尚不存在的新项目目录，须在新旧工程之外')
    parser.add_argument('--apply', action='store_true', help='显示预览后询问并创建新项目；默认只预览')
    args = parser.parse_args(argv)
    if args.gui or (not args.source and not args.target and not args.apply):
        if args.source or args.target or args.apply:
            parser.error('--gui 不与其他参数同时使用。')
        launch_gui()
        return 0
    if not args.source or not args.target:
        parser.error('须同时指定 --source 与 --target。')
    try:
        plan = make_plan(args.source, args.target)
        print(describe(plan))
        if args.apply:
            if input('\n输入 YES 确认生成新工程：').strip() == 'YES':
                print('已创建：', migrate(plan))
            else:
                print('已取消。')
    except (EOFError, KeyboardInterrupt):
        print('\n已取消。', file=sys.stderr)
        return 130
    except (OSError, ValueError, ParseError) as error:
        print('升级未完成：'+str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
