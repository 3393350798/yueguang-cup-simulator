"""Optional repeatable disturbances for practice."""
from collections import deque
from random import Random


class Imperfections:
    def __init__(self, config):
        seed = config.get('seed', 0)
        self.sensor_random = Random(seed)
        self.uart_random = Random(seed + 1)
        self.gain = config.get('motor_gain', [1, 1])
        self.deadzone = config.get('motor_deadzone', [0, 0])
        self.ir_noise = config.get('ir_noise', 0)
        self.sonar_noise_m = config.get('sonar_noise_mm', 0)/1000
        self.uart_delay = config.get('uart_delay_ms', 0)/1000
        self.uart_loss = config.get('uart_loss', 0)
        self.queue = deque()
        self.dropped = 0
        if (len(self.gain) != 2 or len(self.deadzone) != 2 or
                not all(v >= 0 for v in self.gain) or not all(0 <= v < 1 for v in self.deadzone) or
                not all(v >= 0 for v in (self.ir_noise, self.sonar_noise_m, self.uart_delay)) or
                not 0 <= self.uart_loss <= 1):
            raise ValueError('请核对 imperfections 中的增益、死区、噪声、延迟和丢包率。')

    def motor(self, index, ratio):
        deadzone = self.deadzone[index]
        magnitude = max(0, abs(ratio)-deadzone)/(1-deadzone) * self.gain[index]
        return min(1, magnitude) * (-1 if ratio < 0 else 1)

    def infrared(self, value):
        if self.ir_noise:
            value += self.sensor_random.gauss(0, self.ir_noise)
        return max(0, min(1, value))

    def sonar(self, value, limit):
        if self.sonar_noise_m:
            value += self.sensor_random.gauss(0, self.sonar_noise_m)
        return max(0, min(limit, value))

    def send(self, time, frame):
        if not frame:
            return
        if self.uart_loss and self.uart_random.random() < self.uart_loss:
            self.dropped += 1
        else:
            self.queue.append((round(time + self.uart_delay, 9), frame))

    def receive(self, time):
        data = bytearray()
        while self.queue and self.queue[0][0] <= round(time, 9):
            data.extend(self.queue.popleft()[1])
        return data
