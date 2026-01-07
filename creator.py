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
import shutil
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
        self.last_params = None
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
            reader = csv.DictReader(f)
            for row in reader:
                # Convert timestamp to float
                row['timestamp'] = float(row['timestamp'])
                data.append(row)
        
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
    
    def generate_overlay(self, csv_path, template_path, output_path):
        """
        Generate video overlay from CSV data and HTML template.
        
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
        
        total_frames = int(duration * self.fps)
        
        # Create temporary directory for frames
        with tempfile.TemporaryDirectory(delete=not self.args.keep) as temp_dir:
            print(f"Generating {total_frames} frames in {temp_dir}... ")
            
            current_data_idx = 0
            current_params = data[0]
            
            for frame_num in range(total_frames):
                timestamp = frame_num / self.fps
                
                # Update parameters if we've reached a new timestamp
                while (current_data_idx < len(data) - 1 and 
                       timestamp >= data[current_data_idx + 1]['timestamp']):
                    current_data_idx += 1
                    current_params = data[current_data_idx]
                
                # Fill template with current parameters
                html_content = self.fill_template(template, self.global_params, current_params)
                
                # Render to image
                frame_path = os.path.join(temp_dir, f'frame_{frame_num:06d}.png')

                if current_params != self.last_params:
                    self.render_html_to_image(html_content, frame_path)
                    self.crop_transparent_borders(frame_path)
                else:
                    shutil.copy(self.last_frame_path, frame_path)

                self.last_frame_path = frame_path
                self.last_params = current_params
                
                if frame_num % 30 == 0:
                    print(f"  Progress: {frame_num}/{total_frames} frames")
            
            print("Encoding video with FFmpeg...")
            self.encode_video(temp_dir, output_path)
        
        self.cleanup_browser()
        print(f"Wrote: {output_path}")
    
    def encode_video(self, frames_dir, output_path):
        """Encode PNG frames into MP4 video using FFmpeg."""
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

        # Crop the image
        cropped = img.crop((left, top, right + 1, bottom + 1))

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
