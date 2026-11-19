"""
Python ctypes binding for Steam Audio DLL
Provides 3D audio positioning, reverb, and 16-bit sample output
"""

import ctypes
import os
import struct
import threading
from ctypes import c_bool, c_int, c_float, POINTER, byref

try:
	from logHandler import log
except ImportError:
	import logging as log

# Global mutex for thread-safe DLL access
_steam_audio_mutex = threading.Lock()

# Keep references to loaded DLLs to prevent unloading
_loaded_dlls = []


def _preload_dependencies(addon_dir):
	"""Pre-load dependency DLLs before loading steam_audio.dll"""
	global _loaded_dlls

	phonon_path = os.path.join(addon_dir, "phonon.dll")
	if os.path.exists(phonon_path):
		try:
			# Add the addon directory to the DLL search path
			if hasattr(os, 'add_dll_directory'):
				os.add_dll_directory(addon_dir)

			# Pre-load phonon.dll
			phonon_dll = ctypes.CDLL(phonon_path)
			_loaded_dlls.append(phonon_dll)
			log.debug(f"Pre-loaded phonon.dll from: {phonon_path}")
		except Exception as e:
			log.warning(f"Could not pre-load phonon.dll: {e}")


# Define ctypes for the DLL functions
class SteamAudio:
	def __init__(self, dll_path=None):
		"""Initialize Steam Audio wrapper

		Args:
		    dll_path: Path to steam_audio.dll. If None, looks in addon directory.
		"""
		self.dll = None
		self.initialized = False

		if dll_path is None:
			# Look for DLL in the parent directory (audiothemes/)
			addon_dir = os.path.dirname(os.path.dirname(__file__))
			dll_path = os.path.join(addon_dir, "steam_audio.dll")
		else:
			addon_dir = os.path.dirname(dll_path)

		if not os.path.exists(dll_path):
			raise FileNotFoundError(f"Steam Audio DLL not found at: {dll_path}")

		try:
			# Pre-load dependency DLLs first
			_preload_dependencies(addon_dir)

			self.dll = ctypes.CDLL(dll_path)
			self._setup_function_signatures()
			log.debug(f"Steam Audio DLL loaded from: {dll_path}")
		except Exception as e:
			log.error(f"Failed to load Steam Audio DLL: {e}")
			raise

	def _setup_function_signatures(self):
		"""Setup ctypes function signatures for all DLL functions"""

		# bool initialize_steam_audio(int samplingrate, int framesize)
		self.dll.initialize_steam_audio.argtypes = [c_int, c_int]
		self.dll.initialize_steam_audio.restype = c_bool

		# void cleanup_steam_audio()
		self.dll.cleanup_steam_audio.argtypes = []
		self.dll.cleanup_steam_audio.restype = None

		# bool set_reverb_settings(float room_size, float damping, float wet_level, float dry_level, float width)
		self.dll.set_reverb_settings.argtypes = [
			c_float,
			c_float,
			c_float,
			c_float,
			c_float,
		]
		self.dll.set_reverb_settings.restype = c_bool

		# bool process_sound(const float* input_buffer, int input_length, float angle_x, float angle_y, int16_t** output_buffer, int* output_length)
		self.dll.process_sound.argtypes = [
			POINTER(c_float),  # input_buffer
			c_int,  # input_length
			c_float,  # angle_x
			c_float,  # angle_y
			POINTER(POINTER(ctypes.c_int16)),  # output_buffer
			POINTER(c_int),  # output_length
		]
		self.dll.process_sound.restype = c_bool

		# bool apply_reverb(const int16_t* input_buffer, int input_length, int16_t** output_buffer, int* output_length)
		self.dll.apply_reverb.argtypes = [
			POINTER(ctypes.c_int16),  # input_buffer
			c_int,  # input_length
			POINTER(POINTER(ctypes.c_int16)),  # output_buffer
			POINTER(c_int),  # output_length
		]
		self.dll.apply_reverb.restype = c_bool

		# bool process_sound_full(const float* input_buffer, int input_length, float angle_x, float angle_y, float volume, bool use_reverb, int16_t** output_buffer, int* output_length)
		self.dll.process_sound_full.argtypes = [
			POINTER(c_float),  # input_buffer
			c_int,  # input_length
			c_float,  # angle_x
			c_float,  # angle_y
			c_float,  # volume
			c_bool,  # use_reverb
			POINTER(POINTER(ctypes.c_int16)),  # output_buffer
			POINTER(c_int),  # output_length
		]
		self.dll.process_sound_full.restype = c_bool

		# Windows Spatial Audio (spatial.cpp). These have their own internal locking and
		# must NOT take _steam_audio_mutex.
		self.dll.spatial_init.argtypes = [ctypes.c_wchar_p]
		self.dll.spatial_init.restype = c_int
		self.dll.spatial_play.argtypes = [POINTER(c_float), c_int, c_int, c_float, c_float, c_float]
		self.dll.spatial_play.restype = c_bool
		self.dll.spatial_is_active.argtypes = []
		self.dll.spatial_is_active.restype = c_bool
		self.dll.spatial_stop_all.argtypes = []
		self.dll.spatial_stop_all.restype = None
		self.dll.spatial_shutdown.argtypes = []
		self.dll.spatial_shutdown.restype = None

		# void free_output_sound(int16_t* buffer)
		self.dll.free_output_sound.argtypes = [POINTER(ctypes.c_int16)]
		self.dll.free_output_sound.restype = None

	def initialize(self, sample_rate=44100, frame_size=1024):
		"""Initialize Steam Audio with given parameters

		Args:
		    sample_rate: Audio sample rate in Hz (default: 44100)
		    frame_size: Audio frame size in samples (default: 1024)

		Returns:
		    bool: True if initialization successful, False otherwise
		"""
		if self.initialized:
			log.debug("Steam Audio already initialized")
			return True

		with _steam_audio_mutex:
			success = self.dll.initialize_steam_audio(sample_rate, frame_size)
			if success:
				self.initialized = True
				self.sample_rate = sample_rate
				self.frame_size = frame_size
				log.debug(
					f"Steam Audio initialized: {sample_rate}Hz, {frame_size} samples"
				)
			else:
				log.error("Failed to initialize Steam Audio")

		return success

	def cleanup(self):
		"""Cleanup Steam Audio resources"""
		try:
			self.dll.spatial_shutdown()
		except Exception:
			pass
		if self.initialized:
			with _steam_audio_mutex:
				self.dll.cleanup_steam_audio()
				self.initialized = False
			log.debug("Steam Audio cleaned up")

	def set_reverb_settings(self, room_size, damping, wet_level, dry_level, width):
		"""Configure verblib reverb settings

		Args:
		    room_size: Room size (0.0 to 1.0)
		    damping: Damping amount (0.0 to 1.0,)
		    wet_level: Wet signal level (0.0 to 1.0, default)
		    dry_level: Dry signal level (0.0 to 1.0)
		    width: Stereo width (0.0 to 1.0)

		Returns:
		    bool: True if successful
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return False

		with _steam_audio_mutex:
			success = self.dll.set_reverb_settings(
				room_size, damping, wet_level, dry_level, width
			)
			if success:
				log.debug(
					f"Reverb settings updated: room_size={room_size}, damping={damping}, wet_level={wet_level}, dry_level={dry_level}, width={width}"
				)
			else:
				log.error("Failed to set reverb settings")

		return success

	# Status codes from spatial_init
	SPATIAL_OK = 0
	SPATIAL_E_DISABLED = -4

	def spatial_init(self, device_id=None):
		"""Open Windows Spatial Audio on an endpoint ID (None = default device).

		Returns 0 on success or a negative status (-4 = spatial sound is off for the device).
		"""
		return int(self.dll.spatial_init(device_id or None))

	def spatial_play(self, input_ctypes, input_length, sample_rate, angle_x, angle_y, volume):
		"""Queue a mono sound as a spatial audio object. False if spatial output isn't running."""
		input_ptr = ctypes.cast(input_ctypes, POINTER(c_float))
		return bool(self.dll.spatial_play(
			input_ptr, input_length, int(sample_rate), c_float(angle_x), c_float(angle_y), c_float(volume)
		))

	def spatial_is_active(self):
		return bool(self.dll.spatial_is_active())

	def spatial_shutdown(self):
		self.dll.spatial_shutdown()

	def process_sound(self, input_buffer, angle_x, angle_y):
		"""Process audio with 3D positioning (without reverb)

		Args:
		    input_buffer: list of float32 mono audio samples
		    angle_x: Horizontal angle in degrees (-90 to 90)
		    angle_y: Vertical angle in degrees (-90 to 90)

		Returns:
		    bytes: Stereo 16-bit audio samples as bytes, or None if failed
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return None

		# Convert list to ctypes array
		input_array = (c_float * len(input_buffer))(*input_buffer)
		input_ptr = ctypes.cast(input_array, POINTER(c_float))
		input_length = len(input_buffer)

		# Prepare output parameters
		output_buffer_ptr = POINTER(ctypes.c_int16)()
		output_length = c_int()

		# Call the DLL function
		with _steam_audio_mutex:
			success = self.dll.process_sound(
				input_ptr,
				input_length,
				c_float(angle_x),
				c_float(angle_y),
				byref(output_buffer_ptr),
				byref(output_length),
			)

		if not success or not output_buffer_ptr:
			log.error("Failed to process sound")
			return None

		try:
			# Convert output to bytes via direct memory copy
			output_samples = output_length.value
			if output_samples > 0:
				num_bytes = output_samples * 2  # 2 bytes per int16
				result = (ctypes.c_char * num_bytes)()
				ctypes.memmove(result, output_buffer_ptr, num_bytes)

				# Free the output buffer
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)

				return bytes(result)
			else:
				return b""

		except Exception as e:
			log.error(f"Error processing output buffer: {e}")
			# Make sure to free the buffer even if there's an error
			if output_buffer_ptr:
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)
			return None

	def make_ctypes_buffer(self, float_samples):
		"""Pre-convert a list of float samples to a ctypes array for fast reuse.

		Args:
		    float_samples: list of float32 mono audio samples

		Returns:
		    tuple of (ctypes float array, int length)
		"""
		length = len(float_samples)
		arr = (c_float * length)(*float_samples)
		return arr, length

	def process_sound_full(self, input_ctypes, input_length, angle_x, angle_y, volume, use_reverb):
		"""Process audio with 3D positioning, volume, and reverb in one DLL call.

		Args:
		    input_ctypes: ctypes float array (from make_ctypes_buffer)
		    input_length: number of float samples
		    angle_x: Horizontal angle in degrees (-90 to 90)
		    angle_y: Vertical angle in degrees (-90 to 90)
		    volume: Volume multiplier (0.0 to ~1.25)
		    use_reverb: Whether to apply reverb

		Returns:
		    bytes: Stereo 16-bit audio samples, or None if failed
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return None

		input_ptr = ctypes.cast(input_ctypes, POINTER(c_float))

		# Prepare output parameters
		output_buffer_ptr = POINTER(ctypes.c_int16)()
		output_length = c_int()

		# Single DLL call does volume + 3D + reverb
		with _steam_audio_mutex:
			success = self.dll.process_sound_full(
				input_ptr,
				input_length,
				c_float(angle_x),
				c_float(angle_y),
				c_float(volume),
				use_reverb,
				byref(output_buffer_ptr),
				byref(output_length),
			)

		if not success or not output_buffer_ptr:
			log.error("Failed to process sound (full)")
			return None

		try:
			output_samples = output_length.value
			if output_samples > 0:
				num_bytes = output_samples * 2
				result = (ctypes.c_char * num_bytes)()
				ctypes.memmove(result, output_buffer_ptr, num_bytes)

				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)

				return bytes(result)
			else:
				return b""

		except Exception as e:
			log.error(f"Error processing full output buffer: {e}")
			if output_buffer_ptr:
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)
			return None

	def apply_reverb(self, input_buffer):
		"""Apply reverb to stereo 16-bit audio

		Args:
		    input_buffer: bytes of stereo 16-bit audio samples

		Returns:
		    bytes: Stereo 16-bit audio samples with reverb, or None if failed
		"""
		if not self.initialized:
			log.error("Steam Audio not initialized")
			return None

		# Pass input bytes directly as int16 pointer (no unpacking needed)
		input_length = len(input_buffer) // 2  # number of int16 samples
		input_array = (ctypes.c_char * len(input_buffer)).from_buffer_copy(input_buffer)
		input_ptr = ctypes.cast(input_array, POINTER(ctypes.c_int16))

		# Prepare output parameters
		output_buffer_ptr = POINTER(ctypes.c_int16)()
		output_length = c_int()

		# Call the DLL function
		with _steam_audio_mutex:
			success = self.dll.apply_reverb(
				input_ptr, input_length, byref(output_buffer_ptr), byref(output_length)
			)

		if not success or not output_buffer_ptr:
			log.error("Failed to apply reverb")
			return None

		try:
			# Convert output to bytes via direct memory copy
			output_samples = output_length.value
			if output_samples > 0:
				num_bytes = output_samples * 2  # 2 bytes per int16
				result = (ctypes.c_char * num_bytes)()
				ctypes.memmove(result, output_buffer_ptr, num_bytes)

				# Free the output buffer
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)

				return bytes(result)
			else:
				return b""

		except Exception as e:
			log.error(f"Error processing reverb output buffer: {e}")
			# Make sure to free the buffer even if there's an error
			if output_buffer_ptr:
				with _steam_audio_mutex:
					self.dll.free_output_sound(output_buffer_ptr)
			return None

	def __del__(self):
		"""Cleanup when object is destroyed"""
		if hasattr(self, "initialized") and self.initialized:
			self.cleanup()


# Global instance for easy access
_steam_audio_instance = None


def get_steam_audio():
	"""Get the global Steam Audio instance"""
	global _steam_audio_instance
	if _steam_audio_instance is None:
		_steam_audio_instance = SteamAudio()
	return _steam_audio_instance


def initialize_steam_audio(sample_rate=44100, frame_size=1024):
	"""Initialize the global Steam Audio instance"""
	steam_audio = get_steam_audio()
	return steam_audio.initialize(sample_rate, frame_size)


def cleanup_steam_audio():
	"""Cleanup the global Steam Audio instance"""
	global _steam_audio_instance
	if _steam_audio_instance:
		_steam_audio_instance.cleanup()
		_steam_audio_instance = None
