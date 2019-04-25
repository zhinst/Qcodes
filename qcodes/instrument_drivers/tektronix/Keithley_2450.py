import numpy as np
from typing import Callable, cast

from qcodes.instrument.base import InstrumentBase
from qcodes import VisaInstrument, InstrumentChannel, ParameterWithSetpoints
from qcodes.utils.validators import Enum, Numbers, Arrays


class Sense2450(InstrumentChannel):
    function_modes = {
        "current": {
            "name": "\"CURR:DC\"",
            "unit": "A",
            "range_vals": Numbers(10E-9, 1)
        },
        "resistance": {
            "name": "\"RES\"",
            "unit": "Ohm",
            "range_vals": Numbers(20, 200E6)
        },
        "voltage": {
            "name": "\"VOLT:DC\"",
            "unit": "V",
            "range_vals": Numbers(0.02, 200)
        }
    }

    def __init__(self, parent: 'Keithley2450', name: str, proper_function: str) -> None:
        super().__init__(parent, name)
        self._proper_function = proper_function
        range_vals = self.function_modes[self._proper_function]["range_vals"]
        unit = self.function_modes[self._proper_function]["unit"]

        self.function = self.parent.sense_function

        self.add_parameter(
            "four_wire_measurement",
            set_cmd=f":SENSe:{self._proper_function}:RSENse {{}}",
            get_cmd=f":SENSe:{self._proper_function}:RSENse?",
            val_mapping={
                True: "1",
                False: "0"
            },
        )

        self.add_parameter(
            "range",
            set_cmd=f":SENSe:{self._proper_function}:RANGe {{}}",
            get_cmd=f":SENSe:{self._proper_function}:RANGe?",
            vals=range_vals,
            get_parser=float,
            unit=unit
        )

        self.add_parameter(
            "auto_range",
            set_cmd=f":SENSe:{self._proper_function}:RANGe:AUTO {{}}",
            get_cmd=f":SENSe:{self._proper_function}:RANGe:AUTO?",
            val_mapping={
                True: "1",
                False: "0"
            }
        )

        self.add_parameter(
            self._proper_function,
            get_cmd=self._measure,
            get_parser=float,
            unit=unit,
            snapshot_value=False
        )

        self.add_parameter(
            "sweep",
            get_cmd=self._measure_sweep,
            unit=unit,
            vals=Arrays(shape=(self.parent.npts,)),
            parameter_class=ParameterWithSetpoints
        )

    def _measure(self) -> Callable:
        if not self.parent.output():
            raise RuntimeError("Output needs to be on for a measurement")
        return self.ask(":MEASure?")

    def _measure_sweep(self) -> np.ndarray:

        source = cast(Source2450, self.parent.source)
        source.sweep_start()
        raw_data = self.ask(f":TRACe:DATA? 1, {self.parent.npts()}")
        self.write(":TRACe:CLEar")

        return np.array([float(i) for i in raw_data.split(",")])


class Source2450(InstrumentChannel):
    function_modes = {
        "current": {
            "name": "CURR",
            "unit": "A",
            "range_vals": Numbers(-1, 1)
        },
        "voltage": {
            "name": "VOLT",
            "unit": "V",
            "range_vals": Numbers(-200, 200)
        }
    }

    def __init__(self, parent: 'Keithley2450', name: str, proper_function: str) -> None:
        super().__init__(parent, name)
        self._proper_function = proper_function
        range_vals = self.function_modes[self._proper_function]["range_vals"]
        unit = self.function_modes[self._proper_function]["unit"]

        self.function = self.parent.source_function
        self._sweep_arguments = {}

        self.add_parameter(
            "range",
            set_cmd=f":SOUR:{self._proper_function}:RANGe {{}}",
            get_cmd=f":SOUR:{self._proper_function}:RANGe?",
            vals=range_vals,
            get_parser=float,
            unit=unit
        )

        self.add_parameter(
            "auto_range",
            set_cmd=f":SOURce:{self._proper_function}:RANGe:AUTO {{}}",
            get_cmd=f":SOURce:{self._proper_function}:RANGe:AUTO?",
            val_mapping={
                True: "1",
                False: "0"
            }
        )

        limit_cmd = {"current": "VLIM", "voltage": "ILIM"}[self._proper_function]
        self.add_parameter(
            "limit",
            set_cmd=f"SOUR:{self._proper_function}:{limit_cmd} {{}}",
            get_cmd=f"SOUR:{self._proper_function}:{limit_cmd}?",
            get_parser=float,
            unit=unit
        )

        self.add_parameter(
            self._proper_function,
            set_cmd=f"SOUR:{self._proper_function} {{}}",
            get_cmd=f"SOUR:{self._proper_function}?",
            get_parser=float,
            unit=unit,
            snapshot_value=False
        )

        self.add_parameter(
            "sweep_axis",
            get_cmd=self.get_sweep_axis,
            vals=Arrays(shape=(self.parent.npts,)),
            unit=unit
        )

    def get_sweep_axis(self):
        if self._sweep_arguments == {}:
            raise ValueError(
                "Before starting a sweep, please call 'sweep_setup'"
            )

        return np.linspace(
            start=self._sweep_arguments["start"],
            stop=self._sweep_arguments["stop"],
            num=self._sweep_arguments["step_count"]
        )

    def sweep_setup(
            self,
            start: float,
            stop: float,
            step_count: int,
            delay: float = 0,
            sweep_count: int = 1,
            range_mode: str = "AUTO"
    ) -> None:

        self._sweep_arguments = dict(
            start=start,
            stop=stop,
            step_count=step_count,
            delay=delay,
            sweep_count=sweep_count,
            range_mode=range_mode
        )

    def sweep_start(self) -> None:
        """
        Start a sweep and return when the sweep has finished.
        Note: This call is blocking
        """
        cmd_args = dict(self._sweep_arguments)
        cmd_args["function"] = self.function.get_latest() or self.function.get()

        cmd = ":SOURce:SWEep:{function}:LINear {start},{stop}," \
              "{step_count},{delay},{sweep_count},{range_mode}".format(**cmd_args)

        self.write(cmd)
        self.write(":INITiate")
        self.write("*WAI")

    def sweep_reset(self) -> None:
        self._sweep_arguments = {}


class Keithley2450(VisaInstrument):
    """
    The QCoDeS driver for the Keithley 2450 source meter
    """

    def __init__(self, name: str, address: str, **kwargs) -> None:

        super().__init__(name, address, terminator='\n', **kwargs)

        if not self._has_correct_language_mode():
            self.log.warning(
                f"The instrument is an unsupported language mode."
                f"Please run `instrument.set_correct_language()` and try to "
                f"initialize the driver again after an instrument power cycle. "
                f"No parameters/sub modules shall be available on this driver "
                f"instance"
            )
            return

        self.add_parameter(
            "source_function",
            set_cmd=self._set_source_function,
            get_cmd=":SOUR:FUNC?",
            val_mapping={
                key: value["name"]
                for key, value in Source2450.function_modes.items()
            }
        )

        self.add_parameter(
            "sense_function",
            set_cmd=self._set_sense_function,
            get_cmd=":SENS:FUNC?",
            val_mapping={
                key: value["name"]
                for key, value in Sense2450.function_modes.items()
            }
        )

        self.add_parameter(
            "terminals",
            set_cmd="ROUTe:TERMinals {}",
            get_cmd="ROUTe:TERMinals?",
            vals=Enum("REAR", "FRONT")
        )

        self.add_parameter(
            "output",
            set_cmd=":OUTP {}",
            get_cmd=":OUTP?",
            val_mapping={
                True: "1",
                False: "0"
            }
        )

        for proper_source_function in Source2450.function_modes:
            self.add_submodule(
                f"_source_{proper_source_function}",
                Source2450(self, "source", proper_source_function)
            )

        for proper_sense_function in Sense2450.function_modes:
            self.add_submodule(
                f"_sense_{proper_sense_function}",
                Sense2450(self, "sense", proper_sense_function)
            )

        self.connect_message()

    def _set_sense_function(self, value: str) -> None:
        self.write(f":SENS:FUNC {value}",)
        self.sense.sweep.setpoints = (self.source.sweep_axis,)

    def _set_source_function(self, value: str) -> None:

        if self.sense_function() == "resistance":
            raise RuntimeError(
                "Cannot change the source function while sense function is in 'resistance' mode"
            )

        self.write(f":SOUR:FUNC {value}")
        self.sense.sweep.setpoints = (self.source.sweep_axis,)
        self.source.reset_sweep()

    @property
    def source(self) -> InstrumentBase:
        return self.submodules[f"_source_{self.source_function()}"]

    @property
    def sense(self) -> InstrumentBase:
        return self.submodules[f"_sense_{self.sense_function()}"]

    def npts(self) -> int:
        """
        Get the number of points in the sweep axis
        """
        return len(self.source.get_sweep_axis())

    def set_correct_language(self) -> None:
        """
        The correct communication protocol is SCPI, make sure this is set
        """
        self.raw_write("*LANG SCPI")
        self.log.warning("PLease power cycle the instrument to make the change take effect")
        # We want the user to be able to instantiate a driver with the same name
        self.close()

    def _has_correct_language_mode(self) -> bool:
        """
        Query if we have the correct language mode
        """
        return self.ask_raw("*LANG?") == "SCPI"
