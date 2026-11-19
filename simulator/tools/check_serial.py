"""Count traffic on a user-selected Windows COM port without recording bytes."""
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from uuid import uuid4


PORT_NAME = re.compile(r'COM[1-9][0-9]{0,4}', re.IGNORECASE)
MAX_FRAME_BYTES = 256
MAX_RX_BYTES = 2_000_000
MESSAGES = {
    'serial_dependency_missing': r'缺少 pyserial。先运行 .venv\Scripts\python.exe -m pip install -r requirements\serial.txt',
    'serial_list_failed': '无法枚举串口，请检查 Windows 设备管理器。',
    'serial_no_ports': '没有发现 COM 口，请检查 USB 连接、无线串口配对或设备驱动。',
    'serial_list_only': '只列举了端口，未打开串口。',
    'serial_cancelled': '已取消检查。',
    'serial_port_unavailable': '所选 COM 口不在当前设备列表。',
    'serial_port_denied_or_busy': '系统拒绝打开该串口，请检查端口权限或关闭正在使用它的程序。',
    'serial_open_failed': '无法打开串口，请检查连接和串口参数。',
    'serial_io_failed': '串口读写中断，请检查设备连接。',
    'serial_write_failed': '测试帧没有写完：系统写入失败或超时。',
    'serial_listening_silent': '监听期间没有收到字节。只监听不会要求板端回应。',
    'serial_bytes_received': '收到了字节；只有与已知协议匹配时才能确认预期响应。',
    'serial_reply_timeout': '测试帧已交给系统串口，等待窗口内没有收到字节。',
    'serial_reply_mismatch': '收到了字节，但等待窗口内没有出现预期字节序列。',
    'serial_reply_match': '等待窗口内收到了预期字节序列。',
    'serial_rx_limit': '接收达到本次检查的上限，已结束监听。',
}
OK_RESULTS = {'serial_list_only', 'serial_bytes_received', 'serial_reply_match'}
EVENTS = {'serial_started', 'serial_ports', 'serial_opened', 'serial_result'}
BOUNDS = {
    'port_count': (0, 4096),
    'baudrate': (300, 3_000_000),
    'data_bits': (5, 8),
    'stop_bits': (1, 2),
    'duration_ms': (100, 30_000),
    'first_rx_ms': (0, 30_000),
    'tx_bytes': (0, MAX_RX_BYTES),
    'rx_bytes': (0, MAX_RX_BYTES),
}


class DiagnosticLog:
    def __init__(self, project):
        self.path = None
        try:
            folder = project / 'output' / 'logs'
            folder.mkdir(exist_ok=True, parents=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            self.path = folder / f'diagnostic-serial-{stamp}-{uuid4().hex[:8]}.jsonl'
        except OSError:
            pass

    def record(self, event, **fields):
        if self.path is None or event not in EVENTS:
            return
        at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        row = {'at': at, 'event': event}
        for key, value in fields.items():
            if key == 'code' and value in MESSAGES:
                row[key] = value
            elif key == 'parity' and value in ('N', 'E', 'O'):
                row[key] = value
            elif key in ('probe_enabled', 'expected_reply') and type(value) is bool:
                row[key] = value
            elif key in BOUNDS and type(value) is int and BOUNDS[key][0] <= value <= BOUNDS[key][1]:
                row[key] = value
        try:
            with self.path.open('a', encoding='utf-8') as out:
                out.write(json.dumps(row, separators=(',', ':')) + '\n')
        except OSError:
            self.path = None


@dataclass(frozen=True)
class Settings:
    port: str
    baudrate: int
    duration_ms: int
    data_bits: int
    parity: str
    stop_bits: int
    frame: bytes = b''
    expected: bytes = b''

    def diagnostic_fields(self):
        return dict(baudrate=self.baudrate, data_bits=self.data_bits, parity=self.parity,
                    stop_bits=self.stop_bits, duration_ms=self.duration_ms,
                    probe_enabled=bool(self.frame), expected_reply=bool(self.expected))


def finish(log, code, **fields):
    log.record('serial_result', code=code, **fields)
    print(MESSAGES[code])
    if 'rx_bytes' in fields:
        print(f"写入字节：{fields.get('tx_bytes', '未知')}；接收字节：{fields['rx_bytes']}")
        if fields.get('first_rx_ms') is not None:
            print(f"首次收到字节：监听开始后约 {fields['first_rx_ms']} ms")
    if log.path is not None:
        print(r'检查结果已记录。可双击 scripts\export-diagnostics.bat 打包。')
    else:
        print(r'未能保存日志，请确认项目的 output\logs 可写。')
    return 0 if code in OK_RESULTS else 1


def open_error(error):
    if (isinstance(error, FileNotFoundError) or getattr(error, 'winerror', None) in (2, 3)
            or 'FileNotFoundError(' in str(error)):
        return 'serial_port_unavailable'
    if (isinstance(error, PermissionError) or getattr(error, 'winerror', None) == 5
            or 'PermissionError(' in str(error)):
        return 'serial_port_denied_or_busy'
    return 'serial_open_failed'


def read_traffic(connection, settings):
    began = time.monotonic()
    deadline = began + settings.duration_ms / 1000
    count = 0
    first_rx_ms = None
    tail = b''
    expected = settings.expected
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            waiting = min(connection.in_waiting, 4096, MAX_RX_BYTES - count)
            chunk = connection.read(waiting) if waiting else b''
            if not chunk:
                time.sleep(min(.01, remaining))
                continue
        except (OSError, ValueError):
            return 'serial_io_failed', count, first_rx_ms
        except KeyboardInterrupt:
            return 'serial_cancelled', count, first_rx_ms
        count += len(chunk)
        if first_rx_ms is None:
            first_rx_ms = min(settings.duration_ms, round((time.monotonic() - began) * 1000))
        if expected:
            combined = tail + chunk
            if expected in combined:
                return 'serial_reply_match', count, first_rx_ms
            tail = combined[1-len(expected):] if len(expected) > 1 else b''
        if count >= MAX_RX_BYTES:
            return 'serial_rx_limit', count, first_rx_ms
    if count:
        return ('serial_reply_mismatch' if expected else 'serial_bytes_received'), count, first_rx_ms
    return ('serial_reply_timeout' if settings.frame else 'serial_listening_silent'), 0, None


def inspect_port(connection, settings, log):
    # Configure control lines before opening. Some drivers can still pulse them.
    configuration = settings.diagnostic_fields()
    connection.port = settings.port
    transmitted = received = 0
    first_rx_ms = None
    try:
        connection.open()
    except (OSError, ValueError) as error:
        return finish(log, open_error(error), **configuration)
    log.record('serial_opened', **configuration)
    try:
        if settings.frame:
            try:
                connection.reset_input_buffer()
                transmitted = connection.write(settings.frame)
            except (OSError, ValueError):
                # Some drivers throw after a partial write; do not report that it sent zero.
                return finish(log, 'serial_write_failed', rx_bytes=0, **configuration)
            if transmitted != len(settings.frame):
                return finish(log, 'serial_write_failed', tx_bytes=transmitted, rx_bytes=0, **configuration)
        code, received, first_rx_ms = read_traffic(connection, settings)
    except (OSError, ValueError):
        code = 'serial_io_failed'
    except KeyboardInterrupt:
        code = 'serial_cancelled'
    finally:
        try:
            connection.close()
        except OSError:
            pass
    return finish(log, code, tx_bytes=transmitted, rx_bytes=received, first_rx_ms=first_rx_ms, **configuration)


def int_range(value, name):
    low, high = BOUNDS[name]
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f'须输入 {low} 至 {high} 的整数。') from None
    if not low <= result <= high:
        raise argparse.ArgumentTypeError(f'须输入 {low} 至 {high} 的整数。')
    return result


def hex_frame(value):
    # bytes.fromhex accepts spaces but does not leave malformed input in exceptions.
    try:
        data = bytes.fromhex(value)
    except ValueError:
        raise argparse.ArgumentTypeError('请输入十六进制字节，每个字节两位。') from None
    if not data or len(data) > MAX_FRAME_BYTES:
        raise argparse.ArgumentTypeError(f'字节序列须为 1 至 {MAX_FRAME_BYTES} 字节。')
    return data


def port_name(value):
    if PORT_NAME.fullmatch(value):
        return value.upper()
    raise argparse.ArgumentTypeError('请输入 Windows COM 口编号。')


def prompt(label, default, parser):
    while True:
        hint = f' [{default}]' if default is not None else ''
        entered = input(label + hint + '：').strip()
        if not entered and default is not None:
            entered = str(default)
        try:
            return parser(entered)
        except argparse.ArgumentTypeError as error:
            print(error)


def choice(value, options):
    if value.upper() in options:
        return value.upper()
    raise argparse.ArgumentTypeError('请从 ' + '/'.join(options) + ' 中选择。')


def prompt_probe():
    frame = prompt('已知不会让设备动作的测试帧（HEX）', None, hex_frame)
    expected = b''
    expect_text = input('预期响应的字节序列（HEX；直接回车仅计数）：').strip()
    while expect_text:
        try:
            expected = hex_frame(expect_text)
            if expected in frame:
                raise argparse.ArgumentTypeError('预期响应不能与发送帧相同，也不能是发送帧的一段，以免误判回显。')
            break
        except argparse.ArgumentTypeError as error:
            expected = b''
            print(error)
            expect_text = input('重新输入预期响应（直接回车仅计数）：').strip()
    if input('确认该帧不会驱动电机或夹爪，输入 SAFE 继续：').strip() != 'SAFE':
        raise KeyboardInterrupt
    return frame, expected


def interactive_settings(ports):
    entered = input('输入要打开的 COM 口，直接回车仅列举：').strip()
    if not entered:
        return None
    port = entered.upper()
    if port not in ports:
        return Settings(port, 115200, 5000, 8, 'N', 1)
    baud = prompt('板端波特率', None, lambda value: int_range(value, 'baudrate'))
    duration = prompt('监听时长（毫秒）', 5000, lambda value: int_range(value, 'duration_ms'))
    bits = prompt('数据位', 8, lambda value: int_range(value, 'data_bits'))
    parity = prompt('校验位（N 无 / E 偶 / O 奇）', 'N', lambda value: choice(value, ('N', 'E', 'O')))
    stop = int(prompt('停止位', 1, lambda value: choice(value, ('1', '2'))))
    active = input('直接回车只监听；输入 T 可以配置测试帧：').strip().upper() == 'T'
    frame, expected = prompt_probe() if active else (b'', b'')
    return Settings(port, baud, duration, bits, parity, stop, frame, expected)


def parser():
    arguments = argparse.ArgumentParser(description='实车串口自检。默认只列举；可指定端口进行监听。')
    arguments.add_argument('--interactive', action='store_true', help='交互选择端口与设置')
    arguments.add_argument('--list', action='store_true', help='只列出 Windows COM 口')
    arguments.add_argument('--port', type=port_name, help='明确指定要打开的 Windows COM 口')
    arguments.add_argument('--baud', type=lambda value: int_range(value, 'baudrate'), help='板端实际波特率')
    arguments.add_argument('--duration-ms', type=lambda value: int_range(value, 'duration_ms'), default=5000,
                           help='监听时长，默认 5000 ms')
    arguments.add_argument('--data-bits', type=int, choices=(5, 6, 7, 8), default=8, help='数据位，默认 8')
    arguments.add_argument('--parity', type=str.upper, choices=('N', 'E', 'O'), default='N', help='校验位，默认 N')
    arguments.add_argument('--stop-bits', type=int, choices=(1, 2), default=1, help='停止位，默认 1')
    arguments.add_argument('--test-frame', action='store_true', help='交互输入自己的无动作测试帧并确认发送')
    return arguments


def main(argv=None):
    arguments = parser()
    options = arguments.parse_args(argv)
    if options.port and options.baud is None:
        arguments.error('指定 --port 时必须填写真实 --baud。')
    if (options.interactive or options.list) and (options.port or options.test_frame):
        arguments.error('--interactive 或 --list 不与 --port 或 --test-frame 一起使用。')
    if options.test_frame and not options.port:
        arguments.error('--test-frame 需要明确指定 --port。')
    log = DiagnosticLog(Path(__file__).resolve().parents[2])
    log.record('serial_started')
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        return finish(log, 'serial_dependency_missing')
    try:
        names = {item.device.upper() for item in list_ports.comports() if PORT_NAME.fullmatch(item.device)}
        ports = sorted(names, key=lambda value: int(value[3:]))
    except (OSError, ValueError):
        return finish(log, 'serial_list_failed')
    log.record('serial_ports', port_count=min(len(ports), 4096))
    if not ports:
        return finish(log, 'serial_no_ports')
    print('Windows 串口：' + ', '.join(ports))
    try:
        if options.interactive:
            settings = interactive_settings(ports)
        elif options.port:
            if options.port not in ports:
                return finish(log, 'serial_port_unavailable')
            frame, expected = prompt_probe() if options.test_frame else (b'', b'')
            settings = Settings(options.port, options.baud, options.duration_ms, options.data_bits,
                                options.parity, options.stop_bits, frame, expected)
        else:
            settings = None
        if settings is None:
            return finish(log, 'serial_list_only')
        if settings.port not in ports:
            return finish(log, 'serial_port_unavailable')
        print('正在打开所选串口；部分 USB 串口驱动打开时可能复位板卡。')
        # Keep the driver nonblocking for the bounded listen loop.
        write_timeout = max(.25, len(settings.frame) * 12 / settings.baudrate + .25)
        try:
            connection = serial.Serial(baudrate=settings.baudrate, bytesize=settings.data_bits,
                                       parity=settings.parity, stopbits=settings.stop_bits,
                                       timeout=0, write_timeout=write_timeout,
                                       xonxoff=False, rtscts=False, dsrdtr=False)
            connection.dtr = False
            connection.rts = False
        except (OSError, ValueError) as error:
            return finish(log, open_error(error), **settings.diagnostic_fields())
        return inspect_port(connection, settings, log)
    except (EOFError, KeyboardInterrupt):
        return finish(log, 'serial_cancelled')


if __name__ == '__main__':
    raise SystemExit(main())
