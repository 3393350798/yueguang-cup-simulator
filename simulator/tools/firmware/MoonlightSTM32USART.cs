using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.Bus;

namespace Antmicro.Renode.Peripherals.UART
{
    [AllowedTranslations(AllowedTranslation.WordToDoubleWord | AllowedTranslation.ByteToDoubleWord)]
    public class MoonlightSTM32USART : STM32_UART
    {
        public MoonlightSTM32USART(IMachine machine, uint frequency) : base(machine, frequency)
        {
        }

        public override void Reset()
        {
            transmitDmaEnabled = false;
            generation++;
            base.Reset();
        }

        public override uint ReadDoubleWord(long offset)
        {
            return base.ReadDoubleWord(offset) | (offset == 0x14 && transmitDmaEnabled ? 0x80u : 0);
        }

        public override void WriteDoubleWord(long offset, uint value)
        {
            base.WriteDoubleWord(offset, offset == 0x14 ? value & ~0x80u : value);
            if(offset != 0x14) return;
            var enable = (value & 0x80u) != 0;
            if(enable == transmitDmaEnabled) return;
            transmitDmaEnabled = enable;
            generation++;
            if(enable) ScheduleTransmit(generation);
        }

        private void ScheduleTransmit(int expected)
        {
            var duration = BaudRate > 0 ? this.GetActualTransmissionDuration(CharacterLength)
                : Antmicro.Renode.Time.TimeInterval.FromMilliseconds(1);
            machine.ScheduleAction(duration, _ =>
            {
                if(expected != generation) return;
                if((base.ReadDoubleWord(0x0C) & 0x2008u) == 0x2008u) TransmitDMARequest.Blink();
                ScheduleTransmit(expected);
            }, name: "Moonlight USART DMA transmit");
        }

        public GPIO TransmitDMARequest { get; } = new GPIO();
        private bool transmitDmaEnabled;
        private int generation;
    }
}
