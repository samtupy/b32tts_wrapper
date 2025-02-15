#include "b32_wrapper.h"
#include <stdio.h>
#include <windows.h>

int main() {
	bst_state* s = bst_init_w(L"b32_tts.dll");
	long size;
	char* data = bst_speak(s, &size, "this is a test", 1, 30);
	FILE* f = fopen("test.wav", "wb");
	fwrite(data, sizeof(char), size, f);
	fclose(f);
	free(data);
	PlaySound("test.wav", nullptr, SND_SYNC);
	data = bst_speak(s, &size, "this is a second test using the same instance");
	f = fopen("test2.wav", "wb");
	fwrite(data, sizeof(char), size, f);
	fclose(f);
	free(data);
	PlaySound("test2.wav", nullptr, SND_SYNC);
	bst_free(s);
}
