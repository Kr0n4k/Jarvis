import datetime
import json
import os
import queue
import random
import struct
import subprocess
import sys
import time
from ctypes import POINTER, cast
import pvporcupine
import simpleaudio as sa
import vosk
import yaml
from comtypes import CLSCTX_ALL
from fuzzywuzzy import fuzz
from pvrecorder import PvRecorder
from pycaw.pycaw import (
    AudioUtilities,
    IAudioEndpointVolume
)
from rich import print

import config
import tts
import requests
from pprint import pprint
from pathlib import Path

url = "https://api.intelligence.io.solutions/api/v1/chat/completions"

CDIR = os.getcwd()

porcupine = pvporcupine.create(access_key=config.PICOVOICE_TOKEN,
                                keywords=['jarvis'],
                                sensitivities=[1])

model = vosk.Model("model_small")
sample_rate = 16000
device = 0
kaldi_rec = vosk.KaldiRecognizer(model, sample_rate)
q = queue.Queue()

def play_sound(phrase, wait_done=True):
    """Play a sound based on the given phrase."""
    global recorder
    sounds = {
        "greet": f"greet{random.choice([1, 2, 3])}.wav",
        "ok": f"ok{random.choice([1, 2, 3])}.wav",
        "not_found": "not_found.wav",
        "thanks": "thanks.wav",
        "run": "run.wav",
        "stupid": "stupid.wav",
        "ready": "ready.wav",
        "off": "off.wav",
    }
    filename = f"{CDIR}\\sound\\{sounds.get(phrase, 'not_found.wav')}"

    if wait_done:
        recorder.stop()  # Stop recording to avoid sound distortion

    wave_obj = sa.WaveObject.from_wave_file(filename)
    play_obj = wave_obj.play()

    if wait_done:
        play_obj.wait_done()
        recorder.start()

def q_callback(indata, frames, time, status):
    q.put(bytes(indata))

def filter_cmd(raw_voice: str, aliases, tbr):
    """
    Filter the raw voice input by removing aliases and trigger words.

    Args:
        raw_voice (str): The raw voice input.
        aliases (list): List of aliases to remove.
        tbr (list): List of trigger words to remove.

    Returns:
        str: The filtered command.
    """
    cmd = raw_voice
    for x in aliases + tbr:
        cmd = cmd.replace(x, "").strip()
    return cmd

def recognize_cmd(cmd: str, va_cmd_list):
    """
    Recognize the command from the given input string.

    Args:
        cmd (str): The filtered voice command.
        va_cmd_list (dict): Dictionary of phrases mapped to commands.

    Returns:
        dict: A dictionary containing the recognized command and its confidence percentage.
    """
    best_match = {'cmd': None, 'percent': 0}

    for phrase, command in va_cmd_list.items():
        similarity = fuzz.ratio(cmd, phrase)
        if similarity > best_match['percent']:
            best_match = {'cmd': command, 'percent': similarity}

    return best_match

def execute_cmd(cmd: dict):
    """Execute a command based on the recognized input."""
    if not cmd:
        return

    action = cmd.get('action')
    exe_path = cmd.get('exe_path')
    exe_args = cmd.get('exe_args') or []  # Убедитесь, что exe_args всегда список

    if action == "ahk" and exe_path:
        full_path = os.path.join(CDIR, exe_path)  # Построение полного пути
        if not os.path.exists(full_path):
            return
        subprocess.Popen([full_path] + exe_args)
        play_sound("ok")
    elif action == "cli":
        cli_cmd = cmd.get('cli_cmd')
        if not cli_cmd:
            return
        subprocess.Popen(cli_cmd, shell=True)
        play_sound("ok")

    elif action == "mute":
        mute_sound(True if cmd.get('mute') else False)
    elif action == "shutdown":
        shutdown()
    elif action == "voice":
        sound = cmd.get('sound')
        if not sound:
            return
        play_sound(sound)

def mute_sound(mute: bool):
    """Mute or unmute the system sound."""
    devices = AudioUtilities.GetSpeakers()
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    volume = cast(interface, POINTER(IAudioEndpointVolume))
    volume.SetMute(1 if mute else 0, None)
    play_sound("ok")

def shutdown():
    """Shut down the assistant."""
    play_sound("off", True)
    porcupine.delete()
    exit(0)

def va_respond(voice: str):
    """Process the recognized voice command and respond accordingly."""
    global recorder, message_log

    filtered_cmd = filter_cmd(voice, config.VA_ALIAS, config.VA_TBR)
    recognized_cmd = recognize_cmd(filtered_cmd, VA_CMD_LIST)


    if not recognized_cmd['cmd']:
        return handle_unrecognized_command(voice)

    if recognized_cmd['percent'] < 70:
        return handle_low_confidence_command(voice)

    execute_cmd(recognized_cmd['cmd'])
    return True

def handle_unrecognized_command(voice: str):
    """Handle cases where no command is recognized."""
    play_sound("not_found")
    time.sleep(1)
    return False

def handle_low_confidence_command(voice: str):
    """Handle cases where the command confidence is too low."""
    if fuzz.ratio(voice.split()[0].strip(), "скажи") > 75:
        return handle_gpt_response(voice)
    else:
        play_sound("not_found")
        time.sleep(1)
        return False

def handle_gpt_response(voice: str):
    """Generate a GPT-based response for the user."""
    global recorder, message_log
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.AI_TOKEN}",
    }

    data = {
        "model": "deepseek-ai/DeepSeek-R1",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant"
            },
            {
                "role": "user",
                "content": voice
            }
        ],
    }

    response = requests.post(url, headers=headers, json=data)
    response_data = response.json()
    text = response_data['choices'][0]['message']['content'].split('</think>\n\n')[1]

    recorder.stop()
    tts.va_speak(text)
    time.sleep(0.5)
    recorder.start()
    return False

def initialize_recorder():
    """Initialize the PvRecorder."""
    recorder = PvRecorder(device_index=0, frame_length=porcupine.frame_length)
    recorder.start()
    return recorder

def handle_keyword_detection():
    """Handle the detection of the wake word."""
    recorder.stop()
    play_sound("greet", True)
    recorder.start()  # Prevent self-recording
    return time.time()

def process_audio_input():
    """Process audio input and handle recognized commands."""
    pcm = recorder.read()
    sp = struct.pack("h" * len(pcm), *pcm)

    if kaldi_rec.AcceptWaveform(sp):
        voice_text = json.loads(kaldi_rec.Result())["text"]
        if va_respond(voice_text):
            return time.time()
    return None

def main_loop():
    """Main loop for the voice assistant."""
    global recorder
    ltc = time.time() - 1000  # Last time command was processed

    while True:
            pcm = recorder.read()
            keyword_index = porcupine.process(pcm)

            if keyword_index >= 0:
                ltc = handle_keyword_detection()

            while time.time() - ltc <= 10:
                new_ltc = process_audio_input()
                if new_ltc:
                    ltc = new_ltc
                    break

def load_va_commands(directory="commands"):
    """Load all VA command files from the specified directory into a single dictionary."""
    va_cmd_list = {}
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".yaml"):
                with open(os.path.join(root, file), 'rt', encoding='utf8') as f:
                    data = yaml.safe_load(f)
                    for item in data.get('list', []):
                        command = item.get('command', {})
                        phrases = item.get('phrases', [])
                        for phrase in phrases:
                            va_cmd_list[phrase] = command  # Сохраняем весь объект команды
    return va_cmd_list

# Load commands at the start of the program
VA_CMD_LIST = load_va_commands()

if __name__ == "__main__":
    recorder = initialize_recorder()
    play_sound("run")
    time.sleep(0.5)
    main_loop()