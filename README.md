# Scoreboard - A Video Overlay Generator

Scoreboard creates a video file that can be used as an overlay in your favourite video editing software. It is designed to create a scoreboard for home-edited sport movies, but could be used for more generic purposes.

Scoreboard takes two inputs:

1. A CSV (Comma-Separated Values) file that provides data for the scoreboard. Each line has a timestamp in seconds followed by the updated values of other parameters (such as the home-team and away-team scores).
2. The scoreboard HTML template. Scoreboard will render this HTML template for each frame of the video, making sure to use the values from the CSV file at the right timestamps.

The output is a movie file that can be imported into your favourite movie editing software and added to your home video in picture-in-picture mode.

Scoreboard supports alpha-channel HTML templates (i.e. transparency).

# Requirements

 - Python 3.12
 - FFmpeg
 
# Setup

1. Setup a Python virtual environment

```
> python3 -m venv .env
> source .env/bin/activate
```

2. Install the Python dependencies with

```
> pip install -r requirements.txt
```

3. Install the playwright browser

```
> playwright install chromium
```

# Usage

## CSV Input file

The CSV file should have a format similar to

```
timestamp, name1, name2, ...
0.0,  0, 0, ..
12.0, 2, 0, ...
25.2, 2, 2, ...
```

where the first line defines the name of the template variables. The first column should always be called `timestamp` and its values should be in seconds. There can be as many additional columns as required, the first line label is used to match any corresponding placeholders in the HTML template.

## HTML Template

The scoreboard HTML template can be any standard HTML/CSS code. It should contain placeholders for the values specified in both the CSV file and on the command line with the `--set` option. The placeholder format is

```
  {{variable_name}}
```

where `variable_name` matches the name specified in the first line of the CSV file, or from the `--set` command line option.

Typically, the CSV file should provide values that change over the length of the video (e.g. the game score), while the command line `--set` options should provide values that are constant (e.g. the name of the teams).

## Example

```
scoreboard.py --set "team1=Los Angeles" --set "team2=Chicago" examples/basketball_scores.csv examples/basketball_template2.html overlay.mov
```

# Video Output

Scoreboard uses the open-source `ffmpeg` tool to generate its video output. By default, it uses the `prores` codec as it supports transparency (alpha-channel) and has good support in tools such as `iMovie`. You can select a different video codec with the `-c` option, for example `-c h264`. `ffmpeg -codecs` will display the available codecs of your ffmpeg installation. For `iMovie` on MacOS, we recommend using the default `prores` codec and save the output into a `.mov` file (Quicktime format).

