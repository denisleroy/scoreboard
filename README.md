# scoreboard
Scoreboard - A Video Overlay Generator

Scoreboard creates a video file that can be used as an overlay in your favourite video editing software. It is designed to create a scoreboard for home-edited sport movies, but could be used for more generic purposes.

Scoreboard takes two inputs:

1. A CSV (Comma-Separated Values) file that provides data for the scoreboard. Each line has a timestamp in seconds followed by the updated values of other parameters (such as the home-team and away-team scores).
2. The scoreboard HTML template. Scoreboard will render this HTML template for each frame of the video, making sure to use the values from the CSV file at the right timestamps.

The output is a movie file that can be imported into your favourite movie editing software and added to your home video in picture-in-picture mode.

Scoreboard supports alpha-channel HTML templates (i.e. transparency).

# Requirements

 - Python 3.x
 - Ffmpeg
 
# Setup

1. Install the Python dependencies with

```
pip install -r requirements.txt
```

2. Install the playwright browser

```
playwright install chromium
```

# Usage

```
scoreboard.py examples/basketball_scores.csv examples/basketball_template2.html overlay.mov
```

# Video Output

Scoreboard uses the open-source `ffmpeg` tool to generate its video output. By default, it uses the `prores` codec as it supports transparency (alpha-channel) and has good support in tools such as `iMovie`. You can select a different video codec with the `-c` option, for example `-c h264`. `ffmpeg -codecs` will display the available codecs of your ffmpeg installation. For `iMovie` on MacOS, we recommend using the default `prores` codec and save the output into a `.mov` file (Quicktime format).

