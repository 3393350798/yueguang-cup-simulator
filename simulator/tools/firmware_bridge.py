"""Webots ↔ Renode；仅交换器件输入输出，不含比赛算法。"""
from pathlib import Path
import atexit
import codecs
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from firmware.pins import resolve_pin

ROOT = Path(__file__).resolve().parents[1]


def find_renode(setting=''):
    candidates = [setting, os.environ.get('RENODE', ''), shutil.which('Renode.exe'), shutil.which('renode')]
    for folder in (ROOT.parent/'.local/renode', ROOT.parent.parent/'.local/renode', Path('C:/Program Files/Renode')):
        if folder.is_dir():
            candidates.extend(folder.rglob('Renode.exe'))
    for item in candidates:
        if item and Path(item).is_file():
            return str(Path(item).resolve())
    raise FileNotFoundError('请先运行 scripts/setup-firmware.bat，或在固件配置中选择 Renode.exe。')


class FirmwareBridge:
    def __init__(self, config=None, board=None):
        self.config = config or json.loads((ROOT/'firmware.json').read_text(encoding='utf-8-sig'))
        board = board or json.loads((ROOT/'board.json').read_text(encoding='utf-8-sig'))
        image = self.config.get('image')
        if not image:
            raise ValueError('请在 scripts/import-hardware.bat 的固件页中导入 ELF / HEX / BIN。')
        image = (ROOT/image).resolve()
        executable = find_renode(self.config.get('renode', ''))
        bindings = {role: resolve_pin(pin, board.get('mcu_reference', '')) for role, pin in board['bindings'].items()}
        for side in ('left','right'):
            if not all(side+suffix in bindings for suffix in ('_pwm','_in1','_in2')):
                raise ValueError('请填写'+side+'_pwm / '+side+'_in1 / '+side+'_in2 三个电机驱动引脚。')
        self.folder = Path(tempfile.mkdtemp(prefix='moonlight-fw-'))
        self.process = self.socket = self.stream = None
        self.uart = ''
        self.uart_raw = b''
        self.uart_decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        self.gripper = None
        atexit.register(self.close)
        for path in (ROOT/'tools/firmware').glob('*.*'):
            shutil.copyfile(path, self.folder/path.name)
        platform = (self.folder/'f103.repl').read_text(encoding='utf-8')
        platform = platform.replace('"rcc.py"', '"'+(self.folder/'rcc.py').as_posix()+'"')
        platform = platform.replace('PerformanceInMips: 8', 'PerformanceInMips: '+str(int(self.config.get('cpu_mips', 8))))
        (self.folder/'f103.repl').write_text(platform, encoding='utf-8')
        suffix = image.suffix.lower()
        shutil.copyfile(image, self.folder/('member'+suffix))
        (self.folder/'session.json').write_text(json.dumps(dict(self.config, bindings=bindings)), encoding='utf-8')
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
        self.process = subprocess.Popen([executable, '--disable-xwt', '--console', '--plain', str(self.folder/'run.resc')],
            cwd=self.folder, env=env, stdin=subprocess.PIPE, stdout=self.log, stderr=self.log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic()+35
            while not (self.folder/'port').exists():
                startup_log = (self.folder/'renode.log').read_text(encoding='utf-8', errors='replace')
                if self.process.poll() is not None or time.monotonic() > deadline or 'There was an error' in startup_log:
                    self.log.flush()
                    raise RuntimeError('Renode未能加载固件：'+(self.folder/'renode.log').read_text(encoding='utf-8', errors='replace')[-2200:])
                time.sleep(.1)
            self.socket = socket.create_connection(('127.0.0.1', int((self.folder/'port').read_text())), timeout=10)
            self.stream = self.socket.makefile('rwb')
        except Exception:
            self.close()
            raise

    def step(self, sensors, dt, uart=''):
        packet = {key:sensors[key] for key in ('ir','sonar','encoder')}
        packet.update(dt=dt, uart=list(uart.encode('utf-8') if isinstance(uart, str) else uart))
        self.stream.write((json.dumps(packet)+'\n').encode('ascii'))
        self.stream.flush()
        line = self.stream.readline()
        if not line:
            raise RuntimeError('Renode执行连接已关闭。')
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
        return result

    def close(self):
        if self.socket:
            try:
                self.socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            if self.stream:
                self.stream.close()
            self.socket.close()
            self.socket = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if hasattr(self, 'log'):
            self.log.close()
