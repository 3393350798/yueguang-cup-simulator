# ADC1 polling model: instantaneous regular conversions, 12-bit channels.
# 0x80..0xBC are bridge-only input registers, outside the STM32 ADC register set.
if request.IsInit:
    registers = {}
    samples = [0] * 16
    index = 0
elif request.IsWrite:
    offset, value = int(request.Offset), int(request.Value)
    if 0x80 <= offset < 0xC0:
        samples[(offset - 0x80) // 4] = value & 4095
    else:
        registers[offset] = value
        if offset == 8:
            registers[8] = value & ~((1 << 2) | (1 << 3) | (1 << 22))
            if value & 1:
                registers[0] = 2
                index = 0
elif request.IsRead:
    if request.Offset == 0x4C:
        length = ((registers.get(0x2C, 0) >> 20) & 15) + 1
        sqr = 0x34 if index < 6 else 0x30 if index < 12 else 0x2C
        channel = (registers.get(sqr, 0) >> ((index % 6) * 5)) & 31
        request.Value = samples[channel] if channel < 16 else 0
        index = (index + 1) % length
        registers[0] = 2 if registers.get(8, 0) & 2 else 0
    else:
        request.Value = registers.get(request.Offset, 0)
