// Encoder counter wrap extension for Renode 1.17 STM32_Timer.
// Base peripheral: Copyright (c) Antmicro, MIT (see RENODE-LICENSE.txt).
using Antmicro.Renode.Core;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Timers
{
    public class MoonlightSTM32Timer : STM32_Timer, IGPIOReceiver
    {
        public MoonlightSTM32Timer(IMachine machine, ulong frequency, uint initialLimit)
            : base(machine, frequency, initialLimit) { }

        public new void OnGPIO(int number, bool value)
        {
            var smcr = ReadDoubleWord(8);
            var mode = smcr & 7;
            if(number > 1 || mode < 1 || mode > 3)
            {
                base.OnGPIO(number, value);
                return;
            }
            var ccmr = ReadDoubleWord(0x18);
            if(((ccmr >> (number * 8)) & 3) == 0) return;
            var old = Connections[number].IsSet;
            var other = Connections[number ^ 1].IsSet;
            var count = Value;
            var enabled = (ReadDoubleWord(0) & 1) != 0;
            // Let the base model update input-capture state without its unchecked count +/- 1.
            WriteDoubleWord(8, smcr & ~7u);
            base.OnGPIO(number, value);
            WriteDoubleWord(8, smcr);
            if(!enabled || old == value || (mode != 3 && mode != number + 1)) return;
            var up = number == 0 ? value ^ other : !(value ^ other);
            var wrap = up ? count == Limit : count == 0;
            if(wrap) WriteDoubleWord(0x14, 1);
            Value = up ? (count == Limit ? 0 : count + 1) : (count == 0 ? Limit : count - 1);
            Direction = up ? Direction.Ascending : Direction.Descending;
        }
    }
}
