import importlib.util
import sys
import types
from pathlib import Path


class Event:
	def __init__(self): self.calls = []
	def notify(self, **kwargs): self.calls.append(kwargs)


class BaseSynth:
	VoiceSetting = RateSetting = RateBoostSetting = PitchSetting = InflectionSetting = VolumeSetting = staticmethod(lambda: object())
	def __init__(self): pass
	def _percentToParam(self, value, minimum, maximum): return round(minimum + (maximum - minimum) * value / 100)
	def _paramToPercent(self, value, minimum, maximum): return round((value - minimum) * 100 / (maximum - minimum))


class IndexCommand:
	def __init__(self, index): self.index = index


class PitchCommand:
	def __init__(self, multiplier): self.multiplier = multiplier


class CharacterModeCommand:
	def __init__(self, state): self.state = state


indexEvent, doneEvent = Event(), Event()
synthModule = types.ModuleType("synthDriverHandler")
synthModule.SynthDriver = BaseSynth
synthModule.synthIndexReached = indexEvent
synthModule.synthDoneSpeaking = doneEvent
synthModule.VoiceInfo = lambda *args: args
sys.modules["synthDriverHandler"] = synthModule

commandsModule = types.ModuleType("speech.commands")
commandsModule.IndexCommand = IndexCommand
commandsModule.PitchCommand = PitchCommand
commandsModule.CharacterModeCommand = CharacterModeCommand
sys.modules["speech"] = types.ModuleType("speech")
sys.modules["speech.commands"] = commandsModule
for name in ("nvwave", "winUser"):
	sys.modules[name] = types.ModuleType(name)
configModule = types.ModuleType("config")
configModule.conf = {}
sys.modules["config"] = configModule
settingsModule = types.ModuleType("autoSettingsUtils.driverSetting")
settingsModule.DriverSetting = settingsModule.BooleanDriverSetting = settingsModule.NumericDriverSetting = lambda *args, **kwargs: object()
sys.modules["autoSettingsUtils"] = types.ModuleType("autoSettingsUtils")
sys.modules["autoSettingsUtils.driverSetting"] = settingsModule
utilsModule = types.ModuleType("autoSettingsUtils.utils")
utilsModule.StringParameterInfo = lambda *args: args
sys.modules["autoSettingsUtils.utils"] = utilsModule
logModule = types.ModuleType("logHandler")
logModule.log = types.SimpleNamespace(error=lambda *args, **kwargs: None)
sys.modules["logHandler"] = logModule

driverPath = Path(__file__).parents[1] / "nvda" / "synthDrivers" / "bestspeech.py"
spec = importlib.util.spec_from_file_location("bestspeech_under_test", driverPath)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

captured = []
driver = module.SynthDriver.__new__(module.SynthDriver)
driver._generation = 0
driver._terminating = False
driver._execWhenDone = lambda func, *args, **kwargs: captured.append((func, args))
driver._abbreviations = driver._phrasePrediction = True
driver._numberProcessing = False
driver._rate, driver._excitation, driver._headsize = 0, "3", "1"
driver.headsize = "1"
driver._pitch, driver._volume, driver._unvoicedVolume, driver._inflection = 80, 0, 0, 0

bareDriver = module.SynthDriver.__new__(module.SynthDriver)
bareDriver._set_voice("fred")
assert bareDriver._voice == "fred"
assert bareDriver._headsize == "1" and bareDriver._excitation == "3"
assert bareDriver._pitch == 80 and bareDriver._inflection == 0 and bareDriver._unvoicedVolume == 0

driver.speak([IndexCommand(1), "first", IndexCommand(2), "second", IndexCommand(3)])
segments, leading, generation = captured.pop()[1]
assert leading == [1]
assert generation == 0
assert [segment[1] for segment in segments] == [[2], [3]]

driver.speak([
	CharacterModeCommand(True), PitchCommand(1.5), "A", IndexCommand(4),
	"B", IndexCommand(5), CharacterModeCommand(False), PitchCommand(1), "normal",
])
segments, leading, _ = captured.pop()[1]
assert not leading and len(segments) == 3
assert "~n1,1]" in segments[0][0] and "~f120]" in segments[0][0]
assert "~n1,1]" in segments[1][0] and "~f120]" in segments[1][0]
assert "~n1,0]" in segments[2][0] and "~f80]" in segments[2][0]

# A sequence that ends while spelling must not leave the persistent legacy
# engine in character mode for the next normal utterance.
driver.speak([CharacterModeCommand(True), "X"])
spellingSegments, _, _ = captured.pop()[1]
assert "~n1,1]" in spellingSegments[0][0]
driver.speak(["This must be spoken normally."])
normalSegments, _, _ = captured.pop()[1]
assert "~n1,0]" in normalSegments[0][0]
assert normalSegments[0][0].index("~n1,0]") < normalSegments[0][0].index("This must be spoken normally.")

driver.speak([PitchCommand(100), "clamped pitch"])
clampedSegments, _, _ = captured.pop()[1]
assert f"~f{module.maxPitch}]" in clampedSegments[0][0]

driver._set_headsize("0")
driver._set_excitation("0")
assert driver._headsize == "1" and driver._excitation == "3"
driver._set_headsize("corrupt")
driver._set_excitation(None)
assert driver._headsize == "1" and driver._excitation == "3"

# Group arbitrarily long digit strings without converting them to int (Python
# 3.11+ limits decimal conversion length by default).
hugeNumber = "1" * 5000
formattedNumber = driver._formatNumbers(hugeNumber)
assert formattedNumber.replace(",", "") == hugeNumber
assert formattedNumber.count(",") == 1666

# A canceled generation must never deliver stale indexes or completion events.
indexCalls = len(indexEvent.calls)
doneCalls = len(doneEvent.calls)
driver._generation = 2
driver._notifyIndexes([99], 1)
driver.speaking = True
driver.done(1)
assert len(indexEvent.calls) == indexCalls and len(doneEvent.calls) == doneCalls
assert driver.speaking
driver.speaking = True
driver.done()
assert not driver.speaking and len(doneEvent.calls) == 1
print("Driver release regressions: OK")
