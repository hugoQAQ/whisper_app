import sounddevice as sd
import numpy as np
import soundfile as sf
import pyautogui
import time
import os
import asyncio
from fireworks.client.audio import AudioInference
import io
from pynput import keyboard
import threading
from PIL import Image
import tempfile
from PIL import ImageDraw
from PyQt6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QObject
from PyQt6.QtGui import QPainter, QColor, QCursor, QIcon
import rumps
import sys
from multiprocessing import Process, Queue, Event
from collections import deque
from datetime import datetime, timedelta

# Configuration
SAMPLE_RATE = 16000  # Whisper expects 16kHz
CHANNELS = 1         # Mono audio
ICON_SIZE = 32
RECORDING_INDICATOR_SIZE = 20
MIN_OPACITY = 0.3
MAX_OPACITY = 1.0
OPACITY_STEP = 0.1
UPDATE_INTERVAL = 50  # ms
MAX_RECORDING_SECONDS = 300  # Maximum recording duration
MAX_BUFFER_SIZE = SAMPLE_RATE * MAX_RECORDING_SECONDS  # Maximum buffer size
API_CALLS_PER_MINUTE = 10  # Rate limit for API calls

# Fireworks API Configuration
FIREWORKS_API_KEY = "fw_3ZcCJovz27asUH7qTURWEWAG"

# Global state
is_recording = False
should_exit = False
loop = None  # Store the event loop
icon = None  # Global icon reference
recording_stream = None  # For continuous recording
recorded_frames = []  # To store audio frames
recording_window = None
animation_running = False
status_queue = None  # For communicating recording status

# Add a new queue for icon updates
class SignalHandler(QObject):
    show_indicator = pyqtSignal()
    hide_indicator = pyqtSignal()
    
    def __init__(self):
        super().__init__()

# Replace the PyQt DictationApp with the Rumps version
class DictationApp(rumps.App):
    def __init__(self, command_queue, status_queue, shutdown_event):
        super().__init__("Dictation", icon="off.png", quit_button=None)
        self.command_queue = command_queue
        self.status_queue = status_queue
        self.shutdown_event = shutdown_event
        self.menu = [
            rumps.MenuItem("Hold Right Option to record", callback=None),
            rumps.MenuItem("Quit", self.quit_app)
        ]
        
        # Start a timer to check the status queue
        self.timer = rumps.Timer(self.check_status, 0.1)
        self.timer.start()
    
    def check_status(self, _):
        try:
            if not self.status_queue.empty():
                is_recording = self.status_queue.get_nowait()
                # Update the icon based on recording status
                self.icon = 'on.png' if is_recording else 'off.png'
        except Exception as e:
            print(f"Error checking status queue: {e}")
    
    @rumps.clicked("Quit")
    def quit_app(self, _):
        """Handle quit from the menu."""
        self.command_queue.put("quit")
        self.shutdown_event.set()
        rumps.quit_application()

class RecordingIndicator(QWidget):
    def __init__(self):
        super().__init__()
        self.setup_window()
        self.setup_animation()
    
    def setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(RECORDING_INDICATOR_SIZE, RECORDING_INDICATOR_SIZE)
        self.hide()
    
    def setup_animation(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.timer.start(UPDATE_INTERVAL)
        self.opacity = MAX_OPACITY
        self.fade_out = True
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(255, 0, 0, int(255 * self.opacity)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(2, 2, 16, 16)
    
    def update(self):
        self.update_opacity()
        self.update_position()
        super().update()
    
    def update_opacity(self):
        if self.fade_out:
            self.opacity = max(MIN_OPACITY, self.opacity - OPACITY_STEP)
            if self.opacity <= MIN_OPACITY:
                self.fade_out = False
        else:
            self.opacity = min(MAX_OPACITY, self.opacity + OPACITY_STEP)
            if self.opacity >= MAX_OPACITY:
                self.fade_out = True
    
    def update_position(self):
        cursor_pos = QCursor.pos()
        self.move(cursor_pos.x() - RECORDING_INDICATOR_SIZE//2, 
                 cursor_pos.y() - RECORDING_INDICATOR_SIZE//2)

class RateLimiter:
    def __init__(self, calls_per_minute):
        self.calls_per_minute = calls_per_minute
        self.calls = deque()
    
    async def acquire(self):
        now = datetime.now()
        # Remove calls older than 1 minute
        while self.calls and now - self.calls[0] > timedelta(minutes=1):
            self.calls.popleft()
        
        if len(self.calls) >= self.calls_per_minute:
            # Wait until the oldest call is more than 1 minute old
            wait_time = 60 - (now - self.calls[0]).total_seconds()
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        
        self.calls.append(now)

# Create rate limiter instance
rate_limiter = RateLimiter(API_CALLS_PER_MINUTE)

class AudioRecorder:
    def __init__(self, status_queue):
        self.status_queue = status_queue
        self.is_recording = False
        self.recording_stream = None
        self.recorded_frames = deque(maxlen=MAX_BUFFER_SIZE)  # Use deque with max size
        self.loop = None
        self.start_time = None
    
    def start(self):
        if self.is_recording:
            return
        
        self.is_recording = True
        self.recorded_frames.clear()
        self.start_time = time.time()
        self.status_queue.put(True)
        print("Recording started...")
        
        self.recording_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            callback=self.audio_callback
        )
        self.recording_stream.start()
    
    def stop(self):
        if not self.is_recording:
            return
        
        self.is_recording = False
        self.status_queue.put(False)
        print("Recording stopped.")
        
        if self.recording_stream:
            self.recording_stream.stop()
            self.recording_stream.close()
            
            if self.recorded_frames:
                recording = np.concatenate(list(self.recorded_frames), axis=0)
                self.recorded_frames.clear()  # Clear memory after processing
                if self.loop:
                    asyncio.run_coroutine_threadsafe(process_audio(recording), self.loop)
    
    def audio_callback(self, indata, frames, time, status):
        if status:
            print(f"Status: {status}")
        
        # Check recording duration
        if self.start_time and time.time() - self.start_time > MAX_RECORDING_SECONDS:
            print("Maximum recording duration reached")
            self.stop()
            return
        
        self.recorded_frames.append(indata.copy())

def on_press(key):
    try:
        # Start recording when the right Option key is pressed
        if key == keyboard.Key.alt_r:
            start_recording()
    except AttributeError:
        pass

def on_release(key):
    try:
        # Stop recording when the right Option key is released
        if key == keyboard.Key.alt_r:
            stop_recording()
    except AttributeError:
        pass

def audio_callback(indata, frames, time, status):
    if status:
        print(f"Status: {status}")
    recorded_frames.append(indata.copy())

def start_recording():
    global is_recording, recording_stream, recorded_frames
    if is_recording:
        return
    
    is_recording = True
    recorded_frames = []
    # Send recording status to Rumps process
    try:
        status_queue.put(True)
    except NameError:
        print("Warning: status_queue not available")
    print("Recording started...")
    
    # Show the recording indicator using the signal
    signal_handler.show_indicator.emit()
    
    # Start recording stream
    recording_stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        callback=audio_callback
    )
    recording_stream.start()

def stop_recording():
    global is_recording, recording_stream, loop
    if not is_recording:
        return
    
    is_recording = False
    # Send recording status to Rumps process
    try:
        status_queue.put(False)
    except NameError:
        print("Warning: status_queue not available")
    print("Recording stopped.")
    
    # Hide the recording indicator using the signal
    signal_handler.hide_indicator.emit()
    
    if recording_stream:
        recording_stream.stop()
        recording_stream.close()
        
        if recorded_frames:
            recording = np.concatenate(recorded_frames, axis=0)
            if loop:
                asyncio.run_coroutine_threadsafe(process_audio(recording), loop)

async def process_audio(recording):
    try:
        # Apply rate limiting
        await rate_limiter.acquire()
        
        audio_file = save_audio(recording)
        audio_bytes = load_audio(audio_file)
        
        text = await transcribe_audio(audio_bytes)
        print(f"Transcribed text: {text}")
        type_text(text)
        
        # Clean up temporary file
        os.unlink(audio_file)
        
        # Clear memory
        del recording
        del audio_bytes
        
    except Exception as e:
        print(f"Error during recording/transcription: {str(e)}")

async def transcribe_audio(audio_bytes):
    print("Transcribing audio using Fireworks API with whisper-v3...")
    
    client = AudioInference(
        model="whisper-v3",
        base_url="https://audio-prod.us-virginia-1.direct.fireworks.ai",
        api_key=FIREWORKS_API_KEY,
    )
    
    try:
        start = time.time()
        result = await client.transcribe_async(audio=audio_bytes)
        print(f"Transcription took: {(time.time() - start):.3f}s")
        return result.text.strip()
    except Exception as e:
        print(f"Error during Fireworks API request: {str(e)}")
        return ""

def type_text(text):
    if not text:
        print("No text to type")
        return
    print(f"Typing text: {text}")
    
    # Use keyboard controller for direct input
    keyboard_controller = keyboard.Controller()
    
    # Type the text character by character
    for char in text:
        keyboard_controller.type(char)
        time.sleep(0.01)  # Small delay to prevent overwhelming the system
    
    # Add a space at the end
    keyboard_controller.type(' ')

def save_audio(recording):
    temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
    sf.write(temp_file.name, recording, SAMPLE_RATE)
    return temp_file.name

def load_audio(filename):
    with open(filename, 'rb') as f:
        return f.read()

async def main():
    global should_exit, loop
    loop = asyncio.get_event_loop()
    
    print("Starting dictation system...")
    print("Hold Right Option to record, release to stop")
    
    # Setup keyboard listener
    listener = keyboard.Listener(
        on_press=on_press,
        on_release=on_release
    )
    listener.start()
    
    try:
        while not should_exit:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\nReceived shutdown signal...")
    finally:
        listener.stop()
        print("Application shutdown complete.")

def run_pyqt(command_queue, status_queue, shutdown_event):
    """Run PyQt application process."""
    global signal_handler, indicator, qt_app
    
    # Create the Qt application
    qt_app = QApplication(sys.argv)
    
    # Create the recording indicator
    indicator = RecordingIndicator()
    
    # Create and set up the signal handler
    signal_handler = SignalHandler()
    signal_handler.show_indicator.connect(indicator.show)
    signal_handler.hide_indicator.connect(indicator.hide)
    
    # Start the async loop in a separate thread
    async_thread = threading.Thread(target=lambda: asyncio.run(main()))
    async_thread.daemon = True
    async_thread.start()
    
    # Create a timer to check the command queue
    def check_queue():
        try:
            if not command_queue.empty():
                command = command_queue.get_nowait()
                if command == "quit":
                    qt_app.quit()
        except Exception as e:
            print(f"Error checking queue: {e}")
    
    # Create a timer to keep Qt responsive and check the queue
    check_timer = QTimer()
    check_timer.timeout.connect(check_queue)
    check_timer.start(100)  # Check every 100ms
    
    try:
        # Run the Qt application
        qt_app.exec()
    except KeyboardInterrupt:
        global should_exit
        should_exit = True

def run_rumps(command_queue, status_queue, shutdown_event):
    """Run Rumps application process."""
    
    # Create and run the Rumps app
    app = DictationApp(command_queue, status_queue, shutdown_event)
    app.run()

def create_menu_icons():
    """Create the menu bar icons if they don't exist"""
    # Create off.png (white circle)
    if not os.path.exists('off.png'):
        size = (22, 22)  # Standard menu bar icon size for macOS
        off_image = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(off_image)
        draw.ellipse([2, 2, 20, 20], fill=(255, 255, 255, 255))
        off_image.save('off.png')
    
    # Create on.png (red circle)
    if not os.path.exists('on.png'):
        size = (22, 22)
        on_image = Image.new('RGBA', size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(on_image)
        draw.ellipse([2, 2, 20, 20], fill=(255, 0, 0, 255))
        on_image.save('on.png')

if __name__ == "__main__":
    # Create the menu bar icons
    create_menu_icons()
    
    # Create queues for inter-process communication
    command_queue = Queue()
    status_queue = Queue()
    shutdown_event = Event()
    
    # Start the Rumps process
    p_rumps = Process(target=run_rumps, args=(command_queue, status_queue, shutdown_event))
    p_rumps.start()
    
    # Run PyQt in the main process
    run_pyqt(command_queue, status_queue, shutdown_event)
    
    # Wait for the Rumps process to finish
    p_rumps.join()