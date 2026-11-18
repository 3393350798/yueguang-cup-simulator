# Runs in Renode's IronPython 2 interpreter; protocol is local JSON lines.
import json
import math
import os
import socket
import sys
sys.path.insert(0, os.environ['YUEGUANG_FW_SESSION'])
from System import Action
from Antmicro.Renode.Core import EmulationManager, IGPIOReceiver
from Antmicro.Renode.Time import TimeInterval
from pins import timer_channel

folder = os.environ['YUEGUANG_FW_SESSION']
with open(os.path.join(folder, 'session.json')) as config_file:
    config = json.load(config_file)
machine = monitor.Machine
bus = machine.SystemBus
emulation = EmulationManager.Instance.CurrentEmulation
bindings = config['bindings']
timer_bases = {1: 0x40012C00, 2: 0x40000000, 3: 0x40000400, 4: 0x40000800}
uart = machine['sysbus.' + config['vision_uart']]
tx = []
active = False


def on_uart(value):
    tx.append(int(value))


uart.CharReceived += on_uart


def port(pin):
    return machine['sysbus.gpioPort' + pin[1]], int(pin[2:])


def input_pin(pin, value):
    device, number = port(pin)
    device.OnGPIO(number, bool(value))


def output_pin(pin):
    if not pin:
        return 0
    device, number = port(pin)
    mode = (int(device.ReadDoubleWord(0 if number < 8 else 4)) >> (4 * (number % 8))) & 15
    return int(bool(int(device.ReadDoubleWord(12)) & (1 << number))) if mode & 3 else 0


def duty(pin):
    device, number = port(pin)
    mode = (int(device.ReadDoubleWord(0 if number < 8 else 4)) >> (4 * (number % 8))) & 15
    if not mode & 3:
        return 0.0
    if not mode & 8:
        return float(output_pin(pin))
    timer, channel = timer_channel(pin, int(bus.ReadDoubleWord(0x40010004)))
    base = timer_bases[timer]
    control = int(bus.ReadDoubleWord(base))
    enable = int(bus.ReadDoubleWord(base + 0x20)) >> (4 * channel)
    if not control & 1 or not enable & 1 or (timer == 1 and not int(bus.ReadDoubleWord(base + 0x44)) & 0x8000):
        return 0.0
    compare_mode = (int(bus.ReadDoubleWord(base + 0x18 + (channel // 2) * 4)) >> ((channel % 2) * 8 + 4)) & 7
    if compare_mode not in (6, 7):
        raise ValueError('Motor PWM requires PWM1/PWM2 mode: ' + pin)
    ratio = min(1.0, float(bus.ReadDoubleWord(base + 0x34 + 4 * channel)) / (int(bus.ReadDoubleWord(base + 0x2C)) + 1))
    if compare_mode == 7:
        ratio = 1 - ratio
    return 1 - ratio if enable & 2 else ratio


def motor(side):
    pwm = bindings.get(side + '_pwm')
    if not pwm:
        return 0.0
    forward = output_pin(bindings.get(side + '_in1'))
    reverse = output_pin(bindings.get(side + '_in2'))
    return duty(pwm) * (forward - reverse)


def later(seconds, callback):
    machine.ScheduleAction(TimeInterval.FromSeconds(seconds), Action[TimeInterval](lambda _: callback()))


class Sonar(IGPIOReceiver):
    def __init__(self, name, echo):
        self.name, self.echo, self.high = name, echo, None
        self.distance = 0.0
        self.count = 0

    def OnGPIO(self, number, value):
        if not active:
            return
        # Sync CPU time before measuring a software-generated trigger pulse.
        machine['sysbus.cpu'].SyncTime()
        now = machine.ElapsedVirtualTime.TimeElapsed.TotalSeconds
        if value:
            self.high = now
        elif self.high is not None:
            width = now - self.high
            self.high = None
            if width >= 0.000009 and 0.02 <= self.distance <= 4.0:
                self.count += 1
                pulse = 2 * self.distance / 343.0
                later(0.0001, lambda: input_pin(self.echo, True))
                later(0.0001 + pulse, lambda: input_pin(self.echo, False))


sonars = []
for role, pin in bindings.items():
    if role.startswith('sonar_') and role.endswith('_trig'):
        name = role[6:-5]
        echo = bindings.get('sonar_' + name + '_echo')
        if echo:
            receiver = Sonar(name, echo)
            device, number = port(pin)
            device.Connections[number].Connect(receiver, 0)
            sonars.append(receiver)

phases = [0, 0]
positions = None
quadrature = [(0, 0), (1, 0), (1, 1), (0, 1)]


def encoder_edges(side, index, delta, dt):
    a, b = bindings.get('encoder_' + side + '_a'), bindings.get('encoder_' + side + '_b')
    if not a or not b:
        return
    count = abs(delta)
    for edge in range(count):
        phases[index] = (phases[index] + (1 if delta > 0 else -1)) % 4
        va, vb = quadrature[phases[index]]
        def apply(a=a, b=b, va=va, vb=vb):
            input_pin(a, va)
            input_pin(b, vb)
        later(dt * (edge + 0.5) / count, apply)


def step(packet):
    global positions, active
    dt = packet['dt']
    for role, pin in bindings.items():
        if role.startswith('ir') and role[2:].isdigit():
            index = int(role[2:])
            if index < len(packet['ir']):
                value = packet['ir'][index]
                input_pin(pin, value >= config['ir_threshold'])
                # PA0..7 / PB0..1 correspond to ADC channels 0..9.
                channel = int(pin[2:]) if pin.startswith('PA') and int(pin[2:]) < 8 else int(pin[2:]) + 8 if pin in ('PB0','PB1') else None
                if channel is not None:
                    bus.WriteDoubleWord(0x40012480 + channel * 4, int(value * 4095))
    for sonar in sonars:
        sonar.distance = packet['sonar'].get(sonar.name, 0.0)
    current = [int(round(packet['encoder'][side] * config['encoder_cpr'] / (2 * math.pi))) for side in ('left','right')]
    if positions is None:
        positions = current
    for i, side in enumerate(('left','right')):
        encoder_edges(side, i, current[i] - positions[i], dt)
    positions = current
    for value in packet.get('uart', []):
        uart.WriteChar(value)
    active = True
    try:
        emulation.RunFor(TimeInterval.FromSeconds(dt))
    finally:
        active = False
    received = tx[:]
    del tx[:]
    result = dict(left=motor('left') * config['motor_signs'][0], right=motor('right') * config['motor_signs'][1],
                uart=received, pc=str(machine['sysbus.cpu'].PC), virtual_s=machine.ElapsedVirtualTime.TimeElapsed.TotalSeconds,
                sonar_triggers=sum(s.count for s in sonars))
    if 'gripper_close' in bindings:
        result['gripper_closed'] = bool(output_pin(bindings['gripper_close']))
    if 'gripper_lift' in bindings:
        result['gripper_raised'] = bool(output_pin(bindings['gripper_lift']))
    return result


listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen(1)
with open(os.path.join(folder, 'port.tmp'), 'w') as port_file:
    port_file.write(str(listener.getsockname()[1]))
os.rename(os.path.join(folder, 'port.tmp'), os.path.join(folder, 'port'))
connection, address = listener.accept()
stream = connection.makefile('rwb', 0)
try:
    while True:
        line = stream.readline()
        if not line:
            break
        try:
            response = step(json.loads(line))
        except Exception as error:
            response = dict(error=str(error))
        stream.write(json.dumps(response) + '\n')
finally:
    stream.close()
    connection.close()
    listener.close()
