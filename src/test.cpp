#include "b32_wrapper.h"
#include <stdio.h>
#include <windows.h>

bool async_callback_stop(char* data, long size, void* user) {
	fwrite(data, sizeof(char), size, (FILE*)user);
	return false; // cancel synth
}
bool async_callback(char* data, long size, void* user) {
	fwrite(data, sizeof(char), size, (FILE*)user);
	return true; // keep going
}
int main() {
	bst_state* s = bst_init_w(L"b32_tts.dll");
	long size;
	char* data = bst_speak(s, &size, "this is a test", 1, 30);
	FILE* f = fopen("test.wav", "wb");
	fwrite(data, sizeof(char), size, f);
	fclose(f);
	bst_speech_free(data);
	PlaySound("test.wav", nullptr, SND_SYNC);
	data = bst_speak(s, &size, "this is a second test using the same instance but with a rate boost applied", 0, -200, 6);
	f = fopen("test2.wav", "wb");
	fwrite(data, sizeof(char), size, f);
	fclose(f);
	bst_speech_free(data);
	PlaySound("test2.wav", nullptr, SND_SYNC);
	f = fopen("test3.pcm", "wb");
	bst_speak_async(s, async_callback_stop, f, "this is a test of synthesizing async");
	bst_speak_async(s, async_callback, f, "this is a test of synthesizing async");
	fclose(f);
	bst_free(s);
}
