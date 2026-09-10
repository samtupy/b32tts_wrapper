#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <stdint.h>
#include <string.h>

extern "C" {

__declspec(dllexport) int __cdecl bstCreate(long** handle)
{
	static long state = 1;
	*handle = &state;
	return 0;
}

__declspec(dllexport) int __cdecl TtsWav(long* /*handle*/, void* callback, const char* text)
{
	WAVEFORMATEX format = {};
	format.wFormatTag = WAVE_FORMAT_PCM;
	format.nChannels = 1;
	format.nSamplesPerSec = 11025;
	format.wBitsPerSample = 16;
	format.nBlockAlign = 2;
	format.nAvgBytesPerSec = 22050;

	HWAVEOUT output = nullptr;
	if (waveOutOpen(&output, WAVE_MAPPER, &format, (DWORD_PTR)callback, 0, CALLBACK_FUNCTION) != MMSYSERR_NOERROR) {
		return 1;
	}

	short samples[1024];
	for (size_t index = 0; index < sizeof(samples) / sizeof(samples[0]); ++index) {
		samples[index] = (short)((index % 200) * 100 - 10000);
	}
	WAVEHDR header = {};
	header.lpData = (LPSTR)samples;
	header.dwBufferLength = sizeof(samples);
	waveOutPrepareHeader(output, &header, sizeof(header));

	const size_t textLength = text ? strlen(text) : 0;
	const size_t chunks = min((size_t)100, max((size_t)1, textLength / 64));
	for (size_t index = 0; index < chunks; ++index) {
		waveOutWrite(output, &header, sizeof(header));
		Sleep(1);
	}

	waveOutUnprepareHeader(output, &header, sizeof(header));
	waveOutClose(output);
	return 0;
}

__declspec(dllexport) void __cdecl bstRelBuf(long* /*handle*/) {}
__declspec(dllexport) void __cdecl bstClose(long* /*handle*/) {}
__declspec(dllexport) void __cdecl bstDestroy() {}
__declspec(dllexport) void __cdecl bstSetParams(long* /*handle*/, int /*setting*/, int /*value*/) {}
__declspec(dllexport) void __cdecl bstGetParams(long* /*handle*/, int /*setting*/, int* value)
{
	if (value) *value = 0;
}

}
