// STM32F1 regular ADC conversions for the Moonlight virtual car.
using System;
using Antmicro.Renode.Core;
using Antmicro.Renode.Peripherals.Bus;
using Antmicro.Renode.Peripherals.Timers;
using Antmicro.Renode.Time;

namespace Antmicro.Renode.Peripherals.Analog
{
    [AllowedTranslations(AllowedTranslation.WordToDoubleWord | AllowedTranslation.ByteToDoubleWord)]
    public class MoonlightSTM32ADC : IDoubleWordPeripheral, IKnownSize
    {
        public MoonlightSTM32ADC(IMachine machine)
        {
            timer = new LimitTimer(machine.ClockSource, 1000000, this, "conversion",
                limit: 100, eventEnabled: true, workMode: WorkMode.OneShot);
            timer.LimitReached += Convert;
            Reset();
        }

        public void Reset()
        {
            Array.Clear(registers, 0, registers.Length);
            Array.Clear(samples, 0, samples.Length);
            registers[0x24 / 4] = 4095;
            rank = 0;
            timer.Reset();
            IRQ.Unset();
            DMARequest.Unset();
        }

        public uint ReadDoubleWord(long offset)
        {
            if(offset >= 0x80 && offset < 0xC0) return samples[(offset - 0x80) / 4];
            if(offset == 0x4C)
            {
                registers[0] &= ~2u;
                IRQ.Unset();
            }
            return offset < 0x50 ? registers[offset / 4] : 0;
        }

        public void WriteDoubleWord(long offset, uint value)
        {
            if(offset >= 0x80 && offset < 0xC0)
            {
                samples[(offset - 0x80) / 4] = value & 4095;
                return;
            }
            if(offset >= 0x50) return;
            if(offset == 0) registers[0] &= value;
            else if(offset == 8)
            {
                var previous = registers[2];
                // Calibration finishes immediately; regular SWSTART is F1 bit 22.
                registers[2] = value & ~((1u << 2) | (1u << 3) | (1u << 22));
                if((value & 1) == 0) timer.Enabled = false;
                else if((value & (1u << 22)) != 0 || ((previous & 1) != 0 && (value ^ previous) == 0))
                {
                    rank = 0;
                    registers[0] |= 16;
                    timer.Enabled = true;
                }
            }
            else registers[offset / 4] = value;
            IRQ.Set((registers[0] & 2) != 0 && (registers[1] & 32) != 0);
        }

        private void Convert()
        {
            if((registers[2] & 1) == 0) return;
            var length = (registers[1] & 256) != 0 ? (int)((registers[11] >> 20) & 15) + 1 : 1;
            var sequence = registers[rank < 6 ? 13 : rank < 12 ? 12 : 11];
            var channel = (int)((sequence >> ((rank % 6) * 5)) & 31);
            var sample = channel < samples.Length ? samples[channel] : 0;
            registers[19] = (registers[2] & 2048) != 0 ? sample << 4 : sample;
            registers[0] |= 2;
            // The DMA read clears EOC before the interrupt state is evaluated.
            if((registers[2] & 256) != 0) DMARequest.Blink();
            rank++;
            if(rank == length)
            {
                rank = 0;
                timer.Enabled = (registers[2] & 2) != 0;
            }
            else timer.Enabled = true;
            IRQ.Set((registers[0] & 2) != 0 && (registers[1] & 32) != 0);
        }

        public long Size => 0x100;
        public GPIO IRQ { get; } = new GPIO();
        public GPIO DMARequest { get; } = new GPIO();
        private readonly uint[] registers = new uint[20];
        private readonly uint[] samples = new uint[16];
        private readonly LimitTimer timer;
        private int rank;
    }
}
