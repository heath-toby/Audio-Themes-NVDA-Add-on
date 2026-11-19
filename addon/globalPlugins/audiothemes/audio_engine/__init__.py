# coding: utf-8

# Audio engine module for Audio Themes NVDA Add-on
# Uses SteamAudio for 3D audio positioning
# Based on Unspoken-NG by Bryan Smart, Austin Hicks, and Mason Armstrong

import os
import math
import random
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

import addonHandler

addonHandler.initTranslation()


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

        # Initialize a pool of WavePlayers for overlapping playback
        self._pool_size = 6
        self._player_pool = []
        for _ in range(self._pool_size):
            self._player_pool.append(nvwave.WavePlayer(
                channels=2,
                samplesPerSec=44100,
                bitsPerSample=16,
                outputDevice=config.conf["audio"]["outputDevice"],
            ))
        self._pool_index = 0

        # State tracking
        self._last_played_object = None
        self._last_played_time = 0
        self._last_played_sound = None
        self._sound_generation = 0

        # Desktop dimension caching
        self._cached_desktop_size = None
        self._desktop_cache_time = 0
        self._cached_volume = 1.0
        self._update_desktop_cache()
        self._update_volume_cache()

        self._direction_test_running = False

        # Output renderer: "steam" (Steam Audio HRTF -> stereo) or "spatial"
        # (Windows Spatial Audio objects, rendered by Windows Sonic / Dolby Atmos / DTS).
        self.output_mode = "steam"
        self._spatial_active = False
        self._spatial_device = None
        self._spatial_lock = threading.Lock()

        # Display parameters (degrees either side of centre; overridden from config)
        self._default_horizontal_spread = 90.0
        self._default_vertical_spread = 45.0

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

                # Pre-convert to ctypes array for fast DLL calls
                ctypes_buf, ctypes_len = self.steam_audio.make_ctypes_buffer(float_samples)
                return {"data": float_samples, "ctypes_buf": ctypes_buf, "ctypes_len": ctypes_len, "sample_rate": sample_rate}

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

        # Assign a player from the pool on the main thread
        params["player"] = self._get_next_player()

        # Play in background thread
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

        # Assign a player from the pool on the main thread
        params["player"] = self._get_next_player()

        # Play in background thread
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

        # Map the screen onto a field of azimuth/elevation angles in front of the listener.
        # Left edge = -spread, right edge = +spread; bottom = -spread, top = +spread.
        try:
            conf = config.conf["audiothemes"]
            h_spread = float(conf.get("HorizontalSpread", self._default_horizontal_spread))
            v_spread = float(conf.get("VerticalSpread", self._default_vertical_spread))
        except Exception:
            h_spread = self._default_horizontal_spread
            v_spread = self._default_vertical_spread
        x_fraction = (obj_x - desktop_max_x / 2.0) / desktop_max_x  # -0.5 .. 0.5
        y_fraction = (desktop_max_y / 2.0 - obj_y) / desktop_max_y  # -0.5 (bottom) .. 0.5 (top)
        angle_x = x_fraction * 2.0 * h_spread
        angle_y = y_fraction * 2.0 * v_spread

        # Clamp angles to valid ranges
        angle_x = clamp(angle_x, -90.0, 90.0)
        angle_y = clamp(angle_y, -90.0, 90.0)

        return {
            "sound_data": sound["data"],
            "ctypes_buf": sound.get("ctypes_buf"),
            "ctypes_len": sound.get("ctypes_len", 0),
            "angle_x": angle_x,
            "angle_y": angle_y,
            "volume": self._cached_volume,
            "sample_rate": sound.get("sample_rate", 44100),
        }

    def _get_next_player(self):
        """Get the next WavePlayer from the pool (round-robin)."""
        player = self._player_pool[self._pool_index]
        self._pool_index = (self._pool_index + 1) % self._pool_size
        return player

    def set_output_mode(self, mode, device_id=None):
        """Select "steam" or "spatial" output. Returns the spatial_init status (0 = fine).

        If spatial output can't start, the player keeps using Steam Audio.
        """
        if device_id in (None, "", "default"):
            device_id = None
        with self._spatial_lock:
            self.output_mode = mode
            if mode != "spatial":
                if self._spatial_active:
                    self.steam_audio.spatial_shutdown()
                self._spatial_active = False
                self._spatial_device = None
                return 0
            if self._spatial_active and self._spatial_device == device_id and self.steam_audio.spatial_is_active():
                return 0
            status = self.steam_audio.spatial_init(device_id)
            self._spatial_active = status == 0
            self._spatial_device = device_id if status == 0 else None
            if status == 0:
                log.info("Audio Themes: Windows Spatial Audio output started")
            else:
                log.info(f"Audio Themes: Windows Spatial Audio unavailable (status {status}); using Steam Audio")
            return status

    @property
    def spatial_active(self):
        return self._spatial_active

    def _render(self, ctypes_buf, ctypes_len, sample_rate, angle_x, angle_y, volume, use_reverb, player):
        """Play one positioned sound through the active renderer (background thread)."""
        if self._spatial_active:
            spatial_volume = clamp(volume, 0.0, 1.0)
            if self.steam_audio.spatial_play(ctypes_buf, ctypes_len, sample_rate, angle_x, angle_y, spatial_volume):
                return True
            # Stream died (device unplugged, spatial format switched off): try to reopen once
            with self._spatial_lock:
                if self._spatial_active and not self.steam_audio.spatial_is_active():
                    status = self.steam_audio.spatial_init(self._spatial_device)
                    self._spatial_active = status == 0
                    if status != 0:
                        log.warning(f"Audio Themes: lost Windows Spatial Audio (status {status}); using Steam Audio")
            if self._spatial_active and self.steam_audio.spatial_play(
                ctypes_buf, ctypes_len, sample_rate, angle_x, angle_y, spatial_volume
            ):
                return True
        audio = self.steam_audio.process_sound_full(
            ctypes_buf, ctypes_len, angle_x, angle_y, volume, use_reverb
        )
        if not audio:
            log.debug("Failed to process sound with Steam Audio")
            return False
        player.feed(audio)
        return True

    def _play_sound_async(self, params, generation):
        """Process and play sound on background thread.

        Uses a pool of WavePlayers so sounds overlap naturally.
        """
        angle_x = params["angle_x"]
        angle_y = params["angle_y"]
        volume = params["volume"]

        # Determine if reverb is enabled
        use_reverb = False
        if self.use_reverb:
            try:
                conf = config.conf.get("audiothemes", {})
                use_reverb = conf.get("use_reverb", True)
            except Exception:
                pass

        self._render_params(params, angle_x, angle_y, volume, use_reverb)

    def _render_params(self, params, angle_x, angle_y, volume, use_reverb):
        ctypes_buf = params.get("ctypes_buf")
        ctypes_len = params.get("ctypes_len", 0)
        if ctypes_buf is None or ctypes_len <= 0:
            # Sounds always get a ctypes buffer in make_sound_object; build one if not
            ctypes_buf, ctypes_len = self.steam_audio.make_ctypes_buffer(params["sound_data"])
        self._render(
            ctypes_buf, ctypes_len, params.get("sample_rate", 44100),
            angle_x, angle_y, volume, use_reverb, params["player"],
        )

    def _play_sound_queued(self, params):
        """Process and play sound overlapping with current playback.

        Uses same pool approach as _play_sound_async.
        """
        angle_x = params["angle_x"]
        angle_y = params["angle_y"]
        volume = params["volume"]

        # Determine if reverb is enabled
        use_reverb = False
        if self.use_reverb:
            try:
                conf = config.conf.get("audiothemes", {})
                use_reverb = conf.get("use_reverb", True)
            except Exception:
                pass

        self._render_params(params, angle_x, angle_y, volume, use_reverb)

    def play_file(self, filepath):
        """Play an audio file directly (for theme editor preview).

        Args:
            filepath: Path to audio file
        """
        sound = self.make_sound_object(os.path.abspath(filepath))
        if sound is None:
            return

        player = self._get_next_player()

        def play_async():
            try:
                # Play centered (no 3D positioning for preview)
                processed = self.steam_audio.process_sound(sound["data"], 0.0, 0.0)
                if processed:
                    player.feed(processed)
            except Exception as e:
                log.debug(f"Error playing file: {e}")

        threading.Thread(target=play_async, daemon=True).start()

    # (label, azimuth, elevation) for the direction test
    DIRECTION_TEST_POSITIONS = (
        # Translators: spoken before the test sound placed straight ahead of the listener
        (_("Front"), 0.0, 0.0),
        # Translators: spoken before the test sound placed to the listener's right
        (_("Right"), 90.0, 0.0),
        # Translators: spoken before the test sound placed behind the listener
        (_("Behind"), 180.0, 0.0),
        # Translators: spoken before the test sound placed to the listener's left
        (_("Left"), -90.0, 0.0),
        # Translators: spoken before the test sound placed above, in front of the listener
        (_("Up"), 0.0, 60.0),
        # Translators: spoken before the test sound placed below, in front of the listener
        (_("Down"), 0.0, -40.0),
    )

    def _make_test_burst(self):
        """Three short broadband noise bursts: the easiest signal to localise."""
        rate = 44100
        burst = int(0.12 * rate)
        gap = int(0.08 * rate)
        fade = int(0.005 * rate)
        rng = random.Random(7)
        samples = []
        for _i in range(3):
            for n in range(burst):
                env = min(1.0, n / fade, (burst - 1 - n) / fade)
                samples.append(rng.uniform(-0.35, 0.35) * env)
            samples.extend([0.0] * gap)
        return samples

    def play_direction_test(self, sound=None):
        """Speak each direction then play a test sound there through the active renderer.

        sound: a make_sound_object() dict (e.g. the theme's button sound); noise bursts if None.
        """
        if self._direction_test_running:
            return
        self._direction_test_running = True
        import queueHandler
        import ui

        if sound and sound.get("ctypes_buf") is not None:
            ctypes_buf, ctypes_len = sound["ctypes_buf"], sound["ctypes_len"]
            sample_rate = sound.get("sample_rate", 44100)
        else:
            burst = self._make_test_burst()
            ctypes_buf, ctypes_len = self.steam_audio.make_ctypes_buffer(burst)
            sample_rate = 44100
        player = self._get_next_player()

        def worker():
            try:
                for label, az, el in self.DIRECTION_TEST_POSITIONS:
                    queueHandler.queueFunction(queueHandler.eventQueue, ui.message, label)
                    time.sleep(0.9)
                    self._render(
                        ctypes_buf, ctypes_len, sample_rate, az, el,
                        self._cached_volume, False, player,
                    )
                    if not self._spatial_active:
                        player.idle()
                    time.sleep(0.8)
            except Exception:
                log.error("Audio Themes direction test failed", exc_info=True)
            finally:
                self._direction_test_running = False

        threading.Thread(target=worker, name="AudioThemesDirectionTest", daemon=True).start()

    def close(self):
        """Clean up resources.

        Note: Does NOT clean up Steam Audio since it's a shared singleton.
        Steam Audio cleanup happens only when the main plugin terminates.
        """
        for player in getattr(self, "_player_pool", ()):
            try:
                player.close()
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
