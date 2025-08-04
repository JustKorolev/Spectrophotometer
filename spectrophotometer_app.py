"""
App to interface with the spectrophotometer.

To serve the app, run

    bokeh serve --show spectrophotometer_app.py

on the command line.
"""

import asyncio
import re
import sys
import time
import os

import numpy as np
import pandas as pd

import serial
import serial.tools.list_ports

import bokeh.plotting
import bokeh.io
import bokeh.layouts
import bokeh.driving


# Set up data dictionaries
stream_data = dict(prev_array_length=0, t=[], A=[], mode="on demand")
on_demand_data = dict(t=[], A=[])

current_dir = os.getcwd()
data_path = os.path.join(current_dir, "Data")


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

# Set up connection
HANDSHAKE = 0
ABSORBANCE_REQUEST = 1
ON_REQUEST = 2
STREAM = 3
READ_DAQ_DELAY = 4

port = find_arduino()
arduino = serial.Serial(port, baudrate=115200)
handshake_arduino(arduino)

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
    """Parse a read with time, absorbance data

    Parameters
    ----------
    read : byte string
        Byte string with comma delimited time/absorbance
        measurements.

    Returns
    -------
    time_ms : list of ints
        Time points in milliseconds.
    absorbance : list of floats
        Voltages in volts.
    remaining_bytes : byte string
        Remaining, unparsed bytes.
    """
    time_ms = []
    absorbance = []

    # Separate independent time/absorbance measurements
    pattern = re.compile(b"\d+|,")
    raw_list = [b"".join(pattern.findall(raw)).decode() for raw in read.split(b"\r\n")]

    for raw in raw_list[:-1]:
        try:
            t, A = raw.split(",")
            time_ms.append(int(t))
            absorbance.append(float(A) / 1000)
        except:
            pass

    if len(raw_list) == 0:
        return time_ms, absorbance, b""
    else:
        return time_ms, absorbance, raw_list[-1].encode()


def parse_raw(raw):
    """Parse bytes output from Arduino."""
    raw = raw.decode()
    if raw[-1] != "\n":
        raise ValueError(
            "Input must end with newline, otherwise message is incomplete."
        )

    t, A = raw.rstrip().split(",")

    return int(t), float(A)


def request_single_absorbance(arduino):
    """Ask Arduino for a single data point"""
    # Ask Arduino for data
    arduino.write(bytes([ABSORBANCE_REQUEST]))

    # Read in the data
    raw = arduino.read_until()

    # Parse and return
    return parse_raw(raw)


def plot(mode):
    """Build a plot of absorbance vs time data"""
    # Set up plot area
    p = bokeh.plotting.figure(
        frame_width=500,
        frame_height=175,
        x_axis_label="time (s)",
        y_axis_label="absorbance",
        title="streaming data" if mode == "stream" else "on-demand data",
        y_range=[-0.2, 1.8],
        toolbar_location="above",
    )

    # No range padding on x: signal spans whole plot
    p.x_range.range_padding = 0

    # We'll sue whitesmoke backgrounds
    p.border_fill_color = "whitesmoke"

    # Defined the data source
    source = bokeh.models.ColumnDataSource(data=dict(t=[], A=[]))

    # If we are in streaming mode, use a line, dots for on-demand
    if mode == "stream":
        p.line(source=source, x="t", y="A")
    else:
        p.scatter(source=source, x="t", y="A")

    # Put a phantom circle so axis labels show before data arrive
    phantom_source = bokeh.models.ColumnDataSource(data=dict(t=[0], A=[0]))
    p.scatter(source=phantom_source, x="t", y="A", visible=False)

    return p, source, phantom_source


def controls(mode):
    if mode == "stream":
        acquire = bokeh.models.Toggle(label="stream", button_type="success", width=100)
        save_notice = bokeh.models.Div(
            text="<p>No streaming data saved.</p>", width=165
        )
    else:
        acquire = bokeh.models.Button(label="acquire", button_type="success", width=100)
        save_notice = bokeh.models.Div(
            text="<p>No on-demand data saved.</p>", width=165
        )

    save = bokeh.models.Button(label="save", button_type="primary", width=100)
    reset = bokeh.models.Button(label="reset", button_type="warning", width=100)
    file_input = bokeh.models.TextInput(
        title="file name", value=f"{mode}.csv", width=165
    )

    return dict(
        acquire=acquire,
        reset=reset,
        save=save,
        file_input=file_input,
        save_notice=save_notice,
    )


def layout(p, ctrls):
    buttons = bokeh.layouts.row(
        bokeh.models.Spacer(width=30),
        ctrls["acquire"],
        bokeh.models.Spacer(width=295),
        ctrls["reset"],
    )
    left = bokeh.layouts.column(p, buttons, spacing=15)
    right = bokeh.layouts.column(
        bokeh.models.Spacer(height=50),
        ctrls["file_input"],
        ctrls["save"],
        ctrls["save_notice"],
    )
    return bokeh.layouts.row(
        left, right, spacing=30, margin=(30, 30, 30, 30), background="whitesmoke",
    )


def acquire_callback(arduino, stream_data, source, phantom_source, rollover):
    # Pull t and A values from stream or request from Arduino
    if stream_data["mode"] == "stream":
        t = stream_data["t"][-1]
        A = stream_data["A"][-1]
    else:
        t, A = request_single_absorbance(arduino)

    # Add to on-demand data dictionary
    on_demand_data["t"].append(t)
    on_demand_data["A"].append(A)

    # Send new data to plot
    new_data = dict(t=[t / 1000], A=[A])
    source.stream(new_data, rollover=rollover)

    # Update the phantom source to keep the x_range on plot ok
    phantom_source.data = new_data


def stream_callback(arduino, stream_data, new):
    if new:
        stream_data["mode"] = "stream"
    else:
        stream_data["mode"] = "on-demand"
        arduino.write(bytes([ON_REQUEST]))

    arduino.reset_input_buffer()


def reset_callback(mode, data, source, phantom_source, controls):
    # Turn off the stream
    if mode == "stream":
        controls["acquire"].active = False

    # Black out the data dictionaries
    data["t"] = []
    data["A"] = []

    # Reset the sources
    source.data = dict(t=[], A=[])
    phantom_source.data = dict(t=[0], A=[0])


def save_callback(mode, data, controls):
    # Get the file name from the UI and append it to the data directory
    file_name = controls["file_input"].value  # e.g., "data.csv"
    destination = os.path.join(data_path, file_name)

    # Convert data to a DataFrame and save
    df = pd.DataFrame(data={"time (ms)": data["t"], "absorbance": data["A"]})
    df.to_csv(destination, index=False)

    # Update notice text
    notice_text = "<p>" + ("Streaming" if mode == "stream" else "On-demand")
    notice_text += f" data was last saved to {destination.removeprefix(current_dir)}.</p>"
    controls["save_notice"].text = notice_text


def disable_controls(controls):
    """Disable all controls."""
    for key in controls:
        controls[key].disabled = True


def shutdown_callback(
    arduino, daq_task, stream_data, stream_controls, on_demand_controls
):
    # Disable controls
    disable_controls(stream_controls)
    disable_controls(on_demand_controls)

    # Strop streaming
    stream_data["mode"] = "on-demand"
    arduino.write(bytes([ON_REQUEST]))

    # Stop DAQ async task
    daq_task.cancel()

    # Disconnect from Arduino
    arduino.close()


def stream_update(data, source, phantom_source, rollover):
    # Update plot by streaming in data
    new_data = {
        "t": list(np.array(data["t"][data["prev_array_length"] :]) / 1000),
        "A": data["A"][data["prev_array_length"] :],
    }
    source.stream(new_data, rollover)

    # Adjust new phantom data point if new data arrived
    if len(new_data["t"]) > 0:
        phantom_source.data = dict(t=[new_data["t"][-1]], A=[new_data["A"][-1]])
    data["prev_array_length"] = len(data["t"])


def potentiometer_app(
    arduino, stream_data, on_demand_data, daq_task, rollover=400, stream_plot_delay=90,
):
    def _app(doc):
        # Plots
        p_stream, stream_source, stream_phantom_source = plot("stream")
        p_on_demand, on_demand_source, on_demand_phantom_source = plot("on demand")

        # Controls
        stream_controls = controls("stream")
        on_demand_controls = controls("on_demand")

        # Shut down
        shutdown_button = bokeh.models.Button(
            label="shut down", button_type="danger", width=100
        )

        # Layouts
        stream_layout = layout(p_stream, stream_controls)
        on_demand_layout = layout(p_on_demand, on_demand_controls)

        # Shut down layout
        shutdown_layout = bokeh.layouts.row(
            bokeh.models.Spacer(width=675), shutdown_button
        )

        app_layout = bokeh.layouts.column(
            stream_layout, on_demand_layout, shutdown_layout
        )

        def _acquire_callback(event=None):
            acquire_callback(
                arduino,
                stream_data,
                on_demand_source,
                on_demand_phantom_source,
                rollover,
            )

        def _stream_callback(attr, old, new):
            stream_callback(arduino, stream_data, new)

        def _stream_reset_callback(event=None):
            reset_callback(
                "stream",
                stream_data,
                stream_source,
                stream_phantom_source,
                stream_controls,
            )

        def _on_demand_reset_callback(event=None):
            reset_callback(
                "on demand",
                on_demand_data,
                on_demand_source,
                on_demand_phantom_source,
                on_demand_controls,
            )

        def _stream_save_callback(event=None):
            save_callback("stream", stream_data, stream_controls)

        def _on_demand_save_callback(event=None):
            save_callback("on demand", on_demand_data, on_demand_controls)

        def _shutdown_callback(event=None):
            shutdown_callback(
                arduino, daq_task, stream_data, stream_controls, on_demand_controls
            )

        @bokeh.driving.linear()
        def _stream_update(step):
            stream_update(stream_data, stream_source, stream_phantom_source, rollover)

            # Shut down server if Arduino disconnects (commented out in Jupyter notebook)
            if not arduino.is_open:
                sys.exit()

        # Link callbacks
        stream_controls["acquire"].on_change("active", _stream_callback)
        stream_controls["reset"].on_click(_stream_reset_callback)
        stream_controls["save"].on_click(_stream_save_callback)
        on_demand_controls["acquire"].on_click(_acquire_callback)
        on_demand_controls["reset"].on_click(_on_demand_reset_callback)
        on_demand_controls["save"].on_click(_on_demand_save_callback)
        shutdown_button.on_click(_shutdown_callback)

        # Add the layout to the app
        doc.add_root(app_layout)

        # Add a periodic callback, monitor changes in stream data
        pc = doc.add_periodic_callback(_stream_update, stream_plot_delay)

    return _app


async def daq_stream_async(
    arduino,
    data,
    delay=20,
    n_trash_reads=5,
    n_reads_per_chunk=4,
    reader=read_all_newlines,
):
    """Obtain streaming data"""
    # Specify delay
    arduino.write(bytes([READ_DAQ_DELAY]) + (str(delay) + "x").encode())

    # Current streaming state
    stream_on = False

    # Receive data
    read_buffer = [b""]
    while True:
        if data["mode"] == "stream":
            # Turn on the stream if need be
            if not stream_on:
                arduino.write(bytes([STREAM]))

                # Read and throw out first few reads
                i = 0
                while i < n_trash_reads:
                    _ = arduino.read_until()
                    i += 1

                stream_on = True

            # Read in chunk of data
            raw = reader(arduino, read_buffer=read_buffer[0], n_reads=n_reads_per_chunk)

            # Parse it, passing if it is gibberish
            try:
                t, A, read_buffer[0] = parse_read(raw)

                # Update data dictionary
                data["t"] += t
                data["A"] += A
            except:
                pass
        else:
            # Make sure stream is off
            stream_on = False

        # Sleep 80% of the time before we need to start reading chunks
        await asyncio.sleep(0.8 * n_reads_per_chunk * delay / 1000)


# Set up asynchronous DAQ task
daq_task = asyncio.create_task(daq_stream_async(arduino, stream_data))

# Build app
app = potentiometer_app(
    arduino, stream_data, on_demand_data, daq_task, rollover=400, stream_plot_delay=90
)

# Build it with curdoc
app(bokeh.plotting.curdoc())