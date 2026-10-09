import struct
import subprocess
import sys
import threading
from pathlib import Path


repo = Path(__file__).parents[1]
helper = repo / "bin" / "b32_helper.exe"
if not helper.exists():
	helper = repo / "build-local" / "b32_helper.exe"
engine = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else repo / "bin" / "b32_tts.dll"
ready = b"BSTR"


def read_exact(stream, size):
	data = bytearray()
	while len(data) < size:
		chunk = stream.read(size - len(data))
		if not chunk:
			raise EOFError(f"helper closed after {len(data)} of {size} bytes")
		data.extend(chunk)
	return bytes(data)


def start_helper():
	assert helper.is_file(), f"helper is missing: {helper}"
	assert engine.is_file(), f"test engine is missing: {engine}"
	process = subprocess.Popen(
		[str(helper), str(engine)],
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
	)
	assert read_exact(process.stdout, len(ready)) == ready
	return process


def send_speak(process, text, speed=1.0):
	payload = text.encode("windows-1252")
	process.stdin.write(struct.pack("<If", len(payload), speed) + payload)
	process.stdin.flush()


def read_utterance(process):
	audio = bytearray()
	while True:
		length = struct.unpack("<I", read_exact(process.stdout, 4))[0]
		if length == 0:
			return bytes(audio)
		audio.extend(read_exact(process.stdout, length))


process = start_helper()
try:
	# A CANCEL arriving in the same pipe write as SPEAK must cancel that command;
	# the helper must not clear the cancellation just before synthesis begins.
	payload = ("Pre-start cancellation. " * 500).encode("windows-1252")
	process.stdin.write(struct.pack("<If", len(payload), 1.0) + payload + struct.pack("<I", 0))
	process.stdin.flush()
	assert len(read_utterance(process)) == 0
	send_speak(process, "Speech after pre-start cancellation.")
	assert read_utterance(process)

	for speed in (1.0, 4.0, 0.75):
		send_speak(process, "BeSTspeech release regression. " * 8, speed)
		audio = read_utterance(process)
		assert audio and len(audio) % 2 == 0

	lengths = []
	for _ in range(10):
		send_speak(process, "~r200]Rate boost must preserve phrase endings. " * 12 + "~|", 4.0)
		lengths.append(len(read_utterance(process)))
	assert lengths[0] > 0 and len(set(lengths)) == 1

	send_speak(process, "Cancellation test. " * 500, 4.0)
	timer = threading.Timer(
		0.01,
		lambda: (process.stdin.write(struct.pack("<I", 0)), process.stdin.flush()),
	)
	timer.start()
	read_utterance(process)
	timer.join()
	send_speak(process, "Speech after cancellation.", 4.0)
	assert read_utterance(process)

	process.stdin.write(struct.pack("<I", 0xFFFFFFFF))
	process.stdin.flush()
	assert process.wait(timeout=5) == 0
finally:
	if process.poll() is None:
		process.kill()
		process.wait()

process = start_helper()
process.stdin.write(struct.pack("<I", 16 * 1024 * 1024 + 1))
process.stdin.flush()
assert process.wait(timeout=5) == 0

# Reject malformed speed values before they reach Sonic.
process = start_helper()
payload = b"invalid speed"
process.stdin.write(struct.pack("<If", len(payload), float("nan")) + payload)
process.stdin.flush()
assert process.wait(timeout=5) == 0

# EOF in a truncated command must promptly cancel active synthesis and exit.
process = start_helper()
send_speak(process, "EOF cancellation test. " * 1000)
process.stdin.write(struct.pack("<I", 20) + b"\x00\x00")
process.stdin.close()
assert process.wait(timeout=5) == 0
print(f"Helper release regressions: OK ({engine.name})")
