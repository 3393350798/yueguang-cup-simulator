"""Browse fixed Webots frames and compare participants' offline predictions."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import struct
import sys


PROJECT = Path(__file__).resolve().parents[2]
SAMPLES = PROJECT / 'simulator' / 'assets' / 'vision_samples'
RESULTS_FORMAT = 'moonlight-vision-results-v1'
DATASET_FORMAT = 'moonlight-vision-samples-v1'
ID = re.compile(r'[a-z][a-z0-9]{1,15}')
COLORS = {'red': '红色', 'yellow': '黄色', 'blue': '蓝色'}
VIEWS = {'tag': '标签区', 'pickup': '取货区', 'combined': '同框'}
CHANNELS = {'tag_id': '标签', 'block_color': '物块'}
STATUS = {
    'match': '一致',
    'missed': '漏检',
    'false_positive': '误报',
    'wrong_value': '错认',
    'untested': '未提交',
}


def unique_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('JSON 含重复字段。')
        out[key] = value
    return out


def reject_special_number(_):
    raise ValueError('JSON 不能含 NaN 或无穷大。')


def read_json(path):
    with path.open('rb') as file:
        raw = file.read(262145)
    if len(raw) > 262144:
        raise ValueError('JSON 文件大于 256 KiB。')
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_keys,
                          parse_constant=reject_special_number)
    except (UnicodeDecodeError, RecursionError, json.JSONDecodeError):
        raise ValueError('文件须为有效的 UTF-8 JSON。') from None


def write_json(path, data):
    with path.open('x', encoding='utf-8') as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
        file.write('\n')


def load_dataset():
    data = read_json(SAMPLES / 'manifest.json')
    if (not isinstance(data, dict) or data.get('schema') != DATASET_FORMAT
            or (data.get('width'), data.get('height')) != (320, 240)
            or data.get('tag_family') != 'tag36h11'
            or not isinstance(data.get('samples'), list) or not data['samples']):
        raise ValueError('视觉样本清单不完整，请重新解压发行包。')
    ids = set()
    for item in data['samples']:
        if not isinstance(item, dict):
            raise ValueError('视觉样本清单格式错误。')
        key = item.get('id')
        if (not isinstance(key, str) or not ID.fullmatch(key) or key in ids
                or item.get('image') != key + '.png'
                or not isinstance(item.get('sha256'), str)
                or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise ValueError('视觉样本清单含无效或重复 ID。')
        raw = (SAMPLES / item['image']).read_bytes()
        if (len(raw) < 24 or raw[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
                or struct.unpack('>II', raw[16:24]) != (320, 240)
                or sha256(raw).hexdigest() != item['sha256']):
            raise ValueError(f'样本 {key} 与发布的图像不一致，请重新解压发行包。')
        ids.add(key)
    return data


def label(field, value):
    if value is None:
        return '无'
    return f'ID {value}' if field == 'tag_id' else COLORS[value]


def conditions(item):
    return (f"{VIEWS[item['view']]} · {item['distance_mm']} mm · "
            f"{item['angle_deg']:+}° · 光强 {item['light_gain']:g}×"
            + (' · 部分遮挡' if item['partially_occluded'] else ''))


def prediction_template(data):
    # Omitted fields are untested; null is a deliberate report of no detection.
    return dict(schema=RESULTS_FORMAT, dataset=data['dataset'],
                results=[{'id': item['id']} for item in data['samples']])


def grade(expected, observed, reported):
    if not reported:
        return 'untested'
    if expected == observed:
        return 'match'
    if observed is None:
        return 'missed'
    if expected is None:
        return 'false_positive'
    return 'wrong_value'


def compare(data, predictions):
    if (not isinstance(predictions, dict) or predictions.get('schema') != RESULTS_FORMAT
            or predictions.get('dataset') != data['dataset']
            or not isinstance(predictions.get('results'), list)):
        raise ValueError('结果格式或样本集版本不符，请先生成对应的空白结果。')
    known = {item['id'] for item in data['samples']}
    supplied = {}
    for row in predictions['results']:
        if not isinstance(row, dict) or set(row) - {'id', *CHANNELS}:
            raise ValueError('结果每行只接受 id、tag_id、block_color。')
        key = row.get('id')
        if not isinstance(key, str) or key not in known or key in supplied:
            raise ValueError('结果含未知或重复的样本 ID。')
        tag = row.get('tag_id')
        if tag is not None and (type(tag) is not int or not 0 <= tag <= 65535):
            raise ValueError(f'{key}：tag_id 须为整数或 null。')
        color = row.get('block_color')
        if color is not None and (not isinstance(color, str) or color not in COLORS):
            raise ValueError(f'{key}：block_color 须为 red、yellow、blue 或 null。')
        supplied[key] = row
    summary = {field: dict.fromkeys(STATUS, 0) for field in CHANNELS}
    details = []
    for sample in data['samples']:
        row = supplied.get(sample['id'], {})
        result = {'id': sample['id']}
        for field in CHANNELS:
            sent = field in row
            actual, expected = row.get(field), sample[field]
            status = grade(expected, actual, sent)
            summary[field][status] += 1
            result[field] = dict(expected=expected, predicted=actual, status=status)
        details.append(result)
    return dict(schema='moonlight-vision-report-v1', dataset=data['dataset'],
                summary=summary, samples=details)


def short_report(report):
    return '；'.join(
        CHANNELS[field] + '：' + ' / '.join(f'{STATUS[s]} {counts[s]}' for s in STATUS)
        for field, counts in report['summary'].items()
    )


def export_openmv(data, destination, selected=None):
    from PIL import Image

    valid_ids = {item['id'] for item in data['samples']}
    if selected and not set(selected) <= valid_ids:
        raise ValueError('所选样本 ID 不存在。')
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for item in data['samples']:
        if selected and item['id'] not in selected:
            continue
        name = item['id'] + '.bmp'
        with Image.open(SAMPLES / item['image']) as img:
            img.convert('RGB').save(destination / name, 'BMP')
        records.append({'id': item['id'], 'image': name})
    writes = dict(schema='moonlight-vision-openmv-v1', dataset=data['dataset'],
                  width=data['width'], height=data['height'], samples=records)
    write_json(destination / 'frames.json', writes)
    return len(records)


def new_export_directory(parent, name):
    destination = parent / name
    index = 2
    while destination.exists():
        destination = parent / f'{name}-{index}'
        index += 1
    return destination


def launch_gui(data):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    window = tk.Tk()
    window.title('月光杯 · 视觉样本回放')
    window.minsize(980, 540)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    window.rowconfigure(2, weight=0)
    controls = ttk.Frame(window, padding=(12, 10))
    controls.grid(row=0, sticky='ew')
    work = ttk.Frame(window, padding=(12, 0))
    work.grid(row=1, sticky='nsew')
    work.columnconfigure(0, weight=1)
    work.rowconfigure(0, weight=1)
    table = ttk.Treeview(work, columns=('sample', 'tag', 'block'), show='headings',
                         selectmode='browse', height=15)
    for name, header, width in [('sample', '样本', 92), ('tag', '标签结果', 102), ('block', '物块结果', 102)]:
        table.heading(name, text=header)
        table.column(name, width=width, anchor='center')
    table.grid(row=0, column=0, sticky='nsew')
    scroll = ttk.Scrollbar(work, orient='vertical', command=table.yview)
    scroll.grid(row=0, column=1, sticky='ns')
    table.configure(yscrollcommand=scroll.set)
    preview = ttk.Frame(work, padding=(14, 0, 0, 0))
    preview.grid(row=0, column=2, sticky='n')
    image_label = ttk.Label(preview)
    image_label.grid(row=0, sticky='ew')
    caption = tk.StringVar()
    expected = tk.StringVar()
    details = tk.StringVar()
    truth = tk.BooleanVar(value=False)
    status = tk.StringVar(value=f"固定样本集：{len(data['samples'])} 张原图；可保存空白结果后用自己的程序检测。")
    ttk.Label(preview, textvariable=caption).grid(row=1, pady=(6, 3), sticky='w')
    ttk.Checkbutton(preview, text='显示场景真值', variable=truth,
                    command=lambda: update_preview()).grid(row=2, sticky='w', pady=(4, 0))
    ttk.Label(preview, textvariable=expected).grid(row=3, pady=(3, 6), sticky='w')
    ttk.Label(preview, textvariable=details).grid(row=4, sticky='w')
    ttk.Label(window, textvariable=status, padding=(12, 10), wraplength=740).grid(row=2, sticky='ew')
    samples = {sample['id']: sample for sample in data['samples']}
    report = None
    report_rows = {}

    def update_preview(_=None):
        if not table.selection():
            return
        key = table.selection()[0]
        item = samples[key]
        frame = tk.PhotoImage(file=str(SAMPLES / item['image']))
        image_label.configure(image=frame)
        image_label.frame = frame
        caption.set(conditions(item))
        expected.set(' / '.join(CHANNELS[k] + '：' + label(k, item[k]) for k in CHANNELS)
                     if truth.get() else '')
        row = report_rows.get(key)
        if row:
            details.set('程序结果\n' + '\n'.join(
                CHANNELS[k] + '：' + ('未提交' if row[k]['status'] == 'untested'
                                    else label(k, row[k]['predicted']))
                for k in CHANNELS))
        else:
            details.set('')

    def save_template():
        file = filedialog.asksaveasfilename(title='保存空白预测结果', initialfile='vision-results.json',
                                           defaultextension='.json', filetypes=[('JSON', '*.json')])
        if file:
            try:
                write_json(Path(file), prediction_template(data))
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=window)
            else:
                status.set('已保存空白结果；未填写字段视为未提交，明确未检出时填写 null。')

    def import_result():
        nonlocal report, report_rows
        file = filedialog.askopenfilename(title='打开程序检测结果', filetypes=[('JSON', '*.json')])
        if not file:
            return
        try:
            next_report = compare(data, read_json(Path(file)))
        except (OSError, ValueError) as error:
            messagebox.showerror('无法读取结果', str(error), parent=window)
            return
        report = next_report
        report_rows = {item['id']: item for item in report['samples']}
        for key, row in report_rows.items():
            table.item(key, values=[key, *(STATUS[row[k]['status']] for k in CHANNELS)])
        status.set(short_report(report))
        save.configure(state='normal')
        update_preview()

    def save_report():
        file = filedialog.asksaveasfilename(title='保存对照报告', initialfile='vision-report.json',
                                           defaultextension='.json', filetypes=[('JSON', '*.json')])
        if file:
            try:
                write_json(Path(file), report)
            except OSError as error:
                messagebox.showerror('保存失败', str(error), parent=window)

    def export():
        folder = filedialog.askdirectory(title='选择 OpenMV 导出目录的上一级')
        if folder:
            destination = new_export_directory(Path(folder), 'moonlight-vision-openmv')
            try:
                count = export_openmv(data, destination)
            except (OSError, ValueError) as error:
                messagebox.showerror('导出失败', str(error), parent=window)
            else:
                messagebox.showinfo('已导出', f'已生成 {count} 张 BMP：\n{destination}', parent=window)

    for key in samples:
        table.insert('', 'end', iid=key, values=(key, '未提交', '未提交'))
    ttk.Button(controls, text='保存空白结果', command=save_template).pack(side='left', padx=(0, 6))
    ttk.Button(controls, text='打开程序结果', command=import_result).pack(side='left', padx=(0, 6))
    save = ttk.Button(controls, text='保存对照报告', command=save_report, state='disabled')
    save.pack(side='left', padx=(0, 6))
    ttk.Button(controls, text='导出 OpenMV', command=export).pack(side='left')
    table.bind('<<TreeviewSelect>>', update_preview)
    table.selection_set(next(iter(samples)))
    update_preview()
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description='浏览月光杯视觉样本，对照自己程序的识别结果。')
    command = parser.add_subparsers(dest='command')
    command.add_parser('list', help='列出样本 ID、工况和场景真值')
    command.add_parser('gui', help='打开样本查看和结果对照窗口')
    template = command.add_parser('template', help='生成空白预测结果文件')
    template.add_argument('destination', type=Path)
    evaluate = command.add_parser('compare', help='比对自己程序的结果')
    evaluate.add_argument('predictions', type=Path)
    evaluate.add_argument('--report', type=Path, help='另存 JSON 对照报告，不覆盖已有文件')
    openmv = command.add_parser('export-openmv', help='生成 OpenMV 可加载的独立 BMP 图像')
    openmv.add_argument('destination', type=Path, help='新建输出目录，不覆盖已有文件夹')
    openmv.add_argument('--sample', dest='selected', action='append', help='只导出该 ID，可重复指定')
    args = parser.parse_args()
    try:
        data = load_dataset()
        if args.command in (None, 'gui'):
            launch_gui(data)
        elif args.command == 'list':
            for sample in data['samples']:
                print(sample['id'], conditions(sample), *(label(k, sample[k]) for k in CHANNELS))
        elif args.command == 'template':
            write_json(args.destination, prediction_template(data))
        elif args.command == 'compare':
            report = compare(data, read_json(args.predictions))
            if args.report:
                write_json(args.report, report)
            print(short_report(report))
            for row in report['samples']:
                failures = [f'{CHANNELS[k]}{STATUS[row[k]["status"]]}' for k in CHANNELS
                            if row[k]['status'] not in ('match', 'untested')]
                if failures:
                    print(row['id'], '、'.join(failures))
        elif args.command == 'export-openmv':
            print(f'已导出 {export_openmv(data, args.destination, args.selected)} 张 BMP 图像。')
    except (OSError, ValueError) as error:
        print(f'视觉样本回放失败：{error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
