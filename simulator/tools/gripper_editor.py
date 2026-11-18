"""Configure builtin or imported physical grippers."""
import tkinter as tk
from tkinter import ttk

from gripper_model import GRIPPER_DEFAULTS, resolve_parametric_gripper
from hardware_import import gripper_joint_limits, urdf_gripper_elements, validated_urdf_gripper


PARAMETRIC_FIELDS = {
    'reach_mm': '指面中心到车体原点（mm）',
    'opening_mm': '张开时内侧间距（mm）',
    'closed_gap_mm': '闭合时内侧间距（mm）',
    'finger_length_mm': '爪指长度（mm）',
    'finger_thickness_mm': '爪指厚度（mm）',
    'finger_height_mm': '爪指高度（mm）',
    'jaw_center_height_mm': '降低时指面中心离地（mm）',
    'lift_travel_mm': '抬升行程（mm）',
    'jaw_speed_mm_s': '开合速度（mm/s）',
    'lift_speed_mm_s': '升降速度（mm/s）',
    'jaw_force_n': '单指最大推力（N）',
    'lift_force_n': '抬升最大推力（N）',
    'finger_mass_kg': '单指质量（kg）',
    'carriage_mass_kg': '升降架质量（kg）',
    'friction': '夹持面和物块摩擦系数',
}


class GripperEditor(ttk.Frame):
    def __init__(self, parent, edit, robot):
        super().__init__(parent, padding=12)
        self.edit = edit
        self.note = tk.StringVar()
        ttk.Label(self, textvariable=self.note, wraplength=840).pack(anchor='w')
        self.builtin = ttk.Frame(self)
        self.has_builtin = tk.BooleanVar(value=robot.get('gripper', {}).get('kind') != 'none')
        ttk.Checkbutton(self.builtin, text='装配内置双指夹爪', variable=self.has_builtin).grid(
            row=0, column=0, columnspan=4, sticky='w', pady=(0, 8))
        self.builtin_values = {}
        for index, (key, label) in enumerate(PARAMETRIC_FIELDS.items()):
            row, col = 1+index//2, 2*(index%2)
            value = robot.get('gripper', {}).get(key, GRIPPER_DEFAULTS[key])
            self.builtin_values[key] = tk.StringVar(value=str(value))
            ttk.Label(self.builtin, text=label).grid(row=row, column=col, sticky='w', padx=(0, 6), pady=4)
            ttk.Entry(self.builtin, textvariable=self.builtin_values[key], width=9).grid(
                row=row, column=col+1, sticky='w', padx=(0, 12))
        self.form = ttk.Frame(self)
        self.enabled = tk.BooleanVar()
        ttk.Checkbutton(self.form, text='配置导入装配的夹取与释放动作', variable=self.enabled,
                        command=self.toggle).pack(anchor='w', pady=(0, 8))
        self.controls = ttk.Frame(self.form)
        self.controls.columnconfigure(0, weight=1)
        self.controls.columnconfigure(1, weight=1)
        jaws = ttk.LabelFrame(self.controls, text='开合关节', padding=8)
        jaws.grid(row=0, column=0, sticky='nsew', padx=(0, 6))
        jaws.columnconfigure(1, weight=1)
        self.joint = tk.StringVar()
        self.open = tk.StringVar()
        self.closed = tk.StringVar()
        ttk.Label(jaws, text='关节').grid(row=0, column=0, sticky='w')
        self.jaw_joint = ttk.Combobox(jaws, textvariable=self.joint, state='readonly', width=20)
        self.jaw_joint.grid(row=0, column=1, sticky='ew')
        self.jaw_joint.bind('<<ComboboxSelected>>', self.jaw_selected)
        ttk.Label(jaws, text='张开 / 闭合').grid(row=1, column=0, sticky='w')
        values = ttk.Frame(jaws)
        values.grid(row=1, column=1, sticky='ew', pady=4)
        ttk.Entry(values, textvariable=self.open, width=8).pack(side='left')
        ttk.Entry(values, textvariable=self.closed, width=8).pack(side='left', padx=4)
        ttk.Button(values, text='添加或更新', command=lambda: self.edit(self.set_jaw)).pack(side='left')
        self.jaw_info = tk.StringVar()
        ttk.Label(jaws, textvariable=self.jaw_info, wraplength=340).grid(row=2, column=0, columnspan=2, sticky='w')
        self.jaw_list = ttk.Treeview(jaws, columns=('open', 'closed'), show='tree headings', height=4)
        for key, label in (('#0', '关节'), ('open', '张开'), ('closed', '闭合')):
            self.jaw_list.heading(key, text=label)
            self.jaw_list.column(key, width=110 if key == '#0' else 60)
        self.jaw_list.grid(row=3, column=0, columnspan=2, sticky='ew', pady=4)
        self.jaw_list.bind('<<TreeviewSelect>>', self.edit_jaw)
        ttk.Button(jaws, text='移除所选开合关节', command=self.delete_jaw).grid(row=4, column=0, columnspan=2, sticky='w')

        pads = ttk.LabelFrame(self.controls, text='夹持面', padding=8)
        pads.grid(row=0, column=1, sticky='nsew', padx=(6, 0))
        ttk.Label(pads, text='点击选择接触物块的连杆，至少选两个。').pack(anchor='w')
        pad_list = ttk.Frame(pads)
        pad_list.pack(fill='both', expand=True, pady=4)
        self.contacts = tk.Listbox(pad_list, height=6, selectmode='multiple', exportselection=False)
        scrollbar = ttk.Scrollbar(pad_list, command=self.contacts.yview)
        self.contacts.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.contacts.pack(side='left', fill='both', expand=True)
        friction = ttk.Frame(pads)
        friction.pack(anchor='w')
        self.friction = tk.StringVar(value='0.9')
        ttk.Label(friction, text='夹持面和物块摩擦系数').pack(side='left')
        ttk.Entry(friction, textvariable=self.friction, width=7).pack(side='left', padx=4)

        lift = ttk.LabelFrame(self.controls, text='升降关节（可选）', padding=8)
        lift.grid(row=1, column=0, columnspan=2, sticky='ew', pady=8)
        self.lift = tk.StringVar()
        self.lower = tk.StringVar()
        self.raise_ = tk.StringVar()
        self.lift_joint = ttk.Combobox(lift, textvariable=self.lift, state='readonly', width=18)
        self.lift_joint.pack(side='left')
        self.lift_joint.bind('<<ComboboxSelected>>', self.lift_selected)
        ttk.Label(lift, text='取放位置').pack(side='left', padx=(8, 4))
        ttk.Entry(lift, textvariable=self.lower, width=8).pack(side='left')
        ttk.Label(lift, text='抬起位置').pack(side='left', padx=(8, 4))
        ttk.Entry(lift, textvariable=self.raise_, width=8).pack(side='left')
        ttk.Label(self.controls, wraplength=840,
                  text='旋转关节用 rad，滑动关节用 m。关节速度、限力、质量、惯量和碰撞形状取自 URDF。'
                       '在练习面板先闭合夹爪，再单独操作升降；放下后张开释放。').grid(
                           row=2, column=0, columnspan=2, sticky='w')

    def load(self, model):
        if model['kind'] != 'urdf':
            self.note.set('参数车模和单体底盘可用内置双指夹爪练习；导入含夹爪关节与碰撞几何的 URDF 后，可绑定自己的机械设计。')
            self.form.pack_forget()
            self.builtin.pack(anchor='w', pady=10)
            return
        self.builtin.pack_forget()
        self.note.set('选择装配体中实际带动夹持面的关节。夹爪与物块通过接触和摩擦产生作用。')
        self.form.pack(fill='x', pady=10)
        self.joints, self.links, contacts = urdf_gripper_elements(model)
        names = [name for name, joint in self.joints.items()
                 if joint.get('type') in ('revolute', 'prismatic') and joint.find('mimic') is None]
        self.jaw_joint['values'] = names
        self.lift_joint['values'] = ['', *names]
        settings = model.get('gripper') or {}
        self.enabled.set(bool(settings))
        for row in self.jaw_list.get_children():
            self.jaw_list.delete(row)
        for jaw in settings.get('jaws', []):
            self.jaw_list.insert('', 'end', iid=jaw['joint'], text=jaw['joint'], values=(jaw['open'], jaw['closed']))
        self.joint.set('')
        self.open.set('')
        self.closed.set('')
        self.jaw_info.set('')
        self.contacts.delete(0, 'end')
        for index, name in enumerate(contacts):
            self.contacts.insert('end', name)
            if name in settings.get('contact_links', []):
                self.contacts.selection_set(index)
        self.friction.set(str(settings.get('friction', 0.9)))
        lift = settings.get('lift') or {}
        self.lift.set(lift.get('joint', ''))
        self.lower.set(str(lift.get('lower', '')))
        self.raise_.set(str(lift.get('raise', '')))
        self.toggle()

    def toggle(self):
        if self.enabled.get():
            self.controls.pack(fill='x')
        else:
            self.controls.pack_forget()

    def jaw_selected(self, _event=None):
        name = self.joint.get()
        try:
            lower, upper, effort, speed = gripper_joint_limits(self.joints[name], self.links)
        except (KeyError, ValueError) as error:
            self.jaw_info.set(str(error))
            return
        unit, force = ('m', 'N') if self.joints[name].get('type') == 'prismatic' else ('rad', 'N·m')
        self.jaw_info.set(f'行程 {lower:g} ～ {upper:g} {unit}，限力 {effort:g} {force}，速度 {speed:g} {unit}/s')
        if self.jaw_list.exists(name):
            lower, upper = self.jaw_list.item(name, 'values')
        self.open.set(lower)
        self.closed.set(upper)

    def lift_selected(self, _event=None):
        if self.lift.get():
            def setup():
                lower, upper, _, _ = gripper_joint_limits(self.joints[self.lift.get()], self.links)
                self.lower.set(lower)
                self.raise_.set(upper)
            self.edit(setup)

    def edit_jaw(self, _event=None):
        selection = self.jaw_list.selection()
        if selection:
            self.joint.set(selection[0])
            self.jaw_selected()

    def set_jaw(self):
        name = self.joint.get()
        if not name:
            raise ValueError('请先选择一个开合关节。')
        limits = gripper_joint_limits(self.joints[name], self.links)
        targets = float(self.open.get()), float(self.closed.get())
        if targets[0] == targets[1] or not all(limits[0] <= target <= limits[1] for target in targets):
            raise ValueError('两个目标须不同且位于 URDF 行程内。')
        if self.jaw_list.exists(name):
            self.jaw_list.item(name, values=targets)
        else:
            self.jaw_list.insert('', 'end', iid=name, text=name, values=targets)

    def delete_jaw(self):
        selection = self.jaw_list.selection()
        if selection:
            self.jaw_list.delete(*selection)

    def save(self, model):
        if model['kind'] != 'urdf' or not self.enabled.get():
            model.pop('gripper', None)
            return
        lift_name = self.lift.get()
        lift = {'joint': lift_name, 'lower': float(self.lower.get()),
                'raise': float(self.raise_.get())} if lift_name else None
        model['gripper'] = dict(
            jaws=[dict(joint=name, **dict(zip(('open', 'closed'), map(float, self.jaw_list.item(name, 'values')))))
                  for name in self.jaw_list.get_children()],
            contact_links=[self.contacts.get(index) for index in self.contacts.curselection()],
            lift=lift,
            friction=float(self.friction.get()),
        )
        validated_urdf_gripper(model)

    def parametric_values(self, robot):
        values = dict(kind='none')
        if self.has_builtin.get():
            values = dict(kind='parametric', **{key:float(value.get()) for key,value in self.builtin_values.items()})
        resolve_parametric_gripper(dict(robot, gripper=values))
        return values
