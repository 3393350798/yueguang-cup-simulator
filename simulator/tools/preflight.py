"""月光杯启动自检。默认只读；可选离线 Renode 启动探针。"""
import argparse
from collections import defaultdict
from io import BytesIO
import importlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from firmware.pins import resolve_pin, timer_channel
from firmware_bridge import ROOT, FirmwareBridge, find_renode


MOTOR_ROLES = {f'{side}_{signal}' for side in ('left', 'right') for signal in ('pwm', 'in1', 'in2')}
ENCODER_ROLES = {f'encoder_{side}_{phase}' for side in ('left', 'right') for phase in ('a', 'b')}
GRIPPER_ROLES = {'gripper_close', 'gripper_lift'}
ROUTES = {'manual': '手动探索 / Python 会员程序', 'openmv': 'STM32 + OpenMV',
          'phone': 'STM32 + 手机串流 + 电脑视觉'}
FLASH_START, FLASH_END, SRAM_START, SRAM_END = 0x08000000, 0x08010000, 0x20000000, 0x20005000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


LOOPBACK = build_opener(ProxyHandler({}), NoRedirect())


class Report:
    def __init__(self):
        self.failures = 0

    def add(self, state, title, detail):
        print(f'[{state}] {title}：{detail}')
        if state == '失败':
            self.failures += 1


def read_object(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('需要 JSON 对象')
    return data


def check_dependencies(report, explicit_home, repository):
    version = sys.version_info
    if version >= (3, 11):
        report.add('通过', 'Python', platform.python_version())
    else:
        report.add('失败', 'Python', '需要 Python 3.11 或以上，请运行 setup.bat。')
    for name in ('numpy', 'cv2', 'PIL', 'urdf2webots', 'trimesh', 'collada'):
        try:
            module = importlib.import_module(name)
            if name == 'cv2' and not hasattr(getattr(module, 'aruco', None), 'DICT_APRILTAG_36h11'):
                raise ImportError('缺少 OpenCV contrib 的 AprilTag 字典')
        except (ImportError, OSError) as error:
            report.add('失败', f'Python 包 {name}', f'{error}；在项目目录运行 setup.bat。')
        else:
            report.add('通过', f'Python 包 {name}', '可加载')
    home = explicit_home or os.getenv('WEBOTS_HOME')
    if os.name == 'nt':
        if not home:
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Webots_is1') as key:
                    home = winreg.QueryValueEx(key, 'InstallLocation')[0]
            except OSError:
                home = r'C:\Program Files\Webots'
        executable = Path(home) / 'msys64/mingw64/bin/webots.exe'
    else:
        executable = Path(home) / 'webots' if home else Path(shutil.which('webots') or '')
    if executable.is_file():
        report.add('通过', 'Webots 程序', str(executable))
    else:
        detail = '安装 Webots R2025a；已自定义安装目录时设置 WEBOTS_HOME，或用 launch.ps1 -WebotsHome。'
        if os.name == 'nt' and not explicit_home:
            for folder in (repository / '.local/Webots', repository.parent / '.local/Webots'):
                if (folder / 'msys64/mingw64/bin/webots.exe').is_file():
                    detail = f'发现本机副本 {folder}；设置 WEBOTS_HOME 后重新检查。'
                    break
        report.add('失败', 'Webots 程序', detail)


def validate_image(path, config):
    length = path.stat().st_size
    if not length:
        raise ValueError('文件为空；请导入 F103 已编译镜像。')
    suffix = path.suffix.lower()
    if suffix in ('.elf', '.axf'):
        with path.open('rb') as image:
            raw = image.read(52)
            if (len(raw) < 52 or raw[:6] != b'\x7fELF\x01\x01' or
                    struct.unpack_from('<HH', raw, 16) != (2, 40)):
                raise ValueError('不是 32 位小端 ARM ELF 可执行文件；请从 STM32 工程重新导出。')
            entry, header_offset = struct.unpack_from('<II', raw, 24)
            header_size, count = struct.unpack_from('<HH', raw, 42)
            if not count or header_size != 32 or header_offset < 52 or header_offset + header_size * count > length:
                raise ValueError('ELF 程序段表不完整；请重新编译。')
            image.seek(header_offset)
            loadable = False
            for _ in range(count):
                kind, offset, _, _, filesz, memsz, _, _ = struct.unpack('<8I', image.read(header_size))
                if kind == 1 and filesz:
                    if filesz > memsz or offset + filesz > length:
                        raise ValueError('ELF 的加载段越出了文件或目标内存段。')
                    loadable = True
            if not loadable:
                raise ValueError('ELF 中没有可加载的程序数据。')
        return f'ARM ELF，入口 0x{entry:08X}；运行情况需实际验证'
    if suffix == '.hex':
        if length > 2 * 1024 * 1024:
            raise ValueError('HEX 超过 2 MiB；请核对是否导出了 F103 的程序。')
        eof = False
        data_count = 0
        for i, line in enumerate(path.read_text(encoding='ascii').splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if eof or not re.fullmatch(r':(?:[0-9a-fA-F]{2}){5,}', line):
                raise ValueError(f'第 {i} 行不是有效的 Intel HEX 记录。')
            record = bytes.fromhex(line[1:])
            size, kind = record[0], record[3]
            if size + 5 != len(record) or sum(record) % 256 or kind > 5:
                raise ValueError(f'第 {i} 行长度、类型或校验和不正确。')
            if kind and (size != {1: 0, 2: 2, 3: 4, 4: 2, 5: 4}[kind] or any(record[1:3])):
                raise ValueError(f'第 {i} 行扩展或结束记录不正确。')
            eof = kind == 1
            if kind == 0:
                data_count += size
        if not eof or not data_count:
            raise ValueError('HEX 缺少数据或结束记录；请重新导出。')
        return f'Intel HEX，共 {data_count} 字节数据，记录校验通过'
    if suffix == '.bin':
        address = int(str(config.get('bin_address')), 0)
        if address < FLASH_START or address + length > FLASH_END or address % 128:
            raise ValueError('BIN 地址须在 64 KiB Flash 内、按 128 字节对齐，并能容纳整个镜像。')
        if length < 8:
            raise ValueError('BIN 长度不足 8 字节，缺少初始栈和复位向量。')
        with path.open('rb') as image:
            sp, pc = struct.unpack('<II', image.read(8))
        if not SRAM_START < sp <= SRAM_END or sp % 4:
            raise ValueError(f'BIN 栈指针 0x{sp:08X} 超出 F103 20 KiB SRAM，或未按字对齐。')
        physical_pc = (pc & ~1) + (FLASH_START if pc < FLASH_START else 0)
        if not pc & 1 or not address <= physical_pc < address + length:
            raise ValueError(f'BIN 复位入口 0x{pc:08X} 不在已加载镜像内，或没有 Thumb 位。')
        return f'BIN，加载 0x{address:08X}，栈和复位向量合理'
    raise ValueError('支持已编译的 ELF / AXF / HEX / BIN；C 源文件需先编译。')


def has_timer_output(pin):
    for mapping in range(16):
        try:
            timer_channel(pin, mapping << 8)
            return True
        except ValueError:
            pass
    return False


def check_bindings(report, robot, board):
    before = report.failures
    if board.get('mcu') != 'STM32F103C8T6':
        report.add('失败', '目标板卡', '当前 Renode 接入基于 STM32F103C8T6，请核对 board.json。')
    bindings = board.get('bindings', {})
    if not isinstance(bindings, dict):
        report.add('失败', '管脚绑定', 'board.json 中的 bindings 应为设备名到管脚的对应表。')
        return False
    if missing := sorted(MOTOR_ROLES - bindings.keys()):
        report.add('失败', '电机绑定', '缺少 ' + '、'.join(missing) + '；打开硬件配置的 F103 引脚图。')
    sonars = {item['name'] for item in robot.get('sonar', {}).get('sensors', [])}
    ir_roles = {f'ir{i}' for i in range(len(robot.get('ir', {}).get('positions_mm', [])))}
    sonar_roles = {f'sonar_{name}_{signal}' for name in sonars for signal in ('trig', 'echo')}
    valid_roles = MOTOR_ROLES | ENCODER_ROLES | GRIPPER_ROLES | ir_roles | sonar_roles
    by_pin = defaultdict(list)
    for role, value in bindings.items():
        try:
            pin = resolve_pin(str(value), str(board.get('mcu_reference', '')))
        except ValueError as error:
            report.add('失败', f'管脚 {role}', str(error))
            continue
        by_pin[pin].append(role)
        if role not in valid_roles:
            report.add('提醒', f'设备 {role}', '当前车模或桥接没有该信号，检查设备名和传感器编号。')
        if role in ('left_pwm', 'right_pwm') and not has_timer_output(pin):
            report.add('提醒', f'PWM {role}', f'{pin} 只能用普通 GPIO 作 0%/100% 开关；PWM 请用已支持的 TIM1～4 管脚。')
    for pin, roles in by_pin.items():
        if len(roles) > 1:
            report.add('失败', f'管脚冲突 {pin}', '、'.join(roles) + ' 共用此脚；应分别绑定。')
    pairs = [(f'encoder_{side}_a', f'encoder_{side}_b') for side in ('left', 'right')]
    pairs += [(f'sonar_{name}_trig', f'sonar_{name}_echo') for name in sorted(sonars)]
    for a, b in pairs:
        if (a in bindings) != (b in bindings):
            report.add('提醒', f'配对 {a} / {b}', '缺少 ' + (b if a in bindings else a) + '；该设备反馈不会生效。')
    if report.failures == before:
        report.add('通过', '管脚绑定', f'已解析 {len(bindings)} 项且无冲突；外设和复用设置需核对固件。')
    return report.failures == before


def check_firmware(report, root, robot, board, firmware, boot):
    before = report.failures
    pins_ok = check_bindings(report, robot, board)
    executable = None
    try:
        executable = find_renode(firmware.get('renode', ''))
        report.add('通过', 'Renode 程序', executable)
    except FileNotFoundError as error:
        report.add('失败', 'Renode 程序', str(error))
    try:
        image = firmware.get('image')
        if not image:
            raise ValueError('未导入镜像，请运行 scripts/import-hardware.bat，在固件页选择镜像。')
        path = (root / image).resolve()
        report.add('通过', '固件镜像', f'{path.name} · {validate_image(path, firmware)}')
    except (OSError, ValueError, TypeError, struct.error) as error:
        report.add('失败', '固件镜像', str(error))
    mips, cpr = firmware.get('cpu_mips', 8), firmware.get('encoder_cpr')
    threshold, signs = firmware.get('ir_threshold'), firmware.get('motor_signs')
    if (type(mips) not in (int, float) or not 0 < mips <= 0xffffffff or mips != int(mips) or
            type(cpr) is not int or cpr <= 0 or type(threshold) not in (float, int) or
            not 0 <= threshold <= 1 or signs not in ([1, 1], [1, -1], [-1, 1], [-1, -1])):
        report.add('失败', '固件参数', '在硬件配置核对 CPU 指令率和编码器计数（正整数）、红外阈值（0～1）和电机方向（±1）。')
    uart = firmware.get('vision_uart')
    if uart not in ('usart1', 'usart2', 'usart3'):
        report.add('失败', '视觉 USART', '固件配置应选 usart1、usart2 或 usart3。')
    else:
        report.add('通过', '视觉 USART', f'绑定 {uart.upper()}；请核对固件实际使用的接口。')
    if not boot:
        report.add('未实测', '固件执行', '可用 --boot 单独验证虚拟机载入和短时执行。')
    elif not pins_ok or report.failures != before:
        report.add('未实测', '固件执行', '先修复上述固件或绑定问题。')
    else:
        print('正在启动独立 Renode（通常需要几秒，最长约 45 秒）…', flush=True)
        try:
            run_probe(report, robot, board, dict(firmware, image=str(path), renode=executable))
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as error:
            report.add('失败', '固件执行', str(error))


def run_probe(report, robot, board, firmware):
    bridge = FirmwareBridge(firmware, board)
    try:
        sensors = {'ir': [0.5] * len(robot.get('ir', {}).get('positions_mm', [])),
                   'sonar': {}, 'encoder': {'left': 0, 'right': 0}}
        result = bridge.step(sensors, 0.032)
        pc = int(result['pc'], 0)
        if pc == 0 or result['virtual_s'] < .03:
            raise RuntimeError(f'虚拟 MCU 未确认推进，PC={result["pc"]}。查看 {bridge.folder / "renode.log"}')
        report.add('通过', '固件执行', f'推进到 {result["virtual_s"] * 1000:.0f} ms，PC={result["pc"]}；这不等于应用任务通过。')
        if result['uart']:
            report.add('通过', '虚拟 MCU TX', f'实际发出 {len(result["uart"])} 字节（未发送测试请求）。')
        else:
            report.add('未实测', '虚拟 MCU TX', '这 32 ms 内没有主动输出；请在面板用自己的协议验证回包。')
    finally:
        bridge.close()


def get(port, path):
    return LOOPBACK.open(f'http://127.0.0.1:{port}{path}', timeout=2)


def check_panel(report, port):
    try:
        with get(port, '/robot_windows/moonlight/moonlight.html?name=Moonlight') as response:
            if b'id="mode"' not in response.read(128 * 1024):
                raise ValueError('端口上运行的不是月光杯面板，请检查端口或 Webots 启动参数。')
        report.add('通过', '练习面板 HTTP', f'127.0.0.1:{port} 已返回面板；运行状态仍需在浏览器确认。')
    except (HTTPError, ValueError) as error:
        report.add('失败', '练习面板 HTTP', f'{error}；请核对 --panel-port。')
    except (URLError, OSError) as error:
        report.add('未实测', '练习面板 HTTP', f'{error}；请启动 start.bat，采用其他端口时传 --panel-port。')


def check_vision_link(report, robot, port):
    if not port:
        report.add('未启用', '本机图像与串口', '外部电脑视觉要用 HTTP 接口时，先在硬件配置设置联调端口并重启 Webots。')
        return
    try:
        from PIL import Image
        with get(port, '/frame.png') as response:
            if response.headers.get_content_type() != 'image/png':
                raise ValueError('端口未返回 PNG，检查是否填错端口。')
            raw = response.read(24 * 1024 * 1024 + 1)
            if len(raw) > 24 * 1024 * 1024:
                raise ValueError('返回的图像超过 24 MiB。')
            with Image.open(BytesIO(raw)) as image:
                if image.format != 'PNG':
                    raise ValueError('图像文件格式不是 PNG。')
                size = image.size
                camera = robot['camera']
                expected = (camera['width'], camera['height'])
                if size != expected:
                    raise ValueError(f'实际 {size[0]}×{size[1]}、配置 {expected[0]}×{expected[1]}，请重启场景或检查端口。')
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
        report.add('通过', '虚拟相机', f'收到可解码的 {size[0]}×{size[1]} PNG。')
    except HTTPError as error:
        state = '未实测' if error.code == 503 else '失败'
        report.add(state, '虚拟相机', f'HTTP {error.code}；检查 Webots 是否启动且端口正确。')
    except (URLError, TimeoutError) as error:
        report.add('未实测', '虚拟相机', f'{error}；等待 Webots 启动，确认本机联调端口。')
        return
    except (OSError, ImportError, ValueError, KeyError, TypeError) as error:
        report.add('失败', '虚拟相机', str(error))
    try:
        with get(port, '/uart?after=0') as response:
            if response.headers.get_content_type() != 'application/octet-stream':
                raise ValueError('端口未返回串口字节流。')
            cursor = int(response.headers['X-Next-Cursor'])
            data = response.read(65537)
        report.add('通过', '虚拟串口读取', f'可读取字节流；已看到输出 {len(data)} 字节（累积游标 {cursor}）。')
        report.add('未实测', '协议回包', '自检只读取既有输出，未发指令；请在面板发送自有测试帧并核对预期回包。')
    except (HTTPError, URLError, OSError, TypeError, ValueError) as error:
        report.add('失败', '虚拟串口读取', f'{error}；确认同一联调端口提供 /uart。')


def guide(route):
    if route == 'openmv':
        print('\n接入顺序：启动仿真后在面板保存原图 → 用实体 OpenMV 打开本机样图并确认输出 → 将自己的协议帧在模拟串口先验证 → 最后接实板。')
        print('在实体板上另行核对 OpenMV 的 UART 号、两端波特率、TX/RX 交叉及共地；自检不运行 OpenMV 固件。')
    elif route == 'phone':
        print('\n接入顺序：电脑先单独打开自己的手机串流 → 用虚拟 PNG 输入验证同一视觉程序 → 把协议字节送到 127.0.0.1 联调端口并核对回包 → 换接真实串口。')
        print('手机送图，电脑做视觉；串流登录、网络连通、无线串口配对与真板回包需要用队伍自己的设备检查。')
    print('更多说明：docs/启动自检.md')


def port_number(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError('端口应为 1～65535。')
    return port


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description='月光杯仿真启动自检')
    parser.add_argument('--route', choices=ROUTES, default='manual')
    parser.add_argument('--boot', action='store_true', help='启动独立 Renode 运行 32 ms，不驱动小车')
    parser.add_argument('--interactive', action='store_true', help='选择接入方案')
    parser.add_argument('--webots-home', help='自定义 Webots 安装目录')
    parser.add_argument('--panel-port', type=port_number, default=1234)
    parser.add_argument('--vision-port', type=port_number, help='覆盖配置的本机视觉端口，仅用于检查')
    args = parser.parse_args(argv)
    if args.interactive:
        print('请选择接入方案：')
        for i, option in enumerate(ROUTES.values(), 1):
            print(f'  {i}. {option}')
        while (answer := input('输入 1 / 2 / 3（回车选 1）：').strip()) not in ('', '1', '2', '3'):
            pass
        args.route = tuple(ROUTES)[int(answer or '1') - 1]
        if args.route != 'manual':
            args.boot = input('运行独立 Renode 载入探针？[y/N] ').strip().lower() == 'y' or args.boot
    if args.route == 'manual' and args.boot:
        parser.error('--boot 需要选择 --route openmv 或 --route phone。')
    print(f'月光杯仿真 · 启动自检 / {ROUTES[args.route]}\n')
    report = Report()
    check_dependencies(report, args.webots_home, root.parent)
    robot = None
    try:
        robot = read_object(root / 'robot.json')
        step = robot.get('step_ms', 32)
        vision_port = args.vision_port or robot.get('vision_link_port', 0)
        if step not in (4, 8, 16, 32) or type(vision_port) is not int or not 0 <= vision_port <= 65535:
            raise ValueError('step_ms 应为 4/8/16/32，vision_link_port 应为 0～65535。')
        report.add('通过', '小车基础配置', f'控制周期 {step} ms，联调端口 {vision_port or "关闭"}。')
    except (OSError, ValueError, TypeError) as error:
        report.add('失败', '小车基础配置', f'{error}；请检查 simulator/robot.json。')
        robot = None
    if robot is not None and args.route != 'manual':
        try:
            check_firmware(report, root, robot, read_object(root / 'board.json'),
                           read_object(root / 'firmware.json'), args.boot)
        except (OSError, ValueError, KeyError, TypeError) as error:
            report.add('失败', '板卡 / 固件配置', f'{error}；请检查 board.json、firmware.json。')
    check_panel(report, args.panel_port)
    if robot is not None and (vision_port or args.route == 'phone'):
        check_vision_link(report, robot, vision_port)
    guide(args.route)
    print(f'\n待修复 {report.failures} 项。“未实测”与“未启用”需要按当前方案确认。')
    return 1 if report.failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
