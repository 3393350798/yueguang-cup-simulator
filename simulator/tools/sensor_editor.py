"""Local configuration and orthographic mounting diagram for the car sensors."""
from copy import deepcopy
from itertools import product
import math
import re
import tkinter as tk
from tkinter import ttk


class SensorEditor(ttk.Frame):
    colors = {'camera': '#237ad6', 'ir': '#00846e', 'sonar': '#a45b00'}

    def __init__(self, parent, robot, geometry, action, remap):
        super().__init__(parent, padding=12)
        self.devices = {kind: deepcopy(robot[kind]) for kind in self.colors}
        self.geometry, self.action, self.remap = geometry, action, remap
        self.selection = 'camera'
        self.fields = {}
        self.azimuth, self.elevation, self.zoom = math.radians(34), math.radians(32), 1
        self.drag = None
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        form = ttk.Frame(self, width=274)
        form.grid(row=0, column=0, sticky='ns', padx=(0, 12))
        self.list = ttk.Treeview(form, show='tree', height=6, selectmode='browse')
        scrollbar = ttk.Scrollbar(form, command=self.list.yview)
        self.list.configure(yscrollcommand=scrollbar.set)
        self.list.grid(row=0, column=0, sticky='ew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.list.column('#0', width=246, stretch=True)
        self.list.bind('<<TreeviewSelect>>', self.select_row)
        tools = ttk.Frame(form)
        tools.grid(row=1, column=0, columnspan=2, sticky='ew', pady=5)
        for text, command in [('＋ 红外', lambda: self.add('ir')),
                              ('＋ 超声波', lambda: self.add('sonar')),
                              ('移除', self.remove)]:
            ttk.Button(tools, text=text, command=lambda c=command: action(c)).pack(side='left', padx=(0, 3))
        self.details = ttk.LabelFrame(form, text='安装与参数', padding=8)
        self.details.grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
        self.sonar_form = ttk.LabelFrame(form, text='超声波通用参数', padding=8)
        self.sonar_form.grid(row=3, column=0, columnspan=2, sticky='ew', pady=4)
        self.range = self.field(self.sonar_form, 0, '量程 (mm)', robot['sonar']['range_mm'])
        self.aperture = self.field(self.sonar_form, 1, '开角 (°)', robot['sonar']['aperture_deg'])
        preview = ttk.Frame(self)
        preview.grid(row=0, column=1, sticky='nsew')
        bar = ttk.Frame(preview)
        bar.pack(fill='x', pady=(0, 6))
        for label, callback in [
            ('斜视', lambda: self.view(34, 32)),
            ('俯视', lambda: self.view(0, 90)),
            ('侧视', lambda: self.view(0, 0)),
            ('＋', lambda: self.scale(1.2)),
            ('−', lambda: self.scale(1/1.2)),
        ]:
            ttk.Button(bar, text=label, width=5, command=callback).pack(side='left', padx=(0, 4))
        self.canvas = tk.Canvas(preview, background='#f4f7f9', highlightthickness=0, width=420, height=420)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda _: self.draw())
        self.canvas.bind('<Button-1>', self.press)
        self.canvas.bind('<B1-Motion>', self.move)
        self.canvas.bind('<ButtonRelease-1>', lambda _: self.release())
        self.canvas.bind('<MouseWheel>', lambda event: self.scale(1.12 if event.delta > 0 else 1/1.12))
        self.canvas.bind('<Button-4>', lambda _: self.scale(1.12))
        self.canvas.bind('<Button-5>', lambda _: self.scale(1/1.12))
        self.refresh()

    def field(self, parent, row, label, value):
        variable = tk.StringVar(self, value=str(value))
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=1)
        ttk.Entry(parent, textvariable=variable, width=13).grid(row=row, column=1, sticky='ew', padx=(8, 0), pady=1)
        parent.columnconfigure(1, weight=1)
        variable.trace_add('write', lambda *_: self.draw() if hasattr(self, 'canvas') else None)
        return variable

    def rows(self):
        yield 'camera', '相机 camera', 'camera', self.devices['camera']['position_mm']
        for index, position in enumerate(self.devices['ir']['positions_mm']):
            yield f'ir{index}', f'红外 ir{index}', 'ir', position
        for index, sensor in enumerate(self.devices['sonar']['sensors']):
            yield f'sonar{index}', f"超声波 {sensor['name']}", 'sonar', sensor['position_mm']

    def device(self):
        key = self.selection
        if key == 'camera':
            return self.devices['camera'], 'camera'
        if key.startswith('ir'):
            return {'position_mm': self.devices['ir']['positions_mm'][int(key[2:])]}, 'ir'
        return self.devices['sonar']['sensors'][int(key[5:])], 'sonar'

    def refresh(self):
        self.list.delete(*self.list.get_children())
        for key, label, kind, _ in self.rows():
            self.list.insert('', 'end', iid=key, text=label, tags=(kind,))
        for kind, color in self.colors.items():
            self.list.tag_configure(kind, foreground=color)
        self.list.selection_set(self.selection)
        self.list.see(self.selection)
        self.load()

    def select_row(self, _):
        selected = self.list.selection()
        if selected and selected[0] != self.selection:
            old = self.selection
            self.action(lambda: self.select(selected[0]))
            if self.selection == old:
                self.list.selection_set(old)

    def select(self, key):
        if key != self.selection:
            self.commit()
            self.selection = key
            self.refresh()

    def load(self):
        for widget in self.details.winfo_children():
            widget.destroy()
        self.fields.clear()
        dev, kind = self.device()
        if kind == 'sonar':
            self.sonar_form.grid()
        else:
            self.sonar_form.grid_remove()
        labels = [(axis, axis.upper()+' (mm)', val) for axis, val in zip('xyz', dev['position_mm'])]
        if kind == 'sonar':
            labels += [('name', '名称', dev['name']), ('yaw_deg', '朝向 (°)', dev['yaw_deg'])]
        if kind == 'camera':
            labels += [(key, label, dev[key]) for key, label in (
                ('yaw_deg', '朝向 (°)'), ('pitch_deg', '向下俯角 (°)'),
                ('width', '图像宽度 (px)'), ('height', '图像高度 (px)'), ('fov_deg', '水平视场 (°)'))]
        for row, (key, label, value) in enumerate(labels):
            self.fields[key] = self.field(self.details, row, label, value)
        self.draw()

    @staticmethod
    def number(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('请输入有限数值。')
        return value

    def current(self):
        dev, kind = self.device()
        result = {**dev, 'position_mm': [self.number(self.fields[axis].get()) for axis in 'xyz']}
        if kind == 'sonar':
            name = self.fields['name'].get().strip()
            if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_]*', name):
                raise ValueError('超声波名称须以英文字母开头，可含数字和下划线。')
            if any(item is not dev and item['name'] == name for item in self.devices['sonar']['sensors']):
                raise ValueError('超声波名称重复：'+name)
            result['name'] = name
        for key in ('yaw_deg', 'pitch_deg', 'fov_deg'):
            if key in dev:
                result[key] = self.number(self.fields[key].get())
        if kind == 'camera':
            for key in ('width', 'height'):
                result[key] = int(self.fields[key].get())
            if min(result['width'], result['height']) <= 0 or not 0 < result['fov_deg'] < 180:
                raise ValueError('相机分辨率须为正整数，水平视场应在 0° 和 180° 之间。')
        return result

    def commit(self):
        dev, kind = self.device()
        result = self.current()
        if kind == 'sonar' and result['name'] != dev['name']:
            self.remap({f"sonar_{dev['name']}_{role}": f"sonar_{result['name']}_{role}" for role in ('trig', 'echo')})
        dev.update(result)
        if kind == 'ir':
            self.devices['ir']['positions_mm'][int(self.selection[2:])] = result['position_mm']

    def sonar_settings(self):
        distance, angle = self.number(self.range.get()), self.number(self.aperture.get())
        if distance <= 0 or not 0 < angle <= 180:
            raise ValueError('超声波量程应大于 0 mm，开角应在 0° 到 180° 之间。')
        return distance, angle

    def values(self, origin):
        self.commit()
        distance, angle = self.sonar_settings()
        for index, (_, _, z) in enumerate(self.devices['ir']['positions_mm']):
            if origin + z <= 0:
                raise ValueError(f'红外 ir{index} 应安装在地面以上，请调整 Z 坐标或原点高度。')
        self.devices['sonar'].update(range_mm=distance, aperture_deg=angle)
        return deepcopy(self.devices)

    def add(self, kind):
        self.commit()
        if kind == 'ir':
            items = self.devices['ir']['positions_mm']
            self.selection = f'ir{len(items)}'
            _, _, _, origin = self.geometry()
            items.append([60, 0, 12-origin])
        else:
            items = self.devices['sonar']['sensors']
            self.selection = f'sonar{len(items)}'
            names = {item['name'] for item in items}
            number = next(i for i in range(1, len(items)+2) if f'sensor{i}' not in names)
            items.append(dict(name=f'sensor{number}', position_mm=[60, 0, 35], yaw_deg=0))
        self.refresh()

    def remove(self):
        if self.selection == 'camera':
            return
        self.commit()
        dev, kind = self.device()
        if kind == 'ir':
            index = int(self.selection[2:])
            items = self.devices['ir']['positions_mm']
            mapping = {self.selection: None}
            mapping.update({f'ir{i}': f'ir{i-1}' for i in range(index+1, len(items))})
        else:
            index = int(self.selection[5:])
            items = self.devices['sonar']['sensors']
            mapping = {f"sonar_{dev['name']}_{role}": None for role in ('trig', 'echo')}
        self.remap(mapping)
        del items[index]
        self.selection = f'{kind}{min(index, len(items)-1)}' if items else 'camera'
        self.refresh()

    def view(self, azimuth, elevation):
        self.azimuth, self.elevation, self.zoom = math.radians(azimuth), math.radians(elevation), 1
        self.draw()

    def scale(self, factor):
        self.zoom = max(.4, min(4, self.zoom * factor))
        self.draw()

    def press(self, event):
        canvas = self.canvas
        items = canvas.find_withtag('current')
        tag = next((tag for tag in canvas.gettags(items[0]) if tag.startswith('sensor:')), None) if items else None
        if tag:
            key = tag.split(':')[1]
            self.action(lambda: self.select(key))
            if self.selection == key:
                self.drag = (event.x, event.y, 'sensor')
        else:
            self.drag = (event.x, event.y, 'orbit')

    def move(self, event):
        if self.drag is None:
            return
        x, y, mode = self.drag
        if mode == 'orbit':
            self.azimuth += (event.x-x)/120
            self.elevation = min(math.pi/2, max(-math.pi/2, self.elevation+(event.y-y)/120))
        else:
            if abs(math.sin(self.elevation)) < .15:
                return
            try:
                dev = self.current()
            except ValueError:
                return
            horizontal = (event.x-x)/self.unit
            depth = (event.y-y)/(self.unit*math.sin(self.elevation))
            d_x = self.ca*horizontal + self.sa*depth
            d_y = -self.sa*horizontal + self.ca*depth
            self.fields['x'].set(f"{dev['position_mm'][0]+d_x:.1f}")
            self.fields['y'].set(f"{dev['position_mm'][1]+d_y:.1f}")
        self.drag = (event.x, event.y, mode)
        self.draw()

    def release(self):
        if self.drag and self.drag[2] == 'sensor':
            self.action(self.commit)
        self.drag = None

    def draw(self):
        if not hasattr(self, 'canvas') or len(self.fields) < 3:
            return
        canvas = self.canvas
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        length, breadth, body_height, origin = self.geometry()
        cx, cy = width*.52, height*.49
        self.unit = max(1, min(width, height))*self.zoom/max(270, length*1.8, breadth*1.8)
        self.ca, self.sa = math.cos(self.azimuth), math.sin(self.azimuth)
        ce, se = math.cos(self.elevation), math.sin(self.elevation)

        def pt(p):
            x, y, z = p
            return (cx + self.unit*(self.ca*x-self.sa*y),
                    cy - self.unit*(ce*z-se*(self.sa*x+self.ca*y)))

        def line(points, **kwargs):
            canvas.create_line(*(v for p in points for v in pt(p)), **kwargs)

        def box(lo, hi, color):
            for axis in range(3):
                rest = [i for i in range(3) if i != axis]
                for corner in product((0, 1), repeat=2):
                    a, b = list(lo), list(lo)
                    for i, k in zip(rest, corner):
                        a[i] = b[i] = (lo, hi)[k][i]
                    b[axis] = hi[axis]
                    line([a, b], fill=color, width=2)

        span = max(120, length*.72, breadth*.72)
        for i in range(-4, 5):
            m = i*span/4
            line([(-span, m, -origin), (span, m, -origin)], fill='#d9e2e8')
            line([(m, -span, -origin), (m, span, -origin)], fill='#d9e2e8')
        box((-length/2, -breadth/2, -body_height/2), (length/2, breadth/2, body_height/2), '#7b8d9a')
        axes = [((length*.65, 0, 0), 'X 前', '#b94343'),
                ((0, breadth*.72, 0), 'Y 左', '#218354')]
        if abs(ce) > .15:
            axes.append(((0, 0, max(body_height*.85, 27)), 'Z 上', '#2c63a0'))
        for end, text, color in axes:
            line([(0, 0, 0), end], fill=color, arrow=tk.LAST, dash=(3, 2))
            x, y = pt(end)
            canvas.create_text(x, y-8, text=text, fill=color, font=('Segoe UI', 9))

        try:
            active = self.current()
        except ValueError:
            active = self.device()[0]
        try:
            distance, aperture = self.sonar_settings()
        except ValueError:
            distance, aperture = self.devices['sonar']['range_mm'], self.devices['sonar']['aperture_deg']
        shapes = []
        for key, label, kind, position in self.rows():
            selected = key == self.selection
            if selected:
                position = active['position_mm']
            color = self.colors[kind]
            if kind == 'ir':
                line([position, [*position[:2], -origin]], fill=color, dash=(2, 3))
            elif kind == 'sonar':
                dev = active if selected else self.devices['sonar']['sensors'][int(key[5:])]
                yaw, half = math.radians(dev['yaw_deg']), math.radians(aperture)/2
                # Display only the local part of a long beam, preserving its angle.
                reach = min(distance, span*.72)
                arc = [(position[0]+reach*math.cos(yaw+half*i/5),
                        position[1]+reach*math.sin(yaw+half*i/5), position[2]) for i in range(-5, 6)]
                line([position, *arc, position], fill=color, dash=(3, 3))
                line([position, arc[5]], fill=color, arrow=tk.LAST)
            else:
                dev = active if selected else self.devices['camera']
                yaw, pitch = (math.radians(dev[key]) for key in ('yaw_deg', 'pitch_deg'))
                forward = (math.cos(pitch)*math.cos(yaw), math.cos(pitch)*math.sin(yaw), -math.sin(pitch))
                left = (-math.sin(yaw), math.cos(yaw), 0)
                up = (math.sin(pitch)*math.cos(yaw), math.sin(pitch)*math.sin(yaw), math.cos(pitch))
                reach = span*.65
                horizontal = min(reach*math.tan(math.radians(dev['fov_deg'])/2), span*1.5)
                vertical = min(horizontal*dev['height']/dev['width'], span*1.5)
                center = [p+reach*v for p, v in zip(position, forward)]
                corners = [[p+a*horizontal*l+b*vertical*u for p, l, u in zip(center, left, up)]
                           for a, b in [(-1,-1),(-1,1),(1,1),(1,-1)]]
                line(corners+[corners[0]], fill=color, dash=(3, 3))
                for point in corners:
                    line([position, point], fill=color, dash=(3, 3))
                line([position, center], fill=color, arrow=tk.LAST)
            shapes.append((selected, key, label, color, pt(position)))
        for selected, key, label, color, (x, y) in sorted(shapes, key=lambda item: item[0]):
            radius = 7 if selected else 5
            canvas.create_oval(x-radius, y-radius, x+radius, y+radius, fill=color,
                               outline='white', width=2, tags=(f'sensor:{key}',))
            if selected:
                canvas.create_text(x, y-13, text=label, fill=color, font=('Segoe UI', 10, 'bold'),
                                   tags=(f'sensor:{key}',))
