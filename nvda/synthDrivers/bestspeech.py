import os
import struct
import subprocess
from synthDriverHandler import SynthDriver, synthIndexReached, synthDoneSpeaking, VoiceInfo
from speech.commands import IndexCommand, PitchCommand, CharacterModeCommand
import ctypes
from ctypes import c_char_p, c_void_p, c_long, c_float, CFUNCTYPE
import nvwave
import config
from autoSettingsUtils.driverSetting import DriverSetting, BooleanDriverSetting, NumericDriverSetting
from autoSettingsUtils.utils import StringParameterInfo
import re
import queue
import threading
from logHandler import log

minRate = 200
maxRate = -90
minPitch = 43
maxPitch = 413 # carefully chozen so that the range is within all custom voices while the default value of default voice is pitch 10 in the settings ring.
minInflection = -150
maxInflection = 150
minVolume = -68
maxVolume = 12
HELPER_READY = b"BSTR"
MAX_HELPER_AUDIO_CHUNK = 4 * 1024 * 1024

# Thanks Rommix for the custom voices.
voices = {
	"fred": {"headsize": "1", "excitation": "3", "inflection": 0, "unvoicedVolume": 0, "pitch": 80},
	"sara": {"headsize": "2", "excitation": "3", "inflection": -20, "unvoicedVolume": 0, "pitch": 175},
	"hary": {"headsize": "3", "excitation": "3", "inflection": 10, "unvoicedVolume": 0, "pitch": 65},
	"wendy": {"headsize": "2", "excitation": "1", "inflection": 50, "unvoicedVolume": 0, "pitch": 150},
	"dexter": {"headsize": "6", "excitation": "6", "inflection": 0, "unvoicedVolume": -25, "pitch": 90},
	"alien": {"headsize": "4", "excitation": "6", "inflection": -50, "unvoicedVolume": -20, "pitch": 115},
	"kit": {"headsize": "5", "excitation": "3", "inflection": 40, "unvoicedVolume": 0, "pitch": 230},
	"bruno": {"headsize": "3", "excitation": "3", "inflection": 50, "unvoicedVolume": 0, "pitch": 60},
	"ghost": {"headsize": "3", "excitation": "2", "inflection": 50, "unvoicedVolume": 0, "pitch": 60},
	"peeper": {"headsize": "2", "excitation": "2", "inflection": 0, "unvoicedVolume": 5, "pitch": 80},
	"dracula": {"headsize": "3", "excitation": "3", "inflection": 45, "unvoicedVolume": -5, "pitch": 47},
	"granny": {"headsize": "4", "excitation": "3", "inflection": -60, "unvoicedVolume": 0, "pitch": 350},
	"martha": {"headsize": "6", "excitation": "4", "inflection": 100, "unvoicedVolume": -5, "pitch": 300},
	"tim": {"headsize": "3", "excitation": "4", "inflection": -10, "unvoicedVolume": 0, "pitch": 60}
}

bst_async_callback = CFUNCTYPE(c_long, c_void_p, c_long, c_void_p)

# The BGThread from espeak
class BgThread(threading.Thread):
	def __init__(self, workQueue):
		super().__init__(name=f"{self.__class__.__module__}.{self.__class__.__qualname__}")
		self.daemon = True
		self._queue = workQueue

	def run(self):
		while True:
			func, args, kwargs = self._queue.get()
			if not func:
				self._queue.task_done()
				break
			try:
				func(*args, **kwargs)
			except:  # noqa: E722
				log.error("Error running function from queue", exc_info=True)
			self._queue.task_done()

class SynthDriver(SynthDriver):
	name = 'bestspeech'
	description = 'Bestspeech'
	supportedSettings = (
		SynthDriver.VoiceSetting(),
		SynthDriver.RateSetting(),
		SynthDriver.RateBoostSetting(),
		SynthDriver.PitchSetting(),
		SynthDriver.InflectionSetting(),
		SynthDriver.VolumeSetting(),
		NumericDriverSetting("unvoicedVolume", "&Unvoiced Volume", defaultVal=0, availableInSettingsRing=True),
		DriverSetting("headsize", "&Headsize", defaultVal="1", availableInSettingsRing=True),
		DriverSetting("excitation", "&Excitation", defaultVal="3", availableInSettingsRing=True),
		BooleanDriverSetting("numberProcessing", "&Number Processing", defaultVal=False),
		BooleanDriverSetting("abbreviations", "&Abbreviations", defaultVal=True),
		BooleanDriverSetting("phrasePrediction", "&Phrase Prediction", defaultVal=True)
	)
	supportedNotifications = {synthIndexReached, synthDoneSpeaking}
	supportedCommands = {PitchCommand, CharacterModeCommand, IndexCommand}

	@classmethod
	def check(cls):
		driverDir = os.path.dirname(__file__)
		return (
			os.path.isfile(os.path.join(driverDir, "b32_tts.dll"))
			and os.path.isfile(os.path.join(driverDir, "b32_wrapper.dll"))
			and os.path.isfile(os.path.join(driverDir, "b32_helper.exe"))
		)

	def __init__(self):
		super().__init__()
		path = os.path.join(os.path.dirname(__file__), 'b32_tts.dll')
		wrapper_path = os.path.join(os.path.dirname(__file__), 'b32_wrapper.dll')
		self._dll_path = path
		self.player = None
		try:
			currentSoundcardOutput = config.conf['speech']['outputDevice']
		except:
			currentSoundcardOutput = config.conf["audio"]["outputDevice"]
		self.player = nvwave.WavePlayer(1, 11025, 16, outputDevice=currentSoundcardOutput)
		self._helperWriteLock = threading.Lock()
		self._helperStateLock = threading.Lock()
		self._helper = None
		self._terminating = False
		self._generation = 0
		self._bgQueue = queue.Queue()
		try:
			self.dll = ctypes.cdll[wrapper_path]
			self.dll.bst_init_w.argtypes = (ctypes.c_wchar_p,)
			self.dll.bst_init_w.restype = c_void_p
			self.dll.bst_free.argtypes = (c_void_p,)
			self.dll.bst_speak_async.argtypes = (
				c_void_p, bst_async_callback, c_void_p, c_char_p,
				c_long, c_long, c_float, c_long,
			)
			self.dll.bst_speak_async.restype = None
			self.handle = self.dll.bst_init_w(path)
			if not self.handle:
				raise RuntimeError("The BeSTspeech engine failed to initialize")
			self._use_helper = False
		except (OSError, AttributeError, RuntimeError):
			# b32_wrapper.dll could not be loaded in-process (e.g. 32-bit DLL in
			# 64-bit NVDA 2026+, or DLL simply absent). Fall back to the
			# out-of-process 32-bit helper.
			self.dll = None
			self._use_helper = True
			self._start_helper()
		self.bgThread = BgThread(self._bgQueue)
		self.bgThread.start()
		self.rate = 90
		self.rateBoost = False
		self.volume = self._paramToPercent(0, minVolume, maxVolume)
		self.voice = "fred" # This will automatically set all other parameters like pitch, inflection, excitation and more.
		self.numberProcessing = False
		self.abbreviations = True
		self._phrasePrediction = True
		self.table = str.maketrans("\u2019", "'")
		self.canceled = False

	def _start_helper(self):
		with self._helperStateLock:
			if self._terminating:
				raise RuntimeError("The BeSTspeech driver is terminating")
		helper_path = os.path.join(os.path.dirname(__file__), 'b32_helper.exe')
		if not os.path.isfile(helper_path):
			raise RuntimeError("The 32-bit BeSTspeech helper is missing")
		helper = subprocess.Popen(
			[helper_path, self._dll_path],
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.DEVNULL,
			creationflags=subprocess.CREATE_NO_WINDOW
		)
		with self._helperStateLock:
			if self._terminating:
				helper.kill()
				raise RuntimeError("The BeSTspeech driver is terminating")
			self._helper = helper
		startupResult = queue.Queue(maxsize=1)
		def readStartupResult():
			try:
				startupResult.put(helper.stdout.read(len(HELPER_READY)))
			except OSError:
				startupResult.put(b"")
		threading.Thread(target=readStartupResult, name="BeSTspeechHelperStartup", daemon=True).start()
		try:
			ready = startupResult.get(timeout=3)
		except queue.Empty:
			ready = b""
		if ready != HELPER_READY:
			with self._helperStateLock:
				if self._helper is helper:
					self._helper = None
			try:
				helper.kill()
				helper.wait(timeout=2)
			except (OSError, subprocess.TimeoutExpired):
				pass
			raise RuntimeError("The BeSTspeech helper failed to initialize")
		with self._helperStateLock:
			if self._terminating or self._helper is not helper:
				try:
					helper.kill()
				except OSError:
					pass
				raise RuntimeError("The BeSTspeech driver terminated during helper startup")

	def _execWhenDone(self, func, *args, mustBeAsync=False, **kwargs):
		if mustBeAsync or self._bgQueue.unfinished_tasks != 0:
			self._bgQueue.put((func, args, kwargs))
		else:
			func(*args, **kwargs)

	def _isCurrent(self, generation):
		return not self._terminating and generation == self._generation

	def loadSettings(self, onlyChanged = False):
		# We can probably remove this in a bit, we override this to make sure people's excitation setting doesn't break across addon versions.
		super().loadSettings(onlyChanged)
		if self.excitation == "0": self.excitation = "3"

	def _set_rate(self, vl):
		self._rate = self._percentToParam(vl,minRate,maxRate)

	def _get_rate(self):
		return self._paramToPercent(self._rate, minRate, maxRate)

	def _set_rateBoost(self, enable):
		self._rateBoost = enable

	def _get_rateBoost(self):
		return self._rateBoost

	def _set_pitch(self, vl):
		self._pitch = self._percentToParam(vl,minPitch,maxPitch)

	def _get_pitch(self):
		return self._paramToPercent(self._pitch, minPitch, maxPitch)

	def _set_volume(self, vl):
		self._volume = self._percentToParam(vl,minVolume,maxVolume)

	def _get_volume(self):
		return self._paramToPercent(self._volume, minVolume, maxVolume)

	def _set_unvoicedVolume(self, vl):
		self._unvoicedVolume = self._percentToParam(vl,minVolume,maxVolume)

	def _get_unvoicedVolume(self):
		return self._paramToPercent(self._unvoicedVolume, minVolume, maxVolume)

	def _set_inflection(self, vl):
		self._inflection = self._percentToParam(vl,minInflection,maxInflection)

	def _get_inflection(self):
		return self._paramToPercent(self._inflection, minInflection, maxInflection)

	def _set_headsize(self, vl):
		try:
			n = int(vl)
		except (TypeError, ValueError):
			n = 0
		self._headsize = str(n) if 1 <= n <= 6 else "1"

	def _get_headsize(self):
		return self._headsize

	def _get_availableHeadsizes(self):
		return { str(i): StringParameterInfo(str(i), str(i)) for i in range(1, 7)}

	def _set_excitation(self, vl):
		try:
			n = int(vl)
		except (TypeError, ValueError):
			n = 0
		self._excitation = str(n) if 1 <= n <= 7 else "3"

	def _get_excitation(self):
		return self._excitation

	def _get_availableExcitations(self):
		return { str(i): StringParameterInfo(str(i), str(i)) for i in range(1,8)}

	def _set_numberProcessing(self, val):
		self._numberProcessing = bool(val)

	def _get_numberProcessing(self):
		return self._numberProcessing

	def _set_abbreviations(self, val):
		self._abbreviations = bool(val)

	def _get_abbreviations(self):
		return self._abbreviations

	def _set_phrasePrediction(self, val):
		self._phrasePrediction = bool(val)

	def _get_phrasePrediction(self):
		return self._phrasePrediction

	def _set_voice(self, vl):
		if not vl in voices: return
		self._voice = vl
		# set voice parameters
		for p in voices[vl]:
			try:
				minimum = globals()[f"min{p.title()}"] if not "Volume" in p else globals()["minVolume"]
				maximum = globals()[f"max{p.title()}"] if not "Volume" in p else globals()["maxVolume"]
				value = self._paramToPercent(voices[vl][p], minimum, maximum)
			except KeyError:
				value = voices[vl][p]
			setter = getattr(self, f"_set_{p}", None)
			if setter:
				setter(value)
			else:
				setattr(self, p, value)

	def _get_voice(self):
		return self._voice

	def _getAvailableVoices(self):
		return {v: VoiceInfo(v, v) for v in voices}

	def _formatNumbers(self, text):
		def replace_num(m):
			num_str = m.group(0)
			firstGroup = len(num_str) % 3 or 3
			return num_str[:firstGroup] + "".join("," + num_str[i:i + 3] for i in range(firstGroup, len(num_str), 3))
		return re.sub(r"\b\d{5,}\b", replace_num, text)

	def speak(self, speechSequence):
		initialCommands = ["~n10,0]" if self._abbreviations else "~n10,1]", "~~1,0]" if self._phrasePrediction else "~~1,1]"]
		char_mode_on = False
		pitch_multiplier = 1
		def pitchForMultiplier(multiplier):
			return max(minPitch, min(maxPitch, int(self._pitch * multiplier)))

		def newSegmentCommands():
			commands = list(initialCommands)
			# BeSTspeech retains character mode between synthesis calls. Always
			# state the mode explicitly so a spelling request cannot leak into a
			# later ordinary utterance.
			commands.append("~n1,1]" if char_mode_on else "~n1,0]")
			if pitch_multiplier != 1:
				commands.append(f"~f{pitchForMultiplier(pitch_multiplier)}]")
			return commands

		lst = newSegmentCommands()
		segments = []
		leadingIndexes = []
		hasText = False
		for item in speechSequence:
			if isinstance(item, str):
				lst.append(item)
				hasText = True
			elif isinstance(item, IndexCommand):
				if hasText:
					segments.append([self._formatSpeechSegment(lst), [item.index]])
					lst = newSegmentCommands()
					hasText = False
				elif segments:
					segments[-1][1].append(item.index)
				else:
					leadingIndexes.append(item.index)
			elif isinstance(item,CharacterModeCommand):
				char_mode_on = bool(item.state)
				lst.append("~n1,1]" if char_mode_on else "~n1,0]")
			elif isinstance(item,PitchCommand):
				try: multiplier = item.multiplier
				except (AttributeError, ZeroDivisionError): multiplier = 1
				pitch_multiplier = multiplier
				f = pitchForMultiplier(multiplier)
				lst.append(f"~f{f}]")
		if hasText:
			segments.append([self._formatSpeechSegment(lst), []])
		generation = self._generation
		self._execWhenDone(self._speakBg, segments, leadingIndexes, generation, mustBeAsync=True)

	def _formatSpeechSegment(self, commands):
		text = " ".join(commands)
		if self._numberProcessing: text = self._formatNumbers(text)
		return f"~r{self._rate}]~e{self._excitation}]~v{self.headsize}]~f{self._pitch}]~g{self._volume}]~u{self._unvoicedVolume}]~h{self._inflection}]{text} ~|"

	def _speakBg(self, segments, leadingIndexes, generation):
		if not self._isCurrent(generation):
			return
		if self._use_helper:
			self._speakBg_helper(segments, leadingIndexes, generation)
		else:
			self._speakBg_dll(segments, leadingIndexes, generation)

	def _queueIndexes(self, indexes, generation):
		if indexes:
			self.player.feed(b"", 0, onDone=lambda indexes=tuple(indexes): self._notifyIndexes(indexes, generation))

	def _notifyIndexes(self, indexes, generation):
		if not self._isCurrent(generation):
			return
		for index in indexes:
			synthIndexReached.notify(synth=self, index=index)

	def _speechFailed(self, message, generation):
		if not self._isCurrent(generation):
			return
		log.error(message)
		self.speaking = False
		if self.player:
			self.player.stop()
		synthDoneSpeaking.notify(synth=self)

	def _speakBg_dll(self, segments, leadingIndexes, generation):
		@bst_async_callback
		def on_audio(data, size, user):
			if not self.speaking or not self._isCurrent(generation): return False
			self.player.feed(data, size)
			return True
		if not self._isCurrent(generation): return
		self.speaking = True
		self._notifyIndexes(leadingIndexes, generation)
		for text, indexes in segments:
			txt = text.translate(self.table).encode('windows-1252', 'replace')
			self.dll.bst_speak_async(self.handle, on_audio, None, txt, -1, 0, c_float(4 if self.rateBoost else 1), 0)
			if not self.speaking or not self._isCurrent(generation): return
			self._queueIndexes(indexes, generation)
		self.player.feed(b"", 0, onDone=lambda: self.done(generation))
		self.player.idle()

	def _speakBg_helper(self, segments, leadingIndexes, generation):
		if not self._isCurrent(generation): return
		# Restart helper if it died unexpectedly.
		if self._helper is None or self._helper.poll() is not None:
			try:
				self._start_helper()
			except (OSError, RuntimeError):
				log.error("Unable to start the BeSTspeech helper", exc_info=True)
				self._speechFailed("The BeSTspeech helper could not be started", generation)
				return
		if not self._isCurrent(generation): return
		self.speaking = True
		self._notifyIndexes(leadingIndexes, generation)
		for text, indexes in segments:
			if not self._speakHelperSegment(text, generation):
				if self.speaking and self._isCurrent(generation):
					self._speechFailed("The BeSTspeech helper stopped responding", generation)
				return
			if not self.speaking or not self._isCurrent(generation): return
			self._queueIndexes(indexes, generation)
		self.player.feed(b"", 0, onDone=lambda: self.done(generation))
		self.player.idle()

	def _speakHelperSegment(self, text, generation):
		if not self._isCurrent(generation):
			return True
		helper = self._helper
		if helper is None:
			return False
		txt = text.translate(self.table).encode('windows-1252', 'replace')
		rate_mult = 4.0 if self._rateBoost else 1.0
		try:
			with self._helperWriteLock:
				helper.stdin.write(struct.pack('<If', len(txt), rate_mult))
				helper.stdin.write(txt)
				helper.stdin.flush()
		except (AttributeError, BrokenPipeError, OSError, ValueError):
			log.error("Unable to send speech to the BeSTspeech helper", exc_info=True)
			return False
		while True:
			hdr = self._helper_read_exact(helper, 4)
			if hdr is None: return False
			chunk_len = struct.unpack('<I', hdr)[0]
			if chunk_len == 0: return True
			if chunk_len > MAX_HELPER_AUDIO_CHUNK:
				log.error("Invalid audio chunk length from the BeSTspeech helper: %d", chunk_len)
				self._stop_helper()
				return False
			chunk = self._helper_read_exact(helper, chunk_len)
			if chunk is None: return False
			if self.speaking and self._isCurrent(generation):
				self.player.feed(chunk, len(chunk))

	def _helper_read_exact(self, helper, n):
		buf = b""
		while len(buf) < n:
			try:
				chunk = helper.stdout.read(n - len(buf))
			except (AttributeError, OSError, ValueError):
				return None
			if not chunk:
				return None
			buf += chunk
		return buf

	def done(self, generation=None):
		if generation is not None and not self._isCurrent(generation):
			return
		self.speaking = False
		synthDoneSpeaking.notify(synth=self)

	def terminate(self):
		with self._helperStateLock:
			self._terminating = True
		self.cancel()
		# Stop the helper first. The background thread may be blocked reading its
		# stdout, and cannot consume the queue sentinel until that pipe closes.
		if self._use_helper:
			self._stop_helper()
		else:
			# The wrapper's hidden WinMM window must be destroyed by the same
			# thread that created it during synthesis.
			self._bgQueue.put((self._free_direct, (), {}))
		self._bgQueue.put((None, None, None))
		self.bgThread.join(timeout=3)
		if self.bgThread.is_alive():
			log.error("BeSTspeech background thread did not stop during termination")

	def _free_direct(self):
		if self.handle:
			self.dll.bst_free(self.handle)
			self.handle = None

	def _stop_helper(self):
		with self._helperStateLock:
			helper = self._helper
			self._helper = None
		if helper is None:
			return
		try:
			with self._helperWriteLock:
				helper.stdin.write(struct.pack('<I', 0xFFFFFFFF))
				helper.stdin.flush()
		except (BrokenPipeError, OSError, ValueError):
			pass
		try:
			helper.wait(timeout=2)
		except subprocess.TimeoutExpired:
			try:
				helper.kill()
			except OSError:
				pass
			try:
				helper.wait(timeout=2)
			except subprocess.TimeoutExpired:
				log.error("Unable to stop the BeSTspeech helper process")

	def cancel(self):
		self._generation += 1
		self.speaking = False
		while True:
			try:
				item = self._bgQueue.get_nowait()
			except queue.Empty:
				break
			else:
				self._bgQueue.task_done()
		if self.player:
			self.player.stop()
		if self._use_helper and self._helper is not None:
			# Send CANCEL command: text_len == 0
			try:
				with self._helperWriteLock:
					self._helper.stdin.write(struct.pack('<I', 0))
					self._helper.stdin.flush()
			except (BrokenPipeError, OSError, ValueError):
				pass

	def pause(self, switch):
		if self.player: self.player.pause(switch)
