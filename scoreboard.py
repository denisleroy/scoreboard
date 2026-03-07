#!/usr/bin/env python3

import csv
import os, sys
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image
import numpy as np
import io
import tempfile
import argparse
import shlex

class ScoreBoard:
    def __init__(self, args):
        """
        Initialize the overlay generator.
        
        Args:
            width: Video width in pixels
            height: Video height in pixels
            fps: Frames per second
        """
        self.args = args
        self.width = 1920
        self.height = 1080
        self.fps = args.fps
        self.driver = None
        self.page = None
        self.p = None
        self.codec = args.codec
        self.global_params = {}

        if args.params:
            for p in args.params:
                if not '=' in p:
                    raise Exception("--set value should use the format name=value")
                else:
                    sv = p.split('=')
                    self.global_params[sv[0]] = sv[1]
        
    def setup_browser(self):
        self.pwm = sync_playwright()
        self.pw = self.pwm.start()
        self.driver = self.pw.chromium.launch()
        context = self.driver.new_context(
            viewport={"width": self.width, "height": self.height},
            device_scale_factor=1)
        self.page = context.new_page()
        
    def cleanup_browser(self):
        """Close the browser."""
        if self.driver:
            self.driver.close()

    def parse_timestamp(self, strval):
        if 'm' in strval:
            parts = strval.split('m')
            return 60*int(parts[0]) + float(parts[1])
        else:
            return float(strval)
            
    def read_csv_data(self, csv_path):
        """
        Read CSV file with timestamps and parameter values.
        
        CSV format:
        timestamp,param1,param2,...
        0.0,value1,value2,...
        5.5,value1,value2,...
        
        Returns:
            List of dictionaries with timestamp and parameters
        """
        data = []
        with open(csv_path, 'r') as f:
            prev_row = None
            reader = csv.DictReader(f)
            for row in reader:
                # Convert timestamp to float
                row['timestamp'] = self.parse_timestamp(row['timestamp'])
                data.append(row)
                if prev_row:
                    if row['timestamp'] <= prev_row['timestamp']:
                        print("Out of order timestamp", row['timestamp'])
                        sys.exit(-1)
                prev_row = row
        
        # Sort by timestamp
        data.sort(key=lambda x: x['timestamp'])
        return data
    
    def render_html_to_image(self, html_content, output_path):
        """Render HTML content to PNG image using Selenium."""
        # Create a temporary HTML file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html') as f:
            f.write(html_content)
            temp_html = f.name
            f.flush()
        
            # Load the HTML file
            html_absolute = Path(temp_html).resolve().as_uri()
            self.page.goto(html_absolute)

            # Wait for page load
            self.page.wait_for_load_state("networkidle")

            # Take screenshot
            self.page.screenshot(path=output_path,
                                 full_page=False,
                                 omit_background=True)

    def fill_template(self, template_content, global_params, params):
        """
        Fill HTML template with parameter values.
        
        Placeholders in template: {{param_name}}
        """
        result = template_content
        allparams = params.copy()
        allparams.update(global_params)
        for key, value in allparams.items():
            if key != 'timestamp':
                placeholder = '{{' + key + '}}'
                result = result.replace(placeholder, str(value))
        return result
    
    def build_segments(self, data, duration):
        """
        Build a list of segments, where each segment is a unique set of
        parameters and the time span it covers.

        Returns:
            List of dicts: [{"params": {...}, "start": float, "end": float}, ...]
        """
        segments = []
        for i, row in enumerate(data):
            start = row['timestamp']
            if i + 1 < len(data):
                end = data[i + 1]['timestamp']
            else:
                end = duration
            if end > start:
                segments.append({
                    "params": {k: v for k, v in row.items() if k != 'timestamp'},
                    "start": start,
                    "end": end,
                })
        return segments

    def generate_overlay(self, csv_path, template_path, output_path):
        """
        Generate video overlay from CSV data and HTML template.

        Only renders one image per unique parameter change, then uses
        FFmpeg's concat demuxer with per-segment durations to build the
        video. This avoids generating (and copying) thousands of identical
        frames.
        
        Args:
            csv_path: Path to CSV file with timestamps and parameters
            template_path: Path to HTML template file
            output_path: Path for output MP4 file
        """
        print("Reading CSV data...")
        data = self.read_csv_data(csv_path)
        
        if not data:
            raise ValueError("CSV file is empty or invalid")
        
        print("Reading HTML template...")
        with open(template_path, 'r') as f:
            template = f.read()
        
        print("Setting up browser...")
        self.setup_browser()

        if self.args.duration:
            duration = self.args.duration
        else:
            duration = data[-1]['timestamp'] + 1.0  # Add 1 second after last change

        segments = self.build_segments(data, duration)
        total_frames = int(duration * self.fps)
        
        # Create temporary directory for frames
        with tempfile.TemporaryDirectory(delete=not self.args.keep) as temp_dir:
            print(f"Rendering {len(segments)} unique frames "
                  f"(instead of {total_frames} total)...")

            concat_entries = []

            for idx, seg in enumerate(segments):
                seg_duration = seg['end'] - seg['start']
                frame_path = os.path.join(temp_dir, f'segment_{idx:06d}.png')

                html_content = self.fill_template(
                    template, self.global_params, seg['params'])
                self.render_html_to_image(html_content, frame_path)
                self.crop_transparent_borders(frame_path)

                concat_entries.append({
                    "file": frame_path,
                    "duration": seg_duration,
                })

                print(f"  Rendered segment {idx + 1}/{len(segments)} "
                      f"({seg_duration:.2f}s)")

            print("Encoding video with FFmpeg...")
            self.encode_video_concat(concat_entries, output_path)
        
        self.cleanup_browser()
        print(f"Wrote: {output_path}")
    
    def encode_video_concat(self, concat_entries, output_path):
        """
        Encode video using FFmpeg's concat demuxer.

        Each entry specifies an image file and how long it should be shown,
        so FFmpeg handles the "frame duplication" internally without us
        needing to produce thousands of identical PNGs on disk.
        """
        # Write the concat list file
        concat_dir = os.path.dirname(concat_entries[0]['file'])
        concat_path = os.path.join(concat_dir, 'concat.txt')
        with open(concat_path, 'w') as f:
            for entry in concat_entries:
                # FFmpeg concat format: file, then duration directive
                f.write(f"file {shlex.quote(entry['file'])}\n")
                f.write(f"duration {entry['duration']:.6f}\n")
            # Repeat the last file so the final duration is honoured
            f.write(f"file {shlex.quote(concat_entries[-1]['file'])}\n")

        cmd = [
            'ffmpeg',
            '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_path,
            '-c:v', self.codec,
            '-preset', 'medium',
            '-crf', '23',
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-pix_fmt', 'yuva420p',
            '-movflags', '+faststart',
            '-r', str(self.fps),   # Force constant output frame rate
        ]
        if self.args.ffmpeg_extras:
            for xarg in self.args.ffmpeg_extras:
                cmd.append(xarg)
        cmd.append(output_path)

        print("Running command:\n   ", " ".join(map(shlex.quote, cmd)))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Exception(proc.stderr)

    def encode_video(self, frames_dir, output_path):
        """Encode PNG frames into MP4 video using FFmpeg (legacy frame-sequence mode)."""
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file
            '-framerate', str(self.fps),
            '-i', os.path.join(frames_dir, 'frame_%06d.png'),
            '-c:v', self.codec,
            '-preset', 'medium',
            '-crf', '23',
            '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-pix_fmt', 'yuva420p',  # Support alpha channel
            '-movflags', '+faststart']
        if self.args.ffmpeg_extras:
            for xarg in self.args.ffmpeg_extras:
                cmd.append(xarg)
        cmd.append(output_path)

        print("Running command:\n   ", " ".join(map(shlex.quote, cmd)))
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Exception(proc.stderr)

    def crop_transparent_borders(self, image_path: str):
        """
        Crop transparent borders from a PNG image.

        Args:
            image_path: Path to the PNG image to crop in-place
        """
        img = Image.open(image_path)

        # Ensure image has alpha channel
        if img.mode != 'RGBA':
            return

        # Convert to numpy array for efficient processing
        img_array = np.array(img)

        # Get the alpha channel
        alpha = img_array[:, :, 3]

        # Find rows and columns that contain non-transparent pixels
        non_transparent_rows = np.where(alpha.max(axis=1) > 0)[0]
        non_transparent_cols = np.where(alpha.max(axis=0) > 0)[0]

        # Check if image is completely transparent
        if len(non_transparent_rows) == 0 or len(non_transparent_cols) == 0:
            print("Warning: Image is completely transparent, skipping crop")
            return

        # Get bounding box
        top = non_transparent_rows[0]
        bottom = non_transparent_rows[-1]
        left = non_transparent_cols[0]
        right = non_transparent_cols[-1]

        left = left - 20
        if left < 0:
            left = 0
        top = top - 20
        if top < 0:
            top = 0
        right = right + 20
        if right >= img.width:
            right = img.width-1
        bottom = bottom + 20
        if bottom >= img.height:
            bottim = img.height - 1

        # Crop the image
        cropped = img.crop((left, top, right, bottom))

        # Save back to the same file
        cropped.save(image_path)

        original_size = f"{img.width}x{img.height}"
        cropped_size = f"{cropped.width}x{cropped.height}"

def main():
    parser = argparse.ArgumentParser(
        description='Generate a video overlay from CSV data and HTML template'
    )
    parser.add_argument('csv_file', help='Path to CSV file with timestamps and parameters')
    parser.add_argument('template_file', help='Path to HTML template file')
    parser.add_argument('output_file', help='Path for output video file')
    parser.add_argument('-d', '--duration', type=float,
                        help="Total video duration in seconds (default: last timestamp + 1s)")
    parser.add_argument('-f', '--fps', type=int, default=5, help='Frames per second (default: 5)')
    parser.add_argument('-c', '--codec', default='prores', dest='codec',
                        help='Output video format (default: prores)')
    parser.add_argument('--set', action='append', metavar='NAME=VAL', dest='params',
                        help='Set additional value in HTML template')
    parser.add_argument('-E', action='append', dest='ffmpeg_extras',
                        help="Optional additional FFMPEG argument")
    parser.add_argument('--keep', action='store_true', default=False, help='Do not delete generated frames')

    args = parser.parse_args()

    try:
        generator = ScoreBoard(args)
    
        generator.generate_overlay(
            csv_path=args.csv_file,
            template_path=args.template_file,
            output_path=args.output_file
        )
    except Exception as e:
        print("Error:", e)
        sys.exit(-1)


if __name__ == '__main__':
    main()
