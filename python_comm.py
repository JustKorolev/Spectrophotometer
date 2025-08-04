import re
import serial
import time
import asyncio
import pandas as pd

HANDSHAKE = 0
VOLTAGE_REQUEST = 1
ON_REQUEST = 2
STREAM = 3
READ_DAQ_DELAY = 4

def find_arduino(port=None):
    """Get the name of the port that is connected to Arduino."""
    if port is None:
        ports = serial.tools.list_ports.comports()
        for p in ports:
            if p.manufacturer is not None and "Arduino" in p.manufacturer:
                port = p.device
    return port


def handshake_arduino(
    arduino, sleep_time=1, print_handshake_message=False, handshake_code=0
):
    """Make sure connection is established by sending
    and receiving bytes."""
    # Close and reopen
    arduino.close()
    arduino.open()

    # Chill out while everything gets set
    time.sleep(sleep_time)

    # Set a long timeout to complete handshake
    timeout = arduino.timeout
    arduino.timeout = 2

    # Read and discard everything that may be in the input buffer
    _ = arduino.read_all()

    # Send request to Arduino
    arduino.write(bytes([handshake_code]))

    # Read in what Arduino sent
    handshake_message = arduino.read_until()

    # Send and receive request again
    arduino.write(bytes([handshake_code]))
    handshake_message = arduino.read_until()

    # Print the handshake message, if desired
    if print_handshake_message:
        print("Handshake message: " + handshake_message.decode())

    # Reset the timeout
    arduino.timeout = timeout


def read_all(ser, read_buffer=b"", **args):
    """Read all available bytes from the serial port
    and append to the read buffer.

    Parameters
    ----------
    ser : serial.Serial() instance
        The device we are reading from.
    read_buffer : bytes, default b''
        Previous read buffer that is appended to.

    Returns
    -------
    output : bytes
        Bytes object that contains read_buffer + read.

    Notes
    -----
    .. `**args` appears, but is never used. This is for
       compatibility with `read_all_newlines()` as a
       drop-in replacement for this function.
    """
    # Set timeout to None to make sure we read all bytes
    previous_timeout = ser.timeout
    ser.timeout = None

    in_waiting = ser.in_waiting
    read = ser.read(size=in_waiting)

    # Reset to previous timeout
    ser.timeout = previous_timeout

    return read_buffer + read


def read_all_newlines(ser, read_buffer=b"", n_reads=4):
    """Read data in until encountering newlines.

    Parameters
    ----------
    ser : serial.Serial() instance
        The device we are reading from.
    n_reads : int
        The number of reads up to newlines
    read_buffer : bytes, default b''
        Previous read buffer that is appended to.

    Returns
    -------
    output : bytes
        Bytes object that contains read_buffer + read.

    Notes
    -----
    .. This is a drop-in replacement for read_all().
    """
    raw = read_buffer
    for _ in range(n_reads):
        raw += ser.read_until()

    return raw


def parse_read(read):
    """Parse a read with time, volage data

    Parameters
    ----------
    read : byte string
        Byte string with comma delimited time/voltage
        measurements.

    Returns
    -------
    time_ms : list of ints
        Time points in milliseconds.
    voltage : list of floats
        Voltages in volts.
    remaining_bytes : byte string
        Remaining, unparsed bytes.
    """
    time_ms = []
    voltage = []

    # Separate independent time/voltage measurements
    pattern = re.compile(b"\d+|,")
    raw_list = [
        b"".join(pattern.findall(raw)).decode()
        for raw in read.split(b"\r\n")
    ]

    for raw in raw_list[:-1]:
        try:
            t, V = raw.split(",")
            time_ms.append(int(t))
            voltage.append(int(V) * 5 / 1023)
        except:
            pass

    if len(raw_list) == 0:
        return time_ms, voltage, b""
    else:
        return time_ms, voltage, raw_list[-1].encode()


async def daq_stream_async(
    arduino,
    data,
    n_data=100,
    delay=20,
    n_trash_reads=5,
    n_reads_per_chunk=4,
    reader=read_all_newlines,
):
    """Obtain `n_data` data points from an Arduino stream
    with a delay of `delay` milliseconds between each."""
    # Specify delay
    arduino.write(bytes([READ_DAQ_DELAY]) + (str(delay) + "x").encode())

    # Turn on the stream
    arduino.write(bytes([STREAM]))

    # Read and throw out first few reads
    i = 0
    while i < n_trash_reads:
        _ = arduino.read_until()
        i += 1

    # Receive data
    read_buffer = [b""]
    while len(data["time_ms"]) < n_data:
        # Read in chunk of data
        raw = reader(arduino, read_buffer=read_buffer[0], n_reads=n_reads_per_chunk)

        # Parse it, passing if it is gibberish
        try:
            t, V, read_buffer[0] = parse_read(raw)

            # Update data dictionary
            data["time_ms"] += t
            data["voltage"] += V
        except:
            pass

        # Sleep 80% of the time before we need to start reading chunks
        await asyncio.sleep(0.8 * n_reads_per_chunk * delay / 1000)

    # Turn off the stream
    arduino.write(bytes([ON_REQUEST]))

    return pd.DataFrame(
        {"time (ms)": data["time_ms"][:n_data], "voltage (V)": data["voltage"][:n_data]}
    )