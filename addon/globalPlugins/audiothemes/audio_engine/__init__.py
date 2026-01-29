# coding: utf-8

# Audio engine module for Audio Themes NVDA Add-on
# Uses SteamAudio for 3D audio positioning
# Based on Unspoken-NG by Bryan Smart, Austin Hicks, and Mason Armstrong

import os
import time
import threading
import dataclasses
import wave
import struct

import config
import nvwave
import NVDAObjects
import synthDriverHandler

try:
    from logHandler import log
except ImportError:
    import logging as log

from . import steam_audio


def clamp(value, min_value, max_value):
    """Clamp value between min and max."""
    return max(min(value, max_value), min_value)


@dataclasses.dataclass
class SteamAudioPlayer:
    """Audio player using SteamAudio for 3D positioning.

    Replaces UnspokenPlayer with modern SteamAudio-based implementation.
    Compatible with AudioTheme's sound loading interface.
    """

    audio3d: bool = True
    use_in_say_all: bool = True
    speak_roles: bool = True
    use_synth_volume: bool = True
    volume: int = 100
    use_reverb: bool = True

    def __post_init__(self):
        # Initialize Steam Audio
        self.steam_audio = steam_audio.get_steam_audio()
        if not self.steam_audio.initialize():
            log.error("Failed to initialize Steam Audio")
            raise RuntimeError("Steam Audio initialization failed")

        # Configure default reverb settings
        self._configure_reverb()

        # Initialize WavePlayer for audio output (stereo, 44100Hz, 16-bit)
        self._create_wave_player()

        # State tracking
        self._last_played_object = None
        self._last_played_time = 0
        self._last_played_sound = None
        self._wave_player_lock = threading.Lock()
        self._sound_generation = 0

        # Desktop dimension caching
        self._cached_desktop_size = None
        self._desktop_cache_time = 0
        self._cached_volume = 1.0
        self._update_desktop_cache()
        self._update_volume_cache()

        # Display parameters (in degrees)
        self._display_width = 180.0
        self._display_height_min = -40.0
        self._display_height_magnitude = 50.0

    def _configure_reverb(self):
        """Configure reverb settings from config if available."""
        try:
            conf = config.conf.get("audiothemes", {})
            room_size = conf.get("RoomSize", 10) / 100.0
            damping = conf.get("Damping", 100) / 100.0
            wet_level = conf.get("WetLevel", 9) / 100.0
            dry_level = conf.get("DryLevel", 30) / 100.0
            width = conf.get("Width", 100) / 100.0
            self.steam_audio.set_reverb_settings(
                room_size, damping, wet_level, dry_level, width
            )
        except Exception as e:
            log.debug(f"Using default reverb settings: {e}")
            self.steam_audio.set_reverb_settings(0.1, 1.0, 0.09, 0.3, 1.0)

    def _create_wave_player(self):
        """Create nvwave WavePlayer for audio output."""
        self.wave_player = nvwave.WavePlayer(
            channels=2,
            samplesPerSec=44100,
            bitsPerSample=16,
            outputDevice=config.conf["audio"]["outputDevice"],
        )

    def make_sound_object(self, filename):
        """Load a WAV audio file and return a dict with float32 mono samples.

        Args:
            filename: Path to WAV audio file

        Returns:
            dict with 'data' (list of float32 samples) and 'sample_rate'
        """
        try:
            with wave.open(filename, "rb") as wav_file:
                frames = wav_file.readframes(wav_file.getnframes())
                sample_width = wav_file.getsampwidth()
                channels = wav_file.getnchannels()
                sample_rate = wav_file.getframerate()

                # Convert to float32 samples
                if sample_width == 2:  # 16-bit
                    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                    float_samples = [s / 32768.0 for s in samples]
                elif sample_width == 1:  # 8-bit
                    samples = struct.unpack(f"<{len(frames)}B", frames)
                    float_samples = [(s - 128) / 128.0 for s in samples]
                else:
                    raise ValueError(f"Unsupported sample width: {sample_width}")

                # Convert to mono if stereo
                if channels == 2:
                    float_samples = [float_samples[i] for i in range(0, len(float_samples), 2)]

                return {"data": float_samples, "sample_rate": sample_rate}

        except Exception as e:
            log.error(f"Failed to load audio file {filename}: {e}")
            return None

    def _compute_volume(self):
        """Compute volume based on settings."""
        if not self.use_synth_volume:
            return clamp(self.volume / 100.0, 0.0, 1.0)
        driver = synthDriverHandler.getSynth()
        volume = getattr(driver, "volume", 100) / 100.0
        volume = clamp(volume, 0.0, 1.0)
        # Boost volume slightly when using HRTF (3D audio)
        return volume + 0.25 if self.audio3d else volume

    def _update_volume_cache(self):
        """Update cached volume value."""
        self._cached_volume = self._compute_volume()

    def _update_desktop_cache(self):
        """Update cached desktop dimensions."""
        try:
            desktop = NVDAObjects.api.getDesktopObject()
            self._cached_desktop_size = (desktop.location[2], desktop.location[3])
            self._desktop_cache_time = time.time()
        except Exception:
            self._cached_desktop_size = (1920, 1080)  # Fallback
            self._desktop_cache_time = time.time()

    def _get_desktop_size(self):
        """Get desktop dimensions, refreshing cache if stale (>5 seconds)."""
        if time.time() - self._desktop_cache_time > 5.0:
            self._update_desktop_cache()
        return self._cached_desktop_size

    def play(self, obj, sound, role=None):
        """Play a sound with 3D positioning based on object location.

        Args:
            obj: NVDA object with location property
            sound: Dict returned by make_sound_object()
            role: The controlTypes role being played (optional, for future use)
        """
        if sound is None:
            return

        curtime = time.time()
        if curtime - self._last_played_time < 0.1 and obj is self._last_played_object:
            return

        self._last_played_object = obj
        self._last_played_time = curtime

        # Extract object properties on main thread (COM threading requirement)
        params = self._extract_sound_params(obj, sound)
        if params is None:
            return

        # Play in background thread (interrupts previous sound for responsive navigation)
        self._sound_generation += 1
        my_generation = self._sound_generation

        def play_async():
            try:
                self._play_sound_async(params, my_generation)
            except Exception as e:
                log.debug(f"Error in async playback: {e}")

        threading.Thread(target=play_async, daemon=True).start()

    def play_queued(self, obj, sound, role=None):
        """Play a sound without interrupting current playback.

        Used for container sounds that should queue up before the main sound.

        Args:
            obj: NVDA object with location property
            sound: Dict returned by make_sound_object()
            role: The controlTypes role being played (optional)
        """
        if sound is None:
            return

        # Extract object properties on main thread (COM threading requirement)
        params = self._extract_sound_params(obj, sound)
        if params is None:
            return

        # Play in background thread (queued, doesn't interrupt)
        def play_async():
            try:
                self._play_sound_queued(params)
            except Exception as e:
                log.debug(f"Error in queued playback: {e}")

        threading.Thread(target=play_async, daemon=True).start()

    def _extract_sound_params(self, obj, sound):
        """Extract parameters needed for sound playback from NVDA object.

        Must be called from main thread due to COM threading requirements.
        """
        # Get coordinate bounds of desktop
        desktop_max_x, desktop_max_y = self._get_desktop_size()

        # Get location of the object (handle None objects - play centered)
        obj_location = getattr(obj, 'location', None) if obj else None
        if self.audio3d and obj_location is not None:
            obj_x = obj_location[0] + (obj_location[2] / 2.0)
            obj_y = obj_location[1] + (obj_location[3] / 2.0)
        else:
            # Objects without location are centered
            obj_x = desktop_max_x / 2.0
            obj_y = desktop_max_y / 2.0

        # Scale object position to audio display
        angle_x = ((obj_x - desktop_max_x / 2.0) / desktop_max_x) * self._display_width
        percent = (desktop_max_y - obj_y) / desktop_max_y
        angle_y = self._display_height_magnitude * percent + self._display_height_min

        # Clamp angles to valid ranges
        angle_x = clamp(angle_x, -90.0, 90.0)
        angle_y = clamp(angle_y, -90.0, 90.0)

        return {
            "sound_data": sound["data"],
            "angle_x": angle_x,
            "angle_y": angle_y,
            "volume": self._cached_volume,
        }

    def _play_sound_async(self, params, generation):
        """Process and play sound on background thread."""
        sound_data = params["sound_data"]
        angle_x = params["angle_x"]
        angle_y = params["angle_y"]
        volume = params["volume"]

        # Adjust volume
        adjusted_audio = [sample * volume for sample in sound_data]

        # Process with Steam Audio for 3D positioning
        processed_audio = self.steam_audio.process_sound(
            adjusted_audio, angle_x, angle_y
        )
        if not processed_audio:
            log.debug("Failed to process sound with Steam Audio")
            return

        # Apply reverb if enabled
        final_audio = processed_audio
        if self.use_reverb:
            try:
                conf = config.conf.get("audiothemes", {})
                if conf.get("use_reverb", True):
                    reverb_audio = self.steam_audio.apply_reverb(processed_audio)
                    if reverb_audio:
                        final_audio = reverb_audio
            except Exception:
                pass

        # Check if this sound has been superseded
        if generation != self._sound_generation:
            return

        # Stop previous sound and play new one
        self.wave_player.stop()

        with self._wave_player_lock:
            if generation != self._sound_generation:
                return
            self.wave_player.feed(final_audio)

    def _play_sound_queued(self, params):
        """Process and queue sound without interrupting current playback.

        Sounds are queued and play one after another instead of interrupting.
        """
        sound_data = params["sound_data"]
        angle_x = params["angle_x"]
        angle_y = params["angle_y"]
        volume = params["volume"]

        # Adjust volume
        adjusted_audio = [sample * volume for sample in sound_data]

        # Process with Steam Audio for 3D positioning
        processed_audio = self.steam_audio.process_sound(
            adjusted_audio, angle_x, angle_y
        )
        if not processed_audio:
            log.debug("Failed to process sound with Steam Audio")
            return

        # Apply reverb if enabled
        final_audio = processed_audio
        if self.use_reverb:
            try:
                conf = config.conf.get("audiothemes", {})
                if conf.get("use_reverb", True):
                    reverb_audio = self.steam_audio.apply_reverb(processed_audio)
                    if reverb_audio:
                        final_audio = reverb_audio
            except Exception:
                pass

        # Queue the sound (no stop() call - sounds play sequentially)
        with self._wave_player_lock:
            self.wave_player.feed(final_audio)

    def play_file(self, filepath):
        """Play an audio file directly (for theme editor preview).

        Args:
            filepath: Path to audio file
        """
        sound = self.make_sound_object(os.path.abspath(filepath))
        if sound is None:
            return

        self._sound_generation += 1
        my_generation = self._sound_generation

        def play_async():
            try:
                # Play centered (no 3D positioning for preview)
                processed = self.steam_audio.process_sound(sound["data"], 0.0, 0.0)
                if processed and my_generation == self._sound_generation:
                    self.wave_player.stop()
                    with self._wave_player_lock:
                        if my_generation == self._sound_generation:
                            self.wave_player.feed(processed)
            except Exception as e:
                log.debug(f"Error playing file: {e}")

        threading.Thread(target=play_async, daemon=True).start()

    def close(self):
        """Clean up resources.

        Note: Does NOT clean up Steam Audio since it's a shared singleton.
        Steam Audio cleanup happens only when the main plugin terminates.
        """
        try:
            with self._wave_player_lock:
                self.wave_player.close()
        except Exception:
            pass

    def close_and_cleanup_steam_audio(self):
        """Clean up all resources including Steam Audio singleton.

        Only call this when the main plugin is terminating.
        """
        self.close()
        if hasattr(self, "steam_audio"):
            self.steam_audio.cleanup()

    def __del__(self):
        """Cleanup when object is destroyed."""
        self.close()
