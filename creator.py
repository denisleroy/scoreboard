#!/usr/bin/env python3

import csv
import os
import subprocess
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image
import numpy as np
import io
import tempfile
import argparse
import shutil

def crop_transparent_borders(image_path: str) -> None:
    """
    Crop transparent borders from a PNG image.
    
    Args:
        image_path: Path to the PNG image to crop in-place
    """
    img = Image.open(image_path)
    
    # Ensure image has alpha channel
    if img.mode != 'RGBA':
        print("Warning: Image doesn't have alpha channel, skipping crop")
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
    print(f"Cropped transparent borders: {original_size} → {cropped_size}")

class VideoOverlayGenerator:
    def __init__(self, width=1920, height=1080, fps=30):
        """
        Initialize the overlay generator.
        
        Args:
            width: Video width in pixels
            height: Video height in pixels
            fps: Frames per second
        """
        self.width = width
        self.height = height
        self.fps = fps
        self.driver = None
        self.page = None
        self.p = None
        self.last_params = None
        
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
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(html_content)
            temp_html = f.name
        
        # Load the HTML file
        html_absolute = Path(temp_html).resolve().as_uri()
        self.page.goto(html_absolute)

        # Wait for page load
        self.page.wait_for_load_state("networkidle")

        # Take screenshot
        self.page.screenshot(
            path=output_path,
            full_page=False,
            omit_background=True)
            
    
    def fill_template(self, template_content, params):
        """
        Fill HTML template with parameter values.
        
        Placeholders in template: {{param_name}}
        """
        result = template_content
        for key, value in params.items():
            if key != 'timestamp':
                placeholder = '{{' + key + '}}'
                result = result.replace(placeholder, str(value))
        return result
    
    def generate_overlay(self, csv_path, template_path, output_path, duration=None):
        """
        Generate video overlay from CSV data and HTML template.
        
        Args:
            csv_path: Path to CSV file with timestamps and parameters
            template_path: Path to HTML template file
            output_path: Path for output MP4 file
            duration: Optional total video duration (if None, uses last timestamp)
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
        
        # Determine total duration
        if duration is None:
            duration = data[-1]['timestamp'] + 1.0  # Add 1 second after last change
        
        total_frames = int(duration * self.fps)
        
        # Create temporary directory for frames
        with tempfile.TemporaryDirectory() as temp_dir:
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
                html_content = self.fill_template(template, current_params)
                
                # Render to image
                frame_path = os.path.join(temp_dir, f'frame_{frame_num:06d}.png')

                if current_params != self.last_params:
                    self.render_html_to_image(html_content, frame_path)
                    crop_transparent_borders(frame_path)
                else:
                    shutil.copy(self.last_frame_path, frame_path)

                self.last_frame_path = frame_path
                self.last_params = current_params
                
                if frame_num % 30 == 0:
                    print(f"  Progress: {frame_num}/{total_frames} frames")
            
            print("Encoding video with FFmpeg...")
            self.encode_video(temp_dir, output_path)
        
        self.cleanup_browser()
        print(f"Video overlay created: {output_path}")
    
    def encode_video(self, frames_dir, output_path):
        """Encode PNG frames into MP4 video using FFmpeg."""
        cmd = [
            'ffmpeg',
            '-y',  # Overwrite output file
            '-framerate', str(self.fps),
            '-i', os.path.join(frames_dir, 'frame_%06d.png'),
            '-c:v', 'prores',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuva420p',  # Support alpha channel
            '-movflags', '+faststart',
            output_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(
        description='Generate video overlays from CSV data and HTML templates'
    )
    parser.add_argument('csv_file', help='Path to CSV file with timestamps and parameters')
    parser.add_argument('template_file', help='Path to HTML template file')
    parser.add_argument('output_file', help='Path for output MP4 file')
    parser.add_argument('--width', type=int, default=1920, help='Video width (default: 1920)')
    parser.add_argument('--height', type=int, default=1080, help='Video height (default: 1080)')
    parser.add_argument('--fps', type=int, default=30, help='Frames per second (default: 30)')
    parser.add_argument('--duration', type=float, help='Total video duration in seconds')
    
    args = parser.parse_args()
    
    generator = VideoOverlayGenerator(
        width=args.width,
        height=args.height,
        fps=args.fps
    )
    
    generator.generate_overlay(
        csv_path=args.csv_file,
        template_path=args.template_file,
        output_path=args.output_file,
        duration=args.duration
    )


if __name__ == '__main__':
    main()
