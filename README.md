# Whisper Dictation App

A macOS menu bar application that allows you to easily dictate text using Whisper speech recognition. Simply hold the Right Option key to record your voice, and the app will automatically transcribe your speech into text.

## Features

- Convenient menu bar interface with recording status indicator
- Hold Right Option key to record
- Automatic text transcription using Whisper v3 via Fireworks AI
- Visual recording indicator

## Requirements

- macOS (required for rumps menu bar functionality)
- Python 3.8 or newer
- Fireworks AI API key (a default is provided but you may want to use your own)

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd whisper_app
   ```

2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

   If you're using a virtual environment (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On macOS/Linux
   pip install -r requirements.txt
   ```

3. API Key Configuration (Optional):
   
   The app uses a default Fireworks AI API key. If you want to use your own:
   - Sign up at [Fireworks AI](https://fireworks.ai/)
   - Get your API key
   - Replace the `FIREWORKS_API_KEY` value in `dictation.py`

## Usage

1. Start the application:
   ```bash
   python dictation.py
   ```

2. The app will appear in your menu bar with a white circle icon.

3. To use dictation:
   - Hold the Right Option key to start recording
   - Speak clearly
   - Release the Right Option key to stop recording and process the audio
   - The transcribed text will be typed at the current cursor position

4. The menu bar icon turns red during recording.

5. To quit the application, click on the menu bar icon and select "Quit".

## Troubleshooting

- **Permissions**: The app needs microphone access. If prompted, allow access in System Preferences > Security & Privacy > Privacy > Microphone.
- **Menu bar icon doesn't appear**: Ensure that rumps and PyQt are properly installed.
- **Transcription issues**: Check your internet connection as the app uses the Fireworks AI API for transcription.
- **Recording issues**: Verify your microphone is working and set as the default input device.

## Rate Limiting

The application limits API calls to Fireworks to 10 per minute to avoid excessive usage.

## License

[Your License Information] 