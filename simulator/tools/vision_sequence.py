"""Timed loopback replay of the published visual samples; no recognition or control."""
import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
from pathlib import Path
import sys
import time

from vision_replay import SAMPLES, export_openmv, load_dataset
from vision_clip import add_commands


STEP_MS = 250
SAMPLES_BY_SLOT = (
    'empty01', 'tag01', 'tag03', 'tag02', 'empty02', 'both01',
    'tag04', 'both02', 'block01', 'block04', 'tag05', 'block02',
    'block06', 'both01', 'block03', 'tag04', 'empty01', 'block05',
    'both02', 'block03', 'tag01', 'empty02', 'block06', 'empty01',
)
DROP_SLOTS = range(5, 8)
HOLD_SLOTS = range(12, 16)
MODES = ('baseline', 'fault')
BOUNDARY = 'moonlight-frame'
STREAM_END = f'--{BOUNDARY}--\r\n'.encode('ascii')
FIELDS = ('slot', 'scheduled_ms', 'state', 'sample_id', 'source_slot', 'captured_ms')
HEADERS = ('X-Replay-Slot', 'X-Replay-Time-Ms', 'X-Replay-State',
           'X-Sample-ID', 'X-Source-Slot', 'X-Capture-Time-Ms')


def timeline(mode):
    if mode not in MODES:
        raise ValueError('回放模式只能是 baseline 或 fault。')
    for slot in range(len(SAMPLES_BY_SLOT)):
        source_slot = slot
        state = 'normal'
        if mode == 'fault':
            if slot in DROP_SLOTS:
                state, source_slot = 'drop', None
            elif slot in HOLD_SLOTS:
                state, source_slot = 'hold', HOLD_SLOTS.start - 1
            elif slot in (DROP_SLOTS.stop, HOLD_SLOTS.stop):
                state = 'recover'
        sample_id = SAMPLES_BY_SLOT[source_slot] if source_slot is not None else None
        captured = source_slot * STEP_MS if source_slot is not None else None
        yield dict(zip(FIELDS, (slot, slot * STEP_MS, state, sample_id, source_slot, captured)))


def timeline_csv(mode):
    out = StringIO(newline='')
    rows = csv.DictWriter(out, FIELDS, lineterminator='\n')
    rows.writeheader()
    rows.writerows(timeline(mode))
    return out.getvalue().encode('ascii')


def export(data, destination, image_format):
    selected = set(SAMPLES_BY_SLOT)
    if image_format == 'openmv':
        export_openmv(data, destination, selected)
    else:
        destination.mkdir(parents=True, exist_ok=False)
        for item in data['samples']:
            if item['id'] in selected:
                (destination / item['image']).write_bytes((SAMPLES / item['image']).read_bytes())
    for mode in MODES:
        (destination / (mode + '.csv')).write_bytes(timeline_csv(mode))


def jpeg_images(data):
    from PIL import Image

    frames = {}
    for item in data['samples']:
        key = item['id']
        if key in SAMPLES_BY_SLOT:
            buf = BytesIO()
            with Image.open(SAMPLES / item['image']) as img:
                img.convert('RGB').save(buf, 'JPEG', quality=95, subsampling=0)
            frames[key] = buf.getvalue()
    return frames


def mjpeg_parts(mode, frames):
    for row in timeline(mode):
        if row['state'] == 'drop':
            continue  # Do not send a placeholder on the wire.
        data = frames[row['sample_id']]
        part = [f'--{BOUNDARY}', 'Content-Type: image/jpeg', f'Content-Length: {len(data)}']
        part.extend(f'{name}: {row[field]}' for field, name in zip(FIELDS, HEADERS))
        yield row['scheduled_ms'], ('\r\n'.join(part) + '\r\n\r\n').encode('ascii') + data + b'\r\n'


def wait_until(target):
    seconds = target - time.monotonic()
    if seconds > 0:
        time.sleep(seconds)


def preview(mode):
    reading = (f'<figure><img src="/{mode}.mjpg" width="320" height="240" '
               f'alt="视觉连续帧：{mode}"><figcaption>{mode} · 每次打开播放一遍</figcaption></figure>') if mode else ''
    return ('''<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,">
<title>月光杯 · 视觉连续帧回放</title>
<style>body{font:16px/1.6 system-ui,sans-serif;margin:24px;max-width:620px}
figure{margin:18px 0}img{max-width:100%;height:auto;background:#eee}small{display:block}</style>
<h1>视觉连续帧回放</h1><p>每轮 6 秒，间隔 250 毫秒。选择一轮播放，再点一次可重播。</p>
<p><a href="/baseline">正常回放</a>　<a href="/fault">故障回放</a></p>
<p>故障轮先掉帧，再发送旧画面，最后恢复发送新帧。</p>
<p>电脑程序输入：<code>/baseline.mjpg</code> 或 <code>/fault.mjpg</code>。<br>
时间表：<a href="/baseline.csv">正常 CSV</a>　<a href="/fault.csv">故障 CSV</a></p>'''
            + reading + '</html>').encode('utf-8')


def handler_for(streams, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def send_payload(self, data, mime):
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            host = self.headers.get('Host', '').lower()
            if host not in {f'127.0.0.1:{port}', f'localhost:{port}'}:
                self.send_error(403)
                return
            path = self.path
            if path == '/' or path[1:] in MODES:
                self.send_payload(preview(path[1:]), 'text/html; charset=utf-8')
            elif path in ('/baseline.csv', '/fault.csv'):
                self.send_payload(timeline_csv(path[1:-4]), 'text/csv; charset=us-ascii')
            elif path in ('/baseline.mjpg', '/fault.mjpg'):
                self.stream(path[1:-5])
            else:
                self.send_error(404)

        def stream(self, mode):
            parts = streams[mode]
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=' + BOUNDARY)
            self.send_header('Content-Length', str(sum(len(part) for _, part in parts) + len(STREAM_END)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            started = time.monotonic()
            for scheduled_ms, part in parts:
                wait_until(started + scheduled_ms / 1000)
                self.wfile.write(part)
            wait_until(started + len(SAMPLES_BY_SLOT) * STEP_MS / 1000)
            self.wfile.write(STREAM_END)

        def handle(self):
            try:
                super().handle()
            except (ConnectionError, TimeoutError):
                pass  # The member can stop reading or close the tab at any point.

    return Handler


def serve(data, port):
    frames = jpeg_images(data)
    streams = {mode: tuple(mjpeg_parts(mode, frames)) for mode in MODES}
    with ThreadingHTTPServer(('127.0.0.1', port), handler_for(streams, port)) as server:
        print(f'本机回放页：http://127.0.0.1:{port}/', flush=True)
        print(f'电脑程序流：http://127.0.0.1:{port}/baseline.mjpg 或 /fault.mjpg', flush=True)
        print('按 Ctrl+C 停止。无需启动 Webots。', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description='回放相同视觉帧序列，复现掉帧、旧帧及恢复。')
    commands = parser.add_subparsers(dest='command')
    server = commands.add_parser('serve', help='在本机播放正常与故障 MJPEG')
    server.add_argument('--port', type=int, default=8767)
    table = commands.add_parser('timeline', help='将计划时间表打印为 CSV')
    table.add_argument('mode', choices=MODES)
    bundle = commands.add_parser('export', help='导出图像与正常、故障 CSV 计划')
    bundle.add_argument('destination', type=Path, help='新建输出目录，不覆盖已有文件夹')
    bundle.add_argument('--format', choices=('png', 'openmv'), default='png')
    add_commands(commands)
    args = parser.parse_args()
    try:
        if hasattr(args, 'clip_action'):
            args.clip_action(args)
            return 0
        if args.command == 'timeline':
            sys.stdout.buffer.write(timeline_csv(args.mode))
            return 0
        data = load_dataset()
        if set(SAMPLES_BY_SLOT) - {item['id'] for item in data['samples']}:
            raise ValueError('连续帧所需样本不完整，请重新解压发行包。')
        if args.command == 'export':
            export(data, args.destination, args.format)
            print(f'已生成正常与故障时间表及图像：{args.destination}')
        else:
            port = getattr(args, 'port', 8767)
            if not 1024 <= port <= 65535:
                raise ValueError('端口须在 1024 至 65535 之间。')
            serve(data, port)
    except (OSError, ValueError) as error:
        print(f'视觉连续帧回放失败：{error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('已取消视觉录制或对照。', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
