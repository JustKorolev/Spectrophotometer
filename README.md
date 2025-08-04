# DIY Spectrophotometer

This project is a low-cost, multi-wavelength spectrophotometer built using off-the-shelf electronic components and a microcontroller. It measures the absorbance of liquid samples by shining selectable LED wavelengths through a cuvette and capturing the transmitted light with a photodiode.

Over the course of three weeks, the system was fully designed, built, and tested — integrating analog signal conditioning, embedded ADC sampling, and a custom Python interface for real-time data visualization and calibration.

## Features

- Multi-wavelength LED array for spectral flexibility  
- Photodiode-based detection with op-amp amplification  
- Microcontroller (e.g., Arduino or Pi Pico) for ADC and data transmission  
- Python GUI for plotting absorbance and performing calibration  
- Light-isolated chamber to reduce noise and improve consistency  
- Implements Beer-Lambert law for absorbance calculation
