# Simulations: what they show

The panel sends 40 kHz pings from its 64 transducers and listens for the echoes. Each element has its own driver (transmit) and its own amplifier and ADC channel (receive). These simulations follow that path and answer one question each; click a question for the full report with its plot.

The transceiver channel is exported from the KiCad schematic before every run, so the results always match the current design. All of them come from `sim/run.py`.

| Part | Question | Answer | Result |
|---|---|---|---|
| Receiver | [When an echo arrives, how big is the signal that reaches the ADC, and does the ADC's own input filter weaken it?](rx_chain.md) | A 1 m echo (0.12 V at the transducer) reaches the ADC as about **2.7 V peak**: the receiver amplifies ×22 (27.0 dB) at 40 kHz and passes 31–51 kHz. | **PASS** |
| Receiver | [How quiet is the receiver compared with the echoes it has to hear, and compared with the ADC?](rx_noise.md) | In the band that matters (30–50 kHz) the receiver adds **33 µV rms** of noise at the ADC, about the same as the ADC itself (~50 µV). | **PASS** |
| Receiver | [Real resistors and capacitors are never exactly their value. How different will the 64 receive channels be in gain and phase?](rx_mc.md) | With real part tolerances the 64 channels differ by **0.10 dB in gain** and **1.3° in phase** (standard deviation; 6.4° from best to worst of 200). 1.3° is like the echo arriving 0.03 mm early or late, small next to the 8.6 mm wavelength, and fixed for each channel, so a one-time calibration in the FPGA removes it. | **PASS** |
| Receiver | [Does a very strong echo (from a close object) damage or confuse the receiver, and how soon can it hear the next, weak echo?](rx_overload.md) | No. Even an echo 50× stronger than a 1 m echo stays inside the ADC range (**4.2 V** at most: the clamp diodes and the amplifier's ±5 V rails limit it), and the amplifier comes out of saturation at most **0.46 ms** after the echo ends. | **PASS** |
| Transmitter | [When an element sends a ping, is the drive safe for every part, and how soon after can the same element hear an echo?](tx_burst.md) | Yes. The transducer gets **14.2 V** pulses, the driver peaks at 0.29 A (rated 5 A) and the clamp keeps the amplifier input within **+0.63 / -0.45 V**. | **PASS** |
| Transmitter | [In continuous-wave mode (Doppler, split mode) an element transmits all the time. How much current does it draw from the 15 V supply, and does any part get too hot?](tx_cw.md) | Each transmitting element draws **3.7 mA** (56 mW) from the 15 V supply; all 64 together draw **239 mA**, about a quarter of what the supply can give. | **PASS** |
| Microphones | [Do the ferrite bead and capacitors keep digital noise on the 3.3 V rail out of the microphones' supply, without creating a resonance?](mic_supply.md) | Yes. Digital noise at the microphones' clock frequency (3 MHz) is cut by **72 dB** (to 1/4156), and the filter's resonance near 36 kHz peaks by only 1.4 dB, so it does not amplify noise in the ultrasonic band. | **PASS** |
| Clocks | [Does each clock arrive at all four chips clean, with one crossing per edge and no dangerous overshoot?](clock_si.md) | Yes. With the 33 Ω resistor the clock reaches the farthest of the four chips with clean edges (3.31 V / -0.01 V, one crossing per edge). | **PASS** |
| Clocks | [The clocks come from the FPGA over a 15 cm ribbon cable. Which FPGA output strength gives clean edges at our end?](clock_si_drive.md) | DRIVE 4. At that setting the FPGA's output roughly matches the ribbon, so clocks arrive clean (3.32 V / -0.02 V). | **PASS** |
| Clocks | [If the FPGA had to stay at the stronger DRIVE 8, which resistor/capacitor at our end would clean up the edges?](clock_si_in.md) | If the FPGA ever has to stay at DRIVE 8, the unfitted 100 Ω + 47 pF parts on the header sheet clean the clock up (3.28 V / +0.02 V). | **PASS** |

## Run them

```
cd C:\GitHub\ultrasonic-aesa
%LOCALAPPDATA%\Programs\KiCad\10.0\bin\python.exe sim\run.py
```

Add test names (for example `rx_chain tx_burst`) to run only those.

## Look at the waveforms yourself in LTspice

1. Run the tests once (above), so `sim\gen\channel.cir` exists.
2. Open a testbench: `"%LOCALAPPDATA%\Programs\ADI\LTspice\LTspice.exe" -Run sim\tx_burst_q20.cir`.
3. LTspice runs it and opens an empty (black) plot window: it does not choose signals for you.
4. Click in the black window, right-click → **Add Traces**, pick for example V(xd), V(lim), V(rx_out), then OK. Use **Plot Settings → Add Plot Pane** to give each signal its own pane.
