"""会员硬件导入窗口；所有资料保存在本机。"""
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from hardware_import import ROOT, import_geometry, import_netlist, import_urdf, read_json, write_json
from hardware_import import destination, relative
from gripper_editor import GripperEditor
from pin_editor import PinEditor
from sensor_editor import SensorEditor
import shutil
from pathlib import Path


class HardwareEditor:
    def __init__(self, window):
        self.window = window
        window.title('月光杯 · 导入自己的小车与板卡')
        window.geometry(f'{min(1000, window.winfo_screenwidth()-40)}x{min(820, window.winfo_screenheight()-80)}')
        self.robot = read_json(ROOT/'robot.json')
        self.board = read_json(ROOT/'board.json')
        self.firmware = read_json(ROOT/'firmware.json')
        self.model = self.robot.get('model', {'kind': 'parametric'})
        self.fields = {}
        self.status = tk.StringVar(value='关闭 Webots 后配置；保存后运行 start.bat。')
        ttk.Label(window, text='自己的机械 · 自己的板卡', font=('Microsoft YaHei UI', 18)).pack(anchor='w', padx=20, pady=(16, 4))
        ttk.Label(window, text='在本机导入文件，绑定车轮和引脚，再进入月光杯赛场。').pack(anchor='w', padx=20)
        footer = ttk.Frame(window, padding=(20, 4, 20, 12))
        footer.pack(side='bottom', fill='x')
        ttk.Button(footer, text='保存配置', command=self.save).pack(side='right', padx=(8, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=710).pack(side='left')
        tabs = ttk.Notebook(window)
        tabs.pack(fill='both', expand=True, padx=16, pady=12)
        self.mechanical = ttk.Frame(tabs, padding=12)
        self.electronic = ttk.Frame(tabs, padding=12)
        self.firmware_frame = ttk.Frame(tabs, padding=12)
        self.mechanical_tab()
        self.board_tab()
        self.firmware_tab()
        self.sensors = SensorEditor(tabs, self.robot, self.preview_geometry, self.edit, self.remap_bindings)
        self.gripper = GripperEditor(tabs, self.edit, self.robot)
        self.gripper.load(self.model)
        self.pins = PinEditor(tabs, self.device_roles, self.read_bindings, self.reference,
                              self.pin_nets, self.write_bindings, self.edit)
        tabs.add(self.mechanical, text='机械模型与驱动')
        tabs.add(self.gripper, text='物理夹爪')
        tabs.add(self.sensors, text='传感器摆位')
        tabs.add(self.electronic, text='嘉立创EDA 网表')
        tabs.add(self.pins, text='F103 引脚图')
        tabs.add(self.firmware_frame, text='固件执行 · Renode')
        tabs.bind('<<NotebookTabChanged>>', lambda _: (self.sensors.draw(), self.pins.refresh()))

    def action(self, callback):
        self.status.set('正在处理文件…')
        self.window.update_idletasks()
        self.edit(callback)

    def edit(self, callback):
        try:
            callback()
        except Exception as error:
            self.status.set(str(error))
            messagebox.showerror('硬件配置', str(error), parent=self.window)

    def field(self, frame, row, label, value, key, choices=None):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=4)
        variable = tk.StringVar(value=str(value))
        widget = ttk.Combobox(frame, textvariable=variable, values=choices, state='readonly') if choices is not None else ttk.Entry(frame, textvariable=variable)
        widget.grid(row=row, column=1, sticky='ew', padx=(12, 0), pady=4)
        self.fields[key] = variable
        return widget

    def mechanical_tab(self):
        f = self.mechanical
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        ttk.Button(bar, text='导入 URDF / ZIP 装配包', command=lambda: self.action(self.choose_urdf)).pack(side='left')
        ttk.Button(bar, text='导入 STEP / STL / OBJ 底盘', command=lambda: self.action(self.choose_mesh)).pack(side='left', padx=8)
        ttk.Button(bar, text='使用参数车模', command=self.parametric).pack(side='left')
        self.model_info = tk.StringVar()
        ttk.Label(f, textvariable=self.model_info, wraplength=870).pack(anchor='w', pady=10)
        self.tree = ttk.Treeview(f, columns=('type',), show='tree headings', height=6)
        self.tree.heading('#0', text='装配体关节')
        self.tree.heading('type', text='类型')
        self.tree.pack(fill='x')
        form = ttk.Frame(f)
        form.pack(fill='x', pady=8)
        form.columnconfigure(1, weight=1)
        model, wheels, body = self.model, self.robot['wheels'], self.robot['body']
        self.left = self.field(form, 0, '左轮关节', model.get('left_joint', ''), 'left_joint', [])
        self.right = self.field(form, 1, '右轮关节', model.get('right_joint', ''), 'right_joint', [])
        self.field(form, 2, '左轮 / 右轮正向系数', str(model.get('left_sign', 1))+','+str(model.get('right_sign', 1)), 'signs')
        self.field(form, 3, 'URDF 根坐标原点离地高度（mm）', model.get('origin_height_mm', body['clearance_mm']+body['height_mm']/2), 'origin')
        self.field(form, 4, '轮半径 / 轮距（mm）', f"{wheels['radius_mm']},{wheels['track_mm']}", 'wheels')
        self.field(form, 5, '底盘长 / 宽 / 高（mm，停车轮廓）', ','.join(str(body[k]) for k in ('length_mm', 'width_mm', 'height_mm')), 'body')
        self.field(form, 6, 'STL / OBJ 输入单位（STEP自动使用mm）', 'mm', 'unit', ['mm', 'cm', 'm'])
        self.field(form, 7, '车体与固件反馈周期（ms）', self.robot.get('step_ms', 32), 'step_ms', [4, 8, 16, 32])
        self.field(form, 8, '本机视觉接口端口（0 关闭）', self.robot.get('vision_link_port', 0), 'vision_link_port')
        ttk.Label(f, text='URDF：X 向前、Y 向左、Z 向上，保留关节、质量及碰撞体。\nSTEP/STL/OBJ：导入单体底盘，配合参数车轮和内置物理夹爪。\nSolidWorks 可导出 URDF 装配包，或将单体底盘另存为 STEP。', wraplength=870).pack(anchor='w', pady=6)
        self.refresh_model()

    def preview_geometry(self):
        body = self.robot['body']
        dimensions = [body[key] for key in ('length_mm', 'width_mm', 'height_mm')]
        try:
            values = [SensorEditor.number(v) for v in self.fields['body'].get().split(',')]
            if len(values) == 3 and min(values) > 0:
                dimensions = values
        except ValueError:
            pass
        origin = body['clearance_mm']+dimensions[2]/2
        if self.model['kind'] == 'urdf':
            try:
                origin = SensorEditor.number(self.fields['origin'].get())
            except ValueError:
                origin = self.model.get('origin_height_mm', origin)
        return (*dimensions, origin)

    def reference(self):
        return self.fields['mcu'].get().strip()

    def pin_nets(self, number):
        ref = self.reference().upper()
        return [net for net, nodes in self.net_data['nets'].items()
                if any(node['ref'].upper() == ref and node['pin'] == str(number) for node in nodes)]

    def device_roles(self):
        for side in ('left', 'right'):
            yield from (f'{side}_{role}' for role in ('pwm', 'in1', 'in2'))
            yield from (f'encoder_{side}_{phase}' for phase in ('a', 'b'))
        yield from (f'ir{i}' for i in range(len(self.sensors.devices['ir']['positions_mm'])))
        for sonar in self.sensors.devices['sonar']['sensors']:
            yield from (f"sonar_{sonar['name']}_{role}" for role in ('trig', 'echo'))
        yield 'gripper_close'
        yield 'gripper_lift'

    def read_bindings(self):
        bindings = {}
        for line in self.bindings.get('1.0', 'end').splitlines():
            if line.strip():
                role, pin = line.split('=', 1)
                bindings[role.strip()] = pin.strip()
        return bindings

    def write_bindings(self, bindings):
        self.bindings.delete('1.0', 'end')
        self.bindings.insert('1.0', '\n'.join(f'{role}={pin}' for role, pin in bindings.items()))

    def remap_bindings(self, mapping):
        bindings = self.read_bindings()
        self.write_bindings({mapping.get(role, role): pin for role, pin in bindings.items()
                             if mapping.get(role, role) is not None})

    def refresh_model(self):
        self.tree.delete(*self.tree.get_children())
        for joint in self.model.get('joints', []):
            self.tree.insert('', 'end', text=joint['name'], values=(joint['type'],))
        names = [j['name'] for j in self.model.get('joints', []) if j['type'] in ('continuous', 'revolute')]
        self.left['values'] = names
        self.right['values'] = names
        if hasattr(self, 'gripper'):
            self.gripper.load(self.model)
        self.model_info.set('当前模型：'+self.model['kind']+'  '+self.model.get('source', '参数化差速小车'))

    def choose_urdf(self):
        path = filedialog.askopenfilename(filetypes=[('URDF装配包', '*.urdf *.zip')])
        if not path:
            self.status.set('已取消选择')
            return
        self.model = import_urdf(path)
        self.fields['left_joint'].set('')
        self.fields['right_joint'].set('')
        self.refresh_model()
        self.status.set('装配体已导入。请选择左右轮关节，填写根原点高度和轮径、轮距，再保存。')

    def choose_mesh(self):
        path = filedialog.askopenfilename(filetypes=[('底盘几何', '*.step *.stp *.stl *.obj')])
        if not path:
            self.status.set('已取消选择')
            return
        self.model = import_geometry(path, self.fields['unit'].get())
        self.fields['body'].set(','.join(map(str, self.model['size_mm'])))
        self.refresh_model()
        self.status.set('底盘模型已导入并居中；车轮与传感器尺寸可在 robot.json 中调整。')

    def parametric(self):
        self.model = {'kind': 'parametric'}
        self.refresh_model()
        self.status.set('已选择参数车模，点击保存生效。')

    def board_tab(self):
        f = self.electronic
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        ttk.Button(bar, text='导入 PADS 9.5 网表', command=lambda: self.action(self.choose_board)).pack(side='left')
        ttk.Button(bar, text='导入板卡 STEP / STL', command=lambda: self.action(self.choose_board_mesh)).pack(side='left', padx=8)
        ttk.Button(bar, text='移除板卡模型', command=self.remove_board_model).pack(side='left')
        self.board_info = tk.StringVar()
        ttk.Label(f, textvariable=self.board_info, wraplength=870).pack(anchor='w', pady=8)
        self.nets = ttk.Treeview(f, columns=('nodes',), show='tree headings', height=8)
        self.nets.heading('#0', text='网络')
        self.nets.heading('nodes', text='器件.引脚')
        self.nets.pack(fill='x')
        form = ttk.Frame(f)
        form.pack(fill='x', pady=8)
        form.columnconfigure(1, weight=1)
        self.mcu = self.field(form, 0, 'MCU器件编号（如U1）', self.board.get('mcu_reference', ''), 'mcu', [])
        self.field(form, 1, '板卡中心安装位置 X,Y,Z（mm）', ','.join(map(str, self.board['position_mm'])), 'board_position')
        ttk.Label(form, text='引脚与设备对应（每行一个）').grid(row=2, column=0, sticky='nw', pady=8)
        self.bindings = tk.Text(form, height=10, font=('Consolas', 10))
        self.bindings.grid(row=2, column=1, sticky='ew', padx=(12, 0))
        self.bindings.insert('1.0', '\n'.join(f'{role}={pin}' for role, pin in self.board.get('bindings', {}).items()))
        ttk.Label(f, text='填写格式：设备=器件.引脚（如 left_pwm=U1.10），或直接填 left_pwm=PA0。PWM复用由固件配置。\n设备：left_pwm / left_in1 / left_in2 / right_pwm / right_in1 / right_in2、encoder_left_a/b、encoder_right_a/b、ir0…、sonar_front_trig/echo（front可换成left/right）、gripper_close、gripper_lift。\n固件页选择USART端口。网表提供接线资料；板卡三维模型用于安装外观。', wraplength=870).pack(anchor='w', pady=6)
        self.refresh_board()

    def refresh_board(self):
        self.nets.delete(*self.nets.get_children())
        source = self.board.get('netlist')
        data = read_json(ROOT/source) if source else {'components': {}, 'nets': {}}
        self.net_data = data
        for net, nodes in data['nets'].items():
            self.nets.insert('', 'end', text=net, values=('  '.join(n['ref']+'.'+n['pin'] for n in nodes),))
        self.mcu['values'] = list(data['components'])
        self.board_info.set(f"STM32F103C8T6 · {len(data['components'])} 个器件 · {len(data['nets'])} 个网络 · 板卡模型："+('已导入' if self.board.get('model') else '未导入'))

    def choose_board(self):
        path = filedialog.askopenfilename(filetypes=[('PADS网表', '*.net *.asc *.txt'), ('全部文件', '*.*')])
        if path:
            data = import_netlist(path)
            self.board['netlist'] = data['source']
            self.board['bindings'] = {}
            self.bindings.delete('1.0', 'end')
            self.fields['mcu'].set('')
            self.refresh_board()
            self.status.set('网表已导入。选择MCU编号，填写引脚与设备对应后保存。')

    def choose_board_mesh(self):
        path = filedialog.askopenfilename(filetypes=[('板卡几何', '*.step *.stp *.stl *.obj')])
        if path:
            self.board['model'] = import_geometry(path, self.fields['unit'].get())
            self.refresh_board()
            self.status.set('板卡外形已导入，调整安装位置并保存。')

    def remove_board_model(self):
        self.board['model'] = None
        self.refresh_board()

    def firmware_tab(self):
        f = self.firmware_frame
        bar = ttk.Frame(f)
        bar.pack(fill='x')
        ttk.Button(bar, text='导入 ELF / AXF / HEX / BIN', command=lambda: self.action(self.choose_firmware)).pack(side='left')
        ttk.Button(bar, text='选择 Renode.exe', command=self.choose_renode).pack(side='left', padx=8)
        self.firmware_info = tk.StringVar(value='当前固件：'+str(self.firmware['image'] or '尚未导入'))
        ttk.Label(f, textvariable=self.firmware_info, wraplength=870).pack(anchor='w', pady=14)
        form = ttk.Frame(f)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        self.field(form, 0, 'Renode路径（留空自动查找）', self.firmware['renode'], 'renode')
        self.field(form, 1, 'BIN加载地址', self.firmware['bin_address'], 'bin_address')
        self.field(form, 2, '编码器每轮一圈计数（四倍频后）', self.firmware['encoder_cpr'], 'encoder_cpr')
        self.field(form, 3, '数字红外黑白阈值（0～1）', self.firmware['ir_threshold'], 'ir_threshold')
        self.field(form, 4, '左右电机正向系数', ','.join(map(str,self.firmware['motor_signs'])), 'motor_signs')
        self.field(form, 5, '视觉与调试串口', self.firmware['vision_uart'], 'vision_uart', ['usart1','usart2','usart3'])
        self.field(form, 6, 'CPU虚拟指令率（MIPS，正整数）', int(self.firmware.get('cpu_mips',8)), 'cpu_mips')
        ttk.Label(f, text='TIM1～4 PWM1/PWM2、AB编码器、GPIO红外、HC-SR04 触发/回波。\nADC1：轮询、扫描、EOC 中断和 DMA1 通道 1，每次转换约 100 µs。\nUSART1～3：轮询、中断及收发 DMA。时钟采用固定 72 MHz 配置。\n详细接线、支持范围与固件导入方式见 docs/固件执行.md。', wraplength=870).pack(anchor='w', pady=20)

    def choose_firmware(self):
        path = filedialog.askopenfilename(filetypes=[('已编译STM32固件','*.elf *.axf *.hex *.bin')])
        if path:
            target = destination('firmware')/('member'+Path(path).suffix.lower())
            shutil.copyfile(path,target)
            self.firmware['image'] = relative(target)
            self.firmware_info.set('当前固件：'+self.firmware['image'])
            self.status.set('固件已复制到本机导入目录；请配置引脚并保存。')

    def choose_renode(self):
        path = filedialog.askopenfilename(filetypes=[('Renode后端','renode.exe')])
        if path:
            self.fields['renode'].set(path)

    def save(self):
        self.action(self.save_files)

    def save_files(self):
        model = dict(self.model)
        if model['kind'] == 'urdf':
            left, right = self.fields['left_joint'].get(), self.fields['right_joint'].get()
            names = [j['name'] for j in model['joints'] if j['type'] in ('continuous', 'revolute')]
            if left not in names or right not in names or left == right:
                raise ValueError('请选择两个不同的旋转关节作为左右轮。')
            signs = [int(v) for v in self.fields['signs'].get().split(',')]
            if len(signs) != 2 or any(v not in (-1, 1) for v in signs):
                raise ValueError('左右轮正向系数填写1或-1，例如1,-1。')
            model.update(left_joint=left, right_joint=right, left_sign=signs[0], right_sign=signs[1], origin_height_mm=float(self.fields['origin'].get()))
        self.gripper.save(model)
        dimensions = [float(v) for v in self.fields['body'].get().split(',')]
        wheels = [float(v) for v in self.fields['wheels'].get().split(',')]
        if len(dimensions) != 3 or len(wheels) != 2 or min(dimensions+wheels) <= 0:
            raise ValueError('请填写三个底盘尺寸，以及轮半径、轮距；数值应大于0。')
        body = dict(self.robot['body'], **dict(zip(('length_mm', 'width_mm', 'height_mm'), dimensions)))
        if model['kind'] != 'urdf':
            gripper = self.gripper.parametric_values(dict(self.robot, body=body))
        else:
            gripper = self.robot.get('gripper', {})
        step = int(self.fields['step_ms'].get())
        vision_port = int(self.fields['vision_link_port'].get())
        if not 0 <= vision_port <= 65535:
            raise ValueError('视觉接口端口填写 0～65535 的整数。')
        origin = model['origin_height_mm'] if model['kind'] == 'urdf' else self.robot['body']['clearance_mm']+dimensions[2]/2
        sensors = self.sensors.values(origin)
        bindings = self.read_bindings()
        position = [float(v) for v in self.fields['board_position'].get().split(',')]
        if len(position) != 3:
            raise ValueError('板卡安装位置应为X,Y,Z三个数值。')
        self.robot['model'] = model
        self.robot.update(body=body, gripper=gripper)
        self.robot['wheels'].update(radius_mm=wheels[0], track_mm=wheels[1])
        self.robot.update(sensors, step_ms=step, vision_link_port=vision_port)
        self.board.update(mcu_reference=self.reference(), bindings=bindings, position_mm=position)
        from firmware.pins import resolve_pin
        for pin in bindings.values():
            resolve_pin(pin, self.board['mcu_reference'])
        self.firmware.update(renode=self.fields['renode'].get(), bin_address=self.fields['bin_address'].get(),
            encoder_cpr=int(self.fields['encoder_cpr'].get()), ir_threshold=float(self.fields['ir_threshold'].get()),
            cpu_mips=int(self.fields['cpu_mips'].get()),
            motor_signs=[int(v) for v in self.fields['motor_signs'].get().split(',')], vision_uart=self.fields['vision_uart'].get())
        if self.firmware['cpu_mips'] <= 0 or self.firmware['encoder_cpr'] <= 0 or len(self.firmware['motor_signs']) != 2 or any(v not in (-1,1) for v in self.firmware['motor_signs']) or not 0 <= self.firmware['ir_threshold'] <= 1:
            raise ValueError('编码器计数应大于0，正向系数为1或-1，红外阈值在0～1之间。')
        write_json(ROOT/'robot.json', self.robot)
        write_json(ROOT/'board.json', self.board)
        write_json(ROOT/'firmware.json', self.firmware)
        self.status.set('已保存小车、板卡与固件配置。重新运行 start.bat 生效。')


if __name__ == '__main__':
    app = tk.Tk()
    HardwareEditor(app)
    app.mainloop()
