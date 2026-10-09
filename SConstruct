env = Environment(TARGET_ARCH = "x86")
VariantDir("obj", "src", duplicate = 0)
# Regarding SONIC_MIN_PITCH, sonic.h includes this comment: "Note that if we go lower than 65, we could overflow in findPitchInRange."
# I don't know what this means, but I do know that numbers lower than 65 sound way better for the lowest bestspeech pitches.
# If rate boost introduces audio problems, try heeding this warning first and removing the below SONIC_MIN_PITCH define.
sonic = env.Object("obj/sonic", "obj/sonic.c", CPPDEFINES = [("SONIC_MIN_PITCH", 45), ("SONIC_MAX_PITCH", 250)])
env.SharedLibrary("bin/b32_wrapper", ["obj/b32_wrapper.cpp", sonic], LIBS = ["user32", "winmm", "bin/MinHook"])
env.Program("bin/test", "obj/test.cpp", LIBS = ["user32", "winmm", "bin/b32_wrapper"])
env.Program("bin/test_rapid", "obj/test_rapid.cpp", LIBS = ["user32", "winmm", "bin/b32_wrapper"])
b32_wrapper_static = env.Object("obj/b32_wrapper_static", "obj/b32_wrapper.cpp", CPPDEFINES = [("b32w_export", "")])
env.Program("bin/b32_spk", ["obj/argparse.c", b32_wrapper_static, "obj/b32_spk.cpp", sonic], CPPDEFINES = [("b32w_export", "")], LIBS = ["user32", "winmm", "bin/MinHook"])
env.Program("bin/b32_helper", [b32_wrapper_static, "obj/b32_helper.cpp", sonic], CPPDEFINES = [("b32w_export", "")], LIBS = ["user32", "winmm", "bin/MinHook"])
# The proprietary engine can return no audio on hosted Windows runners even
# though the helper and wrapper are functioning.  This deterministic engine
# exercises the complete helper protocol and waveOut interception in CI.
env.SharedLibrary("bin/mock_b32_tts", "tests/mock_b32_tts.cpp", LIBS = ["winmm"])
