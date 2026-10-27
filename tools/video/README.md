# Demo video tools

How the demo video was made (docs/video/script.md, "As recorded"). Windows, Node 22+, the
frontend's Playwright, Edge, OBS 32 with obs-websocket enabled, ffmpeg. Paths are those of the
author's machine (`C:/pundit-video`).

1. Start Edge for the recording, full screen on the 1920×1080 monitor, with a debugging port:
   `msedge --remote-debugging-port=9333 --user-data-dir=C:\pundit-video\edge-rec2 --no-first-run
   --disable-sync --start-fullscreen --window-position=0,0 --window-size=1920,1080 --lang=en-US
   --autoplay-policy=no-user-gesture-required about:blank`.
   InPrivate has no online (Natural) voices; a normal profile does.
2. `node obs-setup.mjs`: scene "Video" = the primary monitor (Windows Graphics Capture, no
   cursor) + the desktop's sound; `node obs-volume.mjs -5` keeps the peaks under 0 dB.
3. `node narrate.mjs`: the English voice-over, one WAV per line of docs/video/voiceover.txt.
4. `node record.mjs s1 s2r prep s3 s4 s5 s6 s7 s8`: the takes on the public Space (the Space
   allows 3 new private matches per address in 10 minutes).
5. `node assemble.mjs`: the cut, the voice-over, English subtitles for the Vietnamese parts, the
   closing card → `pundit-copilot-demo.mp4`, `.srt` (burned in) and `.en.srt` (for YouTube).

obs-websocket's password is read from `%APPDATA%\obs-studio\user.ini` at run time and never
printed.
