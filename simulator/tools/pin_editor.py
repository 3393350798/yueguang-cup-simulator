"""STM32F103C8T6 LQFP48 wiring diagram for the hardware editor."""
import tkinter as tk
from tkinter import ttk
from firmware.pins import PACKAGE, resolve_pin


class PinEditor(ttk.Frame):
    def __init__(self, parent, roles, bindings, reference, netlist, store, action):
        super().__init__(parent, padding=12)
        self.roles, self.bindings, self.reference = roles, bindings, reference
        self.netlist, self.store = netlist, store
        self.selection = None
        self.role = tk.StringVar(self)
        self.pin_info = tk.StringVar(self, value='未选管脚')
        self.signal_info = tk.StringVar(self)
        self.net_info = tk.StringVar(self)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.diagram = tk.Canvas(self, background='#f4f7f9', width=580, height=520, highlightthickness=0)
        self.diagram.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.diagram.bind('<Configure>', lambda _: self.refresh())
        self.diagram.bind('<Button-1>', self.click)

        form = ttk.Frame(self, padding=8)
        form.grid(row=0, column=1, sticky='ns')
        ttk.Label(form, textvariable=self.pin_info, font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 8))
        ttk.Label(form, textvariable=self.net_info, wraplength=225, foreground='#526977').pack(anchor='w', pady=(0, 8))
        ttk.Label(form, textvariable=self.signal_info, wraplength=225, foreground='#176a61').pack(anchor='w', pady=(0, 16))
        ttk.Label(form, text='对应设备').pack(anchor='w')
        self.devices = ttk.Combobox(form, textvariable=self.role, state='readonly', width=27)
        self.devices.pack(fill='x', pady=(4, 8))
        self.devices.configure(postcommand=self.update_roles)
        self.bind_button = ttk.Button(form, text='绑定', command=lambda: action(self.assign))
        self.bind_button.pack(fill='x', pady=(0, 5))
        self.clear_button = ttk.Button(form, text='清除管脚绑定', command=lambda: action(self.clear))
        self.clear_button.pack(fill='x')
        self.refresh()

    def update_roles(self):
        try:
            bindings = self.bindings()
        except ValueError:
            bindings = {}
        self.devices['values'] = tuple(dict.fromkeys([*self.roles(), *bindings]))

    def mapped(self, bindings):
        by_pin = {}
        for role, pin in bindings.items():
            try:
                name = resolve_pin(pin, self.reference())
            except ValueError:
                continue
            by_pin.setdefault(name, []).append(role)
        return by_pin

    def refresh(self):
        try:
            bindings = self.bindings()
        except ValueError:
            bindings = {}
        mapped = self.mapped(bindings)
        selected = self.selection
        gpio = PACKAGE.get(selected)
        self.bind_button.configure(state='normal' if gpio else 'disabled')
        self.clear_button.configure(state='normal' if gpio and mapped.get(gpio) else 'disabled')
        if selected is None:
            self.pin_info.set('未选管脚')
            self.net_info.set('')
            self.signal_info.set('')
        else:
            self.pin_info.set(f"第 {selected} 脚  ·  {gpio or '非 GPIO'}")
            net = self.netlist(selected)
            self.net_info.set('网表：'+('、'.join(net) if net else '无网络对应'))
            current = mapped.get(gpio, [])
            self.signal_info.set('已绑定：'+('、'.join(current) if current else '无'))
        self.draw(mapped)

    def click(self, _):
        canvas = self.diagram
        items = canvas.find_withtag('current')
        if not items:
            return
        for tag in canvas.gettags(items[0]):
            if tag.startswith('pin:'):
                self.selection = int(tag[4:])
                self.refresh()
                break

    def assign(self):
        gpio, role = PACKAGE.get(self.selection), self.role.get()
        if not gpio or not role:
            raise ValueError('请选择 GPIO 和对应设备。')
        bindings = self.bindings()
        for other in self.mapped(bindings).get(gpio, []):
            bindings.pop(other)
        ref = self.reference()
        bindings[role] = f'{ref}.{self.selection}' if ref else gpio
        self.store(bindings)
        self.refresh()

    def clear(self):
        bindings = self.bindings()
        for role in self.mapped(bindings).get(PACKAGE.get(self.selection), []):
            bindings.pop(role)
        self.store(bindings)
        self.refresh()

    def draw(self, mapped):
        canvas = self.diagram
        canvas.delete('all')
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if min(width, height) < 120:
            return
        cx, cy = width/2, height/2
        side = max(134, min(width-138, height-150, 340))
        left, right, top, bottom = cx-side/2, cx+side/2, cy-side/2, cy+side/2
        canvas.create_rectangle(left, top, right, bottom, fill='#28333f', outline='')
        canvas.create_oval(left+18, top+18, left+27, top+27, fill='#a2b1be', outline='')
        canvas.create_text(cx, cy-10, fill='white', text='STM32F103C8T6', font=('Segoe UI', 12, 'bold'))
        canvas.create_text(cx, cy+14, fill='#b5c3ce', text='LQFP48 · 顶视图', font=('Segoe UI', 10))
        gap = side/13
        for n in range(1, 49):
            pin = PACKAGE.get(n)
            tag = (f'pin:{n}',)
            color = '#08786c' if pin in mapped else '#a8b7c2' if pin else '#d5dde3'
            outline = '#ff9e27' if n == self.selection else color
            label = f'{n}  {pin}' if pin else str(n)
            font = ('Consolas', 9, 'bold' if pin in mapped else 'normal')
            if n <= 12 or 25 <= n <= 36:
                is_left = n <= 12
                x = left if is_left else right
                end = x+(-21 if is_left else 21)
                y = top+(n if is_left else 37-n)*gap
                canvas.create_rectangle(min(x, end), y-4, max(x, end), y+4, fill=color, outline=outline, width=2, tags=tag)
                canvas.create_text(end+(-4 if is_left else 4), y, anchor='e' if is_left else 'w',
                                   text=label, fill='#26394a', font=font, tags=tag)
            else:
                is_top = n >= 37
                y = top if is_top else bottom
                end = y+(-21 if is_top else 21)
                x = left+(49-n if is_top else n-12)*gap
                canvas.create_rectangle(x-4, min(y, end), x+4, max(y, end), fill=color, outline=outline, width=2, tags=tag)
                canvas.create_text(x, end+(-4 if is_top else 4), anchor='w' if is_top else 'e', angle=90,
                                   text=label, fill='#26394a', font=font, tags=tag)
