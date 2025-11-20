#! @KPYTHON3@
""" Lakeshore 224/336 controller class """

from errno import ETIMEDOUT, EISCONN
import socket
import time
from typing import Union

from hardware_device_base.hardware_sensor_base import HardwareSensorBase


class LakeshoreController(HardwareSensorBase):
    """ Handle all correspondence with the ethernet interface of the
        Lakeshore 224/336 controller.
    """
    # pylint: disable=too-many-instance-attributes
    initialized = False
    revision = None
    termchars = '\r\n'

    # Heater dictionaries
    resistance = {'1': 25, '2': 50}
    max_current = {'0': 0.0, '1': 0.707, '2': 1.0, '3': 1.141, '4': 2.0}
    htr_display = {'1': 'current', '2': 'power'}
    htr_errors = {'0': 'no error', '1': 'heater open load', '2': 'heater short'}

    def __init__(self, log=True, logfile=__name__.rsplit(".", 1)[-1],
                 opt3062=False, model336=True, celsius=True):
        """ Initialize the Lakeshore controller.
        :param log: If True, log to file
        :param logfile: name of log file (defaults to lakeshore.log)
        :param opt3062: set to True if optional 3062 board installed (defaults to False)
        :param model336: set to True if controller is model 336 (default),
                        if False assumes model 224
        :param celsius: set to True to read temperature in Celsius (default),
        """
        # pylint: disable=too-many-positional-arguments, too-many-arguments
        super().__init__(log, logfile)
        self.socket: socket.socket | None = None
        self.host: str | None = None
        self.port: int = -1

        self.celsius = celsius
        if self.celsius:
            self.set_celsius()
            self.report_info("Using Celsius for temperature")
        else:
            self.set_kelvin()
            self.report_info("Using Kelvin for temperature")
        self.model336 = model336

        if model336:
            if opt3062:
                self.sensors = {'A': 1, 'B': 2, 'C': 3,
                                'D1': 4, 'D2': 5, 'D3': 6, 'D4': 7, 'D5': 8}
            else:
                self.sensors = {'A': 1, 'B': 2, 'C': 3, 'D': 4}

            self.outputs = {'1':
                                {'resistance': None, 'max_current': 0.0,
                                 'user_max_current': 0.0, 'htr_display': '',
                                 'status': '', 'p': 0.0, 'i': 0.0, 'd': 0.0},
                            '2':
                                {'resistance': None, 'max_current': 0.0,
                                 'user_max_current': 0.0, 'htr_display': '',
                                 'status': '', 'p': 0.0, 'i': 0.0, 'd': 0.0},
            }
        else:
            # Model 224
            self.sensors = {'A': 1, 'B': 2,
                            'C1': 3, 'C2': 4, 'C3': 5, 'C4': 6, 'C5': 7,
                            'D1': 8, 'D2': 9, 'D3': 10, 'D4': 11, 'D5': 12}
            self.outputs = None

    def disconnect(self) -> None:
        """ Disconnect controller. """
        if not self.is_connected():
            self.report_warning("Already disconnected from device")
            return
        try:
            self.logger.info("Disconnecting from device")
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
            self.socket = None
            self.report_info("Disconnected controller")
            self._set_connected(False)

        except OSError as e:
            self.report_error(f"Disconnection error: {e.strerror}")
            self._set_connected(False)
            self.socket = None
        self.report_info("Disconnected from controller")

    def connect(self, host, port, con_type: str ="tcp") -> None: # pylint: disable=W0221
        """ Connect to controller. """
        if self.validate_connection_params((host, port)):
            self.host = host
            self.port = port
            if con_type == "tcp":
                if self.socket is None:
                    self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    self.socket.connect((host, port))
                    self.report_info(f"Connected to {host}:{port}")
                    self._set_connected(True)

                except OSError as e:
                    if e.errno == EISCONN:
                        self.report_info("Already connected")
                        self._set_connected(True)
                    else:
                        self.report_error(f"Connection error: {e.strerror}")
                        self._set_connected(False)
                # clear socket
                if self.is_connected():
                    self._clear_socket()
            elif con_type == "serial":
                self.report_error("Serial connection not supported")
                self._set_connected(False)
            else:
                self.report_error(f"Unknown connection type: {con_type}")
                self._set_connected(False)
        else:
            self.report_error(f"Invalid connection parameters: {host}:{port}")
            self._set_connected(False)

    def _clear_socket(self):
        """ Clear socket buffer. """
        if self.socket is not None:
            self.socket.setblocking(False)
            while True:
                try:
                    _ = self.socket.recv(1024)
                except BlockingIOError:
                    break
            self.socket.setblocking(True)

    def initialize(self):
        """ Initialize the lakeshore status. """

        self.revision = self.command('*idn?')

        if self.model336:

            for htr_items in self.outputs.items():
                htr = htr_items[0]
                htr_settings = self.get_heater_settings(htr)
                if htr_settings is None:
                    self.report_warning(f"Unable to get settings for htr {htr}")
                else:
                    resistance, max_current, user_max_current, htr_display = htr_settings
                    self.outputs[htr]['resistance'] = resistance
                    self.outputs[htr]['max_current'] = max_current
                    self.outputs[htr]['user_max_current'] = user_max_current
                    self.outputs[htr]['htr_display'] = htr_display

                self.outputs[htr]['status'] = self.get_heater_status(htr)

                pid = self.get_heater_pid(htr)
                if pid is None:
                    self.report_warning(f"PID not set for htr {htr}")
                else:
                    p, i, d = pid
                    self.outputs[htr]['p'] = p
                    self.outputs[htr]['i'] = i
                    self.outputs[htr]['d'] = d

        self.initialized = True

    def command(self, command, params=None):
        """ Wrapper to _send_command(), ensuring the command lock is
            released if an exception occurs.

        :param command: String, command to issue.
        :param params: String, parameters to issue.

        """

        with self.lock:
            try:
                if self._send_command(command, params):
                    result = ''
                    if '?' in command:
                        result = self._read_reply()
                else:
                    result = ''
                    self.report_error(f"Error sending command '{command}'")
            except Exception as ex:
                self.report_error(f"Error sending command '{command}'")
                raise IOError(f"Failed to write command: '{ex}'") from ex
                # Ensure that status is always checked, even on failure
            self.logger.debug("Command sent to lakeshore")

        return result

    def _send_command(self, command: str, *args) -> bool:  # pylint: disable=W0221
        """ Wrapper to send/receive with error checking and retries.

        :param command: String, command to issue.
        :param args: String, parameters to issue.

        """
        if not self.is_connected():
            self.report_info('connecting')
            self.connect(self.host, self.port)

        retries = 3
        if args:
            send_command = f"{command} {args[0]}{self.termchars}".encode('utf-8')
        else:
            send_command = f"{command}{self.termchars}".encode('utf-8')

        while retries > 0:
            self.logger.debug("sending command %s", send_command)
            try:
                self.socket.send(send_command)

            except socket.error:
                self.report_error(
                        f"Failed to send command, re-opening socket, {retries} retries"
                        f" remaining")
                self.disconnect()
                try:
                    self.connect(self.host, self.port)
                except OSError:
                    self.report_error('Could not reconnect to controller, aborting')
                    return False
                retries -= 1
                continue
            break
        if retries <= 0:
            self.report_error("Failed to send command.")
            raise RuntimeError('unable to successfully issue command: ' + repr(command))

        self.logger.debug("Sent command: %s", send_command)
        self._set_status((0, f"Command sent to lakeshore: {command}"))
        return True

    def _read_reply(self) -> Union[str, None]:
        # Get a reply, if needed.
        timeout = 1
        start = time.time()
        reply = self.socket.recv(1024)
        while self.termchars not in reply.decode('utf-8') and \
                time.time() - start < timeout:
            try:
                reply += self.socket.recv(1024)
                self.logger.debug("reply: %s", reply)
            except OSError as e:
                if e.errno == ETIMEDOUT:
                    reply = ''
            time.sleep(0.1)

            if reply == '':
                # Don't log here, because it happens a lot when the controller
                # is unresponsive. Just try again.
                continue

        if isinstance(reply, str):
            reply = reply.strip()
        else:
            reply = reply.decode('utf-8').strip()
        return reply

    def set_celsius(self):
        """ Set units to Celsius. """
        self.celsius = True

    def set_kelvin(self):
        """ Set units to Kelvin. """
        self.celsius = False

    def get_temperature(self, sensor):
        """ Get sensor temperature.

        :param sensor: String, name of the sensor: A-D or A-C, D1=D5.

        """
        retval = None
        if sensor.upper() not in self.sensors:
            self.report_error(f"Sensor {sensor} is not available")
        else:
            if self.celsius:
                reply = self.command('crdg?', sensor)
                if len(reply) > 0:
                    retval = float(reply)
            else:
                reply = self.command('krdg?', sensor)
                if len(reply) > 0:
                    retval = float(reply)
        return retval

    def get_heater_settings(self, output):
        """ Get heater settings.

        :param output: String, output number of the sensor (1 or 2).
        returns resistance, max current, max user current, display.
        """
        retval = None
        if self.model336:
            if output.upper() not in self.outputs:
                self.report_error(f"Heater {output} is not available")
            else:
                reply = self.command('htrset?', output)
                if len(reply) > 0:
                    ires, imaxcur, strusermaxcur, idisp = reply.split(',')
                    retval = (self.resistance[ires], self.max_current[imaxcur],
                              float(strusermaxcur), self.htr_display[idisp])
        else:
            self.report_error("Heater is not available with this model")
        return retval

    def get_heater_pid(self, output):
        """ Get heater PID values.

        :param output: String, output number of the sensor (1 or 2).
        returns p,i,d values
        """
        retval = None
        if self.model336:
            if output.upper() not in self.outputs:
                self.report_error(f"Heater {output} is not available")
            else:
                reply = self.command('pid?', output)
                if len(reply) > 0:
                    p, i, d = reply.split(',')
                    retval = [float(i), float(d), float(p)]
        else:
            self.report_error("Heater is not available with this model")
        return retval

    def get_heater_status(self, output):
        """ Get heater status.

        :param output: String, output number of the sensor (1 or 2).
        returns status string
        """
        retval = 'unknown'
        if self.model336:
            if output.upper() not in self.outputs:
                self.report_error(f"Heater {output} is not available")
            else:
                reply = self.command('htrst?', output)
                if len(reply) > 0:
                    reply = reply.strip()
                    if reply in self.htr_errors:
                        retval = self.htr_errors[reply]
                    else:
                        self.report_error(f"Heater error {reply} and status is unknown")
        else:
            self.report_error("Heater is not available with this model")
        return retval

    def get_heater_output(self, output):
        """ Get heater output.

        :param output: String, output number of the sensor (1 or 2).
        returns heater output.
        """
        retval = None
        if self.model336:
            if output.upper() not in self.outputs:
                self.report_error(f"Heater {output} is not available")
            else:
                reply = self.command('htr?', output)
                if len(reply) > 0:
                    reply = reply.strip()
                    try:
                        retval = float(reply)
                    except ValueError:
                        self.report_error(f"Heater output error: {reply}")
                else:
                    self.report_error("Heater output error")
        else:
            self.report_error("Heater is not available with this model")
        return retval

    def get_atomic_value(self, item: str = "") -> Union[float, None]:
        """
        Read the latest value of a specific item
        :param item: String, name of the item
        returns value of item or None
        """
        retval = None
        if item.upper() in self.sensors or item in self.outputs:
            if item.upper() in self.sensors:
                retval = self.get_temperature(item)
            else:
                retval = self.get_heater_output(item)
        else:
            self.report_error(f"Item {item} is not available")
        return retval
# end of class Controller
