VodClipper
==========
Turns a Twitch VOD into vertical TikTok clips. It finds the best moments,
lets you review and trim them, then renders 1080x1920 MP4s with captions.
Everything runs on your own PC.

INSTALL
  Download VodClipper-Setup.exe from the repo's Releases page and run it.
  It installs VodClipper for your user (no admin prompt) and adds Start menu
  and desktop shortcuts. Windows may show a SmartScreen warning because the
  app isn't signed: More info > Run anyway.

  No-install option: VodClipper-portable.zip. Unzip anywhere and run
  VodClipper.exe. To remove it, delete the folder.

UNINSTALL
  Any of these:
    - Settings > Apps > Installed apps > VodClipper > Uninstall
    - Start menu > Uninstall VodClipper
    - In VodClipper: More options > Storage > Uninstall
  It removes the app, its downloaded tools, caption models and logs, then asks
  whether to delete your VODs and clips too (it keeps them unless you say Yes).

BUILD IT YOURSELF (optional)
  Every push to the repo builds and tests the installer on Windows (GitHub
  Actions) and publishes it as the "latest" release. To build on your own PC
  instead, double-click build_exe.bat.

USE
  1. Open VodClipper. It opens in its own window.
     If ffmpeg or the downloaders are missing it offers a "Download them" button.
  2. Paste a VOD link (https://www.twitch.tv/videos/123456789), or press
     Browse to pick a video on your PC, then press Find clips. Recent VODs
     appear on the start screen so you can jump back in without re-downloading.
  3. Review: the suggested clips line up on the left and play one at a time
     in the middle, already in TikTok format (your layout, captions and title,
     live). For each one:
       Keep and render (Enter)   sends it to the render queue
       Skip (X)                  sets it aside (see the Skipped tab)
       Up / Down arrows          previous / next clip, like scrolling TikTok
     Trim with the -5s/+5s buttons, or play to the exact spot and press Here.
     Click anywhere on the hype timeline to preview any moment and make a clip
     there. Moving the Clips, Length or Chat vs audio sliders re-ranks instantly.
  4. Auto titles: VodClipper listens to each clip (Whisper) and reads how chat
     reacted, then writes a title: usually the best line you said near the
     peak, otherwise one that fits the vibe (funny, hype, shock, fail...).
     Press New title (T) to cycle through other ideas, or type your own.
     Each clip also gets a post caption with hashtags; Copy caption puts it
     on your clipboard. Swear words are masked in titles.
  5. Format: the right-hand Layout tab sets the layout:
       Cam + game  facecam on top, gameplay below (drag a box over your cam)
       Full crop   gameplay cropped to fill the screen
       Blur fit    the whole 16:9 frame over a blurred background
     The Captions tab sets caption style, size, accuracy and language.
  6. Render queue: kept clips render one after another in the background
     while you keep reviewing. Press Watch on a finished one to play the real
     file in the phone. Files are named after their titles, with the post
     caption saved next to each as a .txt. They're in your Videos\VodClipper
     folder (Open folder button).

FREEING UP SPACE
  Downloaded VODs are big (several GB per stream). The start screen and
  More options > Storage show how much space VodClipper is using:
    Delete VODs       removes downloaded streams (and their chat/thumbnails).
                      Your rendered clips are kept. Videos of your own that you
                      opened with Browse are never deleted.
    Delete all clips  removes every rendered TikTok clip. VODs are kept.
  Both ask for confirmation first.

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
