VodClipper
==========
Turns a Twitch VOD into vertical TikTok clips. It finds the best moments,
lets you review and trim them, then renders 1080x1920 MP4s with captions.
Everything runs on your own PC.

MAKE THE EXE (once)
  1. Unzip this folder somewhere simple, like C:\VodClipper
  2. Double-click build_exe.bat and wait 5-10 minutes.
     It sets up Python if needed, packages everything into VodClipper.exe,
     and puts a VodClipper shortcut on your desktop.
  3. From then on, just open VodClipper from the desktop shortcut.
     The finished app is in the dist\VodClipper folder. Copy that whole folder
     to use it on another PC; that PC doesn't need Python.
  Windows may show a SmartScreen warning the first time (the exe isn't signed):
  More info > Run anyway.

  Prefer a single file? Run:  build_exe.bat onefile
  (starts slower, because it unpacks itself each launch)

  Don't want an exe? install.bat then run.bat runs it from source instead.

USE
  1. Open VodClipper. It opens in its own window.
     If ffmpeg or the downloaders are missing it offers a "Download them" button.
  2. Paste a VOD link (https://www.twitch.tv/videos/123456789), or press
     Browse to pick a video on your PC, then press Find clips. Recent VODs
     appear on the start screen so you can jump back in without re-downloading.
  3. Moving the Clips, Length or Chat vs audio sliders updates the list instantly.
  4. Click a clip to preview it. Trim with the -5s/+5s buttons, or play to the
     exact spot and press Start here / End here. Click anywhere on the hype
     timeline and press Add clip here to grab your own moment.
     Keys: Space plays/pauses, Left/Right arrows jump 5 seconds.
  5. Optional: type a title for a clip. It shows as a white banner at the top.
  6. Pick a layout:
       Cam + game  facecam on top, gameplay below (drag a box over your cam)
       Full crop   gameplay cropped to fill the screen
       Blur fit    the whole 16:9 frame over a blurred background
     The phone preview shows how it will look.
  7. Press Render. Clips are saved in your Videos\VodClipper folder
     (More options > Clips folder > Open).

HOW CLIPS ARE PICKED
  - Chat: sudden surges in messages, weighted for KEKW, LUL, POG, "clip it",
    ????? and similar. Chat reacts a few seconds late, so it's shifted back
    (More options > Chat delay).
  - Audio: moments much louder than the few minutes around them. Twitch's
    muted (DMCA) sections are ignored instead of counted as boring.
  - With no chat log it ranks by audio only.
  - Local video? Put its chat next to it as <videoname>.chat.json, or choose
    the file under More options. TwitchDownloader chat JSON works.

CAPTIONS
  Made with Whisper. The first render with captions downloads the model
  (a few hundred MB). "Good" is a solid default. With an NVIDIA GPU and
  CUDA 12 + cuDNN installed, Whisper uses the GPU; otherwise the CPU.
  Rendering uses your GPU's video encoder (NVIDIA, Intel or AMD) when it can,
  and falls back to the CPU on its own.

TROUBLESHOOTING
  - Twitch downloads suddenly fail: More options > Update downloader.
    No rebuild needed.
  - Subscriber-only VOD: More options > Sign in with > the browser where you're
    logged in to Twitch (close that browser first). Firefox works best.
  - The window doesn't open: VodClipper falls back to your web browser.
    Installing Microsoft's "WebView2 Runtime" brings the app window back.
  - Preview won't play: some files (MKV, HEVC) can't play in the preview.
    Finding and rendering clips still works.
  - Something else broke: send the log file at
    %LOCALAPPDATA%\VodClipper\vodclipper.log

Only clip VODs you own or have permission to repost.
