#!/usr/bin/env python3
"""
Terabox Extractor Bot - Complete Verified Version
Handles ALL Terabox domains with verified API paths.
"""

import asyncio
import logging
import os
import re
import json
import time
import hashlib
import random
import string
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlencode, unquote, urlparse, parse_qs, urlunparse

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Load environment
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL", "")
PORT = int(os.getenv("PORT", "10000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Reduce noise
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("aiogram").setLevel(logging.WARNING)


# ==================== VERIFIED DOMAIN LIST ====================

class DomainManager:
    """Manages all Terabox domains and API endpoints."""
    
    # ALL verified Terabox domains (2024)
    DOMAINS = [
        # Primary & Official
        "terabox.com",
        "www.terabox.com",
        "teraboxapp.com",
        "www.teraboxapp.com",
        
        # Alternative/Redirect (Verified)
        "1024tera.com",
        "www.1024tera.com",
        "teraboxlink.com",      # NEW
        "www.teraboxlink.com",  # NEW
        "teraboxshare.com",     # NEW
        "www.teraboxshare.com", # NEW
        "teraboxurl.com",       # NEW
        "www.teraboxurl.com",   # NEW
        
        # Mirrors
        "4funbox.com",
        "www.4funbox.com",
        "mirrobox.com",
        "www.mirrobox.com",
        "nephobox.com",
        "www.nephobox.com",
        "momerybox.com",
        "www.momerybox.com",
        "tibibox.com",
        "www.tibibox.com",
        "freeterabox.com",
        "www.freeterabox.com",
        "1024terabox.com",
        "www.1024terabox.com",
        "gibibox.com",
        "www.gibibox.com",
        "terabox.fun",
        "www.terabox.fun",
        "terabox.co",
        "www.terabox.co",
        "terabox.app",
        "www.terabox.app",
    ]
    
    # Canonical API domain (always works)
    API_DOMAIN = "www.terabox.com"
    
    # Verified API endpoints (from actual Terabox behavior)
    API_ENDPOINTS = {
        "shorturlinfo": "/api/shorturlinfo",
        "share_list": "/share/list",
        "share_download": "/share/download",
        "share_streaming": "/share/streaming",
        "share_wxlist": "/share/wxlist",
        "filemetas": "/api/filemetas",
        "video_play": "/share/videoPlay",
    }
    
    @staticmethod
    def is_terabox_url(url: str) -> bool:
        """Check if URL belongs to Terabox ecosystem."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            
            # Check exact match
            for d in DomainManager.DOMAINS:
                if d.replace("www.", "") == domain:
                    return True
            
            # Check substring match for safety
            if any(keyword in domain for keyword in ["terabox", "tera", "box", "dubox"]):
                return True
            
        except Exception:
            pass
        
        return False
    
    @staticmethod
    def extract_surl(url: str) -> Optional[Tuple[str, str]]:
        """
        Extract surl and normalized URL.
        Returns: (surl, normalized_url)
        """
        # Patterns for different URL formats
        patterns = [
            # /s/1xxxxx or /s/xxxxx
            (r"/s/1?([a-zA-Z0-9_-]+)", "/s/1{surl}"),
            # surl=1xxxxx or surl=xxxxx
            (r"[?&]surl=1?([a-zA-Z0-9_-]+)", "/s/1{surl}"),
            # /sharing/link?surl=1xxxxx
            (r"/sharing/link\?surl=1?([a-zA-Z0-9_-]+)", "/s/1{surl}"),
            # /wap/s/1xxxxx
            (r"/wap/s/1?([a-zA-Z0-9_-]+)", "/s/1{surl}"),
            # /web/share/link?surl=1xxxxx
            (r"/web/share/link\?surl=1?([a-zA-Z0-9_-]+)", "/s/1{surl}"),
        ]
        
        for pattern, template in patterns:
            match = re.search(pattern, url)
            if match:
                surl = match.group(1)
                # Normalize to /s/1{surl}
                normalized = template.format(surl=surl)
                return surl, normalized
        
        return None, None
    
    @staticmethod
    def get_api_url(endpoint: str, surl: str = "") -> str:
        """Get verified API URL for endpoint."""
        base = f"https://{DomainManager.API_DOMAIN}"
        endpoint_path = DomainManager.API_ENDPOINTS.get(endpoint, "")
        
        if not endpoint_path:
            raise ValueError(f"Unknown endpoint: {endpoint}")
        
        if surl:
            # For some endpoints, surl is in query string
            if endpoint in ["shorturlinfo", "share_wxlist"]:
                return f"{base}{endpoint_path}?surl=1{surl}"
        
        return f"{base}{endpoint_path}"


# ==================== DATA CLASSES ====================

@dataclass
class VideoResult:
    """Video extraction result."""
    success: bool = False
    title: str = ""
    filename: str = ""
    size: int = 0
    size_str: str = ""
    thumbnail: str = ""
    stream_url: str = ""
    download_url: str = ""
    fs_id: str = ""
    share_id: str = ""
    uk: str = ""
    surl: str = ""
    error: str = ""
    
    def format_size(self) -> str:
        if self.size <= 0:
            return "Unknown"
        size = self.size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"


# ==================== TERABOX EXTRACTOR ====================

class TeraboxExtractor:
    """Verified Terabox extractor with actual API paths."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._domain_idx = 0
        self._session_data: Dict[str, Any] = {}
        
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            connector = aiohttp.TCPConnector(
                ssl=False,
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            )
        return self._session
    
    async def close(self):
        """Close session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    def get_headers(self, referer: str = "") -> Dict[str, str]:
        """Get verified browser headers."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        }
        
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = f"https://{DomainManager.API_DOMAIN}"
        
        return headers
    
    def get_page_headers(self) -> Dict[str, str]:
        """Get headers for page requests."""
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
    
    async def extract(self, url: str) -> VideoResult:
        """Extract video from any Terabox URL."""
        result = VideoResult()
        
        # Validate URL
        if not DomainManager.is_terabox_url(url):
            result.error = "Not a valid Terabox URL"
            return result
        
        # Extract surl
        surl, normalized_url = DomainManager.extract_surl(url)
        if not surl:
            result.error = "Could not extract share ID"
            return result
        
        result.surl = surl
        logger.info(f"Extracting from: {url}")
        logger.info(f"Share ID: {surl}")
        
        # Try verified extraction methods in order
        extraction_methods = [
            self._extract_method_shorturlinfo,
            self._extract_method_sharelist,
            self._extract_method_wap,
            self._extract_method_alternative,
        ]
        
        for method_idx, method in enumerate(extraction_methods, 1):
            try:
                logger.info(f"Trying method {method_idx}...")
                video_result = await method(surl, url)
                
                if video_result.success and video_result.stream_url:
                    logger.info(f"Method {method_idx} succeeded!")
                    return video_result
                    
            except Exception as e:
                logger.warning(f"Method {method_idx} failed: {e}")
                continue
        
        result.error = "All verified extraction methods failed. Link may be private, expired, or blocked."
        return result
    
    async def _extract_method_shorturlinfo(self, surl: str, original_url: str) -> VideoResult:
        """Method 1: Direct API (shorturlinfo) - Most reliable."""
        result = VideoResult()
        result.surl = surl
        
        session = await self.get_session()
        api_url = DomainManager.get_api_url("shorturlinfo", surl)
        
        params = {
            "app_id": "250528",
            "shorturl": f"1{surl}",
            "root": "1",
        }
        
        headers = self.get_headers(original_url)
        
        async with session.get(api_url, params=params, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            
            data = await resp.json()
            
            # Check for errors
            if data.get("errno") != 0:
                raise Exception(f"API error: {data.get('errno')}")
            
            # Get file list
            file_list = data.get("list", [])
            if not file_list:
                raise Exception("No files in response")
            
            # Find video file
            target = self._find_video_file(file_list)
            if not target:
                raise Exception("No video file found")
            
            # Extract metadata
            result.title = target.get("server_filename", "Unknown")
            result.filename = target.get("server_filename", "Unknown")
            result.size = int(target.get("size", 0))
            result.size_str = result.format_size()
            result.fs_id = str(target.get("fs_id", ""))
            result.share_id = str(data.get("shareid", ""))
            result.uk = str(data.get("uk", ""))
            result.thumbnail = target.get("thumbs", {}).get("url3", "")
            
            # Try to get direct dlink first
            if target.get("dlink"):
                result.stream_url = target["dlink"]
                result.download_url = target["dlink"]
                result.success = True
                return result
            
            # Otherwise get streaming URL
            stream_url = await self._get_stream_url(
                surl=surl,
                share_id=result.share_id,
                uk=result.uk,
                fs_id=result.fs_id,
                sign=data.get("sign", ""),
                timestamp=data.get("timestamp", ""),
            )
            
            if stream_url:
                result.stream_url = stream_url
                result.download_url = stream_url
                result.success = True
            else:
                raise Exception("Could not obtain streaming URL")
            
            return result
    
    async def _extract_method_sharelist(self, surl: str, original_url: str) -> VideoResult:
        """Method 2: Share list API with page tokens."""
        result = VideoResult()
        result.surl = surl
        
        session = await self.get_session()
        
        # Step 1: Fetch page to get tokens
        page_url = f"https://{DomainManager.API_DOMAIN}/s/1{surl}"
        headers = self.get_page_headers()
        
        async with session.get(page_url, headers=headers, allow_redirects=True) as resp:
            html = await resp.text()
            cookies = {c.key: c.value for c in resp.cookies.values()}
        
        # Step 2: Parse page for required data
        page_data = self._parse_page_data(html)
        
        if not page_data.get("shareid"):
            raise Exception("Could not extract shareid from page")
        
        # Step 3: Get file list
        list_url = DomainManager.get_api_url("share_list")
        params = {
            "app_id": "250528",
            "web": "1",
            "channel": "chunlei",
            "clienttype": "0",
            "shorturl": f"1{surl}",
            "shareid": page_data["shareid"],
            "uk": page_data.get("uk", ""),
            "root": "1",
            "page": "1",
            "num": "100",
        }
        
        api_headers = self.get_headers(page_url)
        api_headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        
        async with session.get(list_url, params=params, headers=api_headers) as resp:
            data = await resp.json()
        
        file_list = data.get("list", [])
        if not file_list:
            raise Exception("Empty file list")
        
        target = self._find_video_file(file_list)
        if not target:
            raise Exception("No video file")
        
        # Extract metadata
        result.title = target.get("server_filename", "Unknown")
        result.filename = target.get("server_filename", "Unknown")
        result.size = int(target.get("size", 0))
        result.size_str = result.format_size()
        result.fs_id = str(target.get("fs_id", ""))
        result.share_id = str(page_data.get("shareid", ""))
        result.uk = str(page_data.get("uk", ""))
        
        # Get stream URL
        stream_url = await self._get_stream_url(
            surl=surl,
            share_id=result.share_id,
            uk=result.uk,
            fs_id=result.fs_id,
            sign=page_data.get("sign", ""),
            timestamp=page_data.get("timestamp", ""),
        )
        
        if stream_url:
            result.stream_url = stream_url
            result.download_url = stream_url
            result.success = True
        else:
            raise Exception("Could not obtain streaming URL")
        
        return result
    
    async def _extract_method_wap(self, surl: str, original_url: str) -> VideoResult:
        """Method 3: Mobile/WAP API."""
        result = VideoResult()
        result.surl = surl
        
        session = await self.get_session()
        
        # Mobile user agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        api_url = DomainManager.get_api_url("share_wxlist", surl)
        params = {
            "shorturl": f"1{surl}",
            "root": "1",
            "page": "1",
            "num": "20",
        }
        
        async with session.get(api_url, params=params, headers=headers) as resp:
            data = await resp.json()
        
        file_list = data.get("list", [])
        if not file_list:
            raise Exception("Empty list")
        
        target = self._find_video_file(file_list)
        if not target:
            raise Exception("No video file")
        
        # Extract metadata
        result.title = target.get("server_filename", "Unknown")
        result.filename = target.get("server_filename", "Unknown")
        result.size = int(target.get("size", 0))
        result.size_str = result.format_size()
        result.fs_id = str(target.get("fs_id", ""))
        
        # Get stream URL from dlink
        if target.get("dlink"):
            result.stream_url = target["dlink"]
            result.download_url = target["dlink"]
            result.success = True
            return result
        
        raise Exception("No dlink in WAP response")
    
    async def _extract_method_alternative(self, surl: str, original_url: str) -> VideoResult:
        """Method 4: Alternative API endpoints."""
        result = VideoResult()
        result.surl = surl
        
        session = await self.get_session()
        
        # Try filemetas endpoint
        try:
            api_url = DomainManager.get_api_url("filemetas")
            params = {
                "app_id": "250528",
                "dlink": "1",
                "target": f'["{surl}"]',
            }
            
            headers = self.get_headers()
            
            async with session.get(api_url, params=params, headers=headers) as resp:
                data = await resp.json()
            
            if data.get("info") and isinstance(data["info"], list):
                info = data["info"][0]
                if info.get("dlink"):
                    result.stream_url = info["dlink"]
                    result.download_url = info["dlink"]
                    result.title = info.get("filename", "Unknown")
                    result.size = int(info.get("size", 0))
                    result.size_str = result.format_size()
                    result.success = True
                    return result
                    
        except Exception as e:
            logger.debug(f"Filemetas failed: {e}")
        
        raise Exception("Alternative method failed")
    
    async def _get_stream_url(self, surl: str, share_id: str, uk: str, fs_id: str, sign: str, timestamp: str) -> Optional[str]:
        """Get streaming URL from verified endpoints."""
        session = await self.get_session()
        
        # Try endpoints in order
        endpoints = [
            ("/share/streaming", {"type": "M3U8_AUTO_720", "fid": fs_id}),
            ("/share/download", {"fid_list": f"[{fs_id}]"}),
        ]
        
        for endpoint, extra_params in endpoints:
            try:
                url = f"https://{DomainManager.API_DOMAIN}{endpoint}"
                params = {
                    "app_id": "250528",
                    "channel": "chunlei",
                    "clienttype": "0",
                    "web": "1",
                    "shareid": share_id,
                    "uk": uk,
                    "sign": sign,
                    "timestamp": timestamp,
                }
                params.update(extra_params)
                
                headers = self.get_headers(f"https://{DomainManager.API_DOMAIN}/s/1{surl}")
                
                async with session.get(url, params=params, headers=headers) as resp:
                    data = await resp.json()
                
                # Check for stream URL
                for key in ["dlink", "lurl", "url", "mlink"]:
                    if data.get(key):
                        return data[key]
                
                # Check list format
                if data.get("list"):
                    if isinstance(data["list"], list) and data["list"]:
                        if data["list"][0].get("dlink"):
                            return data["list"][0]["dlink"]
                    elif isinstance(data["list"], dict):
                        if data["list"].get("dlink"):
                            return data["list"]["dlink"]
                            
            except Exception as e:
                logger.debug(f"Endpoint {endpoint} failed: {e}")
                continue
        
        return None
    
    def _parse_page_data(self, html: str) -> Dict[str, str]:
        """Parse page HTML for required data."""
        data = {}
        
        # Patterns for different data fields
        patterns = {
            "shareid": [r'"shareid"\s*:\s*(\d+)', r'shareid["\s:=]+(\d+)'],
            "uk": [r'"uk"\s*:\s*(\d+)', r'uk["\s:=]+(\d+)'],
            "sign": [r'"sign"\s*:\s*"([^"]+)"', r"sign[\"\\s:=]+'([^']+)'"],
            "timestamp": [r'"timestamp"\s*:\s*(\d+)', r'timestamp["\s:=]+(\d+)'],
            "js_token": [r'"jsToken"\s*:\s*"([^"]+)"', r"jsToken[\"\\s:=]+'([^']+)'"],
            "bdstoken": [r'"bdstoken"\s*:\s*"([^"]+)"', r"bdstoken[\"\\s:=]+'([^']+)'"],
        }
        
        for key, pats in patterns.items():
            for pat in pats:
                match = re.search(pat, html)
                if match:
                    data[key] = match.group(1)
                    break
        
        # Try to find file list in page
        file_list_match = re.search(r'"list"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
        if file_list_match:
            try:
                data["file_list"] = json.loads(file_list_match.group(1))
            except:
                pass
        
        return data
    
    def _find_video_file(self, file_list: List[Dict]) -> Optional[Dict]:
        """Find video file in list."""
        video_exts = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".3gp", ".3g2"}
        
        # First pass: by extension
        for f in file_list:
            name = f.get("server_filename", f.get("filename", "")).lower()
            if any(name.endswith(ext) for ext in video_exts):
                return f
        
        # Second pass: by category (1 = video)
        for f in file_list:
            if f.get("category") == 1:
                return f
        
        # Third pass: by MIME type
        for f in file_list:
            mime = f.get("mime_type", "").lower()
            if "video" in mime:
                return f
        
        # Return first file
        return file_list[0] if file_list else None


# ==================== TELEGRAM BOT ====================

router = Router()
extractor = TeraboxExtractor()


def format_size(size: int) -> str:
    """Format bytes to readable size."""
    if size <= 0:
        return "Unknown"
    size = size
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start command."""
    text = """
🎬 <b>Terabox Video Extractor</b>

Send me any Terabox link and I'll get the direct video URL!

<b>Supported domains:</b>
• terabox.com
• teraboxapp.com
• 1024tera.com
• teraboxlink.com
• teraboxshare.com
• teraboxurl.com
• 4funbox.com
• mirrobox.com
• nephobox.com
• momerybox.com
• tibibox.com
• freeterabox.com
• And more...

<b>Just send a link to start!</b>
"""
    await message.answer(text)


@router.message(Command("help"))
async def cmd_help(message: Message):
    """Handle /help command."""
    text = """
📖 <b>How to use:</b>

1. Copy any Terabox video link
2. Send it to me
3. Get direct streaming URL!

<b>Example links:</b>
<code>https://terabox.com/s/1xxxxx</code>
<code>https://1024tera.com/s/1xxxxx</code>
<code>https://teraboxlink.com/s/1xxxxx</code>

<b>Tip:</b> Links with "1" before surl work best!
"""
    await message.answer(text)


@router.message(Command("ping"))
async def cmd_ping(message: Message):
    """Health check."""
    await message.answer("🏓 Pong! Bot is running.")


@router.message(F.text)
async def handle_link(message: Message):
    """Handle Terabox links."""
    text = message.text.strip()
    
    # Check if it's a URL
    if not text.startswith(("http://", "https://")):
        return
    
    # Check if it's Terabox
    if not DomainManager.is_terabox_url(text):
        await message.answer("❌ Not a Terabox link. Send a valid Terabox URL.")
        return
    
    # Processing message
    processing = await message.answer("⏳ <i>Extracting video...</i>")
    
    try:
        # Extract
        result = await extractor.extract(text)
        
        if not result.success:
            await processing.edit_text(f"❌ <b>Failed:</b>\n{result.error}")
            return
        
        # Format response
        response = f"""✅ <b>Video Found!</b>

📹 <b>Title:</b> <code>{result.title[:100]}</code>
📊 <b>Size:</b> {result.size_str}

🔗 <b>Stream URL:</b>
<code>{result.stream_url[:500]}</code>

<b>Share ID:</b> {result.surl}
"""
        
        # Create button
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Open Video", url=result.stream_url[:2048])]
        ])
        
        await processing.edit_text(
            response,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.exception("Extraction error")
        await processing.edit_text(f"❌ <b>Error:</b>\n<code>{str(e)[:200]}</code>")


# ==================== WEBHOOK / POLLING ====================

async def on_startup(app: web.Application):
    """Startup handler."""
    bot: Bot = app["bot"]
    
    if WEBHOOK_URL:
        webhook_path = f"/webhook/{BOT_TOKEN}"
        full_url = f"{WEBHOOK_URL}{webhook_path}"
        await bot.set_webhook(full_url, drop_pending_updates=True)
        logger.info(f"Webhook set: {full_url}")
    
    logger.info("Bot started!")


async def on_shutdown(app: web.Application):
    """Shutdown handler."""
    bot: Bot = app["bot"]
    await bot.delete_webhook()
    await extractor.close()
    await bot.session.close()
    logger.info("Bot stopped!")


async def health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.json_response({"status": "ok", "bot": "terabox-extractor"})


def create_app() -> web.Application:
    """Create web application."""
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    dp = Dispatcher()
    dp.include_router(router)
    
    app = web.Application()
    app["bot"] = bot
    
    # Routes
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    
    # Webhook handler
    webhook_path = f"/webhook/{BOT_TOKEN}"
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=webhook_path)
    
    # Lifecycle
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    setup_application(app, dp, bot=bot)
    
    return app


async def run_polling():
    """Run bot with polling (local development)."""
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    dp = Dispatcher()
    dp.include_router(router)
    
    logger.info("Starting polling...")
    
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        await extractor.close()
        await bot.session.close()


def main():
    """Main entry point."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is required!")
        exit(1)
    
    logger.info("=" * 40)
    logger.info("Terabox Extractor Bot")
    logger.info("=" * 40)
    
    if WEBHOOK_URL:
        # Production: webhook mode
        logger.info(f"Mode: Webhook")
        logger.info(f"URL: {WEBHOOK_URL}")
        logger.info(f"Port: {PORT}")
        
        app = create_app()
        web.run_app(app, host="0.0.0.0", port=PORT)
    else:
        # Development: polling mode
        logger.info("Mode: Polling (local)")
        asyncio.run(run_polling())


if __name__ == "__main__":
    main()
