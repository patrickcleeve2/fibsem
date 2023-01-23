from abc import ABC, abstractmethod
import copy
import logging
from copy import deepcopy
import datetime
import numpy as np
from fibsem.config import load_microscope_manufacturer
import sys

from typing import Union
import fibsem.constants as constants


manufacturer = load_microscope_manufacturer()
if manufacturer == "Tescan":
    from tescanautomation import Automation
    from tescanautomation.SEM import HVBeamStatus as SEMStatus
    from tescanautomation.Common import Bpp
    from tescanautomation.DrawBeam import IEtching 


    # from tescanautomation.GUI import SEMInfobar
    import re

    #del globals()[tescanautomation.GUI]
    sys.modules.pop("tescanautomation.GUI")
    sys.modules.pop("tescanautomation.pyside6gui")
    sys.modules.pop("tescanautomation.pyside6gui.imageViewer_private")
    sys.modules.pop("tescanautomation.pyside6gui.infobar_private")
    sys.modules.pop("tescanautomation.pyside6gui.infobar_utils")
    sys.modules.pop("tescanautomation.pyside6gui.rc_GUI")
    sys.modules.pop("tescanautomation.pyside6gui.workflow_private")
    sys.modules.pop("PySide6.QtCore")

if manufacturer == "Thermo":
    from autoscript_sdb_microscope_client.structures import GrabFrameSettings
    from autoscript_sdb_microscope_client.enumerations import CoordinateSystem
    from autoscript_sdb_microscope_client import SdbMicroscopeClient
    from autoscript_sdb_microscope_client._dynamic_object_proxies import RectanglePattern, CleaningCrossSectionPattern

import sys


from fibsem.structures import (
    BeamType,
    ImageSettings,
    Point,
    FibsemImage,
    FibsemImageMetadata,
    MicroscopeState,
    MicroscopeSettings,
    BeamSettings,
    FibsemStagePosition,
    MillingSettings,
)


class FibsemMicroscope(ABC):
    """Abstract class containing all the core microscope functionalities"""

    @abstractmethod
    def connect_to_microscope(self):
        pass

    @abstractmethod
    def disconnect(self):
        pass

    @abstractmethod
    def acquire_image(self):
        pass

    @abstractmethod
    def last_image(self):
        pass

    @abstractmethod
    def autocontrast(self):
        pass

    @abstractmethod
    def move_stage_absolute(self):
        pass

    @abstractmethod
    def move_stage_relative(self):
        pass

    @abstractmethod
    def stable_move(self):
        pass

    @abstractmethod
    def setup_milling(self):
        pass

    @abstractmethod
    def run_milling(self):
        pass

    @abstractmethod
    def finish_milling(self):
        pass

    @abstractmethod
    def draw_rectangle(self):
        pass

    @abstractmethod
    def set_microscope_state(self):
        pass


class ThermoMicroscope(FibsemMicroscope):
    """ThermoFisher Microscope class, uses FibsemMicroscope as blueprint

    Args:
        FibsemMicroscope (ABC): abstract implementation
    """

    def __init__(self):
        self.connection = SdbMicroscopeClient()

    def disconnect(self):
        self.connection.disconnect()

    # @classmethod
    def connect_to_microscope(self, ip_address: str, port: int = 7520) -> None:
        """Connect to a Thermo Fisher microscope at a specified I.P. Address and Port

        Args:
            ip_address (str): I.P. Address of microscope
            port (int): port of microscope (default: 7520)
        """
        try:
            # TODO: get the port
            logging.info(f"Microscope client connecting to [{ip_address}:{port}]")
            self.connection.connect(host=ip_address, port=port)
            logging.info(f"Microscope client connected to [{ip_address}:{port}]")
        except Exception as e:
            logging.error(f"Unable to connect to the microscope: {e}")

    def acquire_image(self, image_settings=ImageSettings) -> FibsemImage:
        """Acquire a new image.

        Args:
            settings (GrabFrameSettings, optional): frame grab settings. Defaults to None.
            beam_type (BeamType, optional): imaging beam type. Defaults to BeamType.ELECTRON.

        Returns:
            AdornedImage: new image
        """
        # set frame settings
        frame_settings = GrabFrameSettings(
            resolution=image_settings.resolution,
            dwell_time=image_settings.dwell_time,
            reduced_area=image_settings.reduced_area,
        )

        if image_settings.beam_type == BeamType.ELECTRON:
            hfw_limits = (
                self.connection.beams.electron_beam.horizontal_field_width.limits
            )
            image_settings.hfw = np.clip(
                image_settings.hfw, hfw_limits.min, hfw_limits.max
            )
            self.connection.beams.electron_beam.horizontal_field_width.value = (
                image_settings.hfw
            )

        if image_settings.beam_type == BeamType.ION:
            hfw_limits = self.connection.beams.ion_beam.horizontal_field_width.limits
            image_settings.hfw = np.clip(
                image_settings.hfw, hfw_limits.min, hfw_limits.max
            )
            self.connection.beams.ion_beam.horizontal_field_width.value = (
                image_settings.hfw
            )

        logging.info(f"acquiring new {image_settings.beam_type.name} image.")
        self.connection.imaging.set_active_view(image_settings.beam_type.value)
        self.connection.imaging.set_active_device(image_settings.beam_type.value)
        image = self.connection.imaging.grab_frame(frame_settings)

        state = self.get_current_microscope_state()

        fibsem_image = FibsemImage.fromAdornedImage(
            copy.deepcopy(image), copy.deepcopy(image_settings), copy.deepcopy(state)
        )

        return fibsem_image

    def last_image(self, beam_type: BeamType = BeamType.ELECTRON) -> FibsemImage:
        """Get the last previously acquired image.

        Args:
            microscope (SdbMicroscopeClient):  autoscript microscope instance
            beam_type (BeamType, optional): imaging beam type. Defaults to BeamType.ELECTRON.

        Returns:
            AdornedImage: last image
        """

        self.connection.imaging.set_active_view(beam_type.value)
        self.connection.imaging.set_active_device(beam_type.value)
        image = self.connection.imaging.get_image()

        state = self.get_current_microscope_state()

        image_settings = FibsemImageMetadata.image_settings_from_adorned(
            image, beam_type
        )

        fibsem_image = FibsemImage.fromAdornedImage(image, image_settings, state)

        return fibsem_image

    def autocontrast(self, beam_type=BeamType.ELECTRON) -> None:
        """Automatically adjust the microscope image contrast."""
        self.connection.imaging.set_active_view(beam_type.value)
        self.connection.auto_functions.run_auto_cb()

    def reset_beam_shifts(self):
        """Set the beam shift to zero for the electron and ion beams

        Args:
            microscope (SdbMicroscopeClient): Autoscript microscope object
        """
        from autoscript_sdb_microscope_client.structures import Point

        # reset zero beamshift
        logging.debug(
            f"reseting ebeam shift to (0, 0) from: {self.connection.beams.electron_beam.beam_shift.value}"
        )
        self.connection.beams.electron_beam.beam_shift.value = Point(0, 0)
        logging.debug(
            f"reseting ibeam shift to (0, 0) from: {self.connection.beams.electron_beam.beam_shift.value}"
        )
        self.connection.beams.ion_beam.beam_shift.value = Point(0, 0)
        logging.debug(f"reset beam shifts to zero complete")

    def get_stage_position(self):
        self.connection.specimen.stage.set_default_coordinate_system(
            CoordinateSystem.RAW
        )
        stage_position = self.connection.specimen.stage.current_position
        print(stage_position)
        self.connection.specimen.stage.set_default_coordinate_system(
            CoordinateSystem.SPECIMEN
        )
        return stage_position

    def get_current_microscope_state(self) -> MicroscopeState:
        """Get the current microscope state

        Returns:
            MicroscopeState: current microscope state
        """

        current_microscope_state = MicroscopeState(
            timestamp=datetime.datetime.timestamp(datetime.datetime.now()),
            # get absolute stage coordinates (RAW)
            absolute_position=self.get_stage_position(),
            # electron beam settings
            eb_settings=BeamSettings(
                beam_type=BeamType.ELECTRON,
                working_distance=self.connection.beams.electron_beam.working_distance.value,
                beam_current=self.connection.beams.electron_beam.beam_current.value,
                hfw=self.connection.beams.electron_beam.horizontal_field_width.value,
                resolution=self.connection.beams.electron_beam.scanning.resolution.value,
                dwell_time=self.connection.beams.electron_beam.scanning.dwell_time.value,
            ),
            # ion beam settings
            ib_settings=BeamSettings(
                beam_type=BeamType.ION,
                working_distance=self.connection.beams.ion_beam.working_distance.value,
                beam_current=self.connection.beams.ion_beam.beam_current.value,
                hfw=self.connection.beams.ion_beam.horizontal_field_width.value,
                resolution=self.connection.beams.ion_beam.scanning.resolution.value,
                dwell_time=self.connection.beams.ion_beam.scanning.dwell_time.value,
            ),
        )

        return current_microscope_state

    def move_stage_absolute(self, position: FibsemStagePosition):
        """Move the stage to the specified coordinates.

        Args:
            x (float): The x-coordinate to move to (in meters).
            y (float): The y-coordinate to move to (in meters).
            z (float): The z-coordinate to move to (in meters).
            r (float): The rotation to apply (in degrees).
            tx (float): The x-axis tilt to apply (in degrees).

        Returns:
            None
        """
        stage = self.connection.specimen.stage
        thermo_position = position.to_autoscript_position()
        thermo_position.coordinate_system = CoordinateSystem.RAW
        stage.absolute_move(thermo_position)

    def move_stage_relative(
        self,
        position : FibsemStagePosition,
    ):
        """Move the stage by the specified relative move.

        Args:
            x (float): The x-coordinate to move to (in meters).
            y (float): The y-coordinate to move to (in meters).
            z (float): The z-coordinate to move to (in meters).
            r (float): The rotation to apply (in degrees).
            tx (float): The x-axis tilt to apply (in degrees).

        Returns:
            None
        """
        stage = self.connection.specimen.stage
        thermo_position = position.to_autoscript_position()
        thermo_position.coordinate_system = CoordinateSystem.RAW
        stage.relative_move(thermo_position)

    def stable_move(
        self,
        settings: MicroscopeSettings,
        dx: float,
        dy: float,
        beam_type: BeamType,
    ) -> None:
        """Calculate the corrected stage movements based on the beam_type, and then move the stage relatively.

        Args:
            microscope (SdbMicroscopeClient): autoscript microscope instance
            settings (MicroscopeSettings): microscope settings
            dx (float): distance along the x-axis (image coordinates)
            dy (float): distance along the y-axis (image coordinates)
            beam_type (BeamType): beam type to move in
        """
        wd = self.connection.beams.electron_beam.working_distance.value

        # calculate stage movement
        x_move = x_corrected_stage_movement(dx)
        yz_move = y_corrected_stage_movement(
            microscope=self.connection,
            settings=settings,
            expected_y=dy,
            beam_type=beam_type,
        )

        # move stage
        stage_position = FibsemStagePosition(
            x=x_move.x * 1000, y=yz_move.y * 1000, z=yz_move.z * 1000, r=0, t=0, coordinate_system="raw"
        )
        logging.info(f"moving stage ({beam_type.name}): {stage_position}")
        self.move_stage_relative(stage_position)

        # adjust working distance to compensate for stage movement
        self.connection.beams.electron_beam.working_distance.value = wd
        self.connection.specimen.stage.link() 

        return

    def setup_milling(self, application_file: str, patterning_mode: str, hfw: float,mill_settings: MillingSettings):
        self.connection.imaging.set_active_view(BeamType.ION.value)  # the ion beam view
        self.connection.imaging.set_active_device(BeamType.ION.value)
        self.connection.patterning.set_default_beam_type(BeamType.ION.value)  # ion beam default
        self.connection.patterning.set_default_application_file(application_file)
        self.connection.patterning.mode = patterning_mode
        self.connection.patterning.clear_patterns()  # clear any existing patterns
        self.connection.beams.ion_beam.horizontal_field_width.value = hfw

    def run_milling(self, milling_current: float, asynch: bool = False):
        # change to milling current
        self.connection.imaging.set_active_view(BeamType.ION.value)  # the ion beam view
        if self.connection.beams.ion_beam.beam_current.value != milling_current:
            logging.info(f"changing to milling current: {milling_current:.2e}")
            self.connection.beams.ion_beam.beam_current.value = milling_current

        # run milling (asynchronously)
        logging.info(f"running ion beam milling now... asynchronous={asynch}")
        if asynch:
            self.connection.patterning.start()
        else:
            self.connection.patterning.run()
            self.connection.patterning.clear_patterns()
        # NOTE: Make tescan logs the same??

    def finish_milling(self, imaging_current: float):
        self.connection.patterning.clear_patterns()
        self.connection.beams.ion_beam.beam_current.value = imaging_current
        self.connection.patterning.mode = "Serial"

    def draw_rectangle(self, mill_settings: MillingSettings):
        
        if mill_settings.cleaning_cross_section:
            pattern = self.connection.patterning.create_cleaning_cross_section(
                center_x=mill_settings.centre_x,
                center_y=mill_settings.centre_y,
                width=mill_settings.width,
                height=mill_settings.height,
                depth=mill_settings.depth,
            )
        else:
            pattern = self.connection.patterning.create_rectangle(
                center_x=mill_settings.centre_x,
                center_y=mill_settings.centre_y,
                width=mill_settings.width,
                height=mill_settings.height,
                depth=mill_settings.depth,
            )

        pattern.rotation = mill_settings.rotation
        pattern.scan_direction = mill_settings.scan_direction


    def set_microscope_state(
        self, microscope_state: MicroscopeState
    ):
        """Reset the microscope state to the provided state"""

        logging.info(f"restoring microscope state...")

        # move to position
        self.move_stage_absolute(
            stage_position=microscope_state.absolute_position
        )

        # restore electron beam
        logging.info(f"restoring electron beam settings...")
        self.connection.beams.electron_beam.working_distance.value = (
            microscope_state.eb_settings.working_distance
        )
        self.connection.beams.electron_beam.beam_current.value = (
            microscope_state.eb_settings.beam_current
        )
        self.connection.beams.electron_beam.horizontal_field_width.value = (
            microscope_state.eb_settings.hfw
        )
        self.connection.beams.electron_beam.scanning.resolution.value = (
            microscope_state.eb_settings.resolution
        )
        self.connection.beams.electron_beam.scanning.dwell_time.value = (
            microscope_state.eb_settings.dwell_time
        )
        # microscope.beams.electron_beam.stigmator.value = (
        #     microscope_state.eb_settings.stigmation
        # )

        # restore ion beam
        logging.info(f"restoring ion beam settings...")
        self.connection.beams.ion_beam.working_distance.value = (
            microscope_state.ib_settings.working_distance
        )
        self.connection.beams.ion_beam.beam_current.value = (
            microscope_state.ib_settings.beam_current
        )
        self.connection.beams.ion_beam.horizontal_field_width.value = (
            microscope_state.ib_settings.hfw
        )
        self.connection.beams.ion_beam.scanning.resolution.value = (
            microscope_state.ib_settings.resolution
        )
        self.connection.beams.ion_beam.scanning.dwell_time.value = (
            microscope_state.ib_settings.dwell_time
        )
        # microscope.beams.ion_beam.stigmator.value = microscope_state.ib_settings.stigmation

        self.connection.specimen.stage.link()
        logging.info(f"microscope state restored")
        return

class TescanMicroscope(FibsemMicroscope):
    """TESCAN Microscope class, uses FibsemMicroscope as blueprint

    Args:
        FibsemMicroscope (ABC): abstract implementation
    """

    def __init__(self, ip_address: str = "localhost"):
        self.connection = Automation(ip_address)
        detectors = self.connection.FIB.Detector.Enum()
        self.ion_detector_active = detectors[0]
        self.last_image_eb = None
        self.last_image_ib = None

    def disconnect(self):
        self.connection.Disconnect()

    # @classmethod
    def connect_to_microscope(self, ip_address: str, port: int = 8300) -> None:
        self.connection = Automation(ip_address, port)

    def acquire_image(self, image_settings=ImageSettings) -> FibsemImage:

        if image_settings.beam_type.value == 1:
            image = self._get_eb_image(image_settings)
            self.last_image_eb = image
        if image_settings.beam_type.value == 2:
            image = self._get_ib_image(image_settings)
            self.last_image_ib = image

        return image

    def _get_eb_image(self, image_settings=ImageSettings) -> FibsemImage:
        # At first make sure the beam is ON
        self.connection.SEM.Beam.On()
        # important: stop the scanning before we start scanning or before automatic procedures,
        # even before we configure the detectors
        self.connection.SEM.Scan.Stop()
        # Select the detector for image i.e.:
        # 1. assign the detector to a channel
        # 2. enable the channel for acquisition
        detector = self.connection.SEM.Detector.SESuitable()
        self.connection.SEM.Detector.Set(0, detector, Bpp.Grayscale_8_bit)

        dwell_time = image_settings.dwell_time * constants.SI_TO_NANO
        # resolution
        numbers = re.findall(r"\d+", image_settings.resolution)
        imageWidth = int(numbers[0])
        imageHeight = int(numbers[1])

        self.connection.SEM.Optics.SetViewfield(image_settings.hfw * 1000)

        image = self.connection.SEM.Scan.AcquireImageFromChannel(
            0, imageWidth, imageHeight, dwell_time
        )

        microscope_state = MicroscopeState(
            timestamp=datetime.datetime.timestamp(datetime.datetime.now()),
            absolute_position=FibsemStagePosition(
                x=float(image.Header["SEM"]["StageX"]),
                y=float(image.Header["SEM"]["StageY"]),
                z=float(image.Header["SEM"]["StageZ"]),
                r=float(image.Header["SEM"]["StageRotation"]),
                t=float(image.Header["SEM"]["StageTilt"]),
                coordinate_system="Raw",
            ),
            eb_settings=BeamSettings(
                beam_type=BeamType.ELECTRON,
                working_distance=float(image.Header["SEM"]["WD"]),
                beam_current=float(image.Header["SEM"]["BeamCurrent"]),
                resolution="{}x{}".format(imageWidth, imageHeight),
                dwell_time=float(image.Header["SEM"]["DwellTime"]),
                stigmation=Point(
                    float(image.Header["SEM"]["StigmatorX"]),
                    float(image.Header["SEM"]["StigmatorY"]),
                ),
                shift=Point(
                    float(image.Header["SEM"]["ImageShiftX"]),
                    float(image.Header["SEM"]["ImageShiftY"]),
                ),
            ),
            ib_settings=BeamSettings(beam_type=BeamType.ION),
        )
        fibsem_image = FibsemImage.fromTescanImage(
            image, deepcopy(image_settings), microscope_state
        )

        res = fibsem_image.data.shape

        fibsem_image.metadata.image_settings.resolution = str(res[1]) + "x" + str(res[0])

        return fibsem_image

    def _get_ib_image(self, image_settings=ImageSettings):
        # At first make sure the beam is ON
        self.connection.FIB.Beam.On()
        # important: stop the scanning before we start scanning or before automatic procedures,
        # even before we configure the detectors
        self.connection.FIB.Scan.Stop()
        # Select the detector for image i.e.:
        # 1. assign the detector to a channel
        # 2. enable the channel for acquisition
        self.connection.FIB.Detector.Set(
            0, self.ion_detector_active, Bpp.Grayscale_8_bit
        )

        dwell_time = image_settings.dwell_time * constants.SI_TO_NANO

        # resolution
        numbers = re.findall(r"\d+", image_settings.resolution)
        imageWidth = int(numbers[0])
        imageHeight = int(numbers[1])

        self.connection.FIB.Optics.SetViewfield(image_settings.hfw * 1000)

        image = self.connection.FIB.Scan.AcquireImageFromChannel(
            0, imageWidth, imageHeight, dwell_time
        )

        microscope_state = MicroscopeState(
            timestamp=datetime.datetime.timestamp(datetime.datetime.now()),
            absolute_position=FibsemStagePosition(
                x=float(image.Header["FIB"]["StageX"]),
                y=float(image.Header["FIB"]["StageY"]),
                z=float(image.Header["FIB"]["StageZ"]),
                r=float(image.Header["FIB"]["StageRotation"]),
                t=float(image.Header["FIB"]["StageTilt"]),
                coordinate_system="Raw",
            ),
            eb_settings=BeamSettings(beam_type=BeamType.ELECTRON),
            ib_settings=BeamSettings(
                beam_type=BeamType.ION,
                working_distance=float(image.Header["FIB"]["WD"]),
                beam_current=float(image.Header["FIB"]["BeamCurrent"]),
                resolution="{}x{}".format(imageWidth, imageHeight),
                dwell_time=float(image.Header["FIB"]["DwellTime"]),
                stigmation=Point(
                    float(image.Header["FIB"]["StigmatorX"]),
                    float(image.Header["FIB"]["StigmatorY"]),
                ),
                shift=Point(
                    float(image.Header["FIB"]["ImageShiftX"]),
                    float(image.Header["FIB"]["ImageShiftY"]),
                ),
            ),
        )

        fibsem_image = FibsemImage.fromTescanImage(
            image, deepcopy(image_settings), microscope_state
        )

        res = fibsem_image.data.shape

        fibsem_image.metadata.image_settings.resolution = str(res[1]) + "x" + str(res[0])

        return fibsem_image

    def last_image(self, beam_type: BeamType.ELECTRON) -> FibsemImage:
        if beam_type == BeamType.ELECTRON:
            image = self.last_image_eb
        elif beam_type == BeamType.ION:
            image = self.last_image_ib
        else:
            raise Exception("Beam type error")
        return image

    def get_stage_position(self):
        x, y, z, r, t = self.connection.Stage.GetPosition()
        stage_position = FibsemStagePosition(x/1000, y/1000, z/1000, r*constants.DEGREES_TO_RADIANS, t*constants.DEGREES_TO_RADIANS, "raw")
        return stage_position

    def get_current_microscope_state(self) -> MicroscopeState:
        """Get the current microscope state

        Returns:
            MicroscopeState: current microscope state
        """
        image_eb = self.last_image(BeamType.ELECTRON)
        image_ib = self.last_image(BeamType.ION)

        if image_ib is not None:
            ib_settings = (
                BeamSettings(
                    beam_type=BeamType.ION,
                    working_distance=image_ib.metadata.microscope_state.ib_settings.working_distance,
                    beam_current=self.connection.FIB.Beam.ReadProbeCurrent() / (10e12),
                    hfw=self.connection.FIB.Optics.GetViewfield() / 1000,
                    resolution=image_ib.metadata.image_settings.resolution,
                    dwell_time=image_ib.metadata.image_settings.dwell_time,
                    stigmation=image_ib.metadata.microscope_state.ib_settings.stigmation,
                    shift=image_ib.metadata.microscope_state.ib_settings.shift,
                ),
            )
        else:
            ib_settings = BeamSettings(BeamType.ION)

        if image_eb is not None:
            eb_settings = BeamSettings(
                beam_type=BeamType.ELECTRON,
                working_distance=self.connection.SEM.Optics.GetWD() / 1000,
                beam_current=self.connection.SEM.Beam.GetCurrent() / (10e6),
                hfw=self.connection.SEM.Optics.GetViewfield() / 1000,
                resolution=image_eb.metadata.image_settings.resolution,  # TODO fix these empty parameters
                dwell_time=image_eb.metadata.image_settings.dwell_time,
                stigmation=image_eb.metadata.microscope_state.eb_settings.stigmation,
                shift=image_eb.metadata.microscope_state.eb_settings.shift,
            )
        else:
            eb_settings = BeamSettings(BeamType.ELECTRON)

        current_microscope_state = MicroscopeState(
            timestamp=datetime.datetime.timestamp(datetime.datetime.now()),
            # get absolute stage coordinates (RAW)
            absolute_position=self.get_stage_position(),
            # electron beam settings
            eb_settings=eb_settings,
            # ion beam settings
            ib_settings=ib_settings,
        )

        return current_microscope_state

    def autocontrast(self, beam_type: BeamType) -> None:
        if beam_type.name == BeamType.ELECTRON:
            self.connection.SEM.Detector.StartAutoSignal(0)
        if beam_type.name == BeamType.ION:
            self.connection.FIB.Detector.AutoSignal(0)

    def reset_beam_shifts(self):
        pass

    def move_stage_absolute(self, position: FibsemStagePosition):
        """Move the stage to the specified coordinates.

        Args:
            x (float): The x-coordinate to move to (in meters).
            y (float): The y-coordinate to move to (in meters).
            z (float): The z-coordinate to move to (in meters).
            r (float): The rotation to apply (in degrees).
            tx (float): The x-axis tilt to apply (in degrees).

        Returns:
            None
        """
        limits = self.connection.Stage.GetLimits()
        if position.x < limits[0]:
            position.x = limits[0]
            raise Exception("Lower x limit reached")
        elif position.x > limits[1]:
            position.x = limits[1]
            raise Exception("Upper x limit reached")
        if position.y < limits[2]:
            position.y = limits[2]
            raise Exception("Lower y limit reached")
        elif position.y > limits[3]:
            position.y = limits[3]
            raise Exception("Upper y limit reached")
        if position.z < limits[4]:
            position.z = limits[4]
            raise Exception("Lower z limit reached")
        elif position.z > limits[5]:
            position.z = limits[5]
            raise Exception("Upper z limit reached")
        if position.r < limits[6]:
            position.r = limits[6]
            raise Exception("Lower r limit reached")
        elif position.r > limits[7]:
            position.r = limits[7]
            raise Exception("Upper r limit reached")
        if position.t < limits[8]:
            position.t = limits[8]
            raise Exception("Lower t limit reached")
        elif position.t > limits[9]:
            position.t = limits[9]
            raise Exception("Upper t limit reached")

        self.connection.Stage.MoveTo(
            position.x * constants.METRE_TO_MILLIMETRE,
            position.y * constants.METRE_TO_MILLIMETRE,
            position.z * constants.METRE_TO_MILLIMETRE,
            position.r * constants.RADIANS_TO_DEGREES,
            position.t * constants.RADIANS_TO_DEGREES,
        )

    def move_stage_relative(
        self,
        position: FibsemStagePosition,
    ):
        """Move the stage by the specified relative move.

        Args:
            x (float): The x-coordinate to move to (in meters).
            y (float): The y-coordinate to move to (in meters).
            z (float): The z-coordinate to move to (in meters).
            r (float): The rotation to apply (in degrees).
            tx (float): The x-axis tilt to apply (in degrees).

        Returns:
            None
        """

        current_position = self.get_stage_position()
        x_m = current_position.x
        y_m = current_position.y
        z_m = current_position.z
        new_position = FibsemStagePosition(
            x_m + position.x,
            y_m + position.y,
            z_m + position.z,
            current_position.r + position.r,
            current_position.t + position.t,
            "raw",
        )
        self.move_stage_absolute(new_position)

    def stable_move(
        self,
        settings: MicroscopeSettings,
        dx: float,
        dy: float,
        beam_type: BeamType,
    ) -> None:
        """Calculate the corrected stage movements based on the beam_type, and then move the stage relatively.

        Args:
            microscope (Tescan Automation): Tescan microscope instance
            settings (MicroscopeSettings): microscope settings
            dx (float): distance along the x-axis (image coordinates)
            dy (float): distance along the y-axis (image coordinates)
        """
        wd = self.connection.SEM.Optics.GetWD()

        # calculate stage movement
        x_move = x_corrected_stage_movement(dx)
        yz_move = y_corrected_stage_movement(
            self,
            settings=settings,
            expected_y=dy,
            beam_type=beam_type,
        )

        # move stage
        stage_position = FibsemStagePosition(
            x=x_move.x, y=yz_move.y, z=yz_move.z
        )
        logging.info(f"moving stage ({beam_type.name}): {stage_position}")
        self.move_stage_relative(stage_position.x, stage_position.y, 0, 0, 0)

        # adjust working distance to compensate for stage movement
        self.connection.SEM.Optics.SetWD(wd)
        # self.connection.specimen.stage.link() # TODO how to link for TESCAN?

        return

    def setup_milling(self, application_file: str, patterning_mode: str, hfw: float,mill_settings: MillingSettings):
        
        fieldsize = 0.00025 #application_file.ajhsd or mill settings
        beam_current = mill_settings.milling_current
        spot_size = 5.0e-8 # application_file
        rate = 3.0e-3 ## in application file called Volume per Dose (m3/C)
        dwell_time = 1.0e-6 # in seconds ## in application file
        
        if patterning_mode == "Serial":
            parallel_mode = False
        else:
            parallel_mode = True
        

        layer_settings = IEtching(syncWriteField=False,
        writeFieldSize=hfw,
        beamCurrent=beam_current,
        spotSize=spot_size,
        rate=rate,
        dwellTime=dwell_time,
        parallel=parallel_mode,
        )

        self.layer = self.connection.DrawBeam.Layer('Layer',layer_settings)



    def run_milling(self, milling_current: float, asynch: bool = False):
        
        self.connection.FIB.Beam.On()
        self.connection.DrawBeam.LoadLayer(self.layer)
        self.connection.DrawBeam.Start()

    def finish_milling(self, imaging_current: float):
        self.connection.DrawBeam.UnloadLayer()

    def draw_rectangle(self, mill_settings: MillingSettings):
        
        centre_x = mill_settings.centre_x
        centre_y = mill_settings.centre_y
        depth = mill_settings.depth
        width = mill_settings.width
        height = mill_settings.height
        rotation = mill_settings.rotation # CHECK UNITS (TESCAN Takes Degrees)




        self.layer.addRectangleFilled(CenterX=centre_x,CenterY=centre_y,Depth=depth,Width=width,Height=height,Rotation=rotation)

    def set_microscope_state(
        self, microscope_state: MicroscopeState
    ):
        """Reset the microscope state to the provided state"""

        logging.info(f"restoring microscope state...")

        # move to position
        self.move_stage_absolute(
            stage_position=microscope_state.absolute_position
        )

        # restore electron beam
        logging.info(f"restoring electron beam settings...")
        self.connection.SEM.Optics.SetWD(microscope_state.eb_settings.working_distance) 

        self.connection.SEM.Beam.SetCurrent(microscope_state.eb_settings.beam_current)

        self.connection.SEM.Optics.SetViewfield(microscope_state.eb_settings.hfw)

        # microscope.beams.electron_beam.stigmator.value = (
        #     microscope_state.eb_settings.stigmation
        # )

        # restore ion beam
        logging.info(f"restoring ion beam settings...")

        self.connection.FIB.Optics.SetViewfield(
            microscope_state.ib_settings.hfw
        )

        # microscope.beams.ion_beam.stigmator.value = microscope_state.ib_settings.stigmation

        logging.info(f"microscope state restored")
        return  
       



######################################## Helper functions ########################################






def rotation_angle_is_larger(angle1: float, angle2: float, atol: float = 90) -> bool:
    """Check the rotation angles are large

    Args:
        angle1 (float): angle1 (radians)
        angle2 (float): angle2 (radians)
        atol : tolerance (degrees)

    Returns:
        bool: rotation angle is larger than atol
    """

    return angle_difference(angle1, angle2) > (np.deg2rad(atol))


def rotation_angle_is_smaller(angle1: float, angle2: float, atol: float = 5) -> bool:
    """Check the rotation angles are large

    Args:
        angle1 (float): angle1 (radians)
        angle2 (float): angle2 (radians)
        atol : tolerance (degrees)

    Returns:
        bool: rotation angle is smaller than atol
    """

    return angle_difference(angle1, angle2) < (np.deg2rad(atol))


def angle_difference(angle1: float, angle2: float) -> float:
    """Return the difference between two angles, accounting for greater than 360, less than 0 angles

    Args:
        angle1 (float): angle1 (radians)
        angle2 (float): angle2 (radians)

    Returns:
        float: _description_
    """
    angle1 %= 2 * np.pi
    angle2 %= 2 * np.pi

    large_angle = np.max([angle1, angle2])
    small_angle = np.min([angle1, angle2])

    return min((large_angle - small_angle), ((2 * np.pi + small_angle - large_angle)))

def x_corrected_stage_movement(
    expected_x: float,
) -> FibsemStagePosition:
    """Calculate the x corrected stage movement.

    Args:
        expected_x (float): distance along x-axis

    Returns:
        StagePosition: x corrected stage movement (relative position)
    """
    return FibsemStagePosition(x=expected_x, y=0, z=0)


def y_corrected_stage_movement(
    microscope: FibsemMicroscope,
    settings: MicroscopeSettings,
    expected_y: float,
    beam_type: BeamType = BeamType.ELECTRON,
) -> FibsemStagePosition:
    """Calculate the y corrected stage movement, corrected for the additional tilt of the sample holder (pre-tilt angle).

    Args:
        microscope (SdbMicroscopeClient, optional): autoscript microscope instance
        settings (MicroscopeSettings): microscope settings
        expected_y (float, optional): distance along y-axis.
        beam_type (BeamType, optional): beam_type to move in. Defaults to BeamType.ELECTRON.

    Returns:
        StagePosition: y corrected stage movement (relative position)
    """

    # TODO: replace with camera matrix * inverse kinematics
    # TODO: replace stage_tilt_flat_to_electron with pre-tilt

    # all angles in radians
    stage_tilt_flat_to_electron = np.deg2rad(
        settings.system.stage.tilt_flat_to_electron
    )
    stage_tilt_flat_to_ion = np.deg2rad(settings.system.stage.tilt_flat_to_ion)

    stage_rotation_flat_to_eb = np.deg2rad(
        settings.system.stage.rotation_flat_to_electron
    ) % (2 * np.pi)
    stage_rotation_flat_to_ion = np.deg2rad(
        settings.system.stage.rotation_flat_to_ion
    ) % (2 * np.pi)

    # current stage position
    current_stage_position = microscope.get_stage_position()
    stage_rotation = current_stage_position.r % (2 * np.pi)
    stage_tilt = current_stage_position.t

    PRETILT_SIGN = 1.0
    # pretilt angle depends on rotation
    if rotation_angle_is_smaller(stage_rotation, stage_rotation_flat_to_eb, atol=5):
        PRETILT_SIGN = 1.0
    if rotation_angle_is_smaller(stage_rotation, stage_rotation_flat_to_ion, atol=5):
        PRETILT_SIGN = -1.0

    corrected_pretilt_angle = PRETILT_SIGN * stage_tilt_flat_to_electron

    # perspective tilt adjustment (difference between perspective view and sample coordinate system)
    if beam_type == BeamType.ELECTRON:
        perspective_tilt_adjustment = -corrected_pretilt_angle
        SCALE_FACTOR = 1.0  # 0.78342  # patented technology
    elif beam_type == BeamType.ION:
        perspective_tilt_adjustment = -corrected_pretilt_angle - stage_tilt_flat_to_ion
        SCALE_FACTOR = 1.0

    # the amount the sample has to move in the y-axis
    y_sample_move = (expected_y * SCALE_FACTOR) / np.cos(
        stage_tilt + perspective_tilt_adjustment
    )

    # the amount the stage has to move in each axis
    y_move = y_sample_move * np.cos(corrected_pretilt_angle)
    z_move = y_sample_move * np.sin(corrected_pretilt_angle)

    return FibsemStagePosition(x=0, y=y_move, z=z_move)