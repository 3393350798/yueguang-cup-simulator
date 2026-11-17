# coding: utf-8
"""STM32F103C8T6 LQFP48 管脚与默认/重映射定时器通道。"""
PACKAGE = dict(zip(
    [2,3,4,5,6,10,11,12,13,14,15,16,17,18,19,20,21,22,25,26,27,28,29,30,31,32,33,34,37,38,39,40,41,42,43,45,46],
    'PC13 PC14 PC15 PD0 PD1 PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PB0 PB1 PB2 PB10 PB11 PB12 PB13 PB14 PB15 PA8 PA9 PA10 PA11 PA12 PA13 PA14 PA15 PB3 PB4 PB5 PB6 PB7 PB8 PB9'.split()))


def resolve_pin(value, reference=''):
    value = value.upper().strip()
    if '.' in value:
        ref, number = value.split('.')
        if not reference or ref != reference.upper():
            raise ValueError('封装引脚需要与选定的MCU编号一致：'+value)
        value = PACKAGE.get(int(number), '')
    # C8T6 的引出 GPIO；不把电源/复位等脚当作信号。
    if value not in PACKAGE.values():
        raise ValueError('不是STM32F103C8T6的可用GPIO：'+value)
    return value


def timer_channel(pin, mapr):
    tim1 = (mapr >> 6) & 3
    tim2 = (mapr >> 8) & 3
    tim3 = (mapr >> 10) & 3
    groups = {
        1: ['PA8','PA9','PA10','PA11'] if tim1 in (0,1) else [],
        2: [('PA15' if tim2 in (1,3) else 'PA0'), ('PB3' if tim2 in (1,3) else 'PA1'),
            ('PB10' if tim2 in (2,3) else 'PA2'), ('PB11' if tim2 in (2,3) else 'PA3')],
        3: ['PA6','PA7','PB0','PB1'] if tim3 == 0 else ['PB4','PB5','PB0','PB1'] if tim3 == 2 else [],
        4: ['PB6','PB7','PB8','PB9'] if not mapr & (1 << 12) else []
    }
    for timer, pins in groups.items():
        if pin in pins:
            return timer, pins.index(pin)
    raise ValueError('该管脚当前未映射到TIM1～4输出通道：'+pin)
