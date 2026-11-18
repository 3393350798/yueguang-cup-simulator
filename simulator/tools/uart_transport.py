"""Common binary UART interface for a local simulation or an explicit serial port."""
from urllib.request import Request, urlopen


class SimulatedUART:
    def __init__(self, port, *, timeout=1, from_start=False):
        if not 1 <= port <= 65535:
            raise ValueError('仿真端口范围为 1～65535。')
        self.url = f'http://127.0.0.1:{port}/uart'
        self.timeout = timeout
        self.cursor = 0
        self.dropped_bytes = 0
        self._session = None
        self._pending = bytearray()
        if not from_start:
            # 跳过连接前的字节，只消费新到的数据。
            with urlopen(self.url+'?after=0',timeout=timeout) as response:
                self.cursor = int(response.headers['X-Next-Cursor'])
                self._session = response.headers.get('X-UART-Session')

    def write(self, data):
        data = bytes(data)
        if not data:
            return 0
        if len(data) > 4096:
            raise ValueError('仿真串口一次最多可提交 4096 字节。')
        request = Request(self.url, data=data, headers={'Content-Type':'application/octet-stream'})
        with urlopen(request,timeout=self.timeout):
            return len(data)

    def read(self, size=4096):
        if size < 0:
            raise ValueError('读取长度不能小于零。')
        if size and not self._pending:
            with urlopen(f'{self.url}?after={self.cursor}',timeout=self.timeout) as response:
                end = int(response.headers['X-Next-Cursor'])
                start = int(response.headers['X-Buffer-Start'])
                session = response.headers.get('X-UART-Session')
                restarted = session is not None and self._session is not None and session != self._session
                self._session = session
                if restarted or end < self.cursor:
                    self.cursor = 0
                    raise ConnectionError('仿真串口已重启，请重新同步队伍协议。')
                self.dropped_bytes += max(0,start-self.cursor)
                self._pending.extend(response.read())
                self.cursor = end
        data = bytes(self._pending[:size])
        del self._pending[:size]
        return data

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_uart(*, simulator_port=None, serial_port=None, baudrate=115200, timeout=1, from_start=False):
    """Return a context manager with read(size) and write(bytes)."""
    if (simulator_port is None) == (serial_port is None):
        raise ValueError('请选择仿真端口或实体串口之一。')
    if simulator_port is not None:
        return SimulatedUART(simulator_port,timeout=timeout,from_start=from_start)
    if not serial_port:
        raise ValueError('请输入要连接的实体串口名称。')
    try:
        import serial
    except ImportError as error:
        raise RuntimeError('实体串口需额外安装 pyserial。') from error
    return serial.Serial(port=serial_port, baudrate=baudrate, timeout=0, write_timeout=timeout)
