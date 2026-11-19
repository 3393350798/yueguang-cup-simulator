"""Webots ↔ Renode；仅交换器件输入输出，不含比赛算法。"""
from pathlib import Path
import atexit
import codecs
from contextlib import suppress
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from firmware.pins import resolve_pin

ROOT = Path(__file__).resolve().parents[1]


class FirmwareImageMissing(ValueError):
    pass


class RenodeMissing(FileNotFoundError):
    pass


class MotorPinsMissing(ValueError):
    pass


class RenodeLaunchFailed(RuntimeError):
    pass


class RenodeConnectionClosed(RuntimeError):
    pass


def diagnostic_reason(error):
    return {
        FirmwareImageMissing:'firmware_missing', RenodeMissing:'renode_missing',
        MotorPinsMissing:'motor_pins_missing', RenodeLaunchFailed:'renode_launch_failed',
        RenodeConnectionClosed:'renode_connection_closed',
    }.get(type(error))


def find_renode(setting=''):
    candidates = [setting, os.environ.get('RENODE', ''), shutil.which('Renode.exe'), shutil.which('renode')]
    for folder in (ROOT.parent/'.local/renode', ROOT.parent.parent/'.local/renode', Path('C:/Program Files/Renode')):
        if folder.is_dir():
            candidates.extend(folder.rglob('Renode.exe'))
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item).resolve())
    raise RenodeMissing('请先运行 scripts/setup-firmware.bat，或在固件配置中选择 Renode.exe。')


class FirmwareBridge:
    def __init__(self, config=None, board=None, *, bench=False):
        self.config = config if config is not None else json.loads((ROOT/'firmware.json').read_text(encoding='utf-8-sig'))
        board = board if board is not None else json.loads((ROOT/'board.json').read_text(encoding='utf-8-sig'))
        image = self.config.get('image')
        if not image:
            raise FirmwareImageMissing('请在 scripts/import-hardware.bat 的固件页中导入 ELF / HEX / BIN。')
        image = (ROOT/image).resolve()
        if not image.is_file():
            raise FirmwareImageMissing('找不到已导入的固件镜像，请重新导入。')
        executable = find_renode(self.config.get('renode', ''))
        bindings = {role: resolve_pin(pin, board.get('mcu_reference', '')) for role, pin in board['bindings'].items()}
        if not bench:
            for side in ('left','right'):
                if not all(side+suffix in bindings for suffix in ('_pwm','_in1','_in2')):
                    raise MotorPinsMissing('请填写'+side+'_pwm / '+side+'_in1 / '+side+'_in2 三个电机驱动引脚。')
        self._temporary = tempfile.TemporaryDirectory(prefix='moonlight-fw-bench-') if bench else None
        self.folder = Path(self._temporary.name if self._temporary else tempfile.mkdtemp(prefix='moonlight-fw-'))
        self.process = self.socket = self.stream = None
        self.uart = ''
        self.uart_raw = b''
        self.uart_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.gripper = None
        self.lift = None
        atexit.register(self.close)
        try:
            self._launch(image, executable, bindings, bench)
        except BaseException:
            self.close()
            raise

    def _launch(self, image, executable, bindings, bench):
        for path in (ROOT/'tools/firmware').glob('*.*'):
            shutil.copyfile(path, self.folder/path.name)
        platform = (self.folder/'f103.repl').read_text(encoding='utf-8')
        platform = platform.replace('"rcc.py"', '"'+(self.folder/'rcc.py').as_posix()+'"')
        platform = platform.replace('PerformanceInMips: 8', 'PerformanceInMips: '+str(int(self.config.get('cpu_mips', 8))))
        (self.folder/'f103.repl').write_text(platform, encoding='utf-8')
        suffix = image.suffix.lower()
        shutil.copyfile(image, self.folder/('member'+suffix))
        (self.folder/'session.json').write_text(json.dumps(dict(self.config, bindings=bindings, bench=bench)), encoding='utf-8')
        load = {'.elf':'LoadELF', '.axf':'LoadELF', '.hex':'LoadHEX', '.bin':'LoadBinary'}[suffix]
        lines = ['include @MoonlightSTM32Timer.cs', 'include @MoonlightSTM32ADC.cs',
                 'include @MoonlightSTM32USART.cs', 'mach create',
                 'machine LoadPlatformDescription @f103.repl', f'sysbus {load} @member{suffix}']
        if suffix == '.bin':
            address = int(str(self.config['bin_address']), 0)
            lines[-1] += ' '+hex(address)
            vectors = image.read_bytes()[:8]
            lines.extend(['sysbus WriteDoubleWord 0xE000ED08 '+hex(address), 'sysbus.cpu SP '+hex(int.from_bytes(vectors[:4],'little')), 'sysbus.cpu PC '+hex(int.from_bytes(vectors[4:],'little'))])
        lines.extend(['logLevel 3', 'include @server.py'])
        (self.folder/'run.resc').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        self.log = (self.folder/'renode.log').open('wb')
        env = dict(os.environ, YUEGUANG_FW_SESSION=str(self.folder))
        try:
            self.process = subprocess.Popen([executable, '--disable-xwt', '--console', '--plain', str(self.folder/'run.resc')],
                cwd=self.folder, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except OSError as error:
            raise RenodeLaunchFailed('无法启动 Renode。') from error
        deadline = time.monotonic()+35
        while not (self.folder/'port').exists():
            startup_log = (self.folder/'renode.log').read_text(encoding='utf-8', errors='replace')
            if self.process.poll() is not None or time.monotonic() > deadline or 'There was an error' in startup_log:
                self.log.flush()
                if bench:
                    raise RenodeLaunchFailed('Renode 未能载入镜像，或虚拟芯片未就绪。请运行启动自检并核对固件格式。')
                raise RenodeLaunchFailed('Renode未能加载固件：'+(self.folder/'renode.log').read_text(encoding='utf-8', errors='replace')[-2200:])
            time.sleep(.1)
        self.socket = socket.create_connection(('127.0.0.1', int((self.folder/'port').read_text())), timeout=10)
        self.stream = self.socket.makefile('rwb')

    def step(self, sensors, dt, uart=''):
        packet = {key:sensors[key] for key in ('ir','sonar','encoder')}
        packet.update(dt=dt, uart=list(uart.encode('utf-8') if isinstance(uart, str) else uart))
        self.stream.write((json.dumps(packet)+'\n').encode('ascii'))
        self.stream.flush()
        line = self.stream.readline()
        if not line:
            raise RenodeConnectionClosed('Renode执行连接已关闭。')
        result = json.loads(line)
        if 'error' in result:
            raise RuntimeError(result['error'])
        raw = bytes(result['uart'])
        self.uart_raw = (self.uart_raw+raw)[-512:]
        self.uart = (self.uart+self.uart_decoder.decode(raw))[-2000:]
        result['uart_text'] = self.uart
        result['uart_hex'] = self.uart_raw.hex(' ').upper()
        if 'gripper_closed' in result and result['gripper_closed'] != self.gripper:
            self.gripper = result['gripper_closed']
            result['gripper'] = 'grab' if self.gripper else 'release'
        if 'gripper_raised' in result and result['gripper_raised'] != self.lift:
            self.lift = result['gripper_raised']
            result['gripper_lift'] = 'lift' if self.lift else 'lower'
        return result

    def close(self):
        if self.socket:
            with suppress(OSError):
                self.socket.shutdown(socket.SHUT_RDWR)
            if self.stream:
                with suppress(OSError):
                    self.stream.close()
            with suppress(OSError):
                self.socket.close()
            self.stream = None
            self.socket = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.process and self.process.stdin:
            self.process.stdin.close()
        if hasattr(self, 'log'):
            self.log.close()
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        atexit.unregister(self.close)
