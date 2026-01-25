#!/usr/bin/env python3

import sys
import time
import shutil
import argparse
import subprocess
import base64
import mimetypes
import re

from re import sub
from os import walk
from os.path import dirname, abspath, join, getmtime, isfile, basename
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# base directory for the git repository clone
repo_dir = dirname(dirname(abspath(__file__)))

# test to see if asciidoctor is available
if not shutil.which("asciidoctor"):
    sys.exit("ERROR: asciidoctor is not installed or is not in your PATH")


def main():
    """
    Walk through the repository directory looking for asciidoc files
    to convert to HTML. Only files with the .adoc file extension that
    have been modified since the last run will be converted.
    """
    parser = argparse.ArgumentParser(description="convert asciidoc to html")
    parser.add_argument(
        "-f", "--force", action="store_true", help="Force regeneration of all files"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Suppress output"
    )
    parser.add_argument(
        "-w", "--watch", action="store_true", help="Watch for changes"
    )
    parser.add_argument(
        "--no-package", action="store_true", help="Skip packaging (don't inline resources)"
    )
    args = parser.parse_args()

    if args.watch:
        while True:
            build(repo_dir, args)
            time.sleep(1)
    else:
        build(repo_dir, args)


def build(repo_dir, args):
    """
    Process the contents of the given repository directory with the 
    given command line arguments.
    """
    for dir_name, dirs, files in walk(repo_dir):
        for filename in files:
            if filename.endswith(".adoc"):
                adoc_path = join(dir_name, filename)
                adoc_mtime = getmtime(adoc_path)

                html_path = sub(r"\.adoc$", ".html", adoc_path)
                html_mtime = getmtime(html_path) if isfile(html_path) else 0

                if args.force or html_mtime < adoc_mtime:
                    if asciidoc(adoc_path, html_path, args.quiet):
                        # Package slides into self-contained HTML
                        if 'slides' in basename(adoc_path) and not args.no_package:
                            package_html(html_path, args.quiet)


def asciidoc(adoc_file, html_file, quiet=False):
    """
    Convert an asciidoc file to HTML using asciidoctor or asciidoctor-revealjs.
    Returns True if no errors or warnings were generated and False if they
    were.
    """

    if 'slides' in basename(adoc_file):
        # Use asciidoctor with reveal.js backend for slides
        cmd = ["asciidoctor", "-r", "@asciidoctor/reveal.js", "-b", "revealjs", 
               adoc_file, "-o", html_file]
    else:
        cmd = ["asciidoctor", adoc_file, "-o", html_file]

    if not quiet:
        print(adoc_file)

    # Use shell=True on Windows to properly execute .cmd/.bat files
    result = subprocess.run(cmd, capture_output=True, shell=sys.platform == "win32")
    
    if result.returncode != 0:
        print(f"ERROR: {adoc_file}")
        if result.stderr:
            print(f"  {result.stderr.decode('utf8')}")
        if result.stdout:
            print(f"  {result.stdout.decode('utf8')}")
        return False
    
    if result.stderr:
        # Warnings (non-fatal)
        print(f"WARNING: {adoc_file} - {result.stderr.decode('utf8')}")
    
    return True


# ============================================================================
# HTML Packaging Functions - Inline all resources into self-contained HTML
# ============================================================================

def get_mime_type(file_path):
    """Get MIME type for a file."""
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'


def read_url(url):
    """Fetch content from a URL."""
    try:
        with urlopen(url, timeout=30) as response:
            return response.read().decode('utf-8')
    except URLError as e:
        print(f"    Warning: Could not fetch {url}: {e}")
        return None
    except Exception as e:
        print(f"    Warning: Error fetching {url}: {e}")
        return None


def read_file_content(file_path):
    """Read content from a local file."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f"    Warning: Could not read {file_path}: {e}")
        return None


def image_to_base64(image_path):
    """Convert an image file to base64 data URI."""
    try:
        with open(image_path, 'rb') as f:
            data = f.read()
        mime_type = get_mime_type(str(image_path))
        b64 = base64.b64encode(data).decode('utf-8')
        return f"data:{mime_type};base64,{b64}"
    except Exception as e:
        print(f"    Warning: Could not encode image {image_path}: {e}")
        return None


def inline_css_urls(css_content, base_path):
    """Inline url() references in CSS (fonts, images)."""
    def replace_url(match):
        url = match.group(1).strip('\'"')
        
        # Skip data URIs and absolute URLs
        if url.startswith('data:') or url.startswith('http://') or url.startswith('https://'):
            return match.group(0)
        
        # Resolve relative path
        file_path = base_path / url
        if file_path.exists():
            data_uri = image_to_base64(file_path)
            if data_uri:
                return f"url({data_uri})"
        
        return match.group(0)
    
    return re.sub(r'url\(([^)]+)\)', replace_url, css_content)


def inline_stylesheets(html_content, html_path):
    """Replace <link rel="stylesheet"> with inline <style> blocks."""
    html_dir = html_path.parent
    
    def replace_link(match):
        href = match.group(1)
        
        # Fetch CSS content
        if href.startswith('http://') or href.startswith('https://'):
            css_content = read_url(href)
        else:
            css_path = html_dir / href
            css_content = read_file_content(css_path)
            if css_content:
                css_content = inline_css_urls(css_content, css_path.parent)
        
        if css_content:
            return f"<style>\n{css_content}\n</style>"
        else:
            return match.group(0)  # Keep original if fetch failed
    
    # Match <link rel="stylesheet" href="...">
    pattern = r'<link[^>]*rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\'][^>]*/?>'
    html_content = re.sub(pattern, replace_link, html_content)
    
    # Also match href before rel
    pattern = r'<link[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']stylesheet["\'][^>]*/?>'
    html_content = re.sub(pattern, replace_link, html_content)
    
    return html_content


def inline_scripts(html_content):
    """Replace external <script src="..."> with inline <script> blocks."""
    def replace_script(match):
        src = match.group(1)
        
        # Only inline CDN scripts
        if src.startswith('http://') or src.startswith('https://'):
            js_content = read_url(src)
            if js_content:
                return f"<script>\n{js_content}\n</script>"
        
        return match.group(0)  # Keep original if not CDN or fetch failed
    
    pattern = r'<script[^>]*src=["\']([^"\']+)["\'][^>]*></script>'
    return re.sub(pattern, replace_script, html_content)


def inline_images(html_content, html_path):
    """Convert local image src to base64 data URIs."""
    html_dir = html_path.parent
    
    def replace_img(match):
        full_tag = match.group(0)
        src = match.group(1)
        
        # Skip data URIs and external URLs
        if src.startswith('data:') or src.startswith('http://') or src.startswith('https://'):
            return full_tag
        
        # Resolve relative path
        img_path = html_dir / src
        if img_path.exists():
            data_uri = image_to_base64(img_path)
            if data_uri:
                return full_tag.replace(src, data_uri)
        
        return full_tag
    
    # Match img tags with src attribute
    pattern = r'<img[^>]*src=["\']([^"\']+)["\'][^>]*/?>'
    html_content = re.sub(pattern, replace_img, html_content)
    
    # Also handle background-image in style attributes
    def replace_bg_image(match):
        style = match.group(0)
        url_match = re.search(r'url\(["\']?([^"\')\s]+)["\']?\)', style)
        if url_match:
            src = url_match.group(1)
            if not src.startswith('data:') and not src.startswith('http'):
                img_path = html_dir / src
                if img_path.exists():
                    data_uri = image_to_base64(img_path)
                    if data_uri:
                        return style.replace(src, data_uri)
        return style
    
    pattern = r'style=["\'][^"\']*background[^"\']*url\([^)]+\)[^"\']*["\']'
    html_content = re.sub(pattern, replace_bg_image, html_content)
    
    # Handle data-background-image attributes (reveal.js)
    def replace_data_bg(match):
        attr = match.group(0)
        src = match.group(1)
        
        if not src.startswith('data:') and not src.startswith('http'):
            img_path = html_dir / 'images' / src
            if not img_path.exists():
                img_path = html_dir / src
            if img_path.exists():
                data_uri = image_to_base64(img_path)
                if data_uri:
                    return f'data-background-image="{data_uri}"'
        return attr
    
    pattern = r'data-background-image=["\']([^"\']+)["\']'
    html_content = re.sub(pattern, replace_data_bg, html_content)
    
    return html_content


def package_html(html_file, quiet=False):
    """Package an HTML file into a self-contained single file (in-place)."""
    html_path = Path(html_file)
    
    if not html_path.exists():
        print(f"    Error: File not found: {html_path}")
        return False
    
    if not quiet:
        print(f"  Packaging: {html_path.name}")
    
    # Read original HTML
    html_content = read_file_content(html_path)
    if html_content is None:
        return False
    
    # Inline all resources
    html_content = inline_stylesheets(html_content, html_path)
    html_content = inline_scripts(html_content)
    html_content = inline_images(html_content, html_path)
    
    # Write output (overwrite original)
    try:
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        if not quiet:
            size_kb = html_path.stat().st_size / 1024
            print(f"    Done ({size_kb:.0f} KB)")
        return True
    except Exception as e:
        print(f"    Error writing output: {e}")
        return False


if __name__ == "__main__":
    main()
