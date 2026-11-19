// Windows Spatial Audio output for Audio Themes.
//
// Sends each sound to Windows as a dynamic spatial audio object (ISpatialAudioClient), so the
// spatial sound format chosen for the output device (Windows Sonic, Dolby Atmos, DTS Headphone:X,
// or a real multi-speaker setup) does the 3D rendering instead of Steam Audio's HRTF.
//
// All COM work happens on one dedicated MTA render thread; other threads only queue samples.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <mmdeviceapi.h>
#include <spatialaudioclient.h>
#include <wrl/client.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <future>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

using Microsoft::WRL::ComPtr;

#define EXPORT extern "C" __declspec(dllexport)

// Status codes returned by spatial_init
enum SpatialStatus {
	SPATIAL_OK = 0,
	SPATIAL_E_COM = -1,             // COM / device enumerator unavailable
	SPATIAL_E_DEVICE = -2,          // output device not found
	SPATIAL_E_NOT_SUPPORTED = -3,   // device has no spatial audio client
	SPATIAL_E_DISABLED = -4,        // spatial sound is switched off for this device
	SPATIAL_E_FORMAT = -5,          // renderer wants a sample format we don't produce
	SPATIAL_E_STREAM = -6,          // could not open the spatial stream
	SPATIAL_E_START = -7,           // could not start the stream
};

namespace {

constexpr size_t kMaxQueuedVoices = 32;
constexpr float kPi = 3.14159265358979323846f;

struct Voice {
	std::vector<float> samples;  // mono float at the renderer's sample rate
	size_t pos = 0;
	float x = 0.0f, y = 0.0f, z = -1.0f;
	ComPtr<ISpatialAudioObject> object;  // only touched on the render thread
	bool stop = false;
	bool done = false;
};

struct SpatialState {
	std::mutex mutex;  // guards voices
	std::vector<std::unique_ptr<Voice>> voices;
	std::thread thread;
	std::atomic<bool> running{ false };
	std::atomic<bool> failed{ false };
	std::atomic<UINT32> sampleRate{ 48000 };
	UINT32 maxObjects = 0;
};

SpatialState g;

bool is_float_mono(const WAVEFORMATEX* fmt)
{
	if (!fmt || fmt->nChannels != 1 || fmt->wBitsPerSample != 32) {
		return false;
	}
	if (fmt->wFormatTag == WAVE_FORMAT_IEEE_FLOAT) {
		return true;
	}
	if (fmt->wFormatTag == WAVE_FORMAT_EXTENSIBLE && fmt->cbSize >= 22) {
		auto ext = reinterpret_cast<const WAVEFORMATEXTENSIBLE*>(fmt);
		return ext->SubFormat == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
	}
	return false;
}

void render_thread(std::wstring deviceId, std::promise<int> ready)
{
	bool reported = false;
	auto report = [&](int status) {
		if (!reported) {
			reported = true;
			ready.set_value(status);
		}
	};

	HRESULT hrCo = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
	HANDLE event = nullptr;
	{
		ComPtr<IMMDeviceEnumerator> enumerator;
		ComPtr<IMMDevice> device;
		ComPtr<ISpatialAudioClient> client;
		ComPtr<IAudioFormatEnumerator> formats;
		ComPtr<ISpatialAudioObjectRenderStream> stream;
		WAVEFORMATEX* format = nullptr;
		UINT32 maxDynamic = 0;
		int status = SPATIAL_OK;

		if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, IID_PPV_ARGS(&enumerator)))) {
			status = SPATIAL_E_COM;
		}
		if (status == SPATIAL_OK) {
			HRESULT hr = deviceId.empty()
				? enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device)
				: enumerator->GetDevice(deviceId.c_str(), &device);
			if (FAILED(hr)) {
				// A stale device ID falls back to the default device, like NVDA itself does
				hr = enumerator->GetDefaultAudioEndpoint(eRender, eConsole, &device);
			}
			if (FAILED(hr)) status = SPATIAL_E_DEVICE;
		}
		if (status == SPATIAL_OK &&
			FAILED(device->Activate(__uuidof(ISpatialAudioClient), CLSCTX_INPROC_SERVER, nullptr, reinterpret_cast<void**>(client.GetAddressOf())))) {
			status = SPATIAL_E_NOT_SUPPORTED;
		}
		if (status == SPATIAL_OK && (FAILED(client->GetMaxDynamicObjectCount(&maxDynamic)) || maxDynamic == 0)) {
			status = SPATIAL_E_DISABLED;
		}
		if (status == SPATIAL_OK) {
			if (FAILED(client->GetSupportedAudioObjectFormatEnumerator(&formats)) ||
				FAILED(formats->GetFormat(0, &format)) || !is_float_mono(format)) {
				status = SPATIAL_E_FORMAT;
			}
		}
		if (status == SPATIAL_OK) {
			event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
			SpatialAudioObjectRenderStreamActivationParams params{};
			params.ObjectFormat = format;
			params.StaticObjectTypeMask = AudioObjectType_None;
			params.MinDynamicObjectCount = 0;
			params.MaxDynamicObjectCount = std::min<UINT32>(maxDynamic, 16);
			params.Category = AudioCategory_Other;
			params.EventHandle = event;
			params.NotifyObject = nullptr;

			PROPVARIANT pv;
			PropVariantInit(&pv);
			pv.vt = VT_BLOB;
			pv.blob.cbSize = sizeof(params);
			pv.blob.pBlobData = reinterpret_cast<BYTE*>(&params);
			if (!event || FAILED(client->ActivateSpatialAudioStream(&pv, IID_PPV_ARGS(&stream)))) {
				status = SPATIAL_E_STREAM;
			}
			// pv points at a stack struct, so it is not cleared with PropVariantClear
		}
		if (status == SPATIAL_OK && FAILED(stream->Start())) {
			status = SPATIAL_E_START;
		}

		if (status != SPATIAL_OK) {
			report(status);
		} else {
			g.sampleRate = format->nSamplesPerSec;
			g.maxObjects = std::min<UINT32>(maxDynamic, 16);
			g.failed = false;
			report(SPATIAL_OK);

			while (g.running) {
				if (WaitForSingleObject(event, 100) != WAIT_OBJECT_0) {
					continue;
				}
				UINT32 available = 0, frames = 0;
				if (FAILED(stream->BeginUpdatingAudioObjects(&available, &frames))) {
					// Device removed, spatial format switched off, etc.
					g.failed = true;
					break;
				}
				{
					std::lock_guard<std::mutex> lock(g.mutex);
					for (auto& v : g.voices) {
						if (v->done) continue;
						if (!v->object) {
							if (v->stop) { v->done = true; continue; }
							if (available == 0) continue;  // wait for a free object
							if (FAILED(stream->ActivateSpatialAudioObject(AudioObjectType_Dynamic, &v->object))) continue;
							--available;
						}
						BYTE* buffer = nullptr;
						UINT32 bytes = 0;
						if (FAILED(v->object->GetBuffer(&buffer, &bytes))) {
							v->done = true;
							continue;
						}
						size_t capacity = bytes / sizeof(float);
						size_t remaining = v->stop ? 0 : v->samples.size() - v->pos;
						size_t count = std::min(capacity, remaining);
						float* out = reinterpret_cast<float*>(buffer);
						if (count) std::memcpy(out, v->samples.data() + v->pos, count * sizeof(float));
						if (count < capacity) std::memset(out + count, 0, (capacity - count) * sizeof(float));
						v->pos += count;
						v->object->SetPosition(v->x, v->y, v->z);
						v->object->SetVolume(1.0f);
						if (v->pos >= v->samples.size() || v->stop) {
							v->object->SetEndOfStream(0);
							v->done = true;
						}
					}
				}
				stream->EndUpdatingAudioObjects();
				{
					std::lock_guard<std::mutex> lock(g.mutex);
					g.voices.erase(
						std::remove_if(g.voices.begin(), g.voices.end(), [](const std::unique_ptr<Voice>& v) { return v->done; }),
						g.voices.end());
				}
			}

			stream->Stop();
			std::lock_guard<std::mutex> lock(g.mutex);
			g.voices.clear();  // releases objects on this (their) thread
		}
	}
	if (event) CloseHandle(event);
	if (SUCCEEDED(hrCo)) CoUninitialize();
	report(SPATIAL_E_COM);  // only reaches the caller if nothing was reported yet
}

}  // namespace

EXPORT void spatial_shutdown()
{
	g.running = false;
	if (g.thread.joinable()) {
		g.thread.join();
	}
	std::lock_guard<std::mutex> lock(g.mutex);
	g.voices.clear();
}

// Open Windows Spatial Audio on the given endpoint ID (nullptr / empty = default device).
// Returns SPATIAL_OK or a negative SpatialStatus.
EXPORT int spatial_init(const wchar_t* device_id)
{
	spatial_shutdown();
	std::promise<int> ready;
	auto result = ready.get_future();
	g.failed = false;
	g.running = true;
	g.thread = std::thread(render_thread, std::wstring(device_id ? device_id : L""), std::move(ready));
	int status = result.get();
	if (status != SPATIAL_OK) {
		spatial_shutdown();
	}
	return status;
}

EXPORT bool spatial_is_active()
{
	return g.running && !g.failed;
}

// Queue a mono sound at azimuth/elevation (degrees, same convention as process_sound:
// azimuth -90 left .. +90 right, 180 behind; elevation -90 below .. +90 above).
EXPORT bool spatial_play(const float* input, int length, int input_rate, float azimuth_deg, float elevation_deg, float volume)
{
	if (!spatial_is_active() || !input || length <= 0 || input_rate <= 0) {
		return false;
	}
	auto voice = std::make_unique<Voice>();

	// Linear-interpolation resample to the renderer's rate
	const UINT32 outRate = g.sampleRate;
	const double step = static_cast<double>(input_rate) / outRate;
	const size_t outLength = static_cast<size_t>(length / step);
	voice->samples.resize(outLength);
	for (size_t i = 0; i < outLength; ++i) {
		double src = i * step;
		size_t i0 = static_cast<size_t>(src);
		size_t i1 = std::min<size_t>(i0 + 1, static_cast<size_t>(length - 1));
		float frac = static_cast<float>(src - i0);
		float s = (input[i0] * (1.0f - frac) + input[i1] * frac) * volume;
		voice->samples[i] = std::max(-1.0f, std::min(1.0f, s));
	}

	// Spatial audio space: +x right, +y up, -z in front; units are metres from the listener
	const float az = azimuth_deg * kPi / 180.0f;
	const float el = elevation_deg * kPi / 180.0f;
	voice->x = cosf(el) * sinf(az);
	voice->y = sinf(el);
	voice->z = -cosf(el) * cosf(az);

	std::lock_guard<std::mutex> lock(g.mutex);
	if (g.voices.size() >= kMaxQueuedVoices) {
		// Too much queued: end the oldest sound rather than piling up latency
		for (auto& v : g.voices) {
			if (!v->stop) { v->stop = true; break; }
		}
	}
	g.voices.push_back(std::move(voice));
	return true;
}

EXPORT void spatial_stop_all()
{
	std::lock_guard<std::mutex> lock(g.mutex);
	for (auto& v : g.voices) {
		v->stop = true;
	}
}

// If the host exits without calling spatial_shutdown, a joinable std::thread would call
// std::terminate during static destruction. Joining under the loader lock can deadlock,
// so just let the (ending) process take the thread with it.
BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
	if (reason == DLL_PROCESS_DETACH && g.thread.joinable()) {
		g.running = false;
		g.thread.detach();
	}
	return TRUE;
}
