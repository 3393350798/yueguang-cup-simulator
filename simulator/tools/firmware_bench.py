"""在独立 Renode 会话中施加已知输入，观察虚拟 F103 外设。"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

from firmware.pins import resolve_pin
from firmware_bridge import ROOT, FirmwareBridge
from preflight import read_object, validate_image


SIDES = ('left', 'right')
SIDE_NAMES = ('左轮', '右轮')
MODE_NAMES = {'not_bound': '未绑定', 'input': '仍为输入', 'output': '普通输出', 'alternate': '复用输出'}
MAX_FRAME = 256
MAX_TX_PREVIEW = 512


def load_setup(args, *, image_override=None):
    firmware = read_object(args.firmware_config)
    board = read_object(args.board_config)
    if board.get('mcu') != 'STM32F103C8T6':
        raise ValueError('当前台架只支持 STM32F103C8T6，请检查板卡配置。')
    bindings = board.get('bindings')
    if not isinstance(bindings, dict):
        raise ValueError('请在硬件配置中保存管脚绑定。')
    used, resolved = {}, {}
    for role, pin in bindings.items():
        gpio = resolve_pin(str(pin), str(board.get('mcu_reference', '')))
        if gpio in used:
            raise ValueError(f'{gpio} 重复绑定：{used[gpio]}、{role}；请为各外设分别绑定。')
        used[gpio] = role
        resolved[role] = gpio
    image = str(image_override.resolve()) if image_override is not None else firmware.get('image')
    if not isinstance(image, str) or not image.strip():
        raise ValueError('未导入固件。先在 scripts/import-hardware.bat 的固件页导入已编译镜像。')
    path = (ROOT / image).resolve()
    if not path.is_file():
        raise ValueError('找不到指定的固件镜像。' if image_override is not None else '找不到已导入的固件镜像，请重新导入。')
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('固件镜像超过 16 MiB，请检查构建产物。')
    validate_image(path, firmware)
    mips, cpr = firmware.get('cpu_mips', 8), firmware.get('encoder_cpr')
    threshold, signs = firmware.get('ir_threshold'), firmware.get('motor_signs')
    if (type(mips) not in (int, float) or not 0 < mips <= 0xffffffff or mips != int(mips) or
            type(cpr) is not int or not 1 <= cpr <= 1_000_000_000 or
            type(threshold) not in (int, float) or not 0 <= threshold <= 1 or
            not isinstance(signs, list) or len(signs) != 2 or any(type(s) is not int or s not in (-1, 1) for s in signs)):
        raise ValueError('请检查 CPU 指令率、编码器计数、红外阈值和电机方向。')
    if firmware.get('vision_uart') not in ('usart1', 'usart2', 'usart3'):
        raise ValueError('在硬件配置中将调试串口设为 USART1、USART2 或 USART3。')
    return dict(firmware, image=str(path)), dict(board, bindings=resolved)


def assignments(text, label):
    parsed = {}
    for item in text.replace('，', ',').split(',') if text.strip() else ():
        key, separator, value = item.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not key or not value or key in parsed:
            raise ValueError(f'{label}请用 名称=数值，以逗号隔开，每项只能设置一次。')
        parsed[key] = value
    return parsed


def number(text, lower, upper, label, cast=int):
    try:
        value = cast(text)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label}必须是数字。') from error
    if not lower <= value <= upper:
        raise ValueError(f'{label}必须在 {lower} 到 {upper} 之间。')
    return value


def hex_data(text):
    try:
        result = bytes.fromhex(text)
    except ValueError as error:
        raise ValueError('HEX 请输入成对的十六进制数，例如 A1 05，每次最多 256 字节。') from error
    if not 1 <= len(result) <= MAX_FRAME:
        raise ValueError('每帧 HEX 长度须为 1～256 字节。')
    return result


def adc_channel(pin):
    if pin.startswith('PA') and int(pin[2:]) < 8:
        return int(pin[2:])
    return {'PB0': 8, 'PB1': 9}.get(pin)


def build_plan(args, firmware, board):
    step = args.step_ms
    warmup = number(args.warmup_ms, step, 2048, '预热时长（ms）')
    duration = number(args.duration_ms, step, 4096, '测试时长（ms）')
    if warmup % step or duration % step:
        raise ValueError('预热和测试时长必须是步长的整数倍。')
    if (warmup + duration) // step > 512:
        raise ValueError('一次最多记录 512 个周期，请缩短测试或增大步长。')
    bindings = board['bindings']
    adc = [0] * 32
    selected = {}
    for key, raw in assignments(args.ir, '红外').items():
        if not re.fullmatch(r'(?:ir)?(?:0|[1-9][0-9]?)', key):
            raise ValueError('红外通道请写 0 或 ir0 等编号。')
        index = number(key.removeprefix('ir'), 0, 31, '红外通道')
        role = f'ir{index}'
        if role in selected:
            raise ValueError(f'红外通道 {role} 设置了两次。')
        if role not in bindings:
            raise ValueError(f'{role} 未绑定。请在硬件配置中选择 GPIO。')
        pin = resolve_pin(bindings[role], board.get('mcu_reference', ''))
        adc[index] = number(raw, 0, 4095, '红外输入')
        selected[role] = dict(pin=pin, adc_channel=adc_channel(pin), input_12bit=adc[index],
                              gpio_high=adc[index]/4095 >= firmware['ir_threshold'])
    encoders = {}
    for side, label, value in zip(SIDES, SIDE_NAMES, (args.encoder_left, args.encoder_right)):
        edges = number(value, -4096, 4096, '编码器计数')
        if edges and any(f'encoder_{side}_{phase}' not in bindings for phase in ('a', 'b')):
            raise ValueError(f'{label}编码器需要同时绑定 A、B 相。')
        encoders[side] = edges
    sonars = {}
    for name, raw in assignments(args.sonar, '超声波').items():
        if any(f'sonar_{name}_{signal}' not in bindings for signal in ('trig', 'echo')):
            raise ValueError(f'超声波 {name} 需要同时绑定 Trig、Echo。')
        centimeters = None if raw.lower() in ('none', 'off', '无回波') else number(raw, 2, 400, '距离（cm）', float)
        sonars[name] = centimeters
    frames = [hex_data(frame) for frame in args.uart_hex.split(';')] if args.uart_hex.strip() else []
    if len(frames) > min(16, duration // step):
        raise ValueError('UART 最多 16 帧，每个测试周期发一帧；可延长测试时长。')
    expected = hex_data(args.expect_hex) if args.expect_hex.strip() else b''
    if expected and not frames:
        raise ValueError('要检查请求的回包，请先输入自己的测试帧。')
    return dict(step_ms=step, warmup_ms=warmup, duration_ms=duration, inputs_after_warmup=args.inputs_after_warmup,
                adc=adc, selected_ir=selected, encoders=encoders, sonar_cm=sonars, frames=frames, expected=expected)


def run_bench(firmware, board, plan, show_uart=False, progress=print):
    progress('正在启动独立 Renode，会在本机运行，不连接实际串口或 Webots。')
    bridge = FirmwareBridge(firmware, board, bench=True)
    clock = time.monotonic()
    snapshots = []
    preview = tail = b''
    matched = False
    startup_tx = test_tx = 0
    step_ms, warmup_ms, duration_ms = (plan[key] for key in ('step_ms', 'warmup_ms', 'duration_ms'))
    expected = plan['expected']
    zero = {side: 0 for side in SIDES}
    working_sonars = {name: cm / 100 for name, cm in plan['sonar_cm'].items() if cm is not None}
    dt = step_ms/1000
    try:
        progress('已载入虚拟 MCU，正在执行预热和测试周期。')
        for end_ms in range(step_ms, warmup_ms + duration_ms + 1, step_ms):
            if time.monotonic() - clock > 90:
                raise RuntimeError('虚拟测试超过 90 秒，请缩短测试时长或检查程序是否阻塞。')
            test_ms = end_ms - warmup_ms
            active = test_ms > 0
            reached = (dict((s, round(edges * test_ms / duration_ms)) for s, edges in plan['encoders'].items())
                       if active else zero)
            radians = {side: round_count * 2 * math.pi / firmware['encoder_cpr'] for side, round_count in reached.items()}
            frame_index = test_ms // step_ms - 1
            frame = plan['frames'][frame_index] if active and frame_index < len(plan['frames']) else b''
            sensors_enabled = active or not plan['inputs_after_warmup']
            sensors = dict(ir=plan['adc'] if sensors_enabled else [0] * 32,
                           sonar=working_sonars if sensors_enabled else {}, encoder=radians)
            try:
                result = bridge.step(sensors, dt, frame)
            except (OSError, ValueError, RuntimeError) as error:
                raise RuntimeError('虚拟 MCU 执行或收发中断；请检查固件、外设配置和虚拟时间设置。') from error
            raw = bytes(result['uart'])
            if active:
                test_tx += len(raw)
                if show_uart and len(preview) < MAX_TX_PREVIEW:
                    preview += raw[:MAX_TX_PREVIEW - len(preview)]
                if expected and not matched:
                    window = tail + raw
                    matched = expected in window
                    tail = window[-(len(expected)-1):] if len(expected) > 1 else b''
            else:
                startup_tx += len(raw)
            snapshots.append(dict(phase='test' if active else 'warmup', time_ms=round(result['virtual_s']*1000, 3),
                                  pc=result['pc'], motors=result['motors'], sonars=result['sonars'],
                                  encoder_sent_edges=reached, host_to_mcu_bytes=len(frame), mcu_tx_bytes=len(raw)))
        if float(snapshots[-1]['time_ms']) < warmup_ms + duration_ms - .1:
            raise RuntimeError('虚拟 MCU 未推进到请求的时长。')
    finally:
        bridge.close()
    report = dict(format='moonlight-firmware-bench', version=1, model='STM32F103C8T6/Renode',
                  step_ms=step_ms, warmup_ms=warmup_ms, duration_ms=duration_ms,
                  sensor_inputs_after_warmup=plan['inputs_after_warmup'],
                  ir=plan['selected_ir'], encoder_test_edges=plan['encoders'], sonar_test_cm=plan['sonar_cm'],
                  uart=dict(peripheral=firmware['vision_uart'], host_to_mcu_bytes=sum(map(len, plan['frames'])),
                            warmup_tx_bytes=startup_tx, test_tx_bytes=test_tx,
                            expected_reply=('matched' if matched else 'not_seen') if expected else 'not_checked'),
                  samples=snapshots)
    return report, preview


def show_report(report, preview):
    final = report['samples'][-1]
    print(f"虚拟时间 {final['time_ms']:.1f} ms，末次采样 PC={final['pc']}。")
    for side, label in zip(SIDES, SIDE_NAMES):
        motor = final['motors'][side]
        duty = motor['duty']
        signal = f'{100*duty:.1f}%' if duty is not None else ('当前复用或 PWM 模式不支持' if motor['fault'] else MODE_NAMES[motor['modes']['pwm']])
        directions = ' / '.join(str(motor[key]) if motor[key] is not None else MODE_NAMES[motor['modes'][key]]
                                for key in ('in1', 'in2'))
        print(f'{label}：PWM {signal}，IN1 / IN2 = {directions}')
    for role, ir in report['ir'].items():
        observation = f"ADC{ir['adc_channel']} 输入 {ir['input_12bit']}" if ir['adc_channel'] is not None else '该脚没有 ADC1 模拟输入'
        print(f"{role}（{ir['pin']}）：数字输入 = {int(ir['gpio_high'])}，{observation}")
    if report['encoder_test_edges'] != dict.fromkeys(SIDES, 0):
        print('编码器注入计数（左 / 右）：' + ' / '.join(str(report['encoder_test_edges'][s]) for s in SIDES))
    for name, counts in final['sonars'].items():
        print(f"超声波 {name}：合格触发 {counts['triggers']} 次，模拟回波开始 {counts['echo_started']} 次")
    uart = report['uart']
    print(f"{uart['peripheral'].upper()}：注入 {uart['host_to_mcu_bytes']} 字节，MCU 测试阶段发出 {uart['test_tx_bytes']} 字节；预期回包" +
          {'matched': '已匹配', 'not_seen': '未出现', 'not_checked': '未指定'}[uart['expected_reply']])
    if preview:
        print(f'仅本地显示测试阶段 TX（最多 {MAX_TX_PREVIEW} 字节）：{preview.hex(" ").upper()}')


def interactive(args, board):
    print('月光杯仿真 · STM32 外设测试台')
    print('当前板卡绑定：' + ('、'.join(sorted(board['bindings'])) or '未绑定 GPIO，可单独试 UART'))
    print('可按回车跳过不测的通道。UART 输入只送到虚拟 MCU，报告不保存报文。')
    args.ir = input('红外（例如 0=1024,1=3072；0～4095）：')
    args.encoder_left = input('左轮编码器增量（带符号的四倍频后计数，默认 0）：').strip() or '0'
    args.encoder_right = input('右轮编码器增量（带符号的四倍频后计数，默认 0）：').strip() or '0'
    args.sonar = input('超声波（例如 front=20，单位 cm；front=none 表示无回波）：')
    args.uart_hex = input('自有测试帧 HEX（不同帧用英文分号分隔）：')
    if args.uart_hex.strip():
        args.expect_hex = input('预期回包中应出现的 HEX（可留空）：')
    args.duration_ms = input(f'测试时长 ms（{args.step_ms} 的倍数，默认 {args.duration_ms}）：').strip() or args.duration_ms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interactive', action='store_true', help='输入本次要测试的外设')
    parser.add_argument('--firmware-config', type=Path, default=ROOT/'firmware.json', help='覆盖固件配置路径；不会修改文件')
    parser.add_argument('--board-config', type=Path, default=ROOT/'board.json', help='覆盖绑定配置路径；不会修改文件')
    parser.add_argument('--step-ms', type=int, choices=(4, 8, 16, 32), default=32)
    parser.add_argument('--warmup-ms', type=int, default=128)
    parser.add_argument('--duration-ms', type=int, default=320)
    parser.add_argument('--inputs-after-warmup', action='store_true', help='预热时红外为 0、超声波无回波，测试开始才应用输入')
    parser.add_argument('--ir', default='', help='例如 0=1024,1=3072，表示注入的 12 位值')
    parser.add_argument('--encoder-left', type=int, default=0, help='测试期间左轮四倍频后计数增量')
    parser.add_argument('--encoder-right', type=int, default=0, help='测试期间右轮四倍频后计数增量')
    parser.add_argument('--sonar', default='', help='例如 front=20,left=none；单位 cm')
    parser.add_argument('--uart-hex', default='', help='自有测试帧 HEX；不同周期的帧可用分号分隔')
    parser.add_argument('--expect-hex', default='', help='测试期间期望在 MCU TX 中出现的 HEX 片段')
    parser.add_argument('--show-uart', action='store_true', help='在本机终端显示最多 512 字节测试 TX，不写进报告')
    args = parser.parse_args(argv)
    try:
        firmware, board = load_setup(args)
        if args.interactive:
            interactive(args, board)
        plan = build_plan(args, firmware, board)
        report, preview = run_bench(firmware, board, plan, args.show_uart, lambda note: print(note, flush=True))
        folder = ROOT.parent/'output'/('firmware-bench-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8])
        folder.mkdir(parents=True)
        (folder/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        show_report(report, preview)
        print(f'已保存本地报告：{folder/"report.json"}')
        print('台架观察的是模拟外设；实板上的启动、电气和接线需单独验证。')
        return 2 if report['uart']['expected_reply'] == 'not_seen' else 0
    except (EOFError, KeyboardInterrupt):
        print('已取消测试。', file=sys.stderr)
        return 130
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(f'台架未完成：{error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
