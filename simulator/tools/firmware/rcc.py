# Renode IronPython peripheral. Fixed 72MHz co-simulation clock profile.
if request.IsInit:
    registers = {0: 0x83, 4: 0, 0x24: 0x0C000000}
elif request.IsWrite:
    value = int(request.Value)
    if request.Offset == 0:
        value = (value & ~0x02020002) | ((value & 1) << 1) | ((value & 0x10000) << 1) | ((value & 0x1000000) << 1)
    elif request.Offset == 4:
        value = (value & ~12) | ((value & 3) << 2)
    registers[request.Offset] = value
elif request.IsRead:
    request.Value = registers.get(request.Offset, 0)
